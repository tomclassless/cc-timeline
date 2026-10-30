"""Parse Claude Code transcripts and attribute the time between events."""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Tool results that wait for the human; their time counts as "user".
WAITS_ON_USER = {"AskUserQuestion", "ExitPlanMode", "mcp:ccd_director…request_directory"}
# turn origin -> background item kind
ORIGIN_KIND = {
    None: "notify",
    "task_notification": "notify",
    "task-notification": "notify",
    "peer": "peer",
    "sdk": "sdk",
}

# The transcript format is undocumented. These are the values this tool was written against;
# anything else is reported (see report.format_summary) so format changes are noticed early.
KNOWN_TYPES = {"user", "assistant", "system", "attachment", "queue-operation", "api-request",
               "api-request-shape", "file-history-snapshot", "progress", "summary",
               # Session metadata seen in newer Claude Code builds (desktop app included). They carry
               # no conversation turn, so they never split or attribute time.
               "agent-name", "api-request-blob", "artifact-autoreact-ledger", "artifact-comment-monitor",
               "atis-latch", "bridge-session", "cost-state", "custom-title", "file-history-delta",
               "frame-link", "last-prompt", "mode", "permission-mode", "relocated", "worktree-state"}
KNOWN_ORIGINS = {"human", "peer", "task_notification", "task-notification", "sdk"}

_FRAC_RE = re.compile(r"\.(\d+)")


def parse_ts(s):
    s = s.replace("Z", "+00:00")
    # Python 3.9 fromisoformat only accepts 3 or 6 fractional digits.
    s = _FRAC_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), s, count=1)
    return datetime.fromisoformat(s)


def tool_label(block):
    name = block.get("name", "?")
    inp = block.get("input") or {}
    if name in ("Task", "Agent"):
        return f"{name}({inp.get('subagent_type') or 'general-purpose'})"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"mcp:{parts[1][:12]}…{parts[-1]}" if len(parts) > 2 else name
    return name


def user_text(content):
    if isinstance(content, str):
        return content
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "text":
            return b.get("text", "")
    return ""


def match_agent(ref, agents):
    """Find the earlier Agent tool result mentioning this id and return its label."""
    keys = {ref, ref[1:]} if len(ref) > 8 else {ref}
    for text, label in reversed(agents):
        if any(k in text for k in keys):
            return label
    return None


def turn_origin(entry):
    """Origin marker of an entry; tolerates `origin` being a plain string or other junk."""
    o = entry.get("origin")
    return entry.get("turnOrigin") or (o.get("kind") if isinstance(o, dict) else o if isinstance(o, str) else None)


def classify(entry, tool_names, agents=None):
    """Return (category, detail); category is model / tool / bg / user / other.

    Detail is a tool label string, or for bg a tuple key rendered by i18n.item_name.
    `agents` collects Agent tool results so later reports map back to an agent type.
    """
    t = entry.get("type")
    msg = entry.get("message") or {}
    content = msg.get("content")
    if t == "assistant":
        for b in content if isinstance(content, list) else []:
            if b.get("type") == "tool_use":
                tool_names[b.get("id")] = tool_label(b)
        return "model", msg.get("model", "")
    if t == "user":
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    label = tool_names.get(b.get("tool_use_id"), "?")
                    if agents is not None and label.startswith(("Agent(", "Task(")):
                        agents.append((json.dumps([b.get("content"), entry.get("toolUseResult")],
                                                  ensure_ascii=False), label))
                    return "tool", label
        origin = turn_origin(entry)
        if origin == "human":
            return "user", ""
        text = user_text(content).lstrip()
        if origin or text.startswith("<task-notification"):
            kind = ORIGIN_KIND.get(origin, "other")
            base = (kind, origin if kind == "other" else None)
            o = entry.get("origin")
            ref = o.get("from") if isinstance(o, dict) else None
            if not ref:
                m = re.search(r"<task-id>([^<]+)</task-id>", text)
                ref = m and m.group(1).strip()
            if ref and agents is not None:
                agent = match_agent(ref, agents)
                return "bg", ("agent", agent) if agent else ("unmatched", base)
            return "bg", base
        if entry.get("isMeta"):
            return "other", "meta"
        return "user", ""
    return "other", t or "?"


def msg_id(entry):
    if entry.get("type") != "assistant":
        return None
    return (entry.get("message") or {}).get("id")


def shape(entry):
    """Event shape: types and XML tag names only, never content."""
    t = entry.get("type", "?")
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        kind = content[0].get("type", "?")
    else:
        kind = "text"
    text = user_text(content).lstrip() if t == "user" else ""
    if text.startswith("<"):
        # Tag name only: never attributes or content.
        kind = re.match(r"<[^\s>]*", text).group()[:30]
    sub = entry.get("subtype") or (entry.get("attachment") or {}).get("type")
    if isinstance(sub, str):
        kind = sub[:30]
    return f"{t}:{kind}"


def version_key(v):
    return tuple(int(n) for n in re.findall(r"\d+", v))


def count_format(e, stats):
    """Tally entry type, turn origin and Claude Code version of one raw entry."""
    stats["types"][str(e.get("type"))[:40]] += 1
    origin = turn_origin(e)
    if origin:
        stats["origins"][str(origin)[:40]] += 1
    v = e.get("version")
    if isinstance(v, str) and re.search(r"\d", v):
        stats["versions"].add(v[:30])


def load_entries(path, keep_sidechain=False, stats=None):
    """Timestamped entries sorted by time. `stats` (optional) tallies every parsed line."""
    entries = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict):
                continue
            if stats is not None and "type" in e:
                count_format(e, stats)
            # Old versions wrote subagent steps into the main file (isSidechain); that time is
            # already inside the Task tool call. Subagent files are entirely sidechain.
            if (e.get("isSidechain") and not keep_sidechain) or not e.get("timestamp"):
                continue
            entries.append(e)
    entries.sort(key=lambda e: e["timestamp"])
    return entries


def analyze(path, idle_cap):
    """Analyze one main-session transcript. idle_cap is in seconds. Returns None if too short."""
    fmt_stats = {"types": Counter(), "origins": Counter(), "versions": set()}
    entries = load_entries(path, stats=fmt_stats)
    if len(entries) < 2:
        return None

    tool_names = {}
    agents = []
    buckets = defaultdict(float)
    tools = defaultdict(float)
    tool_calls = defaultdict(int)
    agent_reports = defaultdict(int)  # background reports per agent label, counted even after long gaps
    steps = []
    segments = []  # (start, end, category) covering the whole session; idle gaps are "idle"

    def add(cat, detail, gap, ts, transition, start):
        segments.append((start, ts, "idle" if gap > idle_cap else cat))
        if gap > idle_cap:
            # A long gap is usually away / sleep / resume the next day: not any category.
            buckets["idle"] += gap
            return
        buckets[cat] += gap
        if cat in ("tool", "bg"):
            tools[detail] += gap
            tool_calls[detail] += 1
        steps.append((gap, cat, detail, ts, transition))

    prev = parse_ts(entries[0]["timestamp"])
    prev_shape = shape(entries[0])
    prev_mid = msg_id(entries[0])
    skipped = skipped_ts = None
    classify(entries[0], tool_names, agents)
    for e in entries[1:]:
        cat, detail = classify(e, tool_names, agents)
        if cat == "bg" and isinstance(detail, tuple) and detail[0] == "agent":
            agent_reports[detail[1]] += 1
        # Snapshots, hooks, progress etc. are not state changes; the gap goes to the next real event.
        if cat == "other":
            skipped, skipped_ts = shape(e), parse_ts(e["timestamp"])
            continue
        ts = parse_ts(e["timestamp"])
        transition = f"{prev_shape} → {shape(e)}"
        mid = msg_id(e)
        # Two different assistant messages with no user input or tool result in between means a
        # wake-up. Split at the last auxiliary event: before it is waiting, after it is the model.
        if cat == "model" and prev_mid and mid and mid != prev_mid:
            split = min(max(skipped_ts or ts, prev), ts)
            add("bg", ("wake", skipped), (split - prev).total_seconds(), split,
                f"{prev_shape} → {skipped}", prev)
            prev = split
        if cat == "tool" and detail in WAITS_ON_USER:
            cat = "user"
        add(cat, detail, max((ts - prev).total_seconds(), 0), ts, transition, prev)
        prev, prev_shape, prev_mid = ts, shape(e), mid
        skipped = skipped_ts = None

    first, last = parse_ts(entries[0]["timestamp"]), parse_ts(entries[-1]["timestamp"])
    cwd = next((e.get("cwd") for e in entries if e.get("cwd")), "") or path.parent.name
    return {
        "id": path.stem,
        "project": Path(cwd).name,
        "start": first,
        "wall": (last - first).total_seconds(),
        "buckets": buckets,
        "tools": tools,
        "tool_calls": tool_calls,
        "agent_reports": agent_reports,
        "entries": entries,
        "api_errors": sum(1 for e in entries if e.get("isApiErrorMessage")),
        "steps": steps,
        "segments": segments,
        "prompts": sum(1 for e in entries if classify(e, {})[0] == "user"),
        "path": path,
        "agent_corpus": agents,
        "format": fmt_stats,
    }


def default_root():
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base).expanduser() if base else Path.home() / ".claude") / "projects"


def find_sessions(root, project=None, session=None, since=None):
    """Main-session transcripts under root/<project-dir>/*.jsonl (subagents/ are deeper, so skipped).

    `since` is only a cheap pre-screen on file mtime: a file last written before `since`
    cannot hold a session that started after it. The exact filter uses the first timestamp.
    """
    needle = re.sub(r"[^A-Za-z0-9]", "-", project) if project else None
    found = []
    for p in sorted(root.glob("*/*.jsonl")):
        if needle and needle not in p.parent.name:
            continue
        if session and not p.stem.startswith(session):
            continue
        if not session and since is not None and \
                datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) < since:
            continue
        found.append(p)
    return found


# These fields are enums or origin markers; printing their values cannot leak conversation text.
TRACE_VALUE_KEYS = ["isMeta", "origin", "promptSource", "turnOrigin", "turnPosition",
                    "operation", "reason", "querySource", "preventedContinuation",
                    "apiErrorStatus", "isApiErrorMessage"]

REASON_RE = re.compile(r"[a-z0-9_]{1,40}")


def trace_value(key, value):
    """Printable form of a TRACE_VALUE_KEYS field; free-text `reason` values are masked."""
    if key == "reason" and not (isinstance(value, str) and REASON_RE.fullmatch(value)):
        return '"<text>"'
    return json.dumps(value, ensure_ascii=False)[:60]

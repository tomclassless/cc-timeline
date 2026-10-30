"""Build report data and render it as CLI text, JSON or HTML."""
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .agents import PHASE_GROUPS, agent_files, phase_group, scan_agent
from .i18n import agent_name, item_name, labels
from .transcript import (KNOWN_ORIGINS, KNOWN_TYPES, TRACE_VALUE_KEYS, parse_ts, shape, trace_value,
                         version_key)

TEMPLATE = Path(__file__).parent / "templates" / "report.html"
BUCKET_KEYS = ("model", "tool", "bg", "user")
ALL_KEYS = (*BUCKET_KEYS, "idle")
BUILTIN_AGENTS = {"general-purpose", "Explore", "Plan", "statusline-setup", "claude-code-guide"}
_AGENT_LABEL = re.compile(r"(Agent|Task)\((.*)\)", re.S)
_MCP_LABEL = re.compile(r"mcp:([^…]*)…(.*)", re.S)


def fmt(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def pct(part, whole):
    return f"{part / whole * 100:4.0f}%" if whole else "   -"


class Scrubber:
    """Maps custom agent types to agent-N and MCP server names to mcp-N (stable per run)."""

    def __init__(self):
        self.agents = {}
        self.mcps = {}

    def __call__(self, label):
        if not isinstance(label, str):
            return label
        m = _AGENT_LABEL.fullmatch(label)
        if m:
            kind, name = m.groups()
            if name not in BUILTIN_AGENTS:
                name = self.agents.setdefault(name, f"agent-{len(self.agents) + 1}")
            return f"{kind}({name})"
        m = _MCP_LABEL.fullmatch(label)
        if m:
            return f"mcp:{self._mcp(m.group(1))}…{m.group(2)}"
        if label.startswith("mcp__"):
            parts = label.split("__", 2)
            parts[1] = self._mcp(parts[1])
            return "__".join(parts)
        return label

    def _mcp(self, name):
        return self.mcps.setdefault(name, f"mcp-{len(self.mcps) + 1}")

    def key(self, key):
        if isinstance(key, tuple):
            return tuple(self.key(k) for k in key)
        return self(key)


def redact(results):
    """Replace project names, session ids, custom agent types and MCP server names (in place).

    Tool names, built-in agent types and all timings are kept. Agent labels in agent_corpus stay
    raw (they are only used for matching); agent_runs() scrubs them once, at output time.
    """
    names = {}
    scrub = Scrubber()
    for r in results:
        r["project"] = names.setdefault(r["project"], f"project-{len(names) + 1}")
        r["id"] = hashlib.sha256(r["id"].encode("utf-8")).hexdigest()[:8]
        tools, calls = defaultdict(float), defaultdict(int)
        for k, v in r["tools"].items():
            tools[scrub.key(k)] += v
        for k, v in r["tool_calls"].items():
            calls[scrub.key(k)] += v
        r["tools"], r["tool_calls"] = tools, calls
        r["steps"] = [(g, c, scrub.key(d), t, tr) for g, c, d, t, tr in r["steps"]]
        r["scrub"] = scrub
    return results


def agent_runs(r, idle_cap=None):
    """Subagent runs of a session as dicts (label, path, phases, start, end, segments).

    Parsed once per session and cached in the result, so --html and --agents share the work.
    """
    if "agent_runs" not in r:
        scrub = r.get("scrub") or (lambda label: label)
        runs = []
        for label, f in agent_files(r["path"], r["agent_corpus"]):
            runs.append({"label": scrub(label), "path": f, **scan_agent(f, idle_cap)})
        r["agent_runs"] = runs
    return r["agent_runs"]


def aggregate(results):
    """Totals shared by the CLI summary and the report: buckets, tool seconds/calls, errors."""
    totals = defaultdict(float)
    tools = defaultdict(float)
    calls = defaultdict(int)
    for r in results:
        for k in ALL_KEYS:
            totals[k] += r["buckets"][k]
        for k, v in r["tools"].items():
            tools[k] += v
            calls[k] += r["tool_calls"][k]
    return {"n": len(results), "totals": totals, "tools": tools, "calls": calls,
            "api_errors": sum(r["api_errors"] for r in results)}


def format_summary(results):
    """What the scan saw: entry types, turn origins, Claude Code versions, and unknown values."""
    types, origins, versions = Counter(), Counter(), set()
    for r in results:
        f = r["format"]
        types.update(f["types"])
        origins.update(f["origins"])
        versions |= f["versions"]
    ordered = sorted(versions, key=version_key)
    return {"types": dict(sorted(types.items())), "origins": dict(sorted(origins.items())),
            "versions": {"min": ordered[0] if ordered else None, "max": ordered[-1] if ordered else None},
            "unknown": {"types": {k: v for k, v in sorted(types.items()) if k not in KNOWN_TYPES},
                        "origins": {k: v for k, v in sorted(origins.items()) if k not in KNOWN_ORIGINS}}}


def format_warning(L, summary):
    """One-line warning for unknown types/origins; empty string when everything is known."""
    unknown = summary["unknown"]
    parts = []
    for key in ("types", "origins"):
        if unknown[key]:
            names = ", ".join(f"{k} x{v}" for k, v in unknown[key].items())
            parts.append(f"{L['fmt_' + key]}: {names}")
    return L["warn_format"].format(what="; ".join(parts)) if parts else ""


def session_rows(results, L):
    """One dict per session; shared by the HTML sessions table and the CSV export."""
    rows = []
    for r in results:
        b = r["buckets"]
        top = max(r["tools"].items(), key=lambda kv: kv[1], default=("", 0))
        rows.append({"start": f"{r['start'].astimezone():%Y-%m-%d %H:%M}", "project": r["project"],
                     "id": r["id"][:8], "wall": r["wall"], "idle": b["idle"],
                     **{k: b[k] for k in BUCKET_KEYS}, "top": item_name(L, top[0]),
                     "errors": r["api_errors"]})
    return rows


MAX_AGENT_ROWS = 15   # beyond this, non-overlapping agents of one type share a row
MIN_GAP = 60          # an away stretch shorter than this stays on the time axis


def rel_segments(segments, origin, group=None):
    """[(start, end, cat)] -> [[sec, sec, cat]] relative to origin, whole seconds.

    Adjacent segments of the same category are merged; zero-length ones are dropped.
    """
    out = []
    for a, b, cat in segments:
        s = max(round((a - origin).total_seconds()), 0)
        e = max(round((b - origin).total_seconds()), 0)
        if e <= s:
            continue
        cat = group(cat) if group else cat
        if out and out[-1][2] == cat and out[-1][1] == s:
            out[-1][1] = e
        else:
            out.append([s, e, cat])
    return out


def union(spans):
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def away_gaps(main, agent_rows):
    """Away stretches of the main thread during which no subagent was working (axis breaks)."""
    busy = union([(s, e) for a in agent_rows for s, e, _ in a["segs"]])
    gaps = []
    for s, e, cat in main:
        if cat != "idle":
            continue
        cur = s
        for bs, be in busy:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                gaps.append([cur, bs])
            cur = max(cur, be)
        if cur < e:
            gaps.append([cur, e])
    return [g for g in gaps if g[1] - g[0] >= MIN_GAP]


def assign_rows(agents):
    """Row per agent; with many agents, non-overlapping ones of the same type share a row."""
    ordered = sorted(range(len(agents)), key=lambda i: (agents[i]["start"], i))
    if len(agents) <= MAX_AGENT_ROWS:
        for row, i in enumerate(ordered):
            agents[i]["row"] = row
        return len(agents)
    lanes = []  # [type, end]
    for i in ordered:
        a = agents[i]
        lane = next((n for n, (t, end) in enumerate(lanes) if t == a["type"] and end <= a["start"]), None)
        if lane is None:
            lanes.append([a["type"], a["end"]])
            lane = len(lanes) - 1
        else:
            lanes[lane][1] = a["end"]
        a["row"] = lane
    return len(lanes)


def timeline_data(results, L, idle_cap, limit):
    """Gantt data for the `limit` sessions with the most active time (0 = none)."""
    shown = sorted(results, key=lambda r: -(r["buckets"]["model"] + r["buckets"]["tool"]
                                            + r["buckets"]["bg"]))[:max(limit, 0)]
    shown.sort(key=lambda r: r["start"], reverse=True)
    sessions = []
    for r in shown:
        origin = r["start"]
        main = rel_segments(r["segments"], origin)
        agents = []
        for run in agent_runs(r, idle_cap):
            segs = rel_segments(run["segments"], origin, phase_group)
            if not segs or not run["start"]:
                continue
            agents.append({"type": agent_name(L, run["label"]), "segs": segs,
                           "start": max(round((run["start"] - origin).total_seconds()), 0),
                           "end": max(round((run["end"] - origin).total_seconds()), 0)})
        rows = assign_rows(agents)
        agents.sort(key=lambda a: (a["row"], a["start"]))
        b = r["buckets"]
        busy = sum(b[k] for k in BUCKET_KEYS)
        local = origin.astimezone()
        sessions.append({
            "id": r["id"][:8], "project": r["project"], "start": f"{local:%Y-%m-%d %H:%M}",
            "clock0": local.hour * 3600 + local.minute * 60 + local.second,
            "wall": round(r["wall"]), "bgShare": b["bg"] / busy if busy else 0,
            "main": main, "agents": agents, "rows": rows, "gaps": away_gaps(main, agents)})
    default = max(range(len(sessions)), key=lambda i: sessions[i]["bgShare"], default=0)
    return {"sessions": sessions, "default": default}


def group_key(r, by):
    local = r["start"].astimezone()
    if by == "day":
        return f"{local:%Y-%m-%d}"
    if by == "week":
        year, week, _ = local.isocalendar()
        return f"{year}-W{week:02d}"
    if by == "project":
        return r["project"]
    return r["id"][:8]


def group_rows(results, by):
    """Summary rows per day / ISO week / project / session; busy shares use model+tool+bg+user."""
    groups = {}
    for r in sorted(results, key=lambda r: r["start"]):
        groups.setdefault(group_key(r, by), []).append(r)
    rows = []
    for key, rs in groups.items():
        t = aggregate(rs)["totals"]
        rows.append({"key": key, "n": len(rs), "active": t["model"] + t["tool"] + t["bg"],
                     **{k: t[k] for k in ALL_KEYS}})
    if by == "project":
        rows.sort(key=lambda row: -row["active"])
    return rows


def num(v):
    return int(v) if v == int(v) else round(v, 3)


def write_csv(path, header, rows):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow([num(v) if isinstance(v, float) else v for v in row])
    return path


# (CSV header, row key); headers are fixed English so files are language independent.
SESSION_CSV = [("start", "start"), ("project", "project"), ("session", "id"), ("wall", "wall"),
               *[(k, k) for k in BUCKET_KEYS], ("idle", "idle"), ("errors", "errors"), ("top", "top")]
GROUP_CSV = [("group", "key"), ("sessions", "n"), ("active", "active"), *[(k, k) for k in ALL_KEYS]]


def export_csv(path, results, by=None):
    """Session rows, or one row per group when `by` is given. Seconds are numbers; names in English."""
    if by:
        cols, rows = GROUP_CSV, group_rows(results, by)
    else:
        cols, rows = SESSION_CSV, session_rows(results, labels("en"))
    return write_csv(path, [h for h, _ in cols], [[row[k] for _, k in cols] for row in rows])


def period_text(L, days, since, until):
    if since and until:
        return L["period_range"].format(since=since, until=until)
    if since:
        return L["period_from"].format(since=since)
    if until:
        return L["period_until"].format(until=until)
    return L["period_days"].format(days=f"{days:g}")


def build_report(results, days, project, lang, idle_cap, redacted=False, since=None, until=None,
                timeline_sessions=20):
    """Report data; only times, names and ids, never conversation content."""
    L = labels(lang)
    day = lambda ts: f"{ts.astimezone():%Y-%m-%d}"
    agg = aggregate(results)
    totals, tool_secs, tool_calls = agg["totals"], agg["tools"], agg["calls"]
    daily = defaultdict(lambda: defaultdict(float))
    for r in results:
        for k in BUCKET_KEYS:
            daily[day(r["start"])][k] += r["buckets"][k]
    items = {key: {"n": tool_calls[key], "sec": sec, "kind": "bg" if isinstance(key, tuple) else "tool"}
             for key, sec in tool_secs.items()}
    sessions = session_rows(results, L)

    agents = defaultdict(lambda: {"runs": 0, "phases": defaultdict(float)})
    agent_daily = defaultdict(lambda: {"runs": 0, "total": 0.0, "think": 0.0})
    for r in results:
        for run in agent_runs(r, idle_cap):
            label = agent_name(L, run["label"])
            phases, start = run["phases"], run["start"]
            total = sum(phases.values())
            if not total:
                continue
            a = agents[label]
            a["runs"] += 1
            for k, v in phases.items():
                a["phases"][phase_group(k)] += v
            d = agent_daily[(day(start or r["start"]), label)]
            d["runs"] += 1
            d["total"] += total
            d["think"] += phases.get("think", 0)

    fmt_summary = format_summary(results)
    if redacted:
        shown = L["redacted"] if project else L["all_projects"]
    else:
        shown = project or L["all_projects"]
    ranked = sorted(items.items(), key=lambda kv: -kv[1]["sec"])[:15]
    return {
        "lang": lang,
        "labels": L,
        "generated": f"{datetime.now():%Y-%m-%d %H:%M}",
        "days": days, "since": since and str(since), "until": until and str(until),
        "period": period_text(L, days, since, until), "project": shown,
        "sessionCount": len(results),
        "apiErrors": agg["api_errors"],
        "format": fmt_summary, "formatWarning": format_warning(L, fmt_summary),
        "totals": dict(totals),
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "items": [{"name": item_name(L, k), **v} for k, v in ranked],
        "phaseGroups": PHASE_GROUPS,
        "agents": sorted(({"type": k, "runs": v["runs"], "phases": dict(v["phases"])}
                          for k, v in agents.items()), key=lambda x: -sum(x["phases"].values())),
        "agentDaily": [{"date": d, "type": t, **v} for (d, t), v in sorted(agent_daily.items())],
        "sessions": sorted(sessions, key=lambda s: s["start"], reverse=True),
        "timeline": timeline_data(results, L, idle_cap, timeline_sessions),
    }


def to_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


def render_html(data):
    text = json.dumps(data, ensure_ascii=False)
    # Keep "</script>" in the data from ending the script block early.
    text = text.replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8")
    return html.replace("__LANG__", data["lang"]).replace("__DATA__", text)


def write_html(data, out):
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(data), encoding="utf-8")
    return out


# ── CLI text ──

def print_summary(results, lang, top):
    L = labels(lang)
    print(f"{L['col_start']:<12} {L['col_project']:<22} {L['col_session']:<9} {L['col_wall']:>7} "
          f"{L['col_active']:>7} {L['col_model']:>5} {L['col_tool']:>5} {L['col_bg']:>5} "
          f"{L['col_user']:>5} {L['col_idle']:>7}  {L['col_top']}")
    for r in results:
        b = r["buckets"]
        active = b["model"] + b["tool"] + b["bg"]
        busy = active + b["user"]
        key, sec = max(r["tools"].items(), key=lambda kv: kv[1], default=("", 0))
        name = item_name(L, key)
        print(f"{r['start'].astimezone():%m-%d %H:%M}  {r['project'][:22]:<22} {r['id'][:8]:<9} "
              f"{fmt(r['wall']):>7} {fmt(active):>7} {pct(b['model'], busy)} {pct(b['tool'], busy)} "
              f"{pct(b['bg'], busy)} {pct(b['user'], busy)} {fmt(b['idle']):>7}  "
              f"{name} {fmt(sec) if name else ''}")

    agg = aggregate(results)
    total = agg["totals"]
    busy = sum(total[k] for k in BUCKET_KEYS)
    parts = [f"{L['sum_' + k]} {fmt(total[k])} ({pct(total[k], busy).strip()})"
             for k in BUCKET_KEYS]
    print("\n" + L["sum_line"].format(n=len(results), busy=fmt(busy)) + L["sep"].join(parts))
    if agg["api_errors"]:
        print(L["api_errors_line"].format(n=agg["api_errors"]))
    print_tools(L, agg["tools"], agg["calls"], total["tool"] + total["bg"], top)


def print_groups(results, by, lang):
    """Summary table, one row per day / week / project / session."""
    L = labels(lang)
    cell = lambda v, busy: f"{pct(v, busy).strip():>4} {fmt(v):>7}"
    print(f"{L['by_' + by]:<22} {L['col_n']:>4} {L['col_active']:>7} {L['col_model']:>12} "
          f"{L['col_tool']:>12} {L['col_bg']:>12} {L['col_user']:>12} {L['col_idle']:>7}")
    rows = group_rows(results, by)
    t = aggregate(results)["totals"]
    rows.append({"key": L["total"], "n": len(results), "active": t["model"] + t["tool"] + t["bg"],
                 **{k: t[k] for k in ALL_KEYS}})
    for row in rows:
        busy = sum(row[k] for k in BUCKET_KEYS)
        print(f"{row['key'][:22]:<22} {row['n']:>4} {fmt(row['active']):>7} "
              + " ".join(cell(row[k], busy) for k in BUCKET_KEYS) + f" {fmt(row['idle']):>7}")


def print_tools(L, tools, calls, tool_total, top):
    print(f"\n{L['tools_hdr']:<40} {L['calls']:>5} {L['total']:>8} {L['avg']:>7} {L['share']:>6}")
    for key, sec in sorted(tools.items(), key=lambda kv: -kv[1])[:top]:
        n = calls[key]
        print(f"{item_name(L, key)[:40]:<40} {n:>5} {fmt(sec):>8} {fmt(sec / n):>7} {pct(sec, tool_total):>6}")


def print_agents(results, lang, idle_cap):
    L = labels(lang)
    by_type = defaultdict(lambda: defaultdict(float))
    runs = defaultdict(int)
    for r in results:
        for run in agent_runs(r, idle_cap):
            label = agent_name(L, run["label"])
            for k, v in run["phases"].items():
                by_type[label][k] += v
            runs[label] += 1
    if not by_type:
        sys.exit(L["no_agent_files"])
    for label, phases in sorted(by_type.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(phases.values())
        print("\n" + L["agent_head"].format(label=label, runs=runs[label], total=fmt(total),
                                            avg=fmt(total / runs[label])))
        for k, v in sorted(phases.items(), key=lambda kv: -kv[1]):
            bar = "█" * round(v / total * 30) if total else ""
            print(f"  {L['p_' + k]:<24} {fmt(v):>8} {pct(v, total)}  {bar}")


def print_detail(r, lang, top):
    L = labels(lang)
    b = r["buckets"]
    print(L["d_head"].format(id=r["id"], project=r["project"],
                             start=f"{r['start'].astimezone():%Y-%m-%d %H:%M}"))
    print(L["d_totals"].format(wall=fmt(r["wall"]), prompts=r["prompts"], errors=r["api_errors"]))
    for k in ALL_KEYS:
        print(f"  {L['d_' + k]:<12} {fmt(b[k]):>8}  {pct(b[k], r['wall'])}")
    print_tools(L, r["tools"], r["tool_calls"], b["tool"] + b["bg"], top)
    print("\n" + L["d_slowest"].format(n=top))
    for gap, cat, detail, ts, transition in sorted(r["steps"], key=lambda s: -s[0])[:top]:
        print(f"  {ts.astimezone():%H:%M:%S}  {fmt(gap):>7}  {cat:<6} "
              f"{item_name(L, detail)[:30]:<30}  {transition}")


def print_trace(r, lang, n):
    L = labels(lang)
    for gap, cat, detail, ts, _ in sorted(r["steps"], key=lambda s: -s[0])[:n]:
        start = ts - timedelta(seconds=gap)
        print("\n" + L["trace_head"].format(
            gap=fmt(gap), cat=cat, item=item_name(L, detail)[:40],
            start=f"{start.astimezone():%H:%M:%S}", end=f"{ts.astimezone():%H:%M:%S}"))
        for e in r["entries"]:
            t = parse_ts(e["timestamp"])
            if start - timedelta(seconds=5) <= t <= ts + timedelta(seconds=5):
                vals = " ".join(f"{k}={trace_value(k, e[k])}"
                                for k in TRACE_VALUE_KEYS if k in e)
                print(f"   {(t - start).total_seconds():+8.1f}s  {shape(e):<32} {vals}")

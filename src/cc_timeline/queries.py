"""Shared queries behind the MCP tools and the `compare` / `quality` subcommands.

Every function returns plain JSON-serializable data: durations, shares, counts, names and ids.
Conversation text and Bash command text never leave this module.
"""
import argparse
import re
from collections import Counter, defaultdict
from datetime import timezone
from types import SimpleNamespace

from .agents import PHASE_GROUPS, phase_group
from .bash import bash_phase
from .cli import DEFAULT_DAYS, date_range, parse_day
from .i18n import item_name, labels
from .report import BUCKET_KEYS, agent_runs, aggregate, format_summary, group_rows, redact
from .transcript import analyze, find_sessions, load_entries

IDLE_CAP = 1800
SMALL_SAMPLE = 5
SLOWEST = 10
AGENT_RE = re.compile(r"(?:Agent|Task)\((.*)\)", re.S)


class QueryError(Exception):
    """The query cannot be answered (no such session, ...). MCP reports it as isError."""


class ParamError(ValueError):
    """A parameter is invalid. MCP reports it as JSON-RPC -32602."""


def r3(x):
    return round(x, 3)


def share(part, whole):
    return round(part / whole, 4) if whole else 0.0


def diff(after, before, scale=1.0):
    return None if after is None or before is None else r3((after - before) * scale)


def parse_date(value, name):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ParamError(f"{name} must be a YYYY-MM-DD string")
    try:
        return parse_day(value)
    except argparse.ArgumentTypeError:
        raise ParamError(f"{name} must be YYYY-MM-DD, got {value!r}")


def period_bounds(days=None, since=None, until=None, now=None):
    """(lo, hi, period dict); same semantics as the CLI flags."""
    s, u = parse_date(since, "since"), parse_date(until, "until")
    if days is not None and (isinstance(days, bool) or not isinstance(days, (int, float)) or days <= 0):
        raise ParamError("days must be a positive number")
    if s and u and s > u:
        raise ParamError("since must not be after until")
    lo, hi = date_range(SimpleNamespace(since=s, until=u, days=days), now)
    shown_days = None if (s or u) else (DEFAULT_DAYS if days is None else days)
    return lo, hi, {"days": shown_days, "since": s and str(s), "until": u and str(u)}


def load_results(root, project=None, days=None, since=None, until=None, redact_names=False,
                 idle_cap=IDLE_CAP, now=None):
    lo, hi, period = period_bounds(days, since, until, now)
    if project is not None and not isinstance(project, str):
        raise ParamError("project must be a string")
    files = find_sessions(root, project, None, lo and lo.astimezone(timezone.utc))
    results = [r for r in (analyze(p, idle_cap) for p in files) if r]
    results = [r for r in results if (lo is None or r["start"] >= lo) and (hi is None or r["start"] < hi)]
    results.sort(key=lambda r: r["start"])
    if redact_names:
        redact(results)
    return results, period


# ── building blocks ──

def agent_type_of(label):
    m = AGENT_RE.fullmatch(label) if label else None
    return m.group(1) if m else (label or "unmatched")


def bucket_block(results):
    t = aggregate(results)["totals"]
    busy = sum(t[k] for k in BUCKET_KEYS)
    return {"busy_sec": r3(busy), "away_sec": r3(t["idle"]),
            "buckets": {k: {"sec": r3(t[k]), "share": share(t[k], busy)} for k in BUCKET_KEYS}}


def top_items(results, n, L):
    agg = aggregate(results)
    total = agg["totals"]["tool"] + agg["totals"]["bg"]
    ranked = sorted(agg["tools"].items(), key=lambda kv: -kv[1])[:max(n, 0)]
    return [{"name": item_name(L, k), "kind": "background" if isinstance(k, tuple) else "tool",
             "calls": agg["calls"][k], "sec": r3(sec), "avg_sec": r3(sec / agg["calls"][k]),
             "share": share(sec, total)} for k, sec in ranked]


def agent_block(results, idle_cap=IDLE_CAP, agent_type=None):
    want = agent_type_of(agent_type) if agent_type else None
    by = {}
    for r in results:
        for run in agent_runs(r, idle_cap):
            total = sum(run["phases"].values())
            if not total:
                continue
            t = agent_type_of(run["label"])
            if want and t != want:
                continue
            a = by.setdefault(t, {"runs": 0, "phases": defaultdict(float)})
            a["runs"] += 1
            for k, v in run["phases"].items():
                a["phases"][phase_group(k)] += v
    out = []
    for t, a in sorted(by.items(), key=lambda kv: -sum(kv[1]["phases"].values())):
        total = sum(a["phases"].values())
        out.append({"type": t, "runs": a["runs"], "total_sec": r3(total), "avg_sec": r3(total / a["runs"]),
                    "phases": {g: {"sec": r3(a["phases"][g]), "share": share(a["phases"][g], total)}
                               for g in PHASE_GROUPS}})
    return out


def test_results(entries):
    """(test tool results, failed ones, is_error field seen) for Bash calls classified as test."""
    tests, calls, fails, seen = set(), 0, 0, False
    for e in entries:
        content = (e.get("message") or {}).get("content")
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                cmd = (b.get("input") or {}).get("command")
                if bash_phase(cmd if isinstance(cmd, str) else "") == "test":
                    tests.add(b.get("id"))
            elif b.get("type") == "tool_result":
                seen = seen or "is_error" in b
                if b.get("tool_use_id") in tests:
                    calls += 1
                    fails += b.get("is_error") is True
    return calls, fails, seen


def quality_block(results, idle_cap=IDLE_CAP):
    dispatched, reported = Counter(), Counter()
    executors = test_calls = test_fails = 0
    err_seen = False
    agent_time = poll_time = 0.0

    def add_tests(entries):
        nonlocal test_calls, test_fails, err_seen
        c, f, s = test_results(entries)
        test_calls, test_fails, err_seen = test_calls + c, test_fails + f, err_seen or s

    for r in results:
        scrub = r.get("scrub") or (lambda label: label)
        ids = set()
        for e in r["entries"]:
            content = (e.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task") \
                        and b.get("id") not in ids:
                    ids.add(b.get("id"))
                    raw = str((b.get("input") or {}).get("subagent_type") or "general-purpose")
                    executors += "executor" in raw.lower()
                    dispatched[agent_type_of(scrub(f"Agent({raw})"))] += 1
        for key, n in r["tool_calls"].items():
            if isinstance(key, tuple) and key[0] == "agent":
                reported[agent_type_of(key[1])] += n
        add_tests(r["entries"])
        for run in agent_runs(r, idle_cap):
            agent_time += sum(run["phases"].values())
            poll_time += run["phases"].get("poll", 0)
            add_tests(load_entries(run["path"], keep_sidechain=True))
    n = len(results)
    types = sorted(set(dispatched) | set(reported), key=lambda t: (-dispatched[t], t))
    return {
        "sessions": n,
        "agents": [{"type": t, "dispatched": dispatched[t], "reported": reported[t],
                    "reports_per_dispatch": round(reported[t] / dispatched[t], 3) if dispatched[t] else None}
                   for t in types],
        "tests": {"calls": test_calls, "failures": test_fails,
                  "failure_rate": round(test_fails / test_calls, 4) if test_calls else None,
                  "is_error_field_seen": err_seen},
        "api_errors": aggregate(results)["api_errors"],
        "executor_dispatches_per_session": round(executors / n, 3) if executors and n else None,
        "sleep_poll_share": round(poll_time / agent_time, 4) if agent_time else None,
        "agent_time_sec": r3(agent_time),
    }


def quality_notes(L, q):
    notes = []
    if not q["sessions"]:
        notes.append(L["note_no_sessions"])
    elif q["tests"]["calls"] and not q["tests"]["is_error_field_seen"]:
        notes.append(L["note_no_is_error"])
    return notes


def sample_notes(L, n_before, n_after):
    notes = []
    for name, n in (("before", n_before), ("after", n_after)):
        if n < SMALL_SAMPLE:
            notes.append(L["note_small"].format(which=L["cmp_" + name], n=n, min=SMALL_SAMPLE))
    if min(n_before, n_after) >= SMALL_SAMPLE and max(n_before, n_after) > 2 * min(n_before, n_after):
        notes.append(L["note_unbalanced"].format(before=n_before, after=n_after))
    return notes


# ── queries ──

def summary(root, days=None, since=None, until=None, project=None, by=None, redact_names=False,
            top=10, idle_cap=IDLE_CAP, now=None):
    if by is not None and by not in ("day", "week", "project", "session"):
        raise ParamError("by must be one of day, week, project, session")
    results, period = load_results(root, project, days, since, until, redact_names, idle_cap, now)
    L = labels("en")
    unknown = format_summary(results)["unknown"] if results else {"types": {}, "origins": {}}
    data = {"period": period, "sessions": len(results), **bucket_block(results),
            "top_items": top_items(results, top, L) if results else [],
            "api_errors": aggregate(results)["api_errors"],
            "format_warnings": {k: v for k, v in unknown.items() if v}}
    if by and results:
        groups = []
        for row in group_rows(results, by):
            busy = sum(row[k] for k in BUCKET_KEYS)
            groups.append({"key": row["key"], "sessions": row["n"], "active_sec": r3(row["active"]),
                           "away_sec": r3(row["idle"]),
                           "buckets": {k: {"sec": r3(row[k]), "share": share(row[k], busy)}
                                       for k in BUCKET_KEYS}})
        data["groups"] = {"by": by, "rows": groups}
    if not results:
        data["notes"] = [L["note_no_sessions"]]
    return data


def agent_phases(root, days=None, since=None, until=None, project=None, agent_type=None,
                 redact_names=False, idle_cap=IDLE_CAP, now=None):
    results, period = load_results(root, project, days, since, until, redact_names, idle_cap, now)
    data = {"period": period, "sessions": len(results), "agents": agent_block(results, idle_cap, agent_type)}
    if not data["agents"]:
        data["notes"] = ["No matching subagent runs in this period."]
    return data


def quality_signals(root, days=None, since=None, until=None, project=None, redact_names=False,
                    idle_cap=IDLE_CAP, now=None):
    results, period = load_results(root, project, days, since, until, redact_names, idle_cap, now)
    q = quality_block(results, idle_cap)
    return {"period": period, **q, "notes": quality_notes(labels("en"), q)}


def current_session_path(root, cwd):
    folder = root / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    if not files:
        raise QueryError(f"No session folder found for the server's working directory ({folder.name}) "
                         f"under the transcripts root. Pass a session id prefix instead.")
    return files[-1]


def session_timeline(root, session, cwd=None, redact_names=False, idle_cap=IDLE_CAP):
    if not isinstance(session, str) or not session:
        raise ParamError("session must be a non-empty string")
    if session == "current":
        if cwd is None:
            raise QueryError("The server's working directory is unknown.")
        path = current_session_path(root, cwd)
    else:
        files = find_sessions(root, None, session)
        if not files:
            raise QueryError(f"No session id starts with {session!r}.")
        if len(files) > 1:
            raise QueryError(f"{len(files)} sessions match {session!r}; use a longer prefix.")
        path = files[0]
    r = analyze(path, idle_cap)
    if not r:
        raise QueryError("That session is too short to analyze.")
    if redact_names:
        redact([r])
    L = labels("en")
    local = lambda t: f"{t.astimezone():%Y-%m-%d %H:%M:%S}"
    subagents = []
    for run in agent_runs(r, idle_cap):
        if not run["start"]:
            continue
        phases = defaultdict(float)
        for k, v in run["phases"].items():
            phases[phase_group(k)] += v
        subagents.append({
            "type": agent_type_of(run["label"]), "start": local(run["start"]), "end": local(run["end"]),
            "start_offset_sec": r3(max((run["start"] - r["start"]).total_seconds(), 0)),
            "duration_sec": r3((run["end"] - run["start"]).total_seconds()),
            "phases": {g: r3(phases[g]) for g in PHASE_GROUPS}})
    subagents.sort(key=lambda a: a["start_offset_sec"])
    slow = sorted(r["steps"], key=lambda s: -s[0])[:SLOWEST]
    return {"session": r["id"][:8], "project": r["project"], "start": local(r["start"]),
            "wall_sec": r3(r["wall"]), "prompts": r["prompts"], "api_errors": r["api_errors"],
            **bucket_block([r]),
            "slowest_steps": [{"category": cat, "item": item_name(L, detail), "sec": r3(gap), "at": local(ts)}
                              for gap, cat, detail, ts, _ in slow],
            "subagents": subagents}


def check_range(name, value):
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in ("since", "until")):
        raise ParamError(f"{name} must be an object with since and until (YYYY-MM-DD)")
    return value["since"], value["until"]


def compare(root, before, after, project=None, redact_names=False, idle_cap=IDLE_CAP, now=None):
    (bs, bu), (as_, au) = check_range("before", before), check_range("after", after)
    rb, pb = load_results(root, project, None, bs, bu, False, idle_cap, now)
    ra, pa = load_results(root, project, None, as_, au, False, idle_cap, now)
    if redact_names:
        redact(rb + ra)

    def block(results, period):
        return {"period": period, "sessions": len(results), **bucket_block(results),
                "agents": agent_block(results, idle_cap), "quality": quality_block(results, idle_cap)}

    b, a = block(rb, pb), block(ra, pa)
    by_type = lambda blk: {x["type"]: x for x in blk}
    ab, aa = by_type(b["agents"]), by_type(a["agents"])
    agents = []
    for t in sorted(set(ab) | set(aa), key=lambda t: (-(aa.get(t) or ab[t])["total_sec"], t)):
        x, y = ab.get(t), aa.get(t)
        agents.append({"type": t, "runs_before": x["runs"] if x else 0, "runs_after": y["runs"] if y else 0,
                       "avg_sec": diff(y and y["avg_sec"], x and x["avg_sec"]),
                       "phases": {g: diff(y["phases"][g]["share"], x["phases"][g]["share"], 100)
                                  for g in PHASE_GROUPS} if x and y else None})
    qb, qa = b["quality"], a["quality"]
    qab, qaa = by_type(qb["agents"]), by_type(qa["agents"])
    delta = {
        "buckets": {k: {"share_pp": diff(a["buckets"][k]["share"], b["buckets"][k]["share"], 100),
                        "sec": diff(a["buckets"][k]["sec"], b["buckets"][k]["sec"])} for k in BUCKET_KEYS},
        "agents": agents,
        "quality": {
            "reports_per_dispatch": [
                {"type": t, "delta": diff((qaa.get(t) or {}).get("reports_per_dispatch"),
                                          (qab.get(t) or {}).get("reports_per_dispatch"))}
                for t in sorted(set(qab) | set(qaa))],
            "test_failure_rate_pp": diff(qa["tests"]["failure_rate"], qb["tests"]["failure_rate"], 100),
            "api_errors": qa["api_errors"] - qb["api_errors"],
            "executor_dispatches_per_session": diff(qa["executor_dispatches_per_session"],
                                                    qb["executor_dispatches_per_session"]),
            "sleep_poll_share_pp": diff(qa["sleep_poll_share"], qb["sleep_poll_share"], 100)}}
    L = labels("en")
    notes = sample_notes(L, b["sessions"], a["sessions"])
    return {"before": b, "after": a, "delta": delta, "notes": notes}

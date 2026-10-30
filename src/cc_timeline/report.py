"""Build report data and render it as CLI text, JSON or HTML."""
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .agents import PHASE_GROUPS, agent_files, analyze_agent, phase_group
from .i18n import agent_name, item_name, labels
from .transcript import TRACE_VALUE_KEYS, parse_ts, shape, trace_value

TEMPLATE = Path(__file__).parent / "templates" / "report.html"
BUCKET_KEYS = ("model", "tool", "bg", "user")
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

    Tool names, built-in agent types and all timings are kept.
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
        r["agent_corpus"] = [(text, scrub(label)) for text, label in r["agent_corpus"]]
        r["scrub"] = scrub
    return results


def agent_runs(r):
    scrub = r.get("scrub") or (lambda label: label)
    return [(scrub(label), f) for label, f in agent_files(r["path"], r["agent_corpus"])]


def period_text(L, days, since, until):
    if since and until:
        return L["period_range"].format(since=since, until=until)
    if since:
        return L["period_from"].format(since=since)
    if until:
        return L["period_until"].format(until=until)
    return L["period_days"].format(days=f"{days:g}")


def build_report(results, days, project, lang, idle_cap, redacted=False, since=None, until=None):
    """Report data; only times, names and ids, never conversation content."""
    L = labels(lang)
    day = lambda ts: f"{ts.astimezone():%Y-%m-%d}"
    totals = defaultdict(float)
    daily = defaultdict(lambda: defaultdict(float))
    items = defaultdict(lambda: {"n": 0, "sec": 0.0, "kind": "tool"})
    sessions = []
    for r in results:
        b = r["buckets"]
        for k in (*BUCKET_KEYS, "idle"):
            totals[k] += b[k]
        for k in BUCKET_KEYS:
            daily[day(r["start"])][k] += b[k]
        for key, sec in r["tools"].items():
            it = items[key]
            it["n"] += r["tool_calls"][key]
            it["sec"] += sec
            if isinstance(key, tuple):
                it["kind"] = "bg"
        top = max(r["tools"].items(), key=lambda kv: kv[1], default=("", 0))
        sessions.append({"start": f"{r['start'].astimezone():%Y-%m-%d %H:%M}", "project": r["project"],
                         "id": r["id"][:8], "wall": r["wall"], "idle": b["idle"],
                         **{k: b[k] for k in BUCKET_KEYS}, "top": item_name(L, top[0]),
                         "errors": r["api_errors"]})

    agents = defaultdict(lambda: {"runs": 0, "phases": defaultdict(float)})
    agent_daily = defaultdict(lambda: {"runs": 0, "total": 0.0, "think": 0.0})
    for r in results:
        for label, f in agent_runs(r):
            label = agent_name(L, label)
            phases, start = analyze_agent(f, idle_cap)
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
        "apiErrors": sum(r["api_errors"] for r in results),
        "totals": dict(totals),
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "items": [{"name": item_name(L, k), **v} for k, v in ranked],
        "phaseGroups": PHASE_GROUPS,
        "agents": sorted(({"type": k, "runs": v["runs"], "phases": dict(v["phases"])}
                          for k, v in agents.items()), key=lambda x: -sum(x["phases"].values())),
        "agentDaily": [{"date": d, "type": t, **v} for (d, t), v in sorted(agent_daily.items())],
        "sessions": sorted(sessions, key=lambda s: s["start"], reverse=True),
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
    total = defaultdict(float)
    all_tools = defaultdict(float)
    all_calls = defaultdict(int)
    api_errors = 0
    for r in results:
        b = r["buckets"]
        active = b["model"] + b["tool"] + b["bg"] + b["other"]
        busy = active + b["user"]
        key, sec = max(r["tools"].items(), key=lambda kv: kv[1], default=("", 0))
        name = item_name(L, key)
        print(f"{r['start'].astimezone():%m-%d %H:%M}  {r['project'][:22]:<22} {r['id'][:8]:<9} "
              f"{fmt(r['wall']):>7} {fmt(active):>7} {pct(b['model'], busy)} {pct(b['tool'], busy)} "
              f"{pct(b['bg'], busy)} {pct(b['user'], busy)} {fmt(b['idle']):>7}  "
              f"{name} {fmt(sec) if name else ''}")
        for k, v in b.items():
            total[k] += v
        for k, v in r["tools"].items():
            all_tools[k] += v
            all_calls[k] += r["tool_calls"][k]
        api_errors += r["api_errors"]

    busy = sum(total[k] for k in ("model", "tool", "bg", "user", "other"))
    parts = [f"{L['sum_' + k]} {fmt(total[k])} ({pct(total[k], busy).strip()})"
             for k in BUCKET_KEYS]
    print("\n" + L["sum_line"].format(n=len(results), busy=fmt(busy)) + L["sep"].join(parts))
    if api_errors:
        print(L["api_errors_line"].format(n=api_errors))
    print_tools(L, all_tools, all_calls, total["tool"] + total["bg"], top)


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
        for label, f in agent_runs(r):
            label = agent_name(L, label)
            for k, v in analyze_agent(f, idle_cap)[0].items():
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
    for k in ("model", "tool", "bg", "user", "idle"):
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

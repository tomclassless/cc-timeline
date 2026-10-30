"""Text tables for the `compare` and `quality` subcommands."""
from .agents import PHASE_GROUPS
from .i18n import labels
from .queries import quality_notes, sample_notes
from .report import BUCKET_KEYS, fmt


def pc(v):
    return "-" if v is None else f"{v * 100:.0f}%"


def num(v, digits=2):
    return "-" if v is None else f"{v:.{digits}f}"


def sfmt(sec):
    if sec is None:
        return "-"
    return ("-" if sec < 0 else "+") + fmt(abs(sec))


def spp(v):
    return "-" if v is None else f"{v:+.1f}pp"


def snum(v, digits=2):
    return "-" if v is None else f"{v:+.{digits}f}"


def period_str(p):
    return f"{p['since']} → {p['until']}" if p.get("since") or p.get("until") else f"{p['days']:g}d"


def quality_lines(L, q):
    lines = [L["q_head"].format(n=q["sessions"])]
    lines.append(L["q_ratio_hdr"])
    for a in q["agents"]:
        lines.append(f"  {a['type']:<24} {L['q_dispatched']} {a['dispatched']:>3}  {L['q_reported']} "
                     f"{a['reported']:>3}  {L['q_ratio']} {num(a['reports_per_dispatch'])}")
    t = q["tests"]
    lines.append(L["q_tests"].format(fails=t["failures"], calls=t["calls"], rate=pc(t["failure_rate"])))
    lines.append(L["q_api"].format(n=q["api_errors"]))
    lines.append(L["q_executor"].format(v=num(q["executor_dispatches_per_session"])))
    lines.append(L["q_poll"].format(v=pc(q["sleep_poll_share"])))
    return lines


def print_quality(data, lang):
    L = labels(lang)
    print(f"{period_str(data['period'])}")
    print("\n".join(quality_lines(L, data)))
    for n in quality_notes(L, data):
        print(f"\n{L['cmp_notes']}: {n}")


def print_compare(data, lang):
    L = labels(lang)
    b, a, d = data["before"], data["after"], data["delta"]
    print(f"{L['cmp_before']}: {period_str(b['period'])} · {b['sessions']} {L['cmp_sessions']}")
    print(f"{L['cmp_after']}: {period_str(a['period'])} · {a['sessions']} {L['cmp_sessions']}")

    print(f"\n{L['cmp_buckets']}")
    print(f"  {'':<22} {L['cmp_before']:>8} {L['cmp_after']:>8} {'Δ':>9}   {L['cmp_before']:>8} {L['cmp_after']:>8} {'Δ':>9}")
    for k in BUCKET_KEYS:
        bb, ba, dk = b["buckets"][k], a["buckets"][k], d["buckets"][k]
        print(f"  {L['b_' + k]:<22} {pc(bb['share']):>8} {pc(ba['share']):>8} {spp(dk['share_pp']):>9}   "
              f"{fmt(bb['sec']):>8} {fmt(ba['sec']):>8} {sfmt(dk['sec']):>9}")

    print(f"\n{L['cmp_agents']}")
    if not d["agents"]:
        print(f"  {L['no_agent_data']}")
    bt = {x["type"]: x for x in b["agents"]}
    at = {x["type"]: x for x in a["agents"]}
    for x in d["agents"]:
        t = x["type"]
        avg_b = fmt(bt[t]["avg_sec"]) if t in bt else "-"
        avg_a = fmt(at[t]["avg_sec"]) if t in at else "-"
        print(f"  {t:<24} {L['cmp_runs']} {x['runs_before']}→{x['runs_after']}  {L['avg']} {avg_b} → {avg_a} "
              f"({sfmt(x['avg_sec'])})")
        if x["phases"]:
            parts = [f"{L['g_' + g]} {pc(bt[t]['phases'][g]['share'])}→{pc(at[t]['phases'][g]['share'])} "
                     f"({spp(x['phases'][g])})"
                     for g in PHASE_GROUPS if bt[t]["phases"][g]["sec"] or at[t]["phases"][g]["sec"]]
            if parts:
                print("      " + L["sep"].join(parts))

    qb, qa, dq = b["quality"], a["quality"], d["quality"]
    print(f"\n{L['cmp_quality']}")
    print(f"  {'':<34} {L['cmp_before']:>8} {L['cmp_after']:>8} {'Δ':>9}")
    rb = {x["type"]: x for x in qb["agents"]}
    ra = {x["type"]: x for x in qa["agents"]}
    for x in dq["reports_per_dispatch"]:
        t = x["type"]
        print(f"  {L['q_ratio'] + ' ' + t:<34} {num((rb.get(t) or {}).get('reports_per_dispatch')):>8} "
              f"{num((ra.get(t) or {}).get('reports_per_dispatch')):>8} {snum(x['delta']):>9}")
    rows = [
        (L["cmp_test_rate"], pc(qb["tests"]["failure_rate"]), pc(qa["tests"]["failure_rate"]),
         spp(dq["test_failure_rate_pp"])),
        (L["cmp_api"], qb["api_errors"], qa["api_errors"], f"{dq['api_errors']:+d}"),
        (L["cmp_executor"], num(qb["executor_dispatches_per_session"]),
         num(qa["executor_dispatches_per_session"]), snum(dq["executor_dispatches_per_session"])),
        (L["cmp_poll"], pc(qb["sleep_poll_share"]), pc(qa["sleep_poll_share"]), spp(dq["sleep_poll_share_pp"])),
    ]
    for name, x, y, z in rows:
        print(f"  {name:<34} {x!s:>8} {y!s:>8} {z!s:>9}")

    notes = sample_notes(L, b["sessions"], a["sessions"])
    for q in (qb, qa):
        for n in quality_notes(L, q):
            if n not in notes and q["sessions"]:
                notes.append(n)
    for n in notes:
        print(f"\n{L['cmp_notes']}: {n}")

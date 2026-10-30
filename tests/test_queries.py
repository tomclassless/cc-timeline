import json
import os

import pytest
from fixtures_mcp import (AFTER, BEFORE, CANARY, CWD, EXEC, PROJECT, PROJECT_DIR, REV, small_session,
                          standard_root, write_full, write_light)

from cc_timeline import cli, queries
from cc_timeline.queries import ParamError, QueryError

ALL = {"since": "2000-01-01"}


def test_summary_numbers(tmp_path):
    d = queries.summary(standard_root(tmp_path), **ALL)
    assert d["sessions"] == 2
    b = d["buckets"]
    assert b["model"]["sec"] == 13 and b["tool"]["sec"] == 28 and b["bg"]["sec"] == 72 and b["user"]["sec"] == 5
    assert d["busy_sec"] == 118 and d["away_sec"] == 0
    assert b["bg"]["share"] == round(72 / 118, 4)
    assert d["api_errors"] == 1
    top = {i["name"]: i for i in d["top_items"]}
    assert top["Bash"]["sec"] == 15 and top["Bash"]["calls"] == 2 and top["Bash"]["kind"] == "tool"
    assert top[f"Waiting for Agent({REV}) report"]["sec"] == 20 + 15 + 20
    assert top[f"Waiting for Agent({REV}) report"]["kind"] == "background"
    assert d["top_items"][0]["sec"] >= d["top_items"][-1]["sec"]
    assert d["format_warnings"] == {}


def test_summary_top_by_and_filters(tmp_path):
    root = standard_root(tmp_path)
    assert len(queries.summary(root, **ALL, top=2)["top_items"]) == 2
    rows = queries.summary(root, **ALL, by="day")["groups"]["rows"]
    assert [r["sessions"] for r in rows] == [1, 1]
    assert rows[0]["buckets"]["model"]["sec"] == 9 and rows[1]["buckets"]["model"]["sec"] == 4
    assert queries.summary(root, **ALL, by="project")["groups"]["rows"][0]["key"] == PROJECT
    assert queries.summary(root, since="2026-01-08", until="2026-01-20")["sessions"] == 1
    assert queries.summary(root, **ALL, project="nomatch")["sessions"] == 0
    assert queries.summary(root, **ALL, project="zzproj")["sessions"] == 2


@pytest.mark.parametrize("kw", [{"by": "year"}, {"days": -1}, {"days": True}, {"since": "01/02/2026"},
                                {"since": "2026-02-01", "until": "2026-01-01"}])
def test_bad_params(tmp_path, kw):
    with pytest.raises(ParamError):
        queries.summary(tmp_path, **kw)


def test_unknown_format_warning(tmp_path):
    root = standard_root(tmp_path)
    p = next(root.glob("*/11111111-aaaa.jsonl"))
    p.write_text(p.read_text() + json.dumps({"type": "weird-new", "timestamp": "2026-01-01T12:01:30.000Z"}) + "\n")
    assert queries.summary(root, **ALL)["format_warnings"] == {"types": {"weird-new": 1}}


def test_agent_phases(tmp_path):
    d = queries.agent_phases(standard_root(tmp_path), **ALL)
    by = {a["type"]: a for a in d["agents"]}
    ex, rv = by[EXEC], by[REV]
    assert ex["runs"] == 1 and ex["total_sec"] == 35 and ex["avg_sec"] == 35
    assert ex["phases"]["think"] == {"sec": 10, "share": round(10 / 35, 4)}
    assert ex["phases"]["code"]["sec"] == 5 and ex["phases"]["gen"]["sec"] == 8
    assert ex["phases"]["test"]["sec"] == 10 and ex["phases"]["other"]["sec"] == 2
    assert rv["runs"] == 2 and rv["total_sec"] == 50 and rv["avg_sec"] == 25
    assert rv["phases"]["poll"]["sec"] == 30 and rv["phases"]["think"]["sec"] == 10
    only = queries.agent_phases(standard_root(tmp_path), **ALL, agent_type=REV)["agents"]
    assert [a["type"] for a in only] == [REV]
    assert queries.agent_phases(tmp_path, **ALL, agent_type="nope")["notes"]


def test_quality_signals(tmp_path):
    q = queries.quality_signals(standard_root(tmp_path), **ALL)
    by = {a["type"]: a for a in q["agents"]}
    assert by[REV] == {"type": REV, "dispatched": 2, "reported": 3, "reports_per_dispatch": 1.5}
    assert by[EXEC]["reports_per_dispatch"] == 1.0
    assert q["tests"] == {"calls": 3, "failures": 2, "failure_rate": round(2 / 3, 4), "is_error_field_seen": True}
    assert q["api_errors"] == 1
    assert q["executor_dispatches_per_session"] == 0.5
    assert q["agent_time_sec"] == 35 + 40 + 10
    assert q["sleep_poll_share"] == round(30 / 85, 4)
    assert q["notes"] == []


def test_quality_no_executor_and_no_agents(tmp_path):
    write_light(tmp_path, 0)
    q = queries.quality_signals(tmp_path, **ALL)
    assert q["executor_dispatches_per_session"] is None
    assert q["tests"]["failure_rate"] is None and q["tests"]["calls"] == 0
    assert queries.quality_signals(tmp_path / "empty", **ALL)["notes"]


def test_quality_without_is_error_field(tmp_path):
    from helpers import Session
    s = Session().user(0).tool_use(1, "b1", "Bash", {"command": "pytest"}).tool_result(5, "b1")
    s.write(tmp_path)
    q = queries.quality_signals(tmp_path, since="2000-01-01")
    assert q["tests"] == {"calls": 1, "failures": 0, "failure_rate": 0.0, "is_error_field_seen": False}
    assert "is_error" in q["notes"][0]


def test_session_timeline_by_prefix(tmp_path):
    root = standard_root(tmp_path)
    d = queries.session_timeline(root, "1111")
    assert d["session"] == "11111111" and d["project"] == PROJECT and d["wall_sec"] == 83
    assert d["buckets"]["tool"]["sec"] == 17 and d["buckets"]["bg"]["sec"] == 52
    slow = d["slowest_steps"]
    assert len(slow) == 10
    assert slow[0] == {"category": "bg", "item": f"Waiting for Agent({REV}) report", "sec": 20, "at": slow[0]["at"]}
    assert [s["sec"] for s in slow] == sorted((s["sec"] for s in slow), reverse=True)
    sub = {a["type"]: a for a in d["subagents"]}
    assert sub[EXEC]["duration_sec"] == 35 and sub[EXEC]["phases"]["think"] == 10
    assert sub[REV]["duration_sec"] == 40 and sub[REV]["phases"]["poll"] == 30
    assert sub[EXEC]["start_offset_sec"] == 0
    assert [a["start_offset_sec"] for a in d["subagents"]] == sorted(a["start_offset_sec"] for a in d["subagents"])


def test_session_timeline_errors(tmp_path):
    root = standard_root(tmp_path)
    with pytest.raises(QueryError):
        queries.session_timeline(root, "zzzz")
    with pytest.raises(QueryError, match="longer prefix"):
        write_light(root, 3, sid="11111111-zzzz")
        queries.session_timeline(root, "1111")
    with pytest.raises(ParamError):
        queries.session_timeline(root, "")


def test_current_folder_naming_rule(tmp_path):
    from helpers import Session
    Session().user(0).assistant(3).write(tmp_path, project="-Users-x-my-proj-v2", sid="44444444-dddd")
    got = queries.current_session_path(tmp_path, "/Users/x/my_proj.v2")
    assert got.name == "44444444-dddd.jsonl"


def test_session_current_maps_cwd_to_latest_session(tmp_path):
    root = standard_root(tmp_path)
    other = write_light(root, 5, sid="33333333-cccc", project="-Users-x-other")
    old = next(root.glob(f"{PROJECT_DIR}/11111111-aaaa.jsonl"))
    new = next(root.glob(f"{PROJECT_DIR}/22222222-bbbb.jsonl"))
    os.utime(old, (2000, 2000))
    os.utime(new, (1000, 1000))
    os.utime(other, (9000, 9000))
    assert queries.session_timeline(root, "current", cwd=CWD)["session"] == "11111111"
    os.utime(new, (3000, 3000))
    assert queries.session_timeline(root, "current", cwd=CWD)["session"] == "22222222"
    with pytest.raises(QueryError, match="working directory"):
        queries.session_timeline(root, "current", cwd="/Users/x/missing")



def test_compare_values(tmp_path):
    root = standard_root(tmp_path)
    c = queries.compare(root, BEFORE, AFTER)
    b, a, d = c["before"], c["after"], c["delta"]
    assert b["sessions"] == 1 and a["sessions"] == 1
    assert b["period"] == {"days": None, "since": "2025-12-30", "until": "2026-01-05"}
    assert b["buckets"]["model"]["sec"] == 9 and a["buckets"]["model"]["sec"] == 4
    assert d["buckets"]["model"]["sec"] == -5
    assert d["buckets"]["model"]["share_pp"] == round((round(4 / 35, 4) - round(9 / 83, 4)) * 100, 3)
    assert d["buckets"]["bg"]["sec"] == 20 - 52
    ag = {x["type"]: x for x in d["agents"]}
    assert ag[REV]["avg_sec"] == 10 - 40 and ag[REV]["runs_before"] == 1 and ag[REV]["runs_after"] == 1
    assert ag[REV]["phases"]["think"] == 100.0 and ag[REV]["phases"]["poll"] == -75.0
    assert ag[EXEC]["avg_sec"] is None and ag[EXEC]["phases"] is None and ag[EXEC]["runs_after"] == 0
    dq = d["quality"]
    assert {x["type"]: x["delta"] for x in dq["reports_per_dispatch"]}[REV] == 1.0 - 2.0
    assert dq["test_failure_rate_pp"] is None
    assert dq["api_errors"] == -1
    assert dq["executor_dispatches_per_session"] is None
    assert b["quality"]["executor_dispatches_per_session"] == 1.0


def test_compare_sample_notes(tmp_path):
    root = standard_root(tmp_path)
    notes = queries.compare(root, BEFORE, AFTER)["notes"]
    assert len(notes) == 2 and "Before" in notes[0] and "1 sessions" in notes[0]
    for n in range(5):
        small_session(root, 0, n)
    for n in range(5, 16):
        small_session(root, 11, n)
    c = queries.compare(root, BEFORE, AFTER)
    assert (c["before"]["sessions"], c["after"]["sessions"]) == (6, 12)
    assert len(c["notes"]) == 0   # 12 is not more than twice 6
    small_session(root, 11, 99)
    c = queries.compare(root, BEFORE, AFTER)
    assert len(c["notes"]) == 1 and "6 vs 13" in c["notes"][0]


@pytest.mark.parametrize("bad", [None, {"since": "2026-01-01"}, {"since": "x", "until": "y"}, "2026-01-01"])
def test_compare_bad_ranges(tmp_path, bad):
    with pytest.raises(ParamError):
        queries.compare(tmp_path, bad, AFTER)


def test_compare_empty_period_does_not_crash(tmp_path):
    c = queries.compare(standard_root(tmp_path), {"since": "2020-01-01", "until": "2020-01-02"}, AFTER)
    assert c["before"]["sessions"] == 0 and c["before"]["buckets"]["model"]["share"] == 0.0
    assert c["delta"]["agents"]


# ── CLI ──

def test_cli_compare_text_and_json(tmp_path, capsys):
    root = standard_root(tmp_path)
    args = ["compare", "--root", str(root), "--before", "2025-12-30:2026-01-05", "--after", "2026-01-08:2026-01-20"]
    assert cli.main(args) == 0
    en = capsys.readouterr().out
    assert "Before" in en and "Time buckets" in en and REV in en and "Test failure rate" in en
    assert "Sample too small" in en
    cli.main([*args, "--lang", "zh-TW"])
    zh = capsys.readouterr().out
    assert "調整前" in zh and "時間分配" in zh and "樣本不足" in zh
    cli.main([*args, "--json"])
    assert json.loads(capsys.readouterr().out) == queries.compare(root, BEFORE, AFTER)


def test_cli_quality_text_and_json(tmp_path, capsys):
    root = standard_root(tmp_path)
    cli.main(["quality", "--root", str(root), "--since", "2000-01-01"])
    out = capsys.readouterr().out
    assert "reports/dispatch 1.50" in out and "2 / 3" in out
    cli.main(["quality", "--root", str(root), "--since", "2000-01-01", "--lang", "zh-TW"])
    assert "回報÷派出 1.50" in capsys.readouterr().out
    cli.main(["quality", "--root", str(root), "--since", "2000-01-01", "--json"])
    assert json.loads(capsys.readouterr().out) == queries.quality_signals(root, since="2000-01-01")


def test_cli_subcommand_errors(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["compare", "--root", str(tmp_path), "--before", "bad", "--after", "2026-01-01:2026-01-02"])
    with pytest.raises(SystemExit):
        cli.main(["quality", "--root", str(tmp_path), "--since", "2000-01-01"])   # no sessions


def test_flag_usage_still_works(tmp_path, capsys):
    root = standard_root(tmp_path)
    assert cli.main(["--root", str(root), "--since", "2000-01-01", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sessionCount"] == 2

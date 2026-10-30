import csv
import json
import os
import re

import pytest
from helpers import Session

from cc_timeline import cli
from cc_timeline import report
from cc_timeline.agents import agent_files, analyze_agent, phase_group
from cc_timeline.bash import bash_phase
from cc_timeline.i18n import item_name, labels
from cc_timeline.transcript import analyze, default_root, find_sessions, shape, trace_value

CAP = 1800
# Fixtures live in 2026-01; open the date window so tests do not depend on today.
ALL = ["--since", "2000-01-01"]


def run(sess, tmp_path, **kw):
    return analyze(sess.write(tmp_path, **kw), CAP)


def basic():
    return (Session().user(0).assistant(5).tool_use(7, "t1", "Read")
            .tool_result(12, "t1").assistant(14, mid="m2").user(20).assistant(4020, mid="m3"))


def test_basic_model_tool_user_idle(tmp_path):
    r = run(basic(), tmp_path)
    assert dict(r["buckets"]) == {"model": 9, "tool": 5, "user": 6, "idle": 4000}
    assert r["tools"] == {"Read": 5}
    assert r["wall"] == 4020
    assert r["prompts"] == 2
    assert r["project"] == "proj"


def test_sidechain_rows_skipped_and_subagents_not_sessions(tmp_path):
    s = Session().user(0).assistant(5)
    s.raw(50, "assistant", isSidechain=True, message={"id": "z", "content": []})
    s.user(60)
    path = s.write(tmp_path)
    Session(sidechain=True).user(0).assistant(9).write_agent(path, "abc")
    r = analyze(path, CAP)
    assert dict(r["buckets"]) == {"model": 5, "user": 55}
    assert find_sessions(tmp_path) == [path]


@pytest.mark.parametrize("kind,extra", [
    ("progress", {}), ("queue-operation", {}), ("file-history-snapshot", {}),
    ("system", {"subtype": "stop_hook_summary"}), ("attachment", {}), ("api-request", {}),
])
def test_auxiliary_events_do_not_cut_time(tmp_path, kind, extra):
    s = Session().user(0).tool_use(2, "t1", "Bash", {"command": "ls"})
    s.raw(3, kind, **extra)
    s.raw(6, kind, **extra)
    s.tool_result(10, "t1")
    r = run(s, tmp_path)
    assert r["tools"] == {"Bash": 8}
    assert r["tool_calls"] == {"Bash": 1}
    assert r["buckets"]["model"] == 2


def test_ask_user_question_counts_as_user(tmp_path):
    s = Session().user(0).tool_use(2, "t1", "AskUserQuestion").tool_result(32, "t1")
    r = run(s, tmp_path)
    assert r["buckets"]["user"] == 30
    assert r["buckets"]["tool"] == 0
    assert r["tools"] == {}


@pytest.mark.parametrize("name", ["ExitPlanMode", "mcp__ccd_director__request_directory"])
def test_other_user_waits(tmp_path, name):
    s = Session().user(0).tool_use(2, "t1", name).tool_result(12, "t1")
    assert run(s, tmp_path)["buckets"]["user"] == 10


@pytest.mark.parametrize("row,key", [
    ({"turnOrigin": "task_notification"}, ("notify", None)),
    ({"origin": {"kind": "task-notification"}}, ("notify", None)),
    ({"origin": {"kind": "peer"}}, ("peer", None)),
    ({"turnOrigin": "sdk"}, ("sdk", None)),
    ({"turnOrigin": "weird"}, ("other", "weird")),
])
def test_origin_is_background(tmp_path, row, key):
    s = Session().user(0).assistant(1).user(11, **row)
    r = run(s, tmp_path)
    assert r["buckets"]["bg"] == 10
    assert r["tools"] == {key: 10}


def test_task_notification_text_is_background(tmp_path):
    s = Session().user(0).assistant(1).user(11, "<task-notification>done</task-notification>")
    assert run(s, tmp_path)["tools"] == {("notify", None): 10}


def test_human_turn_origin_is_user(tmp_path):
    s = Session().user(0).assistant(1).user(11, turnOrigin="human")
    r = run(s, tmp_path)
    assert r["buckets"]["user"] == 10 and r["buckets"]["bg"] == 0


def test_meta_without_origin_is_skipped(tmp_path):
    s = Session().user(0).assistant(1).user(11, isMeta=True).assistant(20)
    r = run(s, tmp_path)
    assert dict(r["buckets"]) == {"model": 20}


def test_wake_split_at_last_auxiliary_event(tmp_path):
    s = Session().user(0).assistant(1, mid="m1")
    s.raw(10, "attachment")
    s.raw(41, "system", subtype="stop_hook_summary")
    s.assistant(50, mid="m2")
    r = run(s, tmp_path)
    assert r["tools"] == {("wake", "system:stop_hook_summary"): 40}
    assert r["buckets"]["bg"] == 40
    assert r["buckets"]["model"] == 1 + 9


def test_wake_without_auxiliary_event_is_all_background(tmp_path):
    s = Session().user(0).assistant(1, mid="m1").assistant(50, mid="m2")
    r = run(s, tmp_path)
    assert r["tools"] == {("wake", None): 49}
    assert r["buckets"]["model"] == 1


def test_same_message_id_is_not_a_wake(tmp_path):
    s = Session().user(0).assistant(1, mid="m1").assistant(50, mid="m1")
    r = run(s, tmp_path)
    assert r["buckets"]["model"] == 50 and r["buckets"]["bg"] == 0


def agent_session(notify_row, notify_text="hi"):
    return (Session().user(0)
            .tool_use(1, "tu1", "Agent", {"subagent_type": "explorer"})
            .tool_result(2, "tu1", "agentId: abc12345678 started")
            .user(12, notify_text, **notify_row))


@pytest.mark.parametrize("row,text", [
    ({"origin": {"kind": "task_notification", "from": "abc12345678"}}, "hi"),
    ({}, "<task-notification><task-id>abc12345678</task-id></task-notification>"),
    ({"origin": {"kind": "task_notification", "from": "xabc12345678"}}, "hi"),
])
def test_agent_match(tmp_path, row, text):
    r = run(agent_session(row, text), tmp_path)
    assert r["tools"][("agent", "Agent(explorer)")] == 10
    assert r["tools"]["Agent(explorer)"] == 1
    assert item_name(labels("en"), ("agent", "Agent(explorer)")) == "Waiting for Agent(explorer) report"
    assert item_name(labels("zh-TW"), ("agent", "Agent(explorer)")) == "等 Agent(explorer) 回報"


def test_agent_unmatched(tmp_path):
    r = run(agent_session({"origin": {"kind": "task_notification", "from": "zzzzzzzzzzz"}}), tmp_path)
    key = ("unmatched", ("notify", None))
    assert r["tools"][key] == 10
    assert item_name(labels("en"), key) == "Background task notification (unmatched)"
    assert item_name(labels("zh-TW"), key) == "背景工作完成通知（未對應）"


def agent_transcript():
    s = Session(sidechain=True).user(0)
    s.thinking(10)
    s.tool_use(15, "e1", "Edit")
    s.tool_result(17, "e1")
    s.tool_use(20, "b1", "Bash", {"command": "pytest -q"})
    s.tool_result(30, "b1")
    s.assistant(35)
    s.user(1000, "follow-up")
    s.thinking(1004)
    s.tool_use(1010, "r1", "Read")
    s.tool_result(1011, "r1")
    s.assistant(5011)  # gap over the cap: dropped
    return s


def test_subagent_phases_and_retiming(tmp_path):
    main = Session().user(0).assistant(1).write(tmp_path)
    path = agent_transcript().write_agent(main, "a1")
    phases, start = analyze_agent(path, CAP)
    assert dict(phases) == {"think": 10 + 4, "code": 5, "edit": 2, "gen": 3 + 5 + 6,
                            "test": 10, "read": 1}
    assert start.isoformat().startswith("2026-01-01T00:00:00")


def test_phase_groups():
    assert [phase_group(p) for p in ["think", "poll", "edit", "git", "other_tool", "read"]] == \
        ["think", "poll", "other", "other", "other", "read"]


def test_agent_files_meta_then_match(tmp_path):
    main = agent_session({}, "x").write(tmp_path)
    a = Session(sidechain=True).user(0).assistant(1)
    a.write_agent(main, "withmeta", "reviewer")
    a.write_agent(main, "abc12345678")
    a.write_agent(main, "nomatch99999")
    r = analyze(main, CAP)
    got = {f.stem: label for label, f in agent_files(main, r["agent_corpus"])}
    assert got == {"agent-withmeta": "Agent(reviewer)", "agent-abc12345678": "Agent(explorer)",
                   "agent-nomatch99999": None}


@pytest.mark.parametrize("cmd,phase", [
    ("rtk grep -rn foo src", "read"),
    ("cd x && npm test", "test"),
    ("until grep -q done log; do sleep 5; done; tail log", "poll"),
    ("QUICK=1 node a.js | tail", "script"),
    ("git add . && git commit -m x", "git"),
    ("npm install", "install"),
    ("make all", "other_cmd"),
    ("cd x && export A=1", "other_cmd"),
])
def test_bash_phase(cmd, phase):
    assert bash_phase(cmd) == phase


def test_project_filter_normalizes(tmp_path):
    Session().user(0).assistant(5).write(tmp_path, project="-Users-x-my-proj")
    Session().user(0).assistant(5).write(tmp_path, project="-Users-x-other", sid="22222222-bbbb")
    got = find_sessions(tmp_path, project="my_proj")
    assert [p.parent.name for p in got] == ["-Users-x-my-proj"]


def test_default_root_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert default_root() == tmp_path / "projects"


def secret_root(tmp_path):
    s = Session(cwd="/Users/x/secretproj").user(0).tool_use(2, "t1", "Read").tool_result(12, "t1")
    s.write(tmp_path, project="-Users-x-secretproj", sid="deadbeef-1234-5678")
    return tmp_path


@pytest.mark.parametrize("lang,needle", [("en", "Daily time allocation"), ("zh-TW", "每日時間分配")])
def test_html_languages_and_script_escape(tmp_path, capsys, lang, needle):
    s = Session().user(0).tool_use(2, "t1", "mcp__srv__</script><b>x").tool_result(12, "t1")
    s.write(tmp_path)
    out = tmp_path / "x.html"
    cli.main(["--root", str(tmp_path), *ALL, "--html", str(out), "--lang", lang])
    html = out.read_text(encoding="utf-8")
    assert f'<html lang="{lang}">' in html
    assert html.count("</script>") == 2
    assert "<\\/script>" in html
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(m.group(1))
    assert data["labels"]["daily_title"] == needle
    assert data["items"][0]["name"] == "mcp:srv…</script><b>x"
    assert "Report written" in capsys.readouterr().out or lang == "zh-TW"


def test_cli_modes(tmp_path, capsys, monkeypatch):
    path = secret_root(tmp_path)
    Session(sidechain=True).user(0).thinking(4).assistant(9).write_agent(
        next(path.glob("*/*.jsonl")), "a1", "explorer")
    base = ["--root", str(path), *ALL]
    cli.main(base)
    assert "Read" in capsys.readouterr().out
    cli.main([*base, "--session", "deadbeef", "--trace", "2"])
    assert "Slowest" in capsys.readouterr().out
    cli.main([*base, "--agents", "--lang", "zh-TW"])
    out = capsys.readouterr().out
    assert "Agent(explorer)" in out and "思考" in out
    monkeypatch.setenv("CC_TIMELINE_LANG", "zh-TW")
    cli.main(base)
    assert "合計 1 個 session" in capsys.readouterr().out


def test_no_sessions_exits(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--root", str(tmp_path)])


# ── privacy ──

def test_shape_keeps_only_tag_name():
    e = Session().user(0, '<div class="x">secret').rows[0]
    assert shape(e) == "user:<div"
    e = Session().user(0, "<task-notification>done</task-notification>").rows[0]
    assert shape(e) == "user:<task-notification"
    e = Session().user(0, "<" + "a" * 50).rows[0]
    assert shape(e) == "user:<" + "a" * 29


def test_trace_reason_free_text_is_masked(tmp_path, capsys):
    assert trace_value("reason", "free text secret") == '"<text>"'
    assert trace_value("reason", "Has-Upper") == '"<text>"'
    assert trace_value("reason", "stop_sequence") == '"stop_sequence"'
    assert trace_value("operation", "enqueue") == '"enqueue"'
    s = Session().user(0).tool_use(2, "t1", "Read")
    s.raw(5, "system", reason="my private words")
    s.tool_result(12, "t1").write(tmp_path, sid="abcd1234-x")
    cli.main(["--root", str(tmp_path), "--session", "abcd", "--trace", "2"])
    out = capsys.readouterr().out
    assert "private" not in out and '"<text>"' in out


def redact_root(tmp_path):
    s = (Session(cwd="/Users/x/secretproj").user(0)
         .tool_use(1, "tu1", "Agent", {"subagent_type": "secretagent"})
         .tool_result(2, "tu1", "agentId: abc12345678 started")
         .tool_use(3, "tu2", "Agent", {"subagent_type": "Explore"})
         .tool_result(4, "tu2", "agentId: builtin99999 started")
         .tool_use(5, "t2", "mcp__secretsrv__dostuff").tool_result(9, "t2")
         .user(20, "x", origin={"kind": "task_notification", "from": "abc12345678"}))
    main = s.write(tmp_path, project="-Users-x-secretproj", sid="deadbeef-1234-5678")
    a = Session(sidechain=True).user(0).thinking(4).assistant(9)
    a.write_agent(main, "abc12345678", "secretagent")
    a.write_agent(main, "builtin99999", "Explore")
    return tmp_path


REDACT_SECRETS = ("secretproj", "deadbeef", "secretagent", "secretsrv")


def redact_outputs(root, tmp_path, capsys):
    out = tmp_path / "r.html"
    csv_path = tmp_path / "r.csv"
    argsets = [[], ["--json"], ["--agents"], ["--session", "deadbeef", "--trace", "5"],
               ["--html", str(out)], ["--by", "project"], ["--csv", str(csv_path)],
               ["--csv", str(csv_path), "--by", "day"]]
    for extra in argsets:
        capsys.readouterr()
        cli.main(["--root", str(root), *ALL, "--redact", *extra])
        text = capsys.readouterr().out
        for f in (out, csv_path):
            if f.exists():
                text += f.read_text(encoding="utf-8")
                f.unlink()
        yield extra, text


def test_redact_everywhere(tmp_path, capsys):
    root = redact_root(tmp_path)
    seen = []
    for extra, text in redact_outputs(root, tmp_path, capsys):
        for secret in REDACT_SECRETS:
            assert secret not in text, (extra, secret)
        seen.append(text)
    joined = "\n".join(seen)
    assert "Agent(agent-1)" in joined and "mcp-1" in joined and "dostuff" in joined
    assert "Agent(Explore)" in joined
    capsys.readouterr()
    cli.main(["--root", str(root), *ALL, "--redact", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["sessions"][0]["project"] == "project-1"
    assert re.fullmatch(r"[0-9a-f]{8}", data["sessions"][0]["id"])
    assert "labels" not in data
    assert {a["type"] for a in data["agents"]} == {"Agent(agent-1)", "Agent(Explore)"}


# ── date range ──

def test_since_until_use_first_timestamp_not_mtime(tmp_path, capsys):
    # Jan 1 session: its file mtime is "now" (inside --days), but it started outside the range.
    Session().user(0).assistant(5).write(tmp_path, sid="aaaa0000-x")
    mar = 59 * 86400 + 12 * 3600  # 2026-03-01 12:00 UTC
    Session().user(mar).assistant(mar + 5).write(tmp_path, sid="bbbb0000-x")
    base = ["--root", str(tmp_path), "--json"]

    def ids(*a):
        cli.main([*base, *a])
        return [x["id"] for x in json.loads(capsys.readouterr().out)["sessions"]]

    assert ids("--since", "2026-02-28", "--until", "2026-03-02") == ["bbbb0000"]
    assert ids("--since", "2025-12-31", "--until", "2026-01-02") == ["aaaa0000"]
    assert sorted(ids("--since", "2020-01-01")) == ["aaaa0000", "bbbb0000"]
    assert ids("--until", "2026-01-02") == ["aaaa0000"]  # --until alone has no lower bound
    # --since wins over --days
    assert sorted(ids("--since", "2020-01-01", "--days", "1")) == ["aaaa0000", "bbbb0000"]
    # --session ignores the range
    assert ids("--session", "aaaa", "--since", "2030-01-01") == ["aaaa0000"]
    # period text
    cli.main([*base, "--since", "2020-01-01", "--until", "2026-12-31"])
    assert json.loads(capsys.readouterr().out)["period"] == "2020-01-01 to 2026-12-31"


def test_days_default_filters_old_sessions(tmp_path):
    Session().user(0).assistant(5).write(tmp_path, sid="aaaa0000-x")  # starts 2026-01-01
    assert cli.main(["--root", str(tmp_path), "--days", "1000"]) == 0
    with pytest.raises(SystemExit):
        cli.main(["--root", str(tmp_path), "--days", "1"])


def test_mtime_is_only_a_prescreen(tmp_path):
    p = Session().user(0).assistant(5).write(tmp_path)
    os.utime(p, (1_000_000_000, 1_000_000_000))  # 2001
    from datetime import datetime, timezone
    assert find_sessions(tmp_path, since=datetime(2020, 1, 1, tzinfo=timezone.utc)) == []
    assert find_sessions(tmp_path, since=datetime(2000, 1, 1, tzinfo=timezone.utc)) == [p]


def test_bad_date_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--root", str(tmp_path), "--since", "01/02/2026"])


# ── grouping and export ──

DAY = 86400
NOON = 12 * 3600


def group_root(tmp_path):
    def sess(t, tool_sec, cwd, sid, project):
        Session(cwd=cwd).user(t).assistant(t + 10).tool_use(t + 12, "t1", "Read") \
            .tool_result(t + 12 + tool_sec, "t1").user(t + 18 + tool_sec) \
            .write(tmp_path, project=project, sid=sid)
    sess(NOON, 8, "/x/alpha", "aaaa0001-x", "-x-alpha")                 # 2026-01-01, W01
    sess(DAY + NOON, 18, "/x/beta", "bbbb0002-x", "-x-beta")           # 2026-01-02, W01
    sess(4 * DAY + NOON, 8, "/x/alpha", "cccc0003-x", "-x-alpha")      # 2026-01-05, W02
    return tmp_path


def rows_for(root, by):
    results = [analyze(p, CAP) for p in sorted(root.glob("*/*.jsonl"))]
    return {r["key"]: r for r in report.group_rows(results, by)}


def test_group_rows_day_week_project(tmp_path):
    root = group_root(tmp_path)
    day = rows_for(root, "day")
    assert list(day) == ["2026-01-01", "2026-01-02", "2026-01-05"]
    assert (day["2026-01-02"]["n"], day["2026-01-02"]["model"], day["2026-01-02"]["tool"],
            day["2026-01-02"]["user"], day["2026-01-02"]["active"]) == (1, 12, 18, 6, 30)
    week = rows_for(root, "week")
    assert list(week) == ["2026-W01", "2026-W02"]
    w1 = week["2026-W01"]
    assert (w1["n"], w1["model"], w1["tool"], w1["bg"], w1["user"], w1["idle"], w1["active"]) == \
        (2, 24, 26, 0, 12, 0, 50)
    proj = rows_for(root, "project")
    assert (proj["alpha"]["n"], proj["alpha"]["model"], proj["alpha"]["tool"], proj["alpha"]["user"]) == \
        (2, 24, 16, 12)
    assert list(proj)[0] == "alpha"  # alpha 40s active > beta 30s
    assert len(rows_for(root, "session")) == 3


def test_by_table_and_csv(tmp_path, capsys):
    root = group_root(tmp_path / "t")
    base = ["--root", str(root), *ALL]
    cli.main([*base, "--by", "week"])
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert lines[1].startswith("2026-W01") and lines[2].startswith("2026-W02")
    assert lines[3].startswith("Total")
    assert "50s" in lines[1] and "26s" in lines[1]
    # sessions CSV
    path = tmp_path / "a.csv"
    cli.main([*base, "--csv", str(path)])
    capsys.readouterr()
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert list(rows[0]) == ["start", "project", "session", "wall", "model", "tool", "bg", "user",
                             "idle", "errors", "top"]
    assert [(r["session"], r["project"]) for r in rows] == \
        [("aaaa0001", "alpha"), ("bbbb0002", "beta"), ("cccc0003", "alpha")]
    beta = rows[1]
    assert (float(beta["wall"]), float(beta["model"]), float(beta["tool"]), float(beta["user"]),
            float(beta["bg"]), float(beta["idle"]), int(beta["errors"])) == (36, 12, 18, 6, 0, 0, 0)
    assert beta["top"] == "Read"
    # grouped CSV
    cli.main([*base, "--csv", str(path), "--by", "week"])
    capsys.readouterr()
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert list(rows[0]) == ["group", "sessions", "active", "model", "tool", "bg", "user", "idle"]
    assert [(r["group"], r["sessions"], float(r["active"]), float(r["tool"])) for r in rows] == \
        [("2026-W01", "2", 50, 26), ("2026-W02", "1", 20, 8)]


def test_agent_scan_is_cached_per_session(tmp_path, monkeypatch):
    main = Session().user(0).assistant(1).write(tmp_path)
    Session(sidechain=True).user(0).assistant(9).write_agent(main, "a1", "explorer")
    calls = []
    real = report.scan_agent
    monkeypatch.setattr(report, "scan_agent", lambda *a: calls.append(a) or real(*a))
    results = [analyze(main, CAP)]
    report.build_report(results, 7, None, "en", CAP)
    report.print_agents(results, "en", CAP)
    assert len(calls) == 1


# ── format drift ──

def known_session():
    s = Session().user(0, version="2.0.9").assistant(5, version="2.0.10")
    s.raw(6, "progress", version="1.9.0")
    s.raw(7, "attachment")
    s.user(11, turnOrigin="human").user(20, turnOrigin="peer")
    return s


def test_format_known_values_no_warning(tmp_path, capsys):
    path = known_session().write(tmp_path)
    with path.open("a") as f:
        f.write(json.dumps({"type": "summary", "summary": "x"}) + "\n")  # no timestamp
    cli.main(["--root", str(tmp_path), *ALL])
    assert capsys.readouterr().err == ""
    cli.main(["--root", str(tmp_path), *ALL, "--json"])
    data = json.loads(capsys.readouterr().out)
    fmt = data["format"]
    assert fmt["types"] == {"assistant": 1, "attachment": 1, "progress": 1, "summary": 1, "user": 3}
    assert fmt["origins"] == {"human": 1, "peer": 1}
    assert fmt["versions"] == {"min": "1.9.0", "max": "2.0.10"}
    assert fmt["unknown"] == {"types": {}, "origins": {}}
    assert data["formatWarning"] == ""


@pytest.mark.parametrize("lang,needle", [("en", "open an issue"), ("zh-TW", "請開 issue")])
def test_format_unknown_warns(tmp_path, capsys, lang, needle):
    s = known_session()
    s.raw(8, "hologram").raw(9, "hologram")
    s.user(30, turnOrigin="weird")
    s.write(tmp_path)
    out = tmp_path / "f.html"
    cli.main(["--root", str(tmp_path), *ALL, "--lang", lang])
    err = capsys.readouterr().err.strip()
    assert "hologram x2" in err and "weird x1" in err and needle in err
    assert len(err.splitlines()) == 1
    cli.main(["--root", str(tmp_path), *ALL, "--lang", lang, "--html", str(out)])
    capsys.readouterr()
    html = out.read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',
                                html, re.S).group(1).replace("<\\/", "</"))
    assert data["format"]["unknown"] == {"types": {"hologram": 2}, "origins": {"weird": 1}}
    assert needle in data["formatWarning"]
    assert 'id="foot"' in html
    cli.main(["--root", str(tmp_path), *ALL, "--json"])
    assert json.loads(capsys.readouterr().out)["format"]["unknown"]["types"] == {"hologram": 2}
    # unknown origin still counts as background, labelled by its value
    r = analyze(next(tmp_path.glob("*/*.jsonl")), CAP)
    assert r["tools"][("other", "weird")] == 10


# ── session timeline (gantt) ──

def timeline_for(tmp_path, n=20, **kw):
    results = [analyze(p, CAP) for p in sorted(tmp_path.glob("*/*.jsonl"))]
    return report.build_report(results, 7, None, "en", CAP, timeline_sessions=n, **kw)["timeline"]


def test_main_segments_with_wake_split_and_idle(tmp_path):
    s = Session().user(0).assistant(1, mid="m1")
    s.raw(10, "attachment")
    s.raw(41, "system", subtype="stop_hook_summary")
    s.assistant(50, mid="m2")
    assert run(s, tmp_path)["segments"][0][2] == "model"
    tl = timeline_for(tmp_path)
    assert tl["sessions"][0]["main"] == [[0, 1, "model"], [1, 41, "bg"], [41, 50, "model"]]
    assert tl["sessions"][0]["gaps"] == []


def test_main_segments_merge_and_idle_gap(tmp_path):
    basic().write(tmp_path)
    s = timeline_for(tmp_path)["sessions"][0]
    assert s["main"] == [[0, 7, "model"], [7, 12, "tool"], [12, 14, "model"], [14, 20, "user"],
                         [20, 4020, "idle"]]
    assert s["gaps"] == [[20, 4020]]
    assert s["wall"] == 4020 and s["rows"] == 0 and s["agents"] == []


def test_subagent_segments_use_phase_groups_and_merge(tmp_path):
    main = Session().user(0).assistant(1).write(tmp_path)
    agent_transcript().write_agent(main, "a1", "explorer")
    a = Session(sidechain=True).user(0).thinking(2).thinking(5).thinking(9)
    a.write_agent(main, "a2", "reviewer")
    s = timeline_for(tmp_path)["sessions"][0]
    by = {x["type"]: x for x in s["agents"]}
    assert by["Agent(explorer)"]["segs"] == [
        [0, 10, "think"], [10, 15, "code"], [15, 17, "other"], [17, 20, "gen"], [20, 30, "test"],
        [30, 35, "gen"], [1000, 1004, "think"], [1004, 1010, "gen"], [1010, 1011, "read"]]
    assert (by["Agent(explorer)"]["start"], by["Agent(explorer)"]["end"]) == (0, 5011)
    assert by["Agent(reviewer)"]["segs"] == [[0, 9, "think"]]
    assert sorted(x["row"] for x in s["agents"]) == [0, 1] and s["rows"] == 2


def test_gaps_skip_stretches_where_an_agent_works(tmp_path):
    main = basic().write(tmp_path)  # away from 20 to 4020
    Session(sidechain=True).user(100).thinking(500).write_agent(main, "a1", "explorer")
    s = timeline_for(tmp_path)["sessions"][0]
    assert s["main"][-1] == [20, 4020, "idle"]
    assert s["gaps"] == [[20, 100], [500, 4020]]


def test_agents_share_rows_beyond_fifteen(tmp_path):
    main = Session().user(0).assistant(1).write(tmp_path)
    for i in range(17):  # back to back, never overlapping
        Session(sidechain=True).user(i * 10).thinking(i * 10 + 5).write_agent(main, f"s{i:02d}", "explorer")
    Session(sidechain=True).user(0).thinking(160).write_agent(main, "long", "explorer")  # overlaps all
    s = timeline_for(tmp_path)["sessions"][0]
    assert len(s["agents"]) == 18 and s["rows"] == 2
    assert {a["row"] for a in s["agents"]} == {0, 1}
    few = report.assign_rows([{"type": "t", "start": i, "end": i + 1} for i in range(15)])
    assert few == 15


def test_timeline_sessions_limit_and_default(tmp_path, capsys):
    # A: 5s model only. B: long, with 8s of background wait. C: longest active, no background.
    Session().user(0).assistant(5).write(tmp_path, sid="aaaa0000-x")
    Session().user(0).assistant(2, mid="m1").assistant(10, mid="m2").write(tmp_path, sid="bbbb0000-x")
    Session().user(0).assistant(100).write(tmp_path, sid="cccc0000-x")
    tl = timeline_for(tmp_path, n=2)
    assert sorted(s["id"] for s in tl["sessions"]) == ["bbbb0000", "cccc0000"]
    assert tl["sessions"][tl["default"]]["id"] == "bbbb0000"  # highest background share
    assert timeline_for(tmp_path, n=0) == {"sessions": [], "default": 0}
    # CLI option and HTML
    out = tmp_path / "y.html"
    cli.main(["--root", str(tmp_path), *ALL, "--html", str(out), "--timeline-sessions", "0"])
    capsys.readouterr()
    html = out.read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',
                                html, re.S).group(1).replace("<\\/", "</"))
    assert data["timeline"]["sessions"] == []


@pytest.mark.parametrize("lang,needle", [("en", "Session timeline"), ("zh-TW", "Session 時間軸")])
def test_html_timeline_card(tmp_path, capsys, lang, needle):
    main = basic().write(tmp_path)
    Session(sidechain=True).user(100).thinking(500).write_agent(main, "a1", "explorer")
    out = tmp_path / "t.html"
    cli.main(["--root", str(tmp_path), *ALL, "--html", str(out), "--lang", lang])
    capsys.readouterr()
    html = out.read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',
                                html, re.S).group(1).replace("<\\/", "</"))
    assert data["labels"]["tl_title"] == needle
    assert 'id="tlCard"' in html
    s = data["timeline"]["sessions"][0]
    assert s["agents"][0]["type"] == "Agent(explorer)" and s["gaps"] == [[20, 100], [500, 4020]]

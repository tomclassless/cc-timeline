import json
import os
import re

import pytest
from helpers import Session

from cc_timeline import cli
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
    argsets = [[], ["--json"], ["--agents"], ["--session", "deadbeef", "--trace", "5"],
               ["--html", str(out)]]
    for extra in argsets:
        capsys.readouterr()
        cli.main(["--root", str(root), *ALL, "--redact", *extra])
        text = capsys.readouterr().out
        if out.exists():
            text += out.read_text(encoding="utf-8")
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

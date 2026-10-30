import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fixtures_mcp import AFTER, BEFORE, CANARY, CWD, EXEC, PROJECT, PROJECT_DIR, REV, standard_root

from cc_timeline import __version__, cli, mcp
from cc_timeline.mcp import McpServer

SRC = str(Path(__file__).parent.parent / "src")
TOOL_NAMES = {"summary", "agent_phases", "session_timeline", "compare", "quality_signals"}
ALL = {"since": "2000-01-01"}


@pytest.fixture
def srv(tmp_path):
    return McpServer(standard_root(tmp_path), cwd=CWD)


def rpc(srv, method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return srv.handle(msg)


def call(srv, name, **args):
    r = rpc(srv, "tools/call", {"name": name, "arguments": args})
    return r["result"]


def data(srv, name, **args):
    r = call(srv, name, **args)
    assert not r.get("isError"), r
    assert json.loads(r["content"][0]["text"]) == r["structuredContent"]
    return r["structuredContent"]


# ── protocol ──

@pytest.mark.parametrize("asked,expected", [("2025-03-26", "2025-03-26"), ("2024-11-05", "2024-11-05"),
                                            ("2025-06-18", "2025-06-18"), ("2099-01-01", "2025-06-18")])
def test_initialize_version_negotiation(srv, asked, expected):
    r = rpc(srv, "initialize", {"protocolVersion": asked, "capabilities": {}, "clientInfo": {"name": "t"}})
    assert r["id"] == 1 and r["jsonrpc"] == "2.0"
    assert r["result"] == {"protocolVersion": expected, "capabilities": {"tools": {}},
                           "serverInfo": {"name": "cc-timeline", "version": __version__}}


def test_initialize_requires_version(srv):
    assert rpc(srv, "initialize", {})["error"]["code"] == -32602


def test_notifications_get_no_response(srv):
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert srv.handle({"jsonrpc": "2.0", "method": "no/such/notification"}) is None
    assert srv.handle({"jsonrpc": "2.0", "id": 3, "result": {}}) is None   # stray response


def test_ping(srv):
    assert rpc(srv, "ping", id_="x") == {"jsonrpc": "2.0", "id": "x", "result": {}}


def test_tools_list(srv):
    tools = rpc(srv, "tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == TOOL_NAMES and len(tools) == 5
    for t in tools:
        s = t["inputSchema"]
        assert s["type"] == "object" and isinstance(s["properties"], dict)
        assert set(s.get("required", [])) <= set(s["properties"])
        assert len(t["description"]) > 80 and t["description"].isascii()
        assert s["properties"]["redact"]["type"] == "boolean" if "redact" in s["properties"] else False
        for p in s["properties"].values():
            assert p["type"] in ("string", "number", "integer", "boolean", "object")
    assert {"before", "after"} == set(next(t for t in tools if t["name"] == "compare")["inputSchema"]["required"])
    json.dumps(tools)


def test_unknown_method(srv):
    r = rpc(srv, "resources/list")
    assert r["error"]["code"] == -32601 and r["id"] == 1


@pytest.mark.parametrize("params", [
    {}, {"name": 5}, {"name": "nope"}, {"name": "summary", "arguments": []},
    {"name": "summary", "arguments": {"days": "7"}}, {"name": "summary", "arguments": {"days": True}},
    {"name": "summary", "arguments": {"by": "year"}}, {"name": "summary", "arguments": {"redact": "yes"}},
    {"name": "summary", "arguments": {"days": -3}}, {"name": "summary", "arguments": {"since": "bad"}},
    {"name": "session_timeline", "arguments": {}}, {"name": "compare", "arguments": {"before": BEFORE}},
    {"name": "compare", "arguments": {"before": {"since": "2026-01-01"}, "after": AFTER}},
])
def test_invalid_params(srv, params):
    r = rpc(srv, "tools/call", params)
    assert r["error"]["code"] == -32602, r


def test_invalid_request_shapes(srv):
    assert srv.handle([1])["error"]["code"] == -32600
    assert srv.handle({"id": 4})["error"]["code"] == -32600
    assert rpc(srv, "tools/list", params=[1])["error"]["code"] == -32602


def test_tool_error_is_iserror_and_server_survives(srv, monkeypatch):
    r = call(srv, "session_timeline", session="nomatch")
    assert r["isError"] is True and "nomatch" in r["content"][0]["text"] and "structuredContent" not in r

    def boom(server, a):
        raise RuntimeError(f"explodes with {CANARY}")
    monkeypatch.setitem(mcp.BY_NAME["summary"], "handler", boom)
    r = call(srv, "summary")
    assert r["isError"] is True and "RuntimeError" in r["content"][0]["text"]
    assert CANARY not in json.dumps(r)
    assert data(srv, "quality_signals", **ALL)["sessions"] == 2
    assert rpc(srv, "ping")["result"] == {}


def test_null_arguments_are_ignored(srv):
    assert data(srv, "summary", days=None, since="2000-01-01", project=None)["sessions"] == 2


def test_serve_loop_lines(tmp_path):
    import io
    server = McpServer(standard_root(tmp_path), cwd=CWD)
    lines = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        "not json",
        "",
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps([{"jsonrpc": "2.0", "id": 2, "method": "ping"}, {"jsonrpc": "2.0", "id": 3, "method": "x"}]),
    ]) + "\n"
    out = io.StringIO()
    mcp.serve(server, io.StringIO(lines), out)
    got = [json.loads(x) for x in out.getvalue().splitlines()]
    assert got[0]["result"] == {} and got[1]["error"]["code"] == -32700
    assert [m["id"] for m in got[2]] == [2, 3] and got[2][1]["error"]["code"] == -32601
    assert len(got) == 3


def test_subprocess_handshake(tmp_path):
    root = standard_root(tmp_path)
    env = {**os.environ, "PYTHONPATH": SRC, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen([sys.executable, "-m", "cc_timeline", "mcp", "--root", str(root)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", env=env, cwd=str(tmp_path))

    def send(m):
        p.stdin.write(json.dumps(m) + "\n")
        p.stdin.flush()

    def recv():
        line = p.stdout.readline()
        assert line.endswith("\n") and "\n" not in line.strip()
        return json.loads(line)

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        assert recv()["result"]["serverInfo"]["name"] == "cc-timeline"
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert len(recv()["result"]["tools"]) == 5     # nothing was sent for the notification
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "quality_signals", "arguments": {"since": "2000-01-01"}}})
        r = recv()
        assert r["id"] == 3
        q = r["result"]["structuredContent"]
        assert q["sessions"] == 2 and q["api_errors"] == 1
    finally:
        p.stdin.close()
    out, err = p.stdout.read(), p.stderr.read()
    assert p.wait(timeout=10) == 0 and out == ""
    p.stdout.close()
    p.stderr.close()
    assert "ready" in err


# ── tools on synthetic data ──

def test_tool_summary(srv):
    d = data(srv, "summary", **ALL, by="day", top=3)
    assert d["sessions"] == 2 and d["buckets"]["tool"]["sec"] == 28 and len(d["top_items"]) == 3
    assert len(d["groups"]["rows"]) == 2


def test_tool_agent_phases(srv):
    d = data(srv, "agent_phases", **ALL, agent_type=EXEC)
    assert [a["type"] for a in d["agents"]] == [EXEC] and d["agents"][0]["total_sec"] == 35


def test_tool_session_timeline(srv):
    d = data(srv, "session_timeline", session="11111111")
    assert d["wall_sec"] == 83 and len(d["subagents"]) == 2
    assert data(srv, "session_timeline", session="current")["session"] in ("11111111", "22222222")


def test_tool_session_current_not_found(tmp_path):
    s = McpServer(standard_root(tmp_path), cwd="/somewhere/else")
    r = call(s, "session_timeline", session="current")
    assert r["isError"] and "working directory" in r["content"][0]["text"]


def test_tool_compare(srv):
    d = data(srv, "compare", before=BEFORE, after=AFTER)
    assert d["delta"]["buckets"]["model"]["sec"] == -5 and len(d["notes"]) == 2
    assert data(srv, "compare", before=BEFORE, after=AFTER, project="zzproj")["before"]["sessions"] == 1


def test_tool_quality_signals(srv):
    d = data(srv, "quality_signals", **ALL)
    assert d["executor_dispatches_per_session"] == 0.5
    assert {a["type"]: a["reports_per_dispatch"] for a in d["agents"]}[REV] == 1.5


# ── privacy ──

def all_outputs(srv, redact):
    kw = {"redact": redact}
    outs = [call(srv, "summary", **ALL, by="session", **kw), call(srv, "summary", **ALL, by="project", **kw),
            call(srv, "agent_phases", **ALL, **kw), call(srv, "quality_signals", **ALL, **kw),
            call(srv, "session_timeline", session="11111111", **kw),
            call(srv, "session_timeline", session="current", **kw),
            call(srv, "compare", before=BEFORE, after=AFTER, **kw)]
    for o in outs:
        assert not o.get("isError"), o
    return [json.dumps(o, ensure_ascii=False) for o in outs]


def test_no_conversation_content_in_any_tool(srv):
    for text in all_outputs(srv, False) + all_outputs(srv, True):
        assert CANARY not in text and "pytest" not in text and "hello" not in text


def test_redact_hides_names(srv):
    plain = "".join(all_outputs(srv, False))
    assert PROJECT in plain and EXEC in plain and REV in plain
    for text in all_outputs(srv, True):
        assert PROJECT not in text and EXEC not in text and REV not in text
        assert "11111111" not in text and "22222222" not in text
    red = json.loads(all_outputs(srv, True)[3])["structuredContent"]
    assert {a["type"] for a in red["agents"]} == {"agent-1", "agent-2"}
    assert red["executor_dispatches_per_session"] == 0.5   # computed before names are hidden


def test_cli_compare_and_quality_have_no_content(tmp_path, capsys):
    root = standard_root(tmp_path)
    cmds = [["compare", "--before", "2025-12-30:2026-01-05", "--after", "2026-01-08:2026-01-20"],
            ["quality", "--since", "2000-01-01"]]
    for base in cmds:
        for extra in ([], ["--json"], ["--lang", "zh-TW"], ["--redact"], ["--redact", "--json"]):
            cli.main([*base, "--root", str(root), *extra])
            out = capsys.readouterr().out
            assert out and CANARY not in out and "pytest" not in out
            if "--redact" in extra:
                assert PROJECT not in out and EXEC not in out and REV not in out

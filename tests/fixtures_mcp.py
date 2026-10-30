"""Synthetic transcript tree shared by test_queries.py and test_mcp.py."""
from helpers import Session

CANARY = "SECRET-CANARY-7f3a"
DAY = 86400
NOON = 12 * 3600          # sessions start at noon UTC so the local date is stable
BEFORE = {"since": "2025-12-30", "until": "2026-01-05"}
AFTER = {"since": "2026-01-08", "until": "2026-01-20"}
EXEC, REV = "zzexecutor", "zzreviewer"
PROJECT_DIR, PROJECT = "-Users-x-zzproj", "zzproj"
CWD = "/Users/x/zzproj"


def notify(sess, t, ref):
    return sess.user(t, "done", origin={"kind": "task_notification", "from": ref})


def full_session(day=0, sid="11111111-aaaa"):
    """Model 9, tool 17, bg 52, user 5 (busy 83, wall 83). 2 dispatches, 3 reports, 3 test runs."""
    o = day * DAY + NOON
    s = Session(cwd=CWD).user(o, f"hello {CANARY}")
    s.assistant(o + 2, {"type": "text", "text": f"reply {CANARY}"}, mid="m1")
    s.tool_use(o + 3, "b1", "Bash", {"command": f"pytest -q # {CANARY}"}, mid="m1")
    s.tool_result(o + 13, "b1", f"FAILED {CANARY}", is_error=True)
    s.tool_use(o + 14, "b2", "Bash", {"command": "pytest -q"}, mid="m2")
    s.tool_result(o + 19, "b2", is_error=False)
    s.tool_use(o + 20, "a1", "Agent", {"subagent_type": EXEC, "prompt": CANARY}, mid="m3")
    s.tool_result(o + 21, "a1", "agentId: aaa111111111 started")
    s.tool_use(o + 22, "a2", "Agent", {"subagent_type": REV}, mid="m4")
    s.tool_result(o + 23, "a2", "agentId: bbb222222222 started")
    notify(s, o + 40, "aaa111111111")
    notify(s, o + 60, "bbb222222222")
    notify(s, o + 75, "bbb222222222")   # the reviewer reports again after a re-review
    s.user(o + 80, f"next {CANARY}")
    s.assistant(o + 82, mid="m5")
    s.assistant(o + 83, mid="m5", isApiErrorMessage=True)
    return s


def write_full(root, day=0, sid="11111111-aaaa", project=PROJECT_DIR):
    main = full_session(day, sid).write(root, project=project, sid=sid)
    o = day * DAY + NOON
    # executor: think 10, code 5, other (edit) 2, gen 3+5, test 10 (a failed test run) = 35
    a = Session(sidechain=True, cwd=CWD).user(o)
    a.thinking(o + 10).tool_use(o + 15, "e1", "Edit", mid="m2").tool_result(o + 17, "e1")
    a.tool_use(o + 20, "t1", "Bash", {"command": f"pytest {CANARY}"}, mid="m3")
    a.tool_result(o + 30, "t1", is_error=True).assistant(o + 35, mid="m4")
    a.write_agent(main, "aaa111111111", EXEC)
    # reviewer: gen 4+1+5, poll 30 = 40
    b = Session(sidechain=True, cwd=CWD).user(o)
    b.assistant(o + 4, mid="m1").tool_use(o + 5, "p1", "Bash", {"command": "sleep 30"}, mid="m1")
    b.tool_result(o + 35, "p1", is_error=False).assistant(o + 40, mid="m2")
    b.write_agent(main, "bbb222222222", REV)
    return main


def write_light(root, day, sid="22222222-bbbb", project=PROJECT_DIR):
    """Model 4, tool 11, bg 20 (busy 35). 1 reviewer dispatch/report; reviewer thinks 10s."""
    o = day * DAY + NOON
    s = Session(cwd=CWD).user(o, f"hi {CANARY}")
    s.assistant(o + 2, mid="m1").tool_use(o + 3, "r1", "Read", mid="m1").tool_result(o + 13, "r1")
    s.tool_use(o + 14, "a2", "Agent", {"subagent_type": REV}, mid="m2")
    s.tool_result(o + 15, "a2", "agentId: ddd444444444 started")
    notify(s, o + 35, "ddd444444444")
    main = s.write(root, project=project, sid=sid)
    Session(sidechain=True, cwd=CWD).user(o).thinking(o + 10).write_agent(main, "ddd444444444", REV)
    return main


def small_session(root, day, n, project=PROJECT_DIR):
    o = day * DAY + NOON
    Session(cwd=CWD).user(o).assistant(o + 3).write(root, project=project, sid=f"{n:08d}-cccc")


def standard_root(tmp_path):
    write_full(tmp_path, day=0)
    write_light(tmp_path, day=11)
    return tmp_path

"""Generate a synthetic ~/.claude/projects tree for demos and screenshots (no real data).

Usage:
    python scripts/make_demo.py OUT_DIR [--now 2026-10-30T12:00:00Z]

The output is deterministic for a given --now (fixed random seed, ids derived from it), so
screenshots are reproducible. About half of the dispatch turns launch two executor agents
in parallel; the rest run an executor and then a reviewer one after the other.
Then, for example:
    cc-timeline --root OUT_DIR --since 2026-10-22 --html report.html
"""
import argparse
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

rnd = random.Random(7)
now = None
PROJECTS = ["-Users-demo-webapp", "-Users-demo-api-server", "-Users-demo-docs-site"]


def rid():
    """Deterministic stand-in for uuid4()."""
    return uuid.UUID(int=rnd.getrandbits(128), version=4)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


class Writer:
    def __init__(self, path, cwd, sidechain=False):
        self.path, self.cwd, self.side, self.lines, self.n = path, cwd, sidechain, [], 0

    def add(self, t, typ, content=None, **extra):
        e = {"type": typ, "timestamp": iso(t), "uuid": str(rid()), "cwd": self.cwd,
             "version": "2.1.0", "isSidechain": self.side, **extra}
        if content is not None:
            e["message"] = {"content": content}
        self.lines.append(e)

    def asst(self, t, block):
        # In real transcripts a thinking block and the block right after it share one message id.
        last = self.lines[-1] if self.lines else {}
        same = last.get("type") == "assistant" and last["message"]["content"][0].get("type") == "thinking"
        if not same:
            self.n += 1
        mid = last["message"]["id"] if same else f"msg_{self.n}_{rid().hex[:6]}"
        self.add(t, "assistant", [block])
        self.lines[-1]["message"].update(id=mid, model="claude-sonnet-5-5")

    def tool(self, t, name, inp, dur, result="ok"):
        tid = "toolu_" + rid().hex[:10]
        self.asst(t, {"type": "tool_use", "id": tid, "name": name, "input": inp})
        t += timedelta(seconds=dur)
        self.add(t, "user", [{"type": "tool_result", "tool_use_id": tid, "content": result}])
        return t

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(json.dumps(x) for x in self.lines) + "\n")


def subagent(session_dir, cwd, kind, t0):
    aid = "a" + rid().hex[:16]
    w = Writer(session_dir / "subagents" / f"agent-{aid}.jsonl", cwd, sidechain=True)
    (session_dir / "subagents").mkdir(parents=True, exist_ok=True)
    (session_dir / "subagents" / f"agent-{aid}.meta.json").write_text(json.dumps({"agentType": kind}))
    t = t0
    w.add(t, "user", "task")
    steps = 14 if kind == "executor" else 6
    for i in range(steps):
        t += timedelta(seconds=rnd.randint(5, 40))
        w.asst(t, {"type": "thinking", "thinking": ""})
        r = rnd.random()
        if i < 3 or r < .25:
            t = w.tool(t + timedelta(seconds=2), "Read", {"file_path": "x"}, 1)
        elif kind == "executor" and r < .55:
            t = w.tool(t + timedelta(seconds=rnd.randint(15, 60)), "Edit", {"file_path": "x"}, 1)
        elif r < .8:
            t = w.tool(t + timedelta(seconds=3), "Bash", {"command": "npm test -- --run"}, rnd.randint(20, 120))
        elif kind == "executor":
            t = w.tool(t + timedelta(seconds=3), "Bash", {"command": "until [ -f out/done ]; do sleep 10; done"},
                       rnd.randint(60, 240))
        else:
            t = w.tool(t + timedelta(seconds=3), "Bash", {"command": "git diff main...HEAD"}, 2)
    t += timedelta(seconds=rnd.randint(10, 30))
    w.asst(t, {"type": "text", "text": "report"})
    w.save()
    return aid, t


def dispatch(w, out, proj, sid, cwd, t, kind, task):
    """Main thread launches one agent; returns (agent id, agent end time, time of the launch result)."""
    tid = "toolu_" + rid().hex[:10]
    w.asst(t, {"type": "tool_use", "id": tid, "name": "Agent",
               "input": {"subagent_type": kind, "description": task}})
    aid, end = subagent(out / proj / sid, cwd, kind, t + timedelta(seconds=1))
    w.add(t + timedelta(seconds=1), "user",
          [{"type": "tool_result", "tool_use_id": tid, "content": f"Async agent launched. agentId: {aid}"}])
    w.asst(t + timedelta(seconds=4), {"type": "text", "text": "waiting"})
    return aid, end


def notify(w, aid, t):
    w.add(t, "queue-operation", operation="enqueue")
    w.add(t, "user", "report", isMeta=True, turnOrigin="peer", origin={"kind": "peer", "from": aid})


def make_session(out, proj, day):
    sid = str(rid())
    cwd = "/Users/demo/" + proj.split("-demo-")[1]
    w = Writer(out / proj / f"{sid}.jsonl", cwd)
    t = now - timedelta(days=day) + timedelta(hours=rnd.randint(1, 10))
    for turn in range(rnd.randint(2, 5)):
        w.add(t, "user", "prompt", turnOrigin="human", origin={"kind": "human"})
        t += timedelta(seconds=rnd.randint(5, 40))
        w.asst(t, {"type": "thinking", "thinking": ""})
        t = w.tool(t + timedelta(seconds=2), "Grep", {"pattern": "x"}, 1)
        t = w.tool(t + timedelta(seconds=rnd.randint(5, 20)), "Bash", {"command": "pytest -q"}, rnd.randint(10, 90))
        if rnd.random() < .7:
            t += timedelta(seconds=rnd.randint(5, 30))
            if rnd.random() < .5:
                # Two executors on different tasks, overlapping in time.
                a1, e1 = dispatch(w, out, proj, sid, cwd, t, "executor", "backend task")
                t2 = t + timedelta(seconds=rnd.randint(8, 40))
                a2, e2 = dispatch(w, out, proj, sid, cwd, t2, "executor", "frontend task")
                for aid, end in sorted([(a1, e1), (a2, e2)], key=lambda x: x[1]):
                    t = max(t2 + timedelta(seconds=6), end + timedelta(seconds=3))
                    notify(w, aid, t)
            else:
                for kind in ("executor", "reviewer"):
                    aid, end = dispatch(w, out, proj, sid, cwd, t, kind, kind + " task")
                    t = end + timedelta(seconds=3)
                    notify(w, aid, t)
                    t += timedelta(seconds=rnd.randint(5, 30))
        t += timedelta(seconds=rnd.randint(10, 40))
        w.asst(t, {"type": "text", "text": "done"})
        t += timedelta(seconds=rnd.choice([30, 90, 240, 600, 3600 * 3]))
    w.save()


def main():
    global now, rnd
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--now", default="2026-10-30T12:00:00Z", help="reference time (ISO, default %(default)s)")
    args = ap.parse_args()
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")).replace(microsecond=0)
    rnd = random.Random(7)
    for day in range(7, 0, -1):
        for proj in PROJECTS:
            if rnd.random() >= .35:
                make_session(args.out, proj, day)
    print("demo written to", args.out)


if __name__ == "__main__":
    main()

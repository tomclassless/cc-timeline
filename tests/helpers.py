"""Build synthetic transcripts. Times are seconds after a fixed base instant."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ts(sec):
    return (BASE + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Session:
    def __init__(self, cwd="/Users/x/proj", sidechain=False):
        self.rows = []
        self.cwd = cwd
        self.sidechain = sidechain

    def raw(self, t, type_, **kw):
        row = {"type": type_, "timestamp": ts(t), "cwd": self.cwd, **kw}
        if self.sidechain:
            row["isSidechain"] = True
        self.rows.append(row)
        return self

    def user(self, t, text="hello", **kw):
        return self.raw(t, "user", message={"role": "user", "content": text}, **kw)

    def assistant(self, t, block=None, mid="m1", **kw):
        block = block or {"type": "text", "text": "ok"}
        return self.raw(t, "assistant", message={"id": mid, "model": "claude-x", "role": "assistant",
                                                 "content": [block]}, **kw)

    def tool_use(self, t, tid, name, inp=None, mid="m1"):
        return self.assistant(t, {"type": "tool_use", "id": tid, "name": name, "input": inp or {}}, mid=mid)

    def thinking(self, t, mid="m1"):
        return self.assistant(t, {"type": "thinking", "thinking": "..."}, mid=mid)

    def tool_result(self, t, tid, content="ok", is_error=None, **kw):
        block = {"type": "tool_result", "tool_use_id": tid, "content": content}
        if is_error is not None:
            block["is_error"] = is_error
        return self.raw(t, "user", message={"role": "user", "content": [block]}, **kw)

    def write(self, root, project="-Users-x-proj", sid="11111111-aaaa"):
        d = Path(root) / project
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{sid}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in self.rows) + "\n", encoding="utf-8")
        return path

    def write_agent(self, main_path, aid, agent_type=None):
        d = main_path.parent / main_path.stem / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"agent-{aid}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in self.rows) + "\n", encoding="utf-8")
        if agent_type:
            path.with_suffix(".meta.json").write_text(json.dumps({"agentType": agent_type}))
        return path

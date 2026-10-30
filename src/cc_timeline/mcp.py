"""Minimal MCP server (stdio, JSON-RPC 2.0, one message per line). Standard library only.

stdout carries protocol messages only; diagnostics go to stderr. Tools return aggregate numbers,
names and ids, never conversation text or Bash commands.
"""
import json
import os
import sys
import traceback
from pathlib import Path

from . import __version__, queries
from .queries import ParamError, QueryError

SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

REDACT = {"type": "boolean", "description": "Replace project names, session ids and custom agent types "
                                            "with placeholders (use before sharing results)."}
PERIOD_PROPS = {
    "days": {"type": "number", "description": "Only the last N days (default 7). Ignored when since/until is given."},
    "since": {"type": "string", "description": "Sessions started on or after this local date, YYYY-MM-DD."},
    "until": {"type": "string", "description": "Sessions started on or before this local date, YYYY-MM-DD (inclusive)."},
    "project": {"type": "string", "description": "Only projects whose folder name contains this text."},
}
RANGE = {"type": "object", "required": ["since", "until"], "properties": {
    "since": {"type": "string", "description": "YYYY-MM-DD"}, "until": {"type": "string", "description": "YYYY-MM-DD (inclusive)"}}}


def schema(extra=None, required=None, period=True):
    props = dict(PERIOD_PROPS) if period else {}
    props.update(extra or {})
    props["redact"] = REDACT
    return {"type": "object", "properties": props, "required": required or []}


def period_args(a):
    return {k: a.get(k) for k in ("days", "since", "until", "project")}


def t_summary(srv, a):
    return queries.summary(srv.root, **period_args(a), by=a.get("by"), top=a.get("top", 10),
                           redact_names=bool(a.get("redact")))


def t_agent_phases(srv, a):
    return queries.agent_phases(srv.root, **period_args(a), agent_type=a.get("agent_type"),
                                redact_names=bool(a.get("redact")))


def t_session_timeline(srv, a):
    return queries.session_timeline(srv.root, a["session"], cwd=srv.cwd, redact_names=bool(a.get("redact")))


def t_compare(srv, a):
    return queries.compare(srv.root, a["before"], a["after"], a.get("project"), bool(a.get("redact")))


def t_quality(srv, a):
    return queries.quality_signals(srv.root, **period_args(a), redact_names=bool(a.get("redact")))


TOOLS = [
    {"name": "summary",
     "description": "Where Claude Code time went over a period: share of model generation, tool execution, "
                    "background waits (agents, notifications) and waiting for the user, plus the top tools and "
                    "background items, session count, API error count and transcript-format warnings. Use it "
                    "first to see the overall picture, or with `by` for one row per day/week/project/session. "
                    "Returns aggregate seconds and shares only, never conversation content.",
     "inputSchema": schema({"by": {"type": "string", "enum": ["day", "week", "project", "session"],
                                   "description": "Also return one row per day, ISO week, project or session."},
                            "top": {"type": "integer", "description": "Number of top items (default 10)."}}),
     "handler": t_summary},
    {"name": "agent_phases",
     "description": "How each subagent type spends its time: run count, average duration per run and seconds/"
                    "share per phase (thinking, writing code, other generation, test/build, polling, reading, "
                    "other). Use it to find which agent is slow and why (e.g. high thinking or polling share). "
                    "With redact=true, agent_type must use the redacted name.",
     "inputSchema": schema({"agent_type": {"type": "string", "description": "Only this agent type, e.g. reviewer."}}),
     "handler": t_agent_phases},
    {"name": "session_timeline",
     "description": "Breakdown of one session: time buckets, the 10 slowest steps (category, item, seconds) and "
                    "each subagent's type, start, end and phase seconds. Use it to drill into a session found "
                    "via summary. session is a session id prefix, or \"current\" for the most recently modified "
                    "session of the server's working directory (the project Claude Code is running in).",
     "inputSchema": schema({"session": {"type": "string", "description": "Session id prefix, or \"current\"."}},
                           required=["session"], period=False),
     "handler": t_session_timeline},
    {"name": "compare",
     "description": "Compare two date ranges (typically before and after a settings change): bucket shares, per-agent "
                    "average duration and phase shares, quality signals, and the differences (after minus before, in "
                    "percentage points and seconds). Includes notes when a sample is too small. Use it to judge "
                    "whether a change helped, then check the quality signals did not get worse.",
     "inputSchema": {"type": "object", "required": ["before", "after"], "properties": {
         "before": dict(RANGE, description="Earlier period."), "after": dict(RANGE, description="Later period."),
         "project": PERIOD_PROPS["project"], "redact": REDACT}},
     "handler": t_compare},
    {"name": "quality_signals",
     "description": "Guardrail metrics: per agent type reports divided by dispatches (high for reviewer means many "
                    "re-review rounds), test tool failure count and rate, API error count, executor dispatches per "
                    "session (null if none) and the share of agent time spent in sleep polling. Time savings that "
                    "come with worse signals are not real improvements.",
     "inputSchema": schema(),
     "handler": t_quality},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def check(value, spec, path):
    """Validate the subset of JSON Schema the tools use; raises ParamError."""
    t = spec.get("type")
    if t == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif t == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, {"string": str, "boolean": bool, "object": dict}[t])
    if not ok:
        raise ParamError(f"{path} must be of type {t}")
    if "enum" in spec and value not in spec["enum"]:
        raise ParamError(f"{path} must be one of {spec['enum']}")
    if t == "object":
        for key in spec.get("required", []):
            if key not in value:
                raise ParamError(f"{path}.{key} is required" if path != "arguments" else f"{key} is required")
        for key, sub in spec.get("properties", {}).items():
            if value.get(key) is not None:
                check(value[key], sub, f"{path}.{key}")


def error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


class McpServer:
    def __init__(self, root, cwd=None):
        self.root = Path(root)
        self.cwd = cwd if cwd is not None else os.getcwd()

    def handle(self, msg):
        """One decoded message -> response dict, or None (notifications, stray responses)."""
        if not isinstance(msg, dict):
            return error(None, -32600, "Invalid Request")
        if not isinstance(msg.get("method"), str):
            return None if ("result" in msg or "error" in msg) else error(msg.get("id"), -32600, "Invalid Request")
        id_, method = msg.get("id"), msg["method"]
        if "id" not in msg:
            return None  # notification (e.g. notifications/initialized): never answered
        try:
            return {"jsonrpc": "2.0", "id": id_, "result": self.dispatch(method, msg.get("params"))}
        except ParamError as e:
            return error(id_, -32602, str(e))
        except LookupError as e:  # unknown method
            return error(id_, -32601, str(e.args[0]))
        except Exception as e:  # never crash the server
            print(traceback.format_exc(), file=sys.stderr)
            return error(id_, -32603, f"Internal error ({type(e).__name__})")

    def dispatch(self, method, params):
        if params is not None and not isinstance(params, dict):
            raise ParamError("params must be an object")
        params = params or {}
        if method == "initialize":
            wanted = params.get("protocolVersion")
            if not isinstance(wanted, str):
                raise ParamError("protocolVersion is required")
            return {"protocolVersion": wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "cc-timeline", "version": __version__}}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]}
        if method == "tools/call":
            return self.call_tool(params)
        raise LookupError(f"Method not found: {method}")

    def call_tool(self, params):
        name = params.get("name")
        if not isinstance(name, str):
            raise ParamError("name is required")
        tool = BY_NAME.get(name)
        if not tool:
            raise ParamError(f"Unknown tool: {name}")
        args = params.get("arguments")
        if args is not None and not isinstance(args, dict):
            raise ParamError("arguments must be an object")
        args = {k: v for k, v in (args or {}).items() if v is not None}
        check(args, tool["inputSchema"], "arguments")
        try:
            data = tool["handler"](self, args)
        except ParamError:
            raise
        except QueryError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:
            print(traceback.format_exc(), file=sys.stderr)
            return {"content": [{"type": "text", "text": f"Tool failed ({type(e).__name__}); see the server's stderr."}],
                    "isError": True}
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                "structuredContent": data}


def serve(server, stdin, stdout):
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = error(None, -32700, "Parse error")
        else:
            if isinstance(msg, list):  # JSON-RPC batch (allowed by older protocol versions)
                replies = [r for r in (server.handle(m) for m in msg) if r is not None]
                reply = replies or None
            else:
                reply = server.handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def run_stdio(root):
    out = sys.stdout
    sys.stdout = sys.stderr  # stray prints must not corrupt the protocol stream
    for stream in (sys.stdin, out):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    print(f"cc-timeline MCP server {__version__} ready (root {root})", file=sys.stderr)
    serve(McpServer(root), sys.stdin, out)
    return 0

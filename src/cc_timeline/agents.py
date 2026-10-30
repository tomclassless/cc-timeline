"""Subagent transcripts: where each agent type spends its time."""
import json
from collections import defaultdict

from .bash import bash_phase
from .transcript import load_entries, match_agent, parse_ts

__all__ = ["match_agent", "phase_of", "analyze_agent", "agent_files", "phase_group", "PHASE_GROUPS"]

READ_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch", "WebSearch"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Report groups, in colour order.
PHASE_GROUPS = ["think", "code", "gen", "test", "poll", "read", "other"]


def phase_of(block):
    name = block.get("name", "")
    if name in READ_TOOLS:
        return "read"
    if name in EDIT_TOOLS:
        return "edit"
    if name != "Bash":
        return "other_tool"
    return bash_phase((block.get("input") or {}).get("command", ""))


def phase_group(phase):
    return phase if phase in PHASE_GROUPS else "other"


def analyze_agent(path, idle_cap):
    """Subagent transcript: model time split by the next block (think/code/gen), tools by phase.

    Returns (phases, start_datetime_or_None).
    """
    entries = load_entries(path, keep_sidechain=True)
    phases = defaultdict(float)
    pending = {}
    prev = None
    for e in entries:
        t = e.get("type")
        content = (e.get("message") or {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if t == "assistant":
            b = blocks[0] if blocks else {}
            if b.get("type") == "thinking":
                cat = "think"
            elif b.get("type") == "tool_use" and b.get("name") in EDIT_TOOLS:
                cat = "code"
            else:
                cat = "gen"
            for blk in blocks:
                if blk.get("type") == "tool_use":
                    pending[blk.get("id")] = phase_of(blk)
        elif t == "user" and any(b.get("type") == "tool_result" for b in blocks):
            cat = next((pending.get(b.get("tool_use_id")) for b in blocks
                        if b.get("type") == "tool_result"), None) or "other_tool"
        elif t == "user":
            # Dispatch prompt or a later follow-up: restart the clock, idle before it doesn't count.
            prev = parse_ts(e["timestamp"])
            continue
        else:
            continue
        ts = parse_ts(e["timestamp"])
        if prev is not None:
            gap = max((ts - prev).total_seconds(), 0)
            if gap <= idle_cap:
                phases[cat] += gap
        prev = ts
    return phases, (parse_ts(entries[0]["timestamp"]) if entries else None)


def agent_files(path, agents):
    """Subagent transcripts of a main session: [(label or None if unmatched, path)]."""
    found = []
    for f in sorted((path.parent / path.stem / "subagents").glob("agent-*.jsonl")):
        aid = f.stem[len("agent-"):]
        label = None
        meta = f.with_suffix(".meta.json")
        if meta.exists():
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                t = m.get("agentType") or m.get("subagent_type")
                label = t and f"Agent({t})"
            except (json.JSONDecodeError, OSError, AttributeError):
                pass
        found.append((label or match_agent(aid, agents) or None, f))
    return found

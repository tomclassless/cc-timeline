"""Heuristic classification of Bash commands (used only to bucket time, never output)."""
import re
from pathlib import Path

TEST_RE = re.compile(r"\b(test|tests|vitest|jest|pytest|playwright|mocha|tsc|typecheck|"
                     r"lint|eslint|prettier|build|check|biome)\b")
INSTALL_RE = re.compile(r"\b(npm|pnpm|yarn|bun|pip|uv)\s+(install|ci|i|add|sync)\b")
READ_VERBS = {"cat", "sed", "head", "tail", "grep", "rg", "find", "ls", "wc", "tree", "awk", "jq", "diff"}
SCRIPT_VERBS = {"node", "python", "python3", "perl", "bash", "sh", "time", "nice", "npx", "tsx", "deno"}


def bash_phase(cmd):
    """Return one of: poll, test, install, git, read, script, other_cmd."""
    segments = [re.sub(r"^(rtk\s+)+", "", s.strip()) for s in re.split(r"&&|\|\||;|\|", cmd)]
    segments = [s for s in segments if s and not s.startswith(("cd ", "export "))]
    if re.search(r"\bsleep\b", cmd):
        return "poll"
    if any(TEST_RE.search(s) for s in segments):
        return "test"
    if any(INSTALL_RE.search(s) for s in segments):
        return "install"
    # Judge by the first command; `grep`/`tail` after a pipe only filter output.
    first = re.sub(r"^(\w+=\S*\s+)+", "", segments[0]) if segments else ""
    verb = Path(first.split()[0]).name if first else ""
    if verb == "git":
        return "git"
    if verb in READ_VERBS:
        return "read"
    if verb in SCRIPT_VERBS or verb.startswith("python"):
        return "script"
    return "other_cmd"

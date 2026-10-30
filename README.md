# cc-timeline

[![CI](https://github.com/tomclassless/cc-timeline/actions/workflows/ci.yml/badge.svg)](https://github.com/tomclassless/cc-timeline/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/cc-timeline)](https://pypi.org/project/cc-timeline/)
[![Python](https://img.shields.io/pypi/pyversions/cc-timeline)](https://pypi.org/project/cc-timeline/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[繁體中文](README.zh-TW.md)

Where did the time go in your Claude Code sessions? `cc-timeline` reads the transcripts Claude Code already keeps on your machine and attributes every second of a session to **the model**, **tools**, **background waits** (subagents, other sessions, the app), **waiting for you**, or **away**. It can also look inside subagents and show how each agent type splits its time between thinking, writing code, reading, testing and polling.

**What it found in my own week.** In one week of my own work (40 sessions), the main Claude Code session spent **51%** of its active time waiting on subagents. Reviewers took about as long as the executors writing the code (~8h each), and inside the executors only 12% of the time was spent writing code: 31% thinking, 19% running tests, 13% in sleep-polling loops.

*The screenshots below use synthetic demo data.*

![Overview: daily allocation and where the time goes](docs/report-overview.png)

![Session timeline: main thread and parallel subagents](docs/report-timeline.png)

## Related tools

- [ccusage](https://github.com/ccusage/ccusage): token usage and cost reports.
- [claude-devtools](https://github.com/matt1398/claude-devtools): session reconstruction and context breakdown.
- [claude-session-dashboard](https://github.com/dlupiak/claude-session-dashboard): a dashboard with an agent dispatch Gantt chart.
- [claude-code-log](https://github.com/daaain/claude-code-log) and [claude-code-transcripts](https://github.com/simonw/claude-code-transcripts): convert transcripts to HTML.
- [sniffly](https://github.com/chiphuyen/sniffly): usage statistics and error analysis.
- [claude-session-analyzer](https://github.com/yonk-labs/claude-session-analyzer): per-turn timing and skill analysis.
- [Official OpenTelemetry monitoring](https://code.claude.com/docs/en/monitoring-usage): metrics and events exported live, enabled beforehand.

`cc-timeline` focuses on one question: it works retroactively on transcripts already on disk, separates "the model is working" from "nothing is happening, we are waiting for X", and works out which agent type a background wait belongs to.

## Install

Python 3.9+, standard library only.

```bash
# run without installing
uvx cc-timeline

# or install as a tool
uv tool install cc-timeline
pip install cc-timeline

# latest unreleased version from the git repository
uvx --from git+https://github.com/tomclassless/cc-timeline cc-timeline
```

`python -m cc_timeline` works too.

### Use inside Claude Code (plugin)

This repository is also a Claude Code plugin marketplace:

```
/plugin marketplace add tomclassless/cc-timeline
/plugin install cc-timeline@cc-timeline
/cc-timeline:timeline --days 14
```

The `timeline` skill runs `cc-timeline --html --open` with your arguments and summarizes the result. It runs the published package with `uvx cc-timeline`.

## Usage

```bash
cc-timeline                                  # last 7 days, one line per session
cc-timeline --days 30 --project myapp        # filter by project folder name
cc-timeline --since 2026-03-01 --until 2026-03-31   # by start date (local time, inclusive)
cc-timeline --by week                        # one summary row per day / week / project / session
cc-timeline --csv sessions.csv               # sessions as CSV (with --by: the summary rows)
cc-timeline --session 3f2a                   # one session: breakdown + slowest steps
cc-timeline --session 3f2a --trace 3         # event types around the 3 slowest steps (no content)
cc-timeline --agents                         # subagent internals by agent type
cc-timeline --html                           # ./cc-timeline-report.html
cc-timeline --html out.html --open          # write and open in a browser
cc-timeline --json | jq .totals              # machine-readable report data
cc-timeline --html --timeline-sessions 5     # gantt data for the 5 busiest sessions (default 20, 0 = off)
cc-timeline --html --redact                  # safe to screenshot: anonymized names and ids
cc-timeline --lang zh-TW                     # or CC_TIMELINE_LANG=zh-TW
```

Transcripts are read from `$CLAUDE_CONFIG_DIR/projects`, or `~/.claude/projects` if unset (override with `--root`). Other options: `--idle-cap MINUTES` (default 30), `--top N` (default 10).

Date filters: `--since` / `--until` (`YYYY-MM-DD`, local time zone, `--until` includes that day) select sessions by their **first timestamp**; file modification time is only used to skip old files quickly. `--days N` is the same as `--since` N days ago and is ignored when `--since` is given. `--session` ignores date filters.

`--by day|week|project|session` prints one row per group: number of sessions, active time (model + tool + background), time and share for model / tool / background / waiting for you, and away. Weeks are ISO weeks (`2026-W44`). `--csv PATH` writes the per-session table (columns `start, project, session, wall, model, tool, bg, user, idle, errors, top`; seconds are numbers), or the group rows when combined with `--by`.

## Session timeline (HTML)

The report has a **Session timeline** card: a gantt chart for one session at a time (pick it from the drop-down; it starts on the session with the highest share of background waiting). One row is the main thread, followed by one row per subagent, so you can see what the main thread waited on and whether agents ran in parallel.

- Colors follow the time categories: model = blue, tool = orange, test/build = pink, polling = purple, waiting for you = yellow, waiting for background = hatched gray. Main-thread and agent categories share colors where they mean the same thing (an agent's thinking / writing code / other generation are "model"; reading / scripts / git / other tools are "tool").
- Away stretches (longer than `--idle-cap`) during which no agent was working are collapsed to a fixed-width break labelled like `⋯ 3h12m`.
- With more than 15 subagents, agents of the same type that did not overlap share a row.
- Only the `--timeline-sessions N` sessions with the most active time are embedded (default 20, `0` = no timeline card) to keep the report small. A "Table" section lists each agent's type, start, end and duration.

## How time is attributed

Time between two events goes to the **next meaningful event**.

| Bucket | What the gap was |
| --- | --- |
| model | Waiting for the model to generate a reply |
| tool | A tool call running (until its result arrives) |
| background | The main thread was idle: waiting for a subagent, another session/agent, the app (`sdk`), or a wake-up. Named after the agent type where it can be matched |
| user | Waiting for a human turn (`turnOrigin: human`), or for the answer to `AskUserQuestion`, `ExitPlanMode`, `request_directory` |
| away | A single gap longer than `--idle-cap` (sleep, lunch, resumed next day); excluded from the others |

Details:

- Auxiliary events (`queue-operation`, `api-request`, `attachment`, `system`, `file-history-snapshot`, `progress`, ...) do not cut time; the gap goes to the next real event.
- A new assistant message (different message id) with no user message or tool result before it means the session was woken up. The gap is split at the last auxiliary event: before it counts as background, after it as model.
- Background waits are mapped back to the Agent/Task call that started them (via `origin.from` or `<task-id>`). If no match is found they are labeled "unmatched".
- Subagent transcripts (`<session>/subagents/agent-*.jsonl`) are classified by the next block: before a `thinking` block is thinking, before an Edit/Write call is writing code, other assistant output is "other generation"; tool time is split into reading, testing/build, polling, scripts, git, install, other. A new non-tool-result user message restarts the clock.
- Bash commands are bucketed heuristically: `sleep` is polling; test/build/lint words are testing; `npm install` and friends are install; otherwise the first command decides (git, read-like commands, scripts, other).

## Format-change warning

Claude Code's transcript format is undocumented (`turnOrigin`, `queue-operation`, `api-request`, ... are observed, not specified). While scanning, `cc-timeline` counts entry `type`s, `turnOrigin` / `origin.kind` values and the Claude Code `version` of each entry. If it sees a type or origin it does not know, it prints one warning line to stderr (not when `--json` is the only output; `--html` still prints it) and shows it in the HTML footer: the Claude Code format has probably changed, so numbers may be wrong. Please open an issue with the names shown. `--json` has a `format` block (`types`, `origins`, `versions`, `unknown`), and the HTML footer shows the range of Claude Code versions seen. Unknown origins are still counted as background, labelled "Other source (x)".

## Limitations

- The transcript format is not a public API and may change; some attribution relies on fields observed in practice.
- Parallel subagents overlap, so per-agent times add up to more than the wall-clock time the main thread waited.
- Permission prompts are waiting for you, but are recorded as tool time.
- Bash classification is a heuristic based on the command text.
- Column alignment in the terminal output assumes narrow characters (it is off for CJK text).

## MCP server

`cc-timeline mcp` runs an MCP server (stdio, standard library only) so Claude can query its own time allocation. Add it to Claude Code:

```bash
claude mcp add -s user cc-timeline -- uvx cc-timeline mcp
```

(Once the package is on PyPI this becomes `claude mcp add cc-timeline -- uvx cc-timeline mcp`.) Installing the plugin registers the same server automatically. For Claude Desktop, add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cc-timeline": {
      "command": "uvx",
      "args": ["cc-timeline", "mcp"]
    }
  }
}
```

Use `cc-timeline mcp --root PATH` to read transcripts from somewhere other than `~/.claude/projects`. Tools (all take `redact: true` to hide project names, session ids and custom agent types):

- `summary`: time share of model / tools / background / waiting for you, top items, session and API error counts; optional `by` day, week, project or session.
- `agent_phases`: per agent type (named like the CLI, e.g. `Agent(reviewer)`; the `agent_type` filter accepts `reviewer` or `Agent(reviewer)`), run count, average duration and the share of thinking, coding, testing, polling, reading.
- `session_timeline`: one session (id prefix, or `"current"` for the latest session of the server's working directory): buckets, the 10 slowest steps and each subagent's phases.
- `compare`: two date ranges side by side with differences (after minus before) and notes when a sample is too small.
- `quality_signals`: reports per dispatch for each agent type, test failure rate, API errors, executor dispatches per session (an agent type counts as an executor when its name contains `executor`), sleep polling share.

Without MCP, the same data is available from the command line: `cc-timeline compare --before 2026-10-01:2026-10-14 --after 2026-10-15:2026-10-28 [--project X] [--json]` and `cc-timeline quality [--days N | --since D --until D] [--json]`.

**Privacy:** the tools return only aggregate numbers, names and ids. They never return conversation text, assistant output or Bash command text; the test suite checks this with a canary string.

## Self-tuning (`/cc-timeline:tune`)

The `tune` skill asks Claude to compare the last N days (default 7) with the N days before, look at the three biggest time sinks and match them against known patterns (agents thinking too long, running the full test suite every time, sleep polling, reviewers sending work back, the main thread idling while agents work). For each it proposes a concrete change (file, lines), the expected effect, and when to re-check.

```
/cc-timeline:tune --days 7
/cc-timeline:tune --days 14 --project myapp
```

It **only suggests; it never edits your settings**, agent definitions or CLAUDE.md. Every suggestion comes with quality guardrails because time can drop for bad reasons: if reports per dispatch, the test failure rate or API errors go up after a change, the change did not make things better, it only moved the cost. Run `/cc-timeline:tune` again after enough sessions (at least 5 per period) to see whether a change held up.

## Privacy

Everything runs locally; nothing is sent anywhere. Only timestamps, event types, tool names and ids are used. Conversation content is never printed or written to reports. Bash command text is used only to classify the command and is not output. Trace output (`--trace`) shows event types only: for user messages that start with `<` just the tag name (for example `<task-notification`), and the free-text `reason` field is masked unless it is a plain identifier.

Use `--redact` before sharing a screenshot, report, JSON or CSV. It replaces:

- project names with `project-1`, `project-2`, ...
- session ids with a short hash
- custom agent types (anything except `general-purpose`, `Explore`, `Plan`, `statusline-setup`, `claude-code-guide`) with `agent-1`, `agent-2`, ...
- MCP server names with `mcp-1`, `mcp-2`, ... (the tool name after the server is kept)

It applies to the table, `--by`, `--agents`, `--trace`, HTML, JSON and CSV. It does **not** hide built-in agent types, ordinary tool names (`Read`, `Bash`, ...), MCP tool names, timestamps and durations, API error counts, Claude Code versions, or the folder given with `--root` if you print it yourself.

## Development

```bash
uv venv && uv pip install -e . pytest && .venv/bin/python -m pytest -q
```

The screenshots in `docs/` come from synthetic data: `python scripts/make_demo.py /tmp/demo --now 2026-10-30T12:00:00Z`, then `cc-timeline --root /tmp/demo --since 2026-10-22 --html report.html` (timeline: `--session 05bde598`), captured with headless Chrome at 1100px width, 2x scale.

## License

MIT

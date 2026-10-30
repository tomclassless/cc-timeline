# cc-timeline

[繁體中文](README.zh-TW.md)

Where did the time go in your Claude Code sessions? `cc-timeline` reads the transcripts Claude Code already keeps on your machine and attributes every second of a session to **the model**, **tools**, **background waits** (subagents, other sessions, the app), **waiting for you**, or **away**. It can also look inside subagents and show how each agent type splits its time between thinking, writing code, reading, testing and polling.

<!-- screenshot -->

## How is this different?

- **ccusage** counts tokens and cost, not time.
- **claude-devtools** and **claude-session-dashboard** visualize subagents, but do not attribute time to causes.
- **Official OpenTelemetry** exports durations, but you must enable it beforehand, it cannot be applied retroactively, and it does not split out waiting.
- **yonk-labs/claude-session-analyzer** has per-turn breakdowns, but no subagent view and no `turnOrigin` handling.

`cc-timeline` works retroactively on existing transcripts, separates "the model is working" from "nothing is happening, we are waiting for X", and works out which agent type a background wait belongs to.

## Install

Python 3.9+, standard library only.

```bash
# run without installing
uvx --from git+https://github.com/tomclassless/cc-timeline cc-timeline

# or install as a tool
uv tool install git+https://github.com/tomclassless/cc-timeline

# or with pip
pip install git+https://github.com/tomclassless/cc-timeline
```

`python -m cc_timeline` works too.

## Usage

```bash
cc-timeline                                  # last 7 days, one line per session
cc-timeline --days 30 --project myapp        # filter by project folder name
cc-timeline --session 3f2a                   # one session: breakdown + slowest steps
cc-timeline --session 3f2a --trace 3         # event types around the 3 slowest steps (no content)
cc-timeline --agents                         # subagent internals by agent type
cc-timeline --html                           # ./cc-timeline-report.html
cc-timeline --html out.html --open          # write and open in a browser
cc-timeline --json | jq .totals              # machine-readable report data
cc-timeline --html --redact                  # safe to screenshot: anonymized names and ids
cc-timeline --lang zh-TW                     # or CC_TIMELINE_LANG=zh-TW
```

Transcripts are read from `$CLAUDE_CONFIG_DIR/projects`, or `~/.claude/projects` if unset (override with `--root`). Other options: `--idle-cap MINUTES` (default 30), `--top N` (default 10).

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

## Limitations

- The transcript format is not a public API and may change; some attribution relies on fields observed in practice.
- Parallel subagents overlap, so per-agent times add up to more than the wall-clock time the main thread waited.
- Permission prompts are waiting for you, but are recorded as tool time.
- Bash classification is a heuristic based on the command text.
- Column alignment in the terminal output assumes narrow characters (it is off for CJK text).

## Privacy

Everything runs locally; nothing is sent anywhere. Only timestamps, event types, tool names and ids are used. Conversation content is never printed or written to reports. Bash command text is used only to classify the command and is not output. Use `--redact` to replace project names and session ids before sharing a screenshot, report or JSON.

## License

MIT

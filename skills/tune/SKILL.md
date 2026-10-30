---
name: tune
description: Review where recent Claude Code time went, compare it with the previous period, and propose (never apply) settings changes to agents, tests and polling.
argument-hint: "[--days N] [--project NAME]"
disable-model-invocation: true
allowed-tools: Bash(uvx --from git+https://github.com/tomclassless/cc-timeline cc-timeline *)
---

You are tuning the user's Claude Code setup from measured time, not from guesses. Arguments: `$ARGUMENTS` (`--days N`, default 7; `--project NAME`, optional).

## Hard rules

- **Never edit any file or setting.** Do not touch `~/.claude/agents/*`, `settings.json`, `CLAUDE.md`, hooks or anything else. Output suggestions only; the user decides.
- **Do not read transcript files.** Use only the aggregate results from cc-timeline (seconds, shares, counts, names, ids).
- If a sample is too small (the `notes` field says so, or a period has fewer than 5 sessions), say so plainly and label every conclusion as tentative.

## 1. Collect data

Let N be the requested days (default 7). Define the recent period as the last N days and the previous period as the N days before that (use concrete `YYYY-MM-DD` dates, today included in the recent one).

Prefer the cc-timeline MCP tools if they are available:

1. `compare` with `before` = previous period, `after` = recent period (and `project` if given).
2. `quality_signals` for the recent period.
3. `agent_phases` for the recent period.

If the MCP tools are not available, run these with Bash (same data as JSON):

```
uvx --from git+https://github.com/tomclassless/cc-timeline cc-timeline compare --before SINCE:UNTIL --after SINCE:UNTIL [--project NAME] --json
uvx --from git+https://github.com/tomclassless/cc-timeline cc-timeline quality --since SINCE --until UNTIL [--project NAME] --json
```

(`cc-timeline --agents` prints the agent phase table as text if you need it.)

## 2. Find the three biggest time sinks

From the recent period, rank by seconds: the top items of the time buckets (model, tools, background, waiting for you) and the agent phases. Take the top three and, for each, check it against these known patterns:

- **Agent thinking share is high** (thinking is the largest phase of an agent type): check that agent definition's `effort` and `model` in its frontmatter; a lower effort or a lighter model for mechanical work often helps.
- **Test/build share is high**: does the agent run the full test suite every time? Suggest running only the tests related to the change, with the full suite once at the end.
- **Sleep polling is high** (`sleep_poll_share`, or the polling phase): long tasks should run in the foreground with a timeout, or poll less often (longer interval).
- **Waiting for a reviewer is high and reports / dispatches > 1.5**: the reviewer rejects often. Add the most common rejection reasons to the executor's self-check list.
- **Main thread waits on background a lot but agents' internal time is short**: after dispatching, should the main thread work on something else in parallel instead of waiting?

If none of the patterns fits, say that, and describe what the data shows without inventing a cause.

## 3. Write each recommendation

For every recommendation give all of:

1. **Evidence**: the numbers (seconds, shares, counts) and the period they come from.
2. **Concrete change**: which file and which lines, as a small diff. Example: `~/.claude/agents/executor.md` frontmatter, `effort: high` to `effort: medium`.
3. **Expected effect**: which number should drop, roughly by how much, and why.
4. **Quality guardrails to watch**: time going down is not an improvement if reports per dispatch, the test failure rate or the API error count goes up. Name the guardrail metrics that matter for this change and their current values.
5. **When to re-check**: how long to run with the change (enough days for at least 5 sessions) before running `/cc-timeline:tune` again to compare.

## 4. Close

End with a short summary: the top three time sinks, which recommendations you would try first, and a reminder that nothing was changed. If the previous-period comparison shows an earlier change helped or hurt (time and guardrails together), say which.

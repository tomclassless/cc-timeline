# Changelog

## 0.1.0

First public release, packaged from a personal prototype.

- Per-session time attribution: model / tool / background / waiting for you / away.
- Subagent internals (`--agents`): thinking, writing code, reading, testing, polling.
- Terminal tables, single-session detail (`--session`, `--trace`), JSON (`--json`).
- HTML report (`--html`, `--open`) with daily allocation, top items, agent internals, trends, sortable sessions table.
- Session timeline (gantt) card in the HTML report: main-thread and per-subagent rows colored by time category, away stretches collapsed, `--timeline-sessions N` (default 20, 0 = off).
- Date range: `--since` / `--until` (by the session's first timestamp, local time); `--days` kept.
- Grouping and export: `--by day|week|project|session`, `--csv PATH`.
- Privacy: `--redact` hides project names, session ids, custom agent types and MCP server names everywhere (table, `--by`, `--agents`, `--trace`, HTML, JSON, CSV); trace output shows only tag names for `<...>` user messages and masks free-text `reason`.
- Format-change detection: counts entry types, turn origins and Claude Code versions; warns (CLI stderr, except with `--json` alone; HTML footer) on unknown ones; `--json` has a `format` block.
- Claude Code plugin and marketplace (`/plugin marketplace add tomclassless/cc-timeline`, `/cc-timeline:timeline`).
- HTML report footer links back to the project.
- Release workflow: publishing a GitHub release builds and uploads to PyPI via Trusted Publishing; package metadata (classifiers, URLs) for PyPI.
- MCP server (`cc-timeline mcp`, stdio, standard library only) with five aggregate-only tools: `summary`, `agent_phases`, `session_timeline`, `compare`, `quality_signals`; all accept `redact`. Registered by the plugin (`mcpServers` in `plugin.json`).
- `cc-timeline compare --before SINCE:UNTIL --after SINCE:UNTIL` and `cc-timeline quality`: the same data as text tables or `--json`, for users without MCP.
- `/cc-timeline:tune` skill: compares recent and previous periods and proposes (never applies) settings changes, with quality guardrails.
- English and Traditional Chinese (`--lang zh-TW` or `CC_TIMELINE_LANG`).

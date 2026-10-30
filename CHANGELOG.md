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
- Format-change detection: counts entry types, turn origins and Claude Code versions; warns (CLI stderr, HTML footer) on unknown ones; `--json` has a `format` block.
- English and Traditional Chinese (`--lang zh-TW` or `CC_TIMELINE_LANG`).

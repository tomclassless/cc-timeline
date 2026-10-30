---
name: timeline
description: Generate a cc-timeline HTML report showing where time went in recent Claude Code sessions (model, tools, background waits, waiting for you).
argument-hint: "[--days N] [--project NAME] [--since YYYY-MM-DD]"
disable-model-invocation: true
allowed-tools: Bash(uvx cc-timeline *)
---

Run these two commands with the Bash tool, passing the user's arguments through unchanged:

1. Write and open the HTML report:

```
uvx cc-timeline --html --open $ARGUMENTS
```

2. Print the text summary (the HTML command only prints the output path):

```
uvx cc-timeline $ARGUMENTS
```

Then report to the user:

- the path of the HTML report that was written, and that it was opened in the browser;
- the key points of the text summary (share of time in model / tools / background / waiting for you, and the biggest items).

Do not read transcript files yourself; cc-timeline reads them locally and the report contains only timings, names and ids.

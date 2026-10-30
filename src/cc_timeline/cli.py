"""Command line entry point."""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .i18n import labels, resolve_lang
from .report import (build_report, print_agents, print_detail, print_summary, print_trace,
                     redact, to_json, write_html)
from .transcript import analyze, default_root, find_sessions

DEFAULT_REPORT = Path("cc-timeline-report.html")


def build_parser():
    ap = argparse.ArgumentParser(
        prog="cc-timeline",
        description="Where did the time go in your Claude Code sessions? "
                    "Reads local transcripts; only timestamps, event types and tool names are used.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--days", type=float, default=7, help="only the last N days (default 7)")
    ap.add_argument("--project", help="project folder name contains this text")
    ap.add_argument("--session", help="session id prefix; prints a detailed breakdown")
    ap.add_argument("--idle-cap", type=float, default=30,
                    help="a single gap longer than this many minutes counts as away (default 30)")
    ap.add_argument("--top", type=int, default=10, help="rows in the tool ranking (default 10)")
    ap.add_argument("--root", type=Path, default=None,
                    help="transcripts root (default: $CLAUDE_CONFIG_DIR/projects or ~/.claude/projects)")
    ap.add_argument("--trace", type=int, default=0,
                    help="with --session: list event types around the N slowest steps (no content)")
    ap.add_argument("--agents", action="store_true",
                    help="show subagent internals: thinking, reading, editing, testing per agent type")
    ap.add_argument("--html", nargs="?", const=DEFAULT_REPORT, type=Path, metavar="PATH",
                    help=f"write a visual HTML report (default {DEFAULT_REPORT})")
    ap.add_argument("--open", action="store_true", help="with --html: open the report in a browser")
    ap.add_argument("--json", action="store_true", help="print report data as JSON to stdout")
    ap.add_argument("--redact", action="store_true",
                    help="replace project names and session ids (safe to share screenshots)")
    ap.add_argument("--lang", choices=["en", "zh-TW"], default=None,
                    help="output language (default: $CC_TIMELINE_LANG or en)")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    lang = resolve_lang(args.lang)
    L = labels(lang)
    root = (args.root or default_root()).expanduser()
    idle_cap = args.idle_cap * 60

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    files = find_sessions(root, args.project, args.session, since)
    results = [r for r in (analyze(p, idle_cap) for p in files) if r]
    if not results:
        sys.exit(L["no_sessions"])
    results.sort(key=lambda r: r["start"])
    if args.redact:
        redact(results)

    if args.html or args.json:
        data = build_report(results, args.days, args.project, lang, idle_cap, args.redact)
        if args.html:
            out = write_html(data, args.html)
            print(L["report_written"].format(path=out), file=sys.stderr if args.json else sys.stdout)
            if args.open:
                import webbrowser
                webbrowser.open(out.resolve().as_uri())
        if args.json:
            data.pop("labels")
            print(to_json(data))
        return 0

    if args.agents:
        print_agents(results, lang, idle_cap)
    elif args.session:
        for r in results:
            print_detail(r, lang, args.top)
            print_trace(r, lang, args.trace)
    else:
        print_summary(results, lang, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())

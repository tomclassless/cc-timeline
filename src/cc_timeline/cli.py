"""Command line entry point."""
import argparse
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import __version__
from .i18n import labels, resolve_lang
from .report import (build_report, format_summary, format_warning, export_csv, print_agents, print_detail, print_groups, print_summary,
                     print_trace, redact, to_json, write_html)
from .transcript import analyze, default_root, find_sessions

DEFAULT_REPORT = Path("cc-timeline-report.html")
DEFAULT_DAYS = 7


def parse_day(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}")


def local_midnight(d):
    return datetime.combine(d, time.min).astimezone()


def date_range(args, now=None):
    """(lo, hi) as aware datetimes (either may be None); hi is exclusive. Local time zone."""
    now = now or datetime.now().astimezone()
    if args.since:
        lo = local_midnight(args.since)
    elif args.days is not None or not args.until:
        lo = now - timedelta(days=DEFAULT_DAYS if args.days is None else args.days)
    else:
        lo = None
    hi = local_midnight(args.until + timedelta(days=1)) if args.until else None
    return lo, hi


def warn_format(L, results):
    """Last line of CLI output (stderr): unknown transcript entry types / origins, if any."""
    warning = format_warning(L, format_summary(results))
    if warning:
        print(warning, file=sys.stderr)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="cc-timeline",
        description="Where did the time go in your Claude Code sessions? "
                    "Reads local transcripts; only timestamps, event types and tool names are used.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--days", type=float, default=None,
                    help=f"only the last N days (default {DEFAULT_DAYS}); ignored when --since is given")
    ap.add_argument("--since", type=parse_day, metavar="YYYY-MM-DD",
                    help="only sessions that started on or after this local date")
    ap.add_argument("--until", type=parse_day, metavar="YYYY-MM-DD",
                    help="only sessions that started on or before this local date (inclusive)")
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
    ap.add_argument("--by", choices=["day", "week", "project", "session"],
                    help="print one summary row per day / ISO week / project / session")
    ap.add_argument("--csv", type=Path, metavar="PATH",
                    help="write sessions (or the --by summary rows) as CSV; seconds are numbers")
    ap.add_argument("--redact", action="store_true",
                    help="replace project names, session ids, custom agent types and MCP server "
                         "names (safe to share screenshots)")
    ap.add_argument("--lang", choices=["en", "zh-TW"], default=None,
                    help="output language (default: $CC_TIMELINE_LANG or en)")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    lang = resolve_lang(args.lang)
    L = labels(lang)
    root = (args.root or default_root()).expanduser()
    idle_cap = args.idle_cap * 60

    lo, hi = date_range(args)
    days = DEFAULT_DAYS if args.days is None else args.days
    if args.since or args.until:
        days = None
    files = find_sessions(root, args.project, args.session, lo and lo.astimezone(timezone.utc))
    results = [r for r in (analyze(p, idle_cap) for p in files) if r]
    if not args.session:
        # Exact filter on the session's first timestamp (mtime above was only a pre-screen).
        results = [r for r in results if (lo is None or r["start"] >= lo) and (hi is None or r["start"] < hi)]
    if not results:
        sys.exit(L["no_sessions"])
    results.sort(key=lambda r: r["start"])
    if args.redact:
        redact(results)

    if args.csv:
        out = export_csv(args.csv, results, L, args.by)
        print(L["csv_written"].format(path=out), file=sys.stderr if args.json else sys.stdout)

    if args.html or args.json:
        data = build_report(results, days, args.project, lang, idle_cap, args.redact,
                            args.since, args.until)
        if args.html:
            out = write_html(data, args.html)
            print(L["report_written"].format(path=out), file=sys.stderr if args.json else sys.stdout)
            warn_format(L, results)
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
    elif args.by:
        print_groups(results, args.by, lang)
    else:
        print_summary(results, lang, args.top)
    warn_format(L, results)
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
                    "Reads local transcripts; only timestamps, event types and tool names are used.",
        epilog="subcommands (as the first argument): mcp, compare, quality; "
               "see `cc-timeline <subcommand> --help`")
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
    ap.add_argument("--timeline-sessions", type=int, default=20, metavar="N",
                    help="embed the session timeline (gantt) for the N sessions with the most active "
                         "time in the HTML report (default 20, 0 = none)")
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


SUBCOMMANDS = ("mcp", "compare", "quality")


def parse_range(text):
    since, sep, until = text.partition(":")
    if not sep:
        raise argparse.ArgumentTypeError(f"expected SINCE:UNTIL (YYYY-MM-DD:YYYY-MM-DD), got {text!r}")
    return {"since": str(parse_day(since)), "until": str(parse_day(until))}


def sub_parser(name):
    ap = argparse.ArgumentParser(prog=f"cc-timeline {name}")
    ap.add_argument("--root", type=Path, default=None,
                    help="transcripts root (default: $CLAUDE_CONFIG_DIR/projects or ~/.claude/projects)")
    if name == "mcp":
        ap.description = "Run the MCP server (stdio). Tools return only aggregate numbers, names and ids."
        return ap
    ap.add_argument("--project", help="project folder name contains this text")
    ap.add_argument("--redact", action="store_true", help="replace project names and custom agent types")
    ap.add_argument("--json", action="store_true", help="print the data as JSON")
    ap.add_argument("--lang", choices=["en", "zh-TW"], default=None,
                    help="output language (default: $CC_TIMELINE_LANG or en)")
    if name == "compare":
        ap.description = "Compare two periods: time allocation, agent internals and quality signals."
        ap.add_argument("--before", type=parse_range, required=True, metavar="SINCE:UNTIL")
        ap.add_argument("--after", type=parse_range, required=True, metavar="SINCE:UNTIL")
    else:
        ap.description = "Quality signals: reports per dispatch, test failures, API errors, polling share."
        ap.add_argument("--days", type=float, default=None, help=f"only the last N days (default {DEFAULT_DAYS})")
        ap.add_argument("--since", type=parse_day, metavar="YYYY-MM-DD")
        ap.add_argument("--until", type=parse_day, metavar="YYYY-MM-DD")
    return ap


def run_subcommand(name, argv):
    args = sub_parser(name).parse_args(argv)
    root = (args.root or default_root()).expanduser()
    if name == "mcp":
        from .mcp import run_stdio
        return run_stdio(root)
    from . import queries, render
    lang = resolve_lang(args.lang)
    L = labels(lang)
    try:
        if name == "compare":
            data = queries.compare(root, args.before, args.after, args.project, args.redact)
            empty = not (data["before"]["sessions"] or data["after"]["sessions"])
        else:
            data = queries.quality_signals(root, args.days, args.since and str(args.since),
                                           args.until and str(args.until), args.project, args.redact)
            empty = not data["sessions"]
    except queries.ParamError as e:
        sys.exit(f"cc-timeline {name}: error: {e}")
    if empty:
        sys.exit(L["no_sessions"])
    if args.json:
        print(to_json(data))
    elif name == "compare":
        render.print_compare(data, lang)
    else:
        render.print_quality(data, lang)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in SUBCOMMANDS:
        return run_subcommand(argv[0], argv[1:])
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
        out = export_csv(args.csv, results, args.by)
        print(L["csv_written"].format(path=out), file=sys.stderr if args.json else sys.stdout)

    if args.html or args.json:
        data = build_report(results, days, args.project, lang, idle_cap, args.redact,
                            args.since, args.until, args.timeline_sessions,
                            session=args.session)
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

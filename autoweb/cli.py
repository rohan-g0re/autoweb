"""AutoWeb command line.

Deliberately thin. Every command does one thing you could have done by hand, and
prints what it did. If a command ever needs a paragraph of explanation, it is doing
too much.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, lanes, merge as merge_mod, state, trace
from .config import Config, ConfigError, Learned
from .lanes import LaneError
from .merge import MergeError
from .state import StateError
from .trace import TraceError


def _cmd_config_show(args: argparse.Namespace) -> int:
    """Print effective config, including defaults, and where each file lives."""
    cfg = Config.load(args.dir)
    found = (cfg.root_dir / "autoweb.toml").is_file()

    print(f"config file   : {cfg.root_dir / 'autoweb.toml'}"
          f"{'' if found else '   (not found - using defaults)'}")
    print(f"root state    : {cfg.root_state_path}"
          f"{'' if cfg.root_state_path.is_file() else '   (does not exist yet)'}")
    print(f"learned facts : {cfg.learned_path}"
          f"{'' if cfg.learned_path.is_file() else '   (none yet)'}")
    print()
    print("[lanes]")
    print(f"  max                      = {cfg.lanes.max}")
    print(f"  browser                  = {cfg.lanes.browser}")
    print(f"  mcp_version              = {cfg.lanes.mcp_version}")
    print(f"  isolated                 = {str(cfg.lanes.isolated).lower()}")
    print("[state]")
    print(f"  root                     = {cfg.state.root}")
    print(f"  indexeddb                = {str(cfg.state.indexeddb).lower()}")
    print("[caps]")
    print(f"  total_bytes              = {cfg.caps.total_bytes}")
    print(f"  max_origins              = {cfg.caps.max_origins}")
    print(f"  max_indexeddb_per_origin = {cfg.caps.max_indexeddb_per_origin}")

    if cfg.origins:
        print("[origins]")
        for origin, rule in sorted(cfg.origins.items()):
            flags = []
            if rule.indexeddb is not None:
                flags.append(f"indexeddb={str(rule.indexeddb).lower()}")
            if rule.rotates:
                flags.append("rotates")
            if rule.sticky:
                flags.append("sticky")
            print(f"  {origin:<30} {' '.join(flags) or '(no overrides)'}")

    learned = Learned.load(cfg.learned_path)
    if learned.rotates or learned.sticky:
        print()
        print("learned by the loop (machine-written, do not hand-edit):")
        for origin in sorted(learned.rotates):
            print(f"  rotates  {origin}")
        for origin in sorted(learned.sticky):
            print(f"  sticky   {origin}")
    return 0


def _cmd_config_check(args: argparse.Namespace) -> int:
    """Validate every file AutoWeb reads, and exit non-zero if any is malformed.

    Checks the learned facts too. A command called ``check`` that passes while
    ``show`` fails on the same directory is worse than no check at all.
    """
    cfg = Config.load(args.dir)
    path = cfg.root_dir / "autoweb.toml"

    if path.is_file():
        print(f"{path}: ok")
    else:
        print(f"no {path.name} found at or above {args.dir or Path.cwd()} "
              f"- defaults are valid, nothing to check")

    Learned.load(cfg.learned_path)          # raises ConfigError if malformed
    if cfg.learned_path.is_file():
        print(f"{cfg.learned_path}: ok")
    return 0


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n/1:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}MB"


def _cmd_state_export(args: argparse.Namespace) -> int:
    """Capture a session, either from a human logging in or from a profile that has one."""
    cfg = Config.load(args.dir)
    out = Path(args.out).resolve() if args.out else cfg.root_state_path

    if args.from_profile:
        # A profile somebody logged into months ago is a perfectly good source of
        # identity, because state flows out of a profile freely. No human needed.
        if not args.url and not args.visit:
            raise StateError(
                "--from-profile needs at least one origin to walk: pass --visit URL "
                "(repeatable), or a positional url. A storageState only collects "
                "localStorage and IndexedDB for origins the browser has visited, so "
                "without one you get cookies and nothing else."
            )
        visit = list(args.visit or [])
        if args.url and args.url not in visit:
            visit.insert(0, args.url)
        summary = state.export_from_profile(
            out,
            Path(args.from_profile).resolve(),
            visit=visit,
            browser=cfg.lanes.browser,
            indexeddb=cfg.state.indexeddb,
        )
        print()
        print(f"walked {len(visit)} origin(s) in {args.from_profile}")
    else:
        if not args.url:
            raise StateError(
                "'state export' needs a url to open, or --from-profile to read an "
                "existing browser profile instead."
            )
        summary = state.export_interactive(
            out,
            args.url,
            browser=cfg.lanes.browser,
            indexeddb=cfg.state.indexeddb,
        )

    print()
    print(f"wrote {summary.path}")
    print(f"  {summary.cookies} cookies, {len(summary.origins_with_session)} origins "
          f"carrying a session, {_human_bytes(summary.total_bytes)} total")
    if cfg.state.indexeddb and not any(o.indexeddb_stores for o in summary.origins):
        # Not an error: plenty of sites keep everything in cookies. But if a login
        # fails to restore later, this line is the first thing to re-read.
        print("  note: no IndexedDB was captured. Fine if this site keeps its session "
              "in cookies; suspicious if it uses Firebase, Supabase or Auth0.")
    print()
    suffix = "" if not args.out else f" {summary.path}"
    target = args.url or (args.visit[0] if args.visit else "<a logged-in url>")
    print(f"  verify it:  autoweb state verify {target}{suffix}")
    return 0


def _cmd_state_inspect(args: argparse.Namespace) -> int:
    """Measure a state file so caps can be set from evidence rather than guesswork."""
    cfg = Config.load(args.dir)
    path = Path(args.path).resolve() if args.path else cfg.root_state_path
    summary = state.summarise(path)

    print(f"{summary.path}")
    print(f"  {_human_bytes(summary.total_bytes)} total, {summary.cookies} cookies, "
          f"{len(summary.origins)} origins "
          f"({len(summary.origins_with_session)} carrying a session)")
    print()
    print(f"  {'origin':<48} {'cookies':>7} {'ls':>4} {'idb':>4} {'bytes':>9}")
    for origin in summary.origins:
        if not args.all and not origin.carries_session:
            continue
        shown = (origin.origin[:47] + "~") if len(origin.origin) > 48 else origin.origin
        print(f"  {shown:<48} {origin.cookies:>7} "
              f"{origin.local_storage_keys:>4} {origin.indexeddb_stores:>4} "
              f"{origin.bytes:>9}")

    over = []
    if summary.total_bytes > cfg.caps.total_bytes:
        over.append(f"total_bytes ({summary.total_bytes} > {cfg.caps.total_bytes})")
    if len(summary.origins) > cfg.caps.max_origins:
        over.append(f"max_origins ({len(summary.origins)} > {cfg.caps.max_origins})")
    if over:
        print()
        print(f"  OVER CAP: {', '.join(over)}")
    return 0


def _cmd_state_verify(args: argparse.Namespace) -> int:
    """Seed a brand new browser from the state file and check whether it stayed logged in.

    The only honest test of an export. A file that parses proves nothing; a fresh
    browser that loads the page as you proves it worked.

    Exits non-zero when the session did not survive, so this is usable as a gate.
    Redirection is the default signal, because sites bounce unauthenticated requests
    to a login page and frequently serve the same ``<title>`` on both.
    """
    cfg = Config.load(args.dir)
    path = Path(args.path).resolve() if args.path else cfg.root_state_path

    result = state.seeded_context_check(path, args.url, browser=cfg.lanes.browser)

    print(f"seeded a fresh isolated browser from {path}")
    print(f"  requested : {args.url}")
    print(f"  landed on : {result.final_url}")
    print(f"  status    : {result.status if result.status is not None else 'unknown'}")
    print(f"  title     : {result.title!r}")
    print()

    failures = []
    if args.expect_url and args.expect_url not in result.final_url:
        failures.append(f"expected the URL to contain {args.expect_url!r}")
    # Matched against the title and the visible text, because a title is a weak
    # signal and many sites serve one across login and secure pages alike.
    if args.expect_text:
        haystack = (result.title + "\n" + result.body_text).lower()
        if args.expect_text.lower() not in haystack:
            failures.append(f"expected the page to contain {args.expect_text!r}")
    # A non-2xx is a failure even when the URL did not change: "OK" on a 404 with an
    # empty title is a false pass, and this command is meant to be a gate.
    if result.status is not None and not 200 <= result.status < 300:
        failures.append(f"the page returned HTTP {result.status}")
    # With no explicit assertion, fall back to the one signal that is almost always
    # right: a site that bounced you somewhere else logged you out.
    if (not args.expect_url and not args.expect_text
            and result.final_url.rstrip("/") != args.url.rstrip("/")):
        failures.append("the browser was redirected, which usually means logged out")

    if failures:
        for failure in failures:
            print(f"  FAIL: {failure}", file=sys.stderr)
        return 1

    print("  OK: the session survived the round trip.")
    return 0


def _cmd_lanes_sync(args: argparse.Namespace) -> int:
    """Regenerate the lane agent files from config."""
    cfg = Config.load(args.dir)

    if args.dry_run:
        print(f"would write {cfg.lanes.max} lane agent files to "
              f"{cfg.root_dir / lanes.AGENTS_DIRNAME}")
        print(f"  npx {' '.join(lanes.mcp_args(cfg))}")
        return 0

    results = lanes.sync(cfg)
    changed = [r for r in results if r.action != "unchanged"]
    for result in changed:
        print(f"  {result.action:<10} {result.path.name}")
    skipped = [r for r in results if r.action == "skipped"]
    if skipped:
        print()
        for result in skipped:
            print(f"  {result.path.name} was written by hand, so it was left alone. "
                  f"Delete it to let sync manage that lane.")
    if not changed:
        print(f"{cfg.lanes.max} lane agent files already up to date")
    else:
        print(f"{len([r for r in results if r.action != 'removed'])} lanes in "
              f"{cfg.root_dir / lanes.AGENTS_DIRNAME}")

    granted = lanes.sync_permissions(cfg)
    print(f"  granted {len(granted)} lane servers in {lanes.LOCAL_SETTINGS_PATH} "
          f"({granted[0]} .. {granted[-1]})")

    # Measured the hard way: a session that was already running kept serving the lane
    # definitions it cached at startup. Four lanes then connected to one pre-change
    # server, raced on a single tab, and each reported another lane's page as its own.
    # Nothing errored. Say this every time rather than only when files changed, because
    # the stale session cannot tell that it is stale.
    if changed:
        print()
        print("  restart Claude Code before dispatching these lanes. Agent files are")
        print("  read once at startup, like .mcp.json, so a session that is already")
        print("  running will keep using the old ones and the lanes will share a")
        print("  browser without reporting an error.")

    # Only isolated lanes are seeded from the file, so only they break without it.
    # A persistent lane never sees root.json and would be fine.
    if cfg.lanes.isolated and not cfg.root_state_path.is_file():
        # The files themselves are correct, so this is a warning rather than an error.
        # But be accurate about the consequence: a lane does not start logged out, it
        # fails every browser call until the file exists.
        print()
        print(f"  warning: {cfg.root_state_path.name} does not exist, so every browser")
        print("           call in a lane will fail with ENOENT until it does.")
        print("           Create it with: autoweb state export <url>")
    if not cfg.lanes.isolated:
        print()
        print("  note: lanes.isolated is off, so each lane keeps its own profile under")
        print(f"        {lanes.STATE_DIRNAME}/profiles and is not seeded from "
              f"{cfg.root_state_path.name}.")
        print("        A fresh profile starts logged out. Parallel work wants "
              "isolated = true.")
    return 0


def _cmd_lanes_list(args: argparse.Namespace) -> int:
    """Show which lane agent files exist and whether they are generated."""
    cfg = Config.load(args.dir)
    found = lanes.existing(cfg)
    if not found:
        print(f"no lane agent files in {cfg.root_dir / lanes.AGENTS_DIRNAME}")
        print("  create them with: autoweb lanes sync")
        return 0

    print(f"{len(found)} lane agent files in {cfg.root_dir / lanes.AGENTS_DIRNAME} "
          f"(ceiling is lanes.max = {cfg.lanes.max})")
    for lane in found:
        print(f"  {lane.path.name:<14} {lane.action}")
    return 0


def _cmd_trace(args: argparse.Namespace) -> int:
    """Decide from timestamps whether lanes overlapped, and exit non-zero if not.

    Deliberately a gate. "They ran in parallel" is the claim this project has been
    wrong about before, so it gets an exit code rather than a paragraph.
    """
    lanes_traced = trace.load(Path(args.path))
    verdict = trace.judge(lanes_traced)

    print(f"{len(verdict.lanes)} lanes, from {args.path}")
    print()
    print(f"  {'lane':<12} {'T0_start':<14} {'T1_loaded':<14} {'T3_end':<14}  seconds")
    for lane in verdict.lanes:
        def clock(mark: str) -> str:
            return lane.marks[mark].strftime("%H:%M:%S.%f")[:-3]
        print(f"  {lane.lane:<12} {clock('T0_start'):<14} {clock('T1_loaded'):<14} "
              f"{clock('T3_end'):<14}  {lane.duration_seconds:>6.1f}")
    print()

    if verdict.concurrent:
        print(f"  CONCURRENT: all {len(verdict.lanes)} lanes were alive together for "
              f"{verdict.overlap_seconds:.1f}s")
        print(f"    from {verdict.overlap_start.isoformat()} (latest load)")
        print(f"    to   {verdict.overlap_end.isoformat()} (earliest end)")
        return 0

    print("  SERIAL: there is no instant at which every lane was alive.", file=sys.stderr)
    print(f"    latest T1_loaded is after the earliest T3_end.", file=sys.stderr)
    for first, second in verdict.serial_pairs:
        print(f"    {first} had already finished before {second} started",
              file=sys.stderr)
    if not verdict.serial_pairs:
        print("    no pair is cleanly sequential, so the lanes overlapped pairwise but "
              "never all at once. Give each lane a longer hold.", file=sys.stderr)
    return 1


def _cmd_merge(args: argparse.Namespace) -> int:
    """Fold lane states back into root.json. One writer, after every lane is dead."""
    cfg = Config.load(args.dir)
    root_path = Path(args.root).resolve() if args.root else cfg.root_state_path
    lane_paths = [Path(p).resolve() for p in args.lanes]

    result = merge_mod.merge_files(root_path, lane_paths, cfg)

    print(f"merging {len(lane_paths)} lane file(s) onto {root_path}")
    print()
    print(f"  cookies: {result.cookies_kept} kept, {result.cookies_updated} updated, "
          f"{result.cookies_added} added, {result.cookies_evicted} evicted")
    print()
    for decision in result.decisions:
        who = f"  [{', '.join(decision.changed_by)}]" if decision.changed_by else ""
        print(f"  {decision.action:<9} {decision.origin}{who}")
        if decision.action == "evicted":
            print(f"            {decision.reason}")
    print()
    print(f"  {len(result.state['origins'])} origins, "
          f"{_human_bytes(result.total_bytes)} total")

    for violation in result.rotating_violations:
        # Loud, and on stderr, because this is the arrangement that gets a whole token
        # family revoked. Not fatal: the damage, if any, has already happened.
        print(f"  warning: {violation}. A site marked `rotates` should only ever be "
              f"held by one lane.", file=sys.stderr)

    if result.evicted_origins:
        print()
        print("  these need a fresh login:")
        for origin in result.evicted_origins:
            print(f"    {origin}")

    if args.dry_run:
        print()
        print("  --dry-run: nothing was written")
        return 0

    write_path = merge_mod.write_root(root_path, result)
    print()
    print(f"  wrote {write_path} (previous kept as {write_path.name}.bak)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoweb",
        description="A stub for web automation. Not a framework.",
        epilog="Docs: docs/BUILD-SPEC.md for the architecture, "
               "docs/CONSTRAINTS.md for what the browser will not let you do.",
    )
    parser.add_argument("--version", action="version", version=f"autoweb {__version__}")
    parser.add_argument(
        "-C", "--dir", type=Path, default=None, metavar="DIR",
        help="run as if started in DIR (config is searched upward from there)",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    config = sub.add_parser("config", help="inspect configuration")
    config_sub = config.add_subparsers(dest="subcommand", metavar="<subcommand>")

    show = config_sub.add_parser(
        "show", help="print effective config, including defaults")
    show.set_defaults(func=_cmd_config_show)

    check = config_sub.add_parser(
        "check", help="validate autoweb.toml; non-zero exit if malformed")
    check.set_defaults(func=_cmd_config_check)

    config.set_defaults(func=lambda a: (config.print_help(), 1)[1])

    st = sub.add_parser(
        "state", help="build and inspect root.json, the base identity")
    st_sub = st.add_subparsers(dest="subcommand", metavar="<subcommand>")

    export = st_sub.add_parser(
        "export",
        help="open a browser, wait for you to log in, then save the session",
        description="Opens a headed browser and waits. You log in by hand; AutoWeb "
                    "never sees your password, it only reads the session afterwards. "
                    "With --from-profile it reads a profile that is already logged in, "
                    "and no human is needed.",
    )
    export.add_argument("url", nargs="?", default=None,
                        help="page to open, e.g. https://example.com")
    export.add_argument("-o", "--out", default=None, metavar="PATH",
                        help="where to write (default: state.root from config)")
    export.add_argument("--from-profile", default=None, metavar="DIR",
                        help="read an existing browser profile instead of waiting for a "
                             "login. Pass a COPY: launching a browser locks the "
                             "directory. Needs --visit.")
    export.add_argument("--visit", action="append", default=None, metavar="URL",
                        help="origin to walk before capturing, repeatable. Required "
                             "with --from-profile, because a storageState only collects "
                             "localStorage and IndexedDB for visited origins.")
    export.set_defaults(func=_cmd_state_export)

    inspect = st_sub.add_parser(
        "inspect", help="measure a state file: origins, cookies, IndexedDB, bytes")
    inspect.add_argument("path", nargs="?", default=None,
                         help="state file (default: state.root from config)")
    inspect.add_argument("--all", action="store_true",
                         help="include origins that carry no session")
    inspect.set_defaults(func=_cmd_state_inspect)

    verify = st_sub.add_parser(
        "verify",
        help="seed a fresh browser from the state file and report what it sees",
        description="The honest test: a brand new isolated browser, nothing on disk, "
                    "given only the JSON.",
    )
    verify.add_argument("url", help="page to load, e.g. https://example.com")
    verify.add_argument("path", nargs="?", default=None,
                        help="state file (default: state.root from config)")
    verify.add_argument("--expect-url", default=None, metavar="SUBSTRING",
                        help="fail unless the final URL contains this")
    verify.add_argument("--expect-text", default=None, metavar="SUBSTRING",
                        help="fail unless the page title or visible text contains this")
    verify.set_defaults(func=_cmd_state_verify)

    st.set_defaults(func=lambda a: (st.print_help(), 1)[1])

    ln = sub.add_parser(
        "lanes", help="generate and inspect the parallel browser lanes")
    ln_sub = ln.add_subparsers(dest="subcommand", metavar="<subcommand>")

    lsync = ln_sub.add_parser(
        "sync",
        help="write lane agent files from config",
        description="Generates .claude/agents/lane-N.md, one per lane up to "
                    "lanes.max. Each gets an inline MCP server, which is what gives "
                    "it its own browser instead of sharing the session's.",
    )
    lsync.add_argument("--dry-run", action="store_true",
                       help="print what would be written and exit")
    lsync.set_defaults(func=_cmd_lanes_sync)

    llist = ln_sub.add_parser(
        "list", help="show existing lane agent files")
    llist.set_defaults(func=_cmd_lanes_list)

    tr = sub.add_parser(
        "trace",
        help="decide from lane timestamps whether they really overlapped",
        description="Reads the four marks each lane took inside its own browser and "
                    "reports whether every lane was alive at the same instant. Exits "
                    "non-zero when they were not, so it is usable as a gate.",
    )
    tr.add_argument("path", help="JSON of {lane: {T0_start: iso, ...}}")
    tr.set_defaults(func=_cmd_trace)

    mg = sub.add_parser(
        "merge",
        help="fold lane state files back into root.json",
        description="Three-way merge with root.json as the common ancestor. Deletions "
                    "are never propagated, an empty IndexedDB never overwrites a real "
                    "one, and a value two lanes changed differently evicts the origin "
                    "rather than guessing which token is still valid.",
    )
    mg.add_argument("lanes", nargs="+", metavar="LANE_JSON",
                    help="lane state files, e.g. lane-1.json lane-3.json")
    mg.add_argument("--root", default=None, metavar="PATH",
                    help="the ancestor to merge onto (default: state.root from config)")
    mg.add_argument("--dry-run", action="store_true",
                    help="print the decisions and write nothing")
    mg.set_defaults(func=_cmd_merge)

    ln.set_defaults(func=lambda a: (ln.print_help(), 1)[1])
    return parser


def _tolerate_unencodable_output() -> None:
    """Never crash because a hostname will not fit the console encoding.

    Windows consoles default to a legacy code page, so printing an internationalised
    origin such as an IDN domain raises UnicodeEncodeError and takes the whole command
    down with a traceback. The config was valid; only the printing failed. Degrade the
    characters instead of the command.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):  # pragma: no cover - exotic streams
                pass


def main(argv: list[str] | None = None) -> int:
    _tolerate_unencodable_output()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    try:
        return args.func(args)
    except (ConfigError, StateError, LaneError, TraceError, MergeError) as exc:
        # These are the user's problem to fix, so they get a clean message naming the
        # key or the file, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # Ctrl-C during an interactive export is a normal way to abandon it.
        print("\naborted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

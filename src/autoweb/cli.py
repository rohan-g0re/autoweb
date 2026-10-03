"""AutoWeb command line.

Deliberately thin. Every command does one thing you could have done by hand, and
prints what it did. If a command ever needs a paragraph of explanation, it is doing
too much.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, state
from .config import Config, ConfigError, Learned
from .state import StateError


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
    """Open a browser, wait for a human login, capture the session."""
    cfg = Config.load(args.dir)
    out = Path(args.out).resolve() if args.out else cfg.root_state_path

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
    print(f"  verify it:  autoweb state verify {args.url}")
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
        print(f"  {origin.origin[:48]:<48} {origin.cookies:>7} "
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
    """Seed a brand new browser from the state file and see whether it is logged in.

    The only honest test of an export. A file that parses proves nothing; a fresh
    browser that loads the page as you proves it worked.
    """
    cfg = Config.load(args.dir)
    path = Path(args.path).resolve() if args.path else cfg.root_state_path

    title = state.seeded_context_check(
        path, args.url, browser=cfg.lanes.browser, indexeddb=cfg.state.indexeddb)
    print(f"seeded a fresh isolated browser from {path}")
    print(f"  {args.url} -> {title!r}")
    print()
    print("  Read the title: if it names a login or sign-in page, the session did not "
          "survive the round trip.")
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
                    "never sees your password, it only reads the session afterwards.",
    )
    export.add_argument("url", help="page to open, e.g. https://example.com")
    export.add_argument("-o", "--out", default=None, metavar="PATH",
                        help="where to write (default: state.root from config)")
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
    verify.set_defaults(func=_cmd_state_verify)

    st.set_defaults(func=lambda a: (st.print_help(), 1)[1])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    try:
        return args.func(args)
    except (ConfigError, StateError) as exc:
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

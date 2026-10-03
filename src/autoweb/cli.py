"""AutoWeb command line.

Deliberately thin. Every command does one thing you could have done by hand, and
prints what it did. If a command ever needs a paragraph of explanation, it is doing
too much.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, Learned


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    try:
        return args.func(args)
    except ConfigError as exc:
        # Config errors are the user's problem to fix, so they get a clean message
        # naming the key, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # Ctrl-C during an interactive export is a normal way to abandon it.
        print("\naborted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

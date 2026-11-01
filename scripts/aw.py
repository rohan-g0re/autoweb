"""aw.py - the one way a skill, a hook or a human runs the AutoWeb CLI.

    python aw.py [autoweb args...]
    bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" ...

Two ways to run exist, and this file picks between them so that nothing else has
to:

  * an INSTALLED `autoweb` binary, when `uv tool install` has been run. That
    environment also holds `playwright`, so it is the only one where `state
    export` and `state verify` can open a browser. It is handed the arguments
    unchanged and its exit code is this process's exit code - on POSIX by
    replacing this process outright, on Windows by waiting on a child with
    inherited stdio, because `os.execv` on Windows returns to a shell that has
    already printed its prompt.
  * IN PLACE, from the plugin's own directory, when nothing is installed. The
    plugin ships the whole `autoweb` package, so `config`, `lanes`, `trace` and
    `merge` all work on a machine where setup has never run. Only the two
    browser commands do not, and the one line this file prints when they fail
    names the skill that fixes it.

This file never adds an argument the user did not give. In particular it never
supplies `-C`: which project the CLI runs against is the caller's decision, and
`aw_common.autoweb_argv` is where that decision is encoded. A launcher that
guessed a project from its own cwd would make a hook's cwd into a silent
argument.

Every other exit code is the package's own and passes straight through: 0 pass,
1 gate failed, 2 your files are wrong.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aw_common as aw  # noqa: E402  (also reconfigures stdout/stderr to UTF-8)

SETUP_HINT = ("autoweb: this command needs the playwright package, which is not "
              "importable here (only `state export` and `state verify` do). Run "
              "/autoweb:aw-setup.")

PLAYWRIGHT_EXIT = 2


def is_playwright_import_error(exc: BaseException) -> bool:
    """Whether an ImportError is about `playwright` and not about something else.

    `ModuleNotFoundError.name` is the reliable half; the message is checked too
    because an ImportError raised from inside playwright's own package (a broken
    install, a missing driver binding) carries no `name` at all. A missing
    `tomli` or a syntax error in the package is NOT this, and must keep its
    traceback - swallowing it would turn a packaging bug into a misleading
    instruction to run setup.
    """
    name = getattr(exc, "name", None) or ""
    if name.split(".")[0] == "playwright":
        return True
    return "playwright" in str(exc).lower()


def installed_binary() -> str | None:
    """An `autoweb` executable on this machine, or None. See aw_common.find_tool."""
    return aw.find_tool(aw.APP)


def exec_installed(exe: str, argv: list) -> int:
    """Hand the arguments to the installed binary and become it.

    POSIX: `os.execv`, so there is one process and signals (Ctrl-C during an
    interactive `state export`) reach the CLI directly. Windows has no exec that
    a console can follow, so the binary runs as a child with inherited stdio and
    its code is returned. A binary that cannot be executed at all falls back to
    the child path rather than dying: `uv tool install` can leave a shim that
    `os.execv` refuses (a `.cmd` on Windows, a shebang script on a filesystem
    mounted noexec).
    """
    full = [exe, *argv]
    if os.name != "nt":
        try:
            os.execv(exe, full)  # never returns
        except OSError:
            pass
    # check=False deliberately: a non-zero exit is the CLI's answer (1 gate
    # failed, 2 your files are wrong), not an error to raise on.
    return subprocess.run(full, check=False).returncode


def load_cli_main():
    """`autoweb.cli.main`, imported from the plugin's own copy of the package.

    `PLUGIN_ROOT` goes to the FRONT of sys.path: a project directory that happens
    to contain an `autoweb/` of its own must not shadow the package the plugin
    ships, because the two can be different versions.
    """
    root = str(aw.PLUGIN_ROOT)
    if sys.path[:1] != [root]:
        sys.path.insert(0, root)
    from autoweb.cli import main  # noqa: WPS433  (shipped with the plugin)
    return main


def run_in_place(argv: list) -> int:
    """Run the shipped package in this process and return the CLI's exit code.

    `argparse`'s `--version` and its own usage errors raise SystemExit rather
    than returning, so that is caught and converted: a launcher whose exit code
    came from a different path than the CLI's would be a launcher that lies
    about whether a gate passed.
    """
    try:
        cli_main = load_cli_main()
        return int(cli_main(argv) or 0)
    except ImportError as exc:
        if is_playwright_import_error(exc):
            print(SETUP_HINT, file=sys.stderr)
            return PLAYWRIGHT_EXIT
        raise
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    exe = installed_binary()
    if exe:
        return exec_installed(exe, argv)
    return run_in_place(argv)


if __name__ == "__main__":
    sys.exit(main())

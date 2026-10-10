"""SessionStart hook: two lines, and only in a project that opted in.

Whatever this prints on stdout is injected into the session, and SessionStart
fires on start, resume, clear, compact and fork, so the session pays for it
every single time. The budget is therefore hard, and so is the silence:

  * a directory without `autoweb.toml` is not an AutoWeb project, and gets
    NOTHING. A user-scope plugin sees every project on the machine; a repo that
    never asked for browser automation must not be told about it.
  * an AutoWeb project gets exactly two lines, under 700 characters together.
  * no subprocess, under two seconds. Everything printed is read off the
    filesystem: `autoweb.toml` through `autoweb.config`, `root.json`'s presence,
    the lane files in `.claude/agents/`, the goal files, and the trust bit in
    `~/.claude.json`. Nothing shells out to the CLI, because a hook that waits
    on a subprocess is a hook that stalls the session.

Line one is state. Line two is instruction: what this project is, where its
files are, and the exact command line for the CLI - through `py.sh`, so Python
discovery in a skill is the same discovery the hooks already did.

Why the lane count is split into generated and hand-written: `autoweb lanes
sync` writes an inline `mcpServers` block per lane, named per lane, because
Claude Code de-duplicates inline servers BY NAME across concurrent subagents.
A hand-written `lane-N.md` that has no inline server, or one that reuses
another lane's server name, collapses into a sibling's browser - the exact
failure the parallel-lanes skill exists to prevent - and the generator refuses
to overwrite it because it carries no generated marker. So a hand-written lane
is not an error, but it is never silently folded into the "generated" count.

Exits 0 always. A hook that fails must never block a session.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aw_common as aw  # noqa: E402  (also reconfigures stdout/stderr to UTF-8)

# Two lines, and the second carries three absolute paths. 700 is the contract
# (docs/PLUGIN-DESIGN.md §5.3); the guard below is a backstop for a plugin
# installed under an unusually deep cache path, not an expected code path.
STDOUT_BUDGET = 700

INSTRUCTION = (
    "autoweb: this project automates websites with AutoWeb. Goals live in goals/, "
    "runs in runs/; use the autoweb skill or /autoweb:aw-run <goal>. CLI: "
    'bash "{s}/py.sh" "{s}/aw.py" -C "{p}" <subcommand>'
)


def read_payload() -> dict:
    """The hook JSON on stdin (`cwd`, `session_id`, `source`), or an empty dict."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def fwd(path) -> str:
    """Absolute, with forward slashes, so it quotes cleanly in a sh command line."""
    return str(path).replace("\\", "/")


def identity_part(paths: dict) -> str:
    """Whether the identity state file exists, and what to run when it does not.

    Without it every lane starts from an empty storage state, which on a logged-in
    site means the login page. `state export` is interactive by design: a human
    logs in once in a headed browser.

    The file is named rather than assumed: `state.root` is configurable, and a
    project that renamed it would otherwise be told about a `root.json` it does
    not have. With the default in force this reads exactly as the contract does.
    """
    root_json = paths["root_json"]
    if root_json.is_file():
        return f"identity {root_json.name} present"
    return "identity missing (run: autoweb state export <url>)"


def lanes_part(project, cfg) -> str:
    """"N of M lanes generated", plus any hand-written lane files.

    M is `lanes.max` from the project's config - a ceiling, never a target. With
    no readable config there is no M to quote, so the count stands alone rather
    than being compared against a default the project never chose.
    """
    total = len(aw.lane_files(project))
    generated = len(aw.generated_lane_files(project))
    handwritten = total - generated
    if total == 0:
        return "no lanes (run: autoweb lanes sync)"
    maximum = None
    try:
        if cfg is not None:
            maximum = int(cfg.lanes.max)
    except Exception:
        maximum = None
    part = (f"{generated} of {maximum} lanes generated" if maximum is not None
            else f"{generated} lanes generated")
    if handwritten > 0:
        part += f" (+{handwritten} hand-written)"
    return part


def goals_part(project) -> str:
    """How many real goal files there are (`goals/README.md` is the template)."""
    count = len(aw.goal_files(project))
    return "1 goal" if count == 1 else f"{count} goals"


def trust_part(project) -> str:
    """The trust bit, in the words of what it costs.

    Claude Code refuses to start an agent file's `mcpServers` in an untrusted
    folder, and refuses *silently*: the lane runs with no browser tools at all.
    """
    trusted = aw.trust_accepted(project)
    if trusted is True:
        return "trust ok"
    if trusted is False:
        return "trust not granted (open the folder and accept the prompt)"
    return "trust unknown"


def status_line(project, cfg) -> str:
    """One line of state: identity, lanes, goals, trust."""
    paths = aw.project_paths(project, cfg)
    parts = [
        identity_part(paths),
        lanes_part(project, cfg),
        goals_part(project),
        trust_part(project),
    ]
    return "autoweb: " + " · ".join(parts)


def instruction_line(project) -> str:
    """One line of instruction, naming the real scripts directory and project."""
    return INSTRUCTION.format(s=fwd(aw.SCRIPTS_DIR), p=fwd(project))


def main() -> int:
    payload = read_payload()
    project = aw.project_dir(payload.get("cwd"))
    source = str(payload.get("source") or "")
    session = str(payload.get("session_id") or "-")

    if not aw.is_autoweb_project(project):
        return 0

    cfg = aw.load_project_config(project)
    out = status_line(project, cfg) + "\n" + instruction_line(project)
    if len(out) > STDOUT_BUDGET:
        out = out[: STDOUT_BUDGET - 3].rstrip() + "..."
    print(out)
    if source:
        aw.append_log(f"session start: source={source} session={session} project={project}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # a hook never raises into the session
        aw.append_log(f"session_start error: {type(exc).__name__}: {exc}")
        sys.exit(0)

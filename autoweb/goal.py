"""Deciding whether a goal was reached, by a command rather than by a model.

A goal is an end state plus the assertion that proves it was reached. This module reads
those assertions out of the goal file, evaluates each one, and writes the verdict to
``runs/<run-id>/status.json``. The model that did the work never grades it: the run
either left the world in the declared state or it did not, and a program says which.

The assertions live in a fenced ``toml`` block under the goal's ``## Assertions``
heading, one ``[[assert]]`` table each. Four kinds:

* ``url``     - open ``url`` seeded from ``root.json``; the final URL must start with
  ``expect``.
* ``text``    - open ``url``; ``expect`` must appear in the page's visible text.
* ``element`` - open ``url``; an element matching the CSS selector ``expect`` must be
  visible.
* ``command`` - run ``run`` through the shell in the project root; exit 0 passes.

The browser kinds judge after the page has settled, not at ``domcontentloaded``. A
single-page app that is logged out often serves the requested URL first and redirects
to its login page from script a moment later. Judging at ``domcontentloaded`` is how
``state verify`` passes such a page, and this module does not repeat it.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import state
from .state import StateError

SCHEMA = 1
KINDS = ("url", "text", "element", "command")
BROWSER_KINDS = ("url", "text", "element")
DEFAULT_TIMEOUT_S = 30
PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"

NO_ASSERTION = "a goal without a checkable assertion is not a goal"

# The heading the block lives under. The singular is the name older goal files and
# the setup skill use, so it is read too; the template writes the plural.
SECTION_TITLES = ("assertions", "assertion")

# How long to wait for `networkidle` before giving up on it. Pages that long-poll never
# go idle, and that is not a failure of the page; the settle below still runs.
NETWORKIDLE_CAP_S = 10
# A pause after the page reports itself loaded, so a client-side redirect has happened
# before anything is judged.
SETTLE_MS = 2000

_ID = re.compile(r"[a-z0-9-]+")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_DETAIL_LIMIT = 300


class GoalError(Exception):
    """A goal file, a run id or a path the user gave is wrong. Exit 2."""


@dataclass(frozen=True)
class Assertion:
    """One ``[[assert]]`` table, validated."""

    id: str
    kind: str
    timeout_s: float
    url: str | None = None
    expect: str | None = None
    run: str | None = None


@dataclass(frozen=True)
class Outcome:
    """What evaluating one assertion found."""

    id: str
    kind: str
    status: str
    detail: str

    def as_json(self) -> dict[str, str]:
        return {"id": self.id, "kind": self.kind, "status": self.status,
                "detail": self.detail}


@dataclass(frozen=True)
class PageView:
    """What a fresh seeded browser saw once the page had settled."""

    final_url: str
    status: int | None
    body_text: str = ""
    selector_visible: bool | None = None
    """Set only when a selector was asked about."""


# --- reading the goal ---------------------------------------------------------


def find_goal(name: str, root_dir: Path) -> Path:
    """Resolve the goal argument the way ``aw-run`` does.

    ``goals/x.md``, ``x.md`` and ``x`` all name the same file. A path that exists as
    given wins.
    """
    given = Path(name)
    candidates = [given]
    if not given.is_absolute():
        candidates += [root_dir / given, root_dir / "goals" / given]
        if given.suffix != ".md":
            candidates.append(root_dir / "goals" / f"{name}.md")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise GoalError(f"{name}: no goal file here, and none at "
                    f"{(root_dir / 'goals' / given.name).as_posix()}")


def load(path: Path) -> list[Assertion]:
    """Read a goal file and return its assertions, or raise GoalError saying why not."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise GoalError(f"{path}: cannot be read: {exc}") from exc
    return parse(text, source=str(path))


def parse(text: str, *, source: str = "goal") -> list[Assertion]:
    """Pull the ``toml`` block out of ``## Assertions`` and validate every table.

    Strict on purpose. An unknown key is far more often a typo than an extension, and a
    misspelled ``timeout_s`` that silently fell back to the default would change what
    the check means without anyone seeing it.
    """
    found_section, blocks = _toml_blocks(text, source)
    if not found_section:
        raise GoalError(f"{source}: no '## Assertions' section. {_capital(NO_ASSERTION)}.")
    if not blocks:
        raise GoalError(f"{source}: '## Assertions' holds no ```toml block. "
                        f"{_capital(NO_ASSERTION)}.")
    try:
        data = tomllib.loads("\n".join(blocks))
    except tomllib.TOMLDecodeError as exc:
        raise GoalError(f"{source}: the assertions block is not valid TOML: {exc}") from exc

    stray = sorted(set(data) - {"assert"})
    if stray:
        raise GoalError(f"{source}: unknown key(s) {', '.join(map(repr, stray))} in the "
                        f"assertions block. Each assertion is an [[assert]] table.")
    entries = data.get("assert")
    if entries is None or entries == []:
        raise GoalError(f"{source}: the assertions block has no [[assert]] table. "
                        f"{_capital(NO_ASSERTION)}.")
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise GoalError(f"{source}: write each assertion as an [[assert]] table, with "
                        f"two brackets.")

    assertions = [_one(n, entry, source) for n, entry in enumerate(entries, 1)]
    seen: set[str] = set()
    for assertion in assertions:
        if assertion.id in seen:
            raise GoalError(f"{source}: the id '{assertion.id}' is used twice. Each "
                            f"assertion needs its own, because status.json is read by id.")
        seen.add(assertion.id)
    return assertions


def _capital(sentence: str) -> str:
    return sentence[:1].upper() + sentence[1:]


def _toml_blocks(text: str, source: str) -> tuple[bool, list[str]]:
    """Every ``toml`` fence under an ``## Assertions`` heading, in order.

    Fences are tracked so that a heading inside a code block is not taken for a real
    one. That matters for the template, which shows a whole goal inside a fence.
    """
    in_section = found = False
    fence: tuple[str, int, bool, int] | None = None   # char, length, collecting, line
    blocks: list[str] = []
    current: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        match = _FENCE.match(line)
        if fence is not None:
            char, length, collecting, _ = fence
            if (match and match.group(1)[0] == char and len(match.group(1)) >= length
                    and not match.group(2).strip()):
                if collecting:
                    blocks.append("\n".join(current))
                    current = []
                fence = None
            elif collecting:
                current.append(line)
            continue
        if match:
            info = match.group(2).strip().split()
            lang = info[0].lower() if info else ""
            fence = (match.group(1)[0], len(match.group(1)), in_section and lang == "toml",
                     lineno)
            continue
        heading = _HEADING.match(line)
        if heading and len(heading.group(1)) <= 2:
            title = re.sub(r"[ \t]+#+$", "", heading.group(2) or "").strip().lower()
            in_section = len(heading.group(1)) == 2 and title in SECTION_TITLES
            found = found or in_section
    if fence is not None and fence[2]:
        raise GoalError(f"{source}: the ```toml fence opened at line {fence[3]} is never "
                        f"closed")
    return found, blocks


def _one(n: int, raw: dict[str, Any], source: str) -> Assertion:
    ident = raw.get("id")
    if not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise GoalError(f"{source}: assertion {n}: 'id' must be a string of a-z, 0-9 "
                        f"and '-', got {ident!r}")
    where = f"{source}: assertion '{ident}'"

    kind = raw.get("kind")
    if kind not in KINDS:
        raise GoalError(f"{where}: 'kind' must be one of {', '.join(KINDS)}, got {kind!r}")

    allowed = {"id", "kind", "timeout_s"} | (
        {"run"} if kind == "command" else {"url", "expect"})
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise GoalError(f"{where}: a {kind} assertion takes no "
                        f"{', '.join(map(repr, unknown))} key; it takes "
                        f"{', '.join(sorted(allowed))}")

    timeout = raw.get("timeout_s", DEFAULT_TIMEOUT_S)
    if (isinstance(timeout, bool) or not isinstance(timeout, int | float)
            or not math.isfinite(timeout) or timeout <= 0):
        raise GoalError(f"{where}: 'timeout_s' must be a positive number of seconds, "
                        f"got {timeout!r}")

    if kind == "command":
        return Assertion(ident, kind, float(timeout), run=_text(raw, "run", where))

    url = _text(raw, "url", where)
    if not _SCHEME.match(url):
        raise GoalError(f"{where}: 'url' must be a full URL with a scheme, such as "
                        f"https://example.com/, got {url!r}")
    return Assertion(ident, kind, float(timeout), url=url, expect=_text(raw, "expect", where))


def _text(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GoalError(f"{where}: needs '{key}', a non-empty string")
    return value


# --- the run ------------------------------------------------------------------


def check_run_id(run: str) -> str:
    """A run id names a directory under ``runs/``, so it may not climb out of it."""
    if not _RUN_ID.fullmatch(run) or ".." in run:
        raise GoalError(f"--run {run!r}: a run id is one directory name under runs/, "
                        f"made of letters, digits, '.', '_' and '-', such as "
                        f"2026-11-01-daily-digest")
    return run


def status_path(root_dir: Path, run_id: str) -> Path:
    return root_dir / "runs" / run_id / "status.json"


def substitute(command: str, *, run_id: str, goal_name: str) -> str:
    """Replace ``{run}`` and ``{goal}``, and nothing else.

    Plain replacement, not ``str.format``: shell commands are full of braces, from
    ``${VAR}`` to ``awk '{print $1}'``, and those must reach the shell untouched.
    """
    return command.replace("{run}", run_id).replace("{goal}", goal_name)


def plan_line(assertion: Assertion, *, run_id: str, goal_name: str, root_dir: Path) -> str:
    """One line saying what evaluating *assertion* would do, for ``--dry-run``."""
    head = f"PLAN  {assertion.id} ({assertion.kind}):"
    budget = f"timeout {assertion.timeout_s:g}s"
    if assertion.kind == "command":
        command = substitute(assertion.run or "", run_id=run_id, goal_name=goal_name)
        return f"{head} run `{command}` in {root_dir}, {budget}"
    want = {
        "url": f"the final URL to start with {assertion.expect}",
        "text": f"the visible text to contain {assertion.expect!r}",
        "element": f"an element matching {assertion.expect!r} to be visible",
    }[assertion.kind]
    return f"{head} open {assertion.url} seeded from the identity, expect {want}, {budget}"


def outcome_line(outcome: Outcome) -> str:
    return f"{outcome.status:<5} {outcome.id} ({outcome.kind}): {outcome.detail}"


def evaluate(assertion: Assertion, *, root_dir: Path, state_path: Path, browser: str,
             run_id: str, goal_name: str) -> Outcome:
    """Evaluate one assertion. Never raises: what cannot be evaluated is an ERROR."""
    if assertion.kind == "command":
        command = substitute(assertion.run or "", run_id=run_id, goal_name=goal_name)
        status, detail = _check_command(command, cwd=root_dir, timeout_s=assertion.timeout_s)
    else:
        status, detail = _check_page(assertion, state_path=state_path, browser=browser)
    return Outcome(assertion.id, assertion.kind, status, detail)


def status_document(goal_label: str, run_id: str, outcomes: list[Outcome],
                    checked_at: datetime | None = None) -> dict[str, Any]:
    """The ``status.json`` body. ``done`` is true only when there is something to pass
    and every one of it passed: an empty list is never done."""
    moment = (checked_at or datetime.now(UTC)).astimezone(UTC)
    return {
        "schema": SCHEMA,
        "goal": goal_label,
        "run": run_id,
        "checked_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assertions": [o.as_json() for o in outcomes],
        "done": bool(outcomes) and all(o.status == PASS for o in outcomes),
    }


def write_status(path: Path, document: dict[str, Any]) -> None:
    """Write atomically, so a reader never sees half a verdict. LF on every platform,
    so the bytes are the same wherever the check ran."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True)
                       + "\n", encoding="ascii", newline="\n")
        tmp.replace(path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass    # cleanup is best-effort; the original error is what matters
        raise GoalError(f"{path}: cannot write the status file: {exc}") from exc


def goal_label(goal_path: Path, root_dir: Path) -> str:
    """The goal as a project-relative path with forward slashes, when it is inside."""
    try:
        return goal_path.resolve().relative_to(root_dir.resolve()).as_posix()
    except ValueError:
        return goal_path.resolve().as_posix()


# --- command assertions -------------------------------------------------------


def _check_command(command: str, *, cwd: Path, timeout_s: float) -> tuple[str, str]:
    extra: dict[str, Any] = {}
    if os.name == "nt":
        extra["creationflags"] = 0x08000000     # CREATE_NO_WINDOW
    else:
        # Its own process group, so a timeout kills what the shell started and not
        # only the shell. Otherwise a `sleep` outlives the check and holds the pipe.
        extra["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", **extra,
        )
    except OSError as exc:
        return ERROR, f"could not run the command: {exc}"
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = "", ""   # a grandchild still holds the pipe; give up on it
        said = _one_line((out or "") + " " + (err or ""))
        return ERROR, f"timed out after {timeout_s:g}s" + (f": {said}" if said else "")

    said = _one_line((out or "") + " " + (err or ""))
    if proc.returncode == 0:
        return PASS, "exit 0" + (f": {said}" if said else "")
    return FAIL, f"exit {proc.returncode}" + (f": {said}" if said else "")


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        taskkill = shutil.which("taskkill") or os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        try:
            subprocess.run([taskkill, "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, check=False, timeout=10,
                           creationflags=0x08000000)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _one_line(text: str) -> str:
    """Output squeezed onto one line, keeping the end, where errors usually are."""
    flat = " ".join(text.split())
    return flat if len(flat) <= _DETAIL_LIMIT else "..." + flat[-_DETAIL_LIMIT:]


# --- browser assertions -------------------------------------------------------


def _check_page(assertion: Assertion, *, state_path: Path, browser: str) -> tuple[str, str]:
    try:
        view = seeded_page(
            state_path, assertion.url or "", browser=browser,
            timeout_seconds=assertion.timeout_s,
            wait_text=assertion.expect if assertion.kind == "text" else None,
            selector=assertion.expect if assertion.kind == "element" else None,
        )
    except StateError as exc:
        return ERROR, _one_line(str(exc))
    except Exception as exc:  # noqa: BLE001 - any browser failure is an ERROR, not a crash
        return ERROR, _one_line(f"the browser failed: {exc}")
    return judge(assertion, view)


def judge(assertion: Assertion, view: PageView) -> tuple[str, str]:
    """Decide one browser assertion from what the page showed. Pure, so it is tested
    without a browser."""
    landed = view.final_url
    expect = assertion.expect or ""
    # The page you asked for answered with an error. Whatever it shows is not the
    # state the goal declared; "OK" on a 404 is the false pass this repo has shipped.
    if view.status is not None and not 200 <= view.status < 400:
        return FAIL, f"HTTP {view.status} at {landed}"
    if assertion.kind == "url":
        if landed.startswith(expect) or landed.rstrip("/") == expect.rstrip("/"):
            return PASS, f"landed on {landed}"
        return FAIL, f"landed on {landed}, expected a URL starting with {expect}"
    if assertion.kind == "text":
        if contains_text(view.body_text, expect):
            return PASS, f"found {expect!r} on {landed}"
        return FAIL, f"{expect!r} is not in the visible text of {landed}"
    if view.selector_visible:
        return PASS, f"{expect} is visible on {landed}"
    return FAIL, f"no visible element matches {expect} on {landed}"


def contains_text(body: str, expected: str) -> bool:
    """Case and whitespace are ignored. ``innerText`` applies CSS ``text-transform``,
    so a heading styled in capitals reads back in capitals."""
    return " ".join(expected.split()).casefold() in " ".join(body.split()).casefold()


def seeded_page(state_path: Path, url: str, *, browser: str = "chrome",
                timeout_seconds: float = DEFAULT_TIMEOUT_S, wait_text: str | None = None,
                selector: str | None = None) -> PageView:
    """Open *url* in a fresh headless browser seeded from *state_path*, and report
    what it shows once the page has settled.

    The same seeding as ``state.seeded_context_check``: a brand new isolated browser
    given only the JSON. The difference is when it looks. That helper reads the page
    at ``domcontentloaded``; this one waits for ``load``, then for ``networkidle`` up to
    ``NETWORKIDLE_CAP_S``, then ``SETTLE_MS`` more, so a client-side redirect to a login
    page has happened before anything is judged. After that, *wait_text* and *selector*
    are waited for until the budget runs out. The whole visit shares
    *timeout_seconds*.

    Raises StateError when the browser cannot launch or the page cannot load.
    """
    state.load(state_path)      # a malformed identity is a message, not a launch error
    deadline = time.monotonic() + timeout_seconds

    def left_ms() -> int:
        # Never 0: Playwright reads a timeout of 0 as "wait forever".
        return max(1, int((deadline - time.monotonic()) * 1000))

    pw_api = state._playwright()
    with pw_api() as pw:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        instance = state._launch(pw, browser, headless=True)
        try:
            context = instance.new_context(storage_state=str(state_path))
            page = context.new_page()
            try:
                response = page.goto(url, wait_until="load", timeout=left_ms())
            except Exception as exc:
                first = str(exc).strip().splitlines()[0] if str(exc).strip() else str(exc)
                raise StateError(f"could not load {url}: {first}") from exc
            try:
                page.wait_for_load_state(
                    "networkidle", timeout=min(left_ms(), NETWORKIDLE_CAP_S * 1000))
            except PlaywrightTimeout:
                pass    # long-polling pages never go idle; the settle still runs
            page.wait_for_timeout(min(SETTLE_MS, left_ms()))

            visible: bool | None = None
            if selector is not None:
                try:
                    (page.locator(f"css={selector}").filter(visible=True).first
                     .wait_for(state="visible", timeout=left_ms()))
                    visible = True
                except PlaywrightTimeout:
                    visible = False

            body = _body_text(page)
            if wait_text is not None:
                while (not contains_text(body, wait_text)
                       and deadline - time.monotonic() > 0):
                    page.wait_for_timeout(min(250, left_ms()))
                    body = _body_text(page)

            return PageView(
                final_url=page.url,
                status=response.status if response is not None else None,
                body_text=body,
                selector_visible=visible,
            )
        finally:
            instance.close()


def _body_text(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:  # noqa: BLE001 - mid-navigation or no body: nothing visible yet
        return ""


__all__ = [
    "BROWSER_KINDS",
    "DEFAULT_TIMEOUT_S",
    "KINDS",
    "NO_ASSERTION",
    "SCHEMA",
    "Assertion",
    "GoalError",
    "Outcome",
    "PageView",
    "check_run_id",
    "contains_text",
    "evaluate",
    "find_goal",
    "goal_label",
    "judge",
    "load",
    "outcome_line",
    "parse",
    "plan_line",
    "seeded_page",
    "status_document",
    "status_path",
    "substitute",
    "write_status",
]

"""AutoWeb configuration.

Two files, deliberately separate:

``autoweb.toml``
    What a human sets. Hand-edited, committed, reviewed.

``.autoweb/learned.json``
    What the simulation loop discovers: which origins rotate their tokens, which
    ones lose their session if you drop their IndexedDB. Machine-written, never
    hand-edited, gitignored.

Keeping them apart matters more than the formats do. A loop that rewrites the file a
human is editing will eventually clobber an intention, and you will not notice until a
run behaves differently for no visible reason.

Every option is documented at its definition. There is no separate reference page to
drift out of date.

The loader's job is to make every failure legible: a bad config names the key, says
what was expected, and exits 2. Needing a traceback to find out which line of your
TOML was wrong is a bug in this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - trivial import shim
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


CONFIG_FILENAME = "autoweb.toml"
STATE_DIRNAME = ".autoweb"
LEARNED_FILENAME = "learned.json"


class ConfigError(Exception):
    """Raised when config is malformed.

    Always names the offending key. A config error that makes you grep for the
    problem is a bug in this module, not in your config.
    """


@dataclass(frozen=True)
class LaneConfig:
    """How lanes are spawned.

    A lane is one subagent with its own inline ``playwright-mcp`` server, and
    therefore its own browser. See ``docs/BUILD-SPEC.md``.
    """

    max: int = 5
    """Ceiling on concurrent lanes.

    Never a target. The orchestrator decides how many a task actually needs at
    runtime and may use fewer. This exists so a runaway decomposition cannot spawn
    fifty browsers.

    Two real limits sit above this: Claude Code caps concurrent subagents at 20 by
    default (``CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS``), and each lane is a browser,
    so N lanes is roughly N x 2 GB of RAM.
    """

    browser: str = "chrome"
    """Which browser channel lanes launch.

    ``chrome`` is playwright-mcp's own default and uses real Google Chrome.
    ``chromium`` means Chrome for Testing, which Playwright downloads.

    Set this to ``chromium`` on most Linux. Google ships Chrome as a .deb/.rpm for
    x86_64 only, it is absent from Arch's official repositories, and for ARM Linux it
    does not exist at all. Measured on x86_64 Arch: no ``google-chrome`` binary, only
    ``/usr/bin/chromium``. Install the
    build playwright-mcp expects with::

        npx @playwright/mcp@<version> install-browser chrome-for-testing

    not ``npx playwright install chromium``: that installs a different revision, and
    the mismatch surfaces at the first browser call rather than at startup.
    """

    mcp_version: str = "0.0.83"
    """Pinned ``@playwright/mcp`` version.

    Pinned on purpose. ``@latest`` is how a first run hangs on an unexpected download
    and how a working setup breaks silently three weeks later.
    """

    isolated: bool = True
    """Run each lane with ``--isolated``.

    Keeps the profile in memory, so a lane writes nothing to disk and no cache junk
    accumulates. Also what makes lanes safe to run in parallel: they share no profile
    directory, so there is no lock to contend over.

    Turning this off puts every lane on disk instead, and ``autoweb lanes sync`` then
    gives each one its own ``--user-data-dir`` under ``.autoweb/profiles/``, because a
    second browser on a profile already in use fails *silently* on Windows. The cost is
    that ``--storage-state`` applies to isolated sessions only, so a persistent lane is
    **not** seeded from ``root.json``: it carries whatever its own profile already
    holds, and a fresh one starts logged out. Off is for debugging a single lane you
    want to watch across restarts, not for parallel work.
    """


@dataclass(frozen=True)
class StateConfig:
    """Where identity lives."""

    root: str = "root.json"
    """Path to the base ``storageState`` JSON every lane starts from.

    A JSON file, never a Chrome profile directory. ``launchPersistentContext`` has no
    ``storageState`` option: it accepts one, launches successfully, and silently
    discards it. State flows out of a profile and never back in, which is the single
    fact that decides this whole design.
    """

    indexeddb: bool = True
    """Capture IndexedDB when exporting.

    Defaults to ``True`` here even though Playwright's own default is ``False``,
    because ``False`` quietly loses most real logins: Firebase, Supabase and Auth0 all
    keep sessions in IndexedDB. On one measured profile, cookies were 1.5 MB of a
    31 MB identity set.

    Set per-origin overrides in ``[origins]`` if a specific site's IndexedDB is large
    and worthless.
    """


@dataclass(frozen=True)
class CapsConfig:
    """Hard ceilings on ``root.json``, so it cannot grow without bound.

    These are a safety valve, **not** a selection policy. What belongs in root is
    decided by which origins a lane actually navigated to; caps only stop a runaway.
    Hitting one should be loud.

    Shape borrowed from Browserless, which ships 2 MB / 50 origins / 5 IndexedDB
    stores per origin with an auto-fit that drops the heaviest origins first.
    """

    total_bytes: int = 2_000_000
    """Maximum size of ``root.json``. Over this, heaviest origins are dropped first."""

    max_origins: int = 50
    """Maximum number of origins retained in root."""

    max_indexeddb_per_origin: int = 5
    """Maximum IndexedDB stores kept for any single origin."""


@dataclass(frozen=True)
class OriginRule:
    """Per-origin overrides.

    Most entries here are written by the loop, not by you. See ``learned.json``.
    Anything you set by hand in ``autoweb.toml`` wins over what the loop learned.

    Keys match by host, so ``example.com`` covers ``https://app.example.com`` and
    ``https://example.com:8443/path``. When several rules match, the most specific
    one wins, whatever order they appear in the file.
    """

    indexeddb: bool | None = None
    """Capture IndexedDB for this origin. ``None`` means inherit ``state.indexeddb``."""

    rotates: bool = False
    """This site rotates its tokens, so only ONE lane may ever hold it.

    When a site reissues its token on every use, two lanes holding the same token look
    like a replay attack, and the server is entitled to revoke the whole family,
    logging out every lane at once. Discovered by the loop when a cookie value changes
    mid-run; set by hand if you already know.
    """

    sticky: bool = False
    """Never evict this origin, whatever the caps say.

    Set when dropping an origin has been observed to force a re-login. Some apps keep
    cryptographic keys client-side in IndexedDB (WhatsApp Web, Matrix/Element) where
    eviction does not merely log you out, it unpairs the device.
    """


# TOML is typed; dataclasses are not. ``Klass(**data)`` happily builds
# ``LaneConfig(max="five")``, and the damage surfaces later as a TypeError from a
# comparison, or never surfaces at all: ``indexeddb = "false"`` is a non-empty string,
# so it is truthy, so a user who tried to turn IndexedDB off gets it captured.
# Check types here, once, while the key name is still in hand.
#
# Specs are explicit rather than reflected off annotations, so error messages stay
# readable and ``bool | None`` needs no parsing.
_FIELD_TYPES: dict[type, dict[str, tuple[type, bool]]] = {
    LaneConfig: {
        "max": (int, False),
        "browser": (str, False),
        "mcp_version": (str, False),
        "isolated": (bool, False),
    },
    StateConfig: {
        "root": (str, False),
        "indexeddb": (bool, False),
    },
    CapsConfig: {
        "total_bytes": (int, False),
        "max_origins": (int, False),
        "max_indexeddb_per_origin": (int, False),
    },
    OriginRule: {
        "indexeddb": (bool, True),
        "rotates": (bool, False),
        "sticky": (bool, False),
    },
}

_TYPE_NAMES = {bool: "a boolean", int: "an integer", str: "a string"}
_BROWSERS = ("chrome", "chromium", "msedge", "firefox", "webkit")

MAX_LANES = 50
"""Hard ceiling on ``lanes.max``.

Not a performance guess. Claude Code runs at most 20 subagents concurrently by
default, and every lane is a browser worth roughly 2 GB, so a four-digit value is
always a typo rather than an intention.
"""


def _check_type(value: Any, expected: type, optional: bool, where: str) -> None:
    """Reject a wrong-typed value, naming the key and what was expected."""
    if optional and value is None:
        return
    if expected is bool:
        ok = isinstance(value, bool)
    elif expected is int:
        # bool subclasses int, so ``max = true`` would otherwise pass and compare as 1.
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, expected)
    if not ok:
        raise ConfigError(
            f"{where} must be {_TYPE_NAMES.get(expected, expected.__name__)}, "
            f"got {type(value).__name__} ({value!r})"
        )


@dataclass(frozen=True)
class Config:
    """The whole of AutoWeb's configuration."""

    lanes: LaneConfig = field(default_factory=LaneConfig)
    state: StateConfig = field(default_factory=StateConfig)
    caps: CapsConfig = field(default_factory=CapsConfig)
    origins: dict[str, OriginRule] = field(default_factory=dict)
    root_dir: Path = field(default=Path("."))

    # --- loading -------------------------------------------------------------

    @classmethod
    def load(cls, start: Path | None = None) -> Config:
        """Load ``autoweb.toml``, searching upward from *start*.

        Missing config is not an error: every option has a working default, so
        AutoWeb runs out of the box. Malformed config *is* an error, and names the key.

        The search stops at a repository root or your home directory, so a stray
        ``autoweb.toml`` higher up the filesystem cannot silently become this
        project's config.
        """
        start = Path(start or Path.cwd()).resolve()
        if not start.is_dir():
            why = "it is a file" if start.exists() else "does not exist"
            raise ConfigError(f"{start}: not a directory ({why})")

        path = _find_upward(start, CONFIG_FILENAME)
        if path is None:
            return cls(root_dir=start)

        try:
            # utf-8-sig tolerates the BOM that Notepad writes by default.
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ConfigError(f"{path}: not valid UTF-8 text: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"{path}: cannot be read: {exc}") from exc

        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: not valid TOML: {exc}") from exc

        return cls._from_dict(raw, path)

    @classmethod
    def _from_dict(cls, raw: dict[str, Any], path: Path) -> Config:
        known = ("lanes", "state", "caps", "origins")
        for key in raw:
            if key not in known:
                raise ConfigError(
                    f"{path}: unknown top-level key '{key}'. "
                    f"Expected one of: {', '.join(known)}"
                )

        lanes = _build(LaneConfig, raw.get("lanes"), path, "lanes")
        state = _build(StateConfig, raw.get("state"), path, "state")
        caps = _build(CapsConfig, raw.get("caps"), path, "caps")

        origins_raw = raw.get("origins") or {}
        if not isinstance(origins_raw, dict):
            raise ConfigError(
                f"{path}: 'origins' must be a table of per-origin rules, "
                f"got {type(origins_raw).__name__}"
            )

        origins: dict[str, OriginRule] = {}
        seen_hosts: dict[str, str] = {}
        for origin, rule in origins_raw.items():
            section = f"origins.{origin}"
            if not str(origin).strip():
                raise ConfigError(f"{path}: an origin key cannot be empty")
            if not isinstance(rule, dict):
                raise ConfigError(
                    f"{path}: '{section}' must be a table, got {type(rule).__name__}"
                )
            # A rule that can never match is worse than no rule: the user believes
            # a site is protected and nothing enforces it.
            if "*" in str(origin):
                raise ConfigError(
                    f"{path}: '{section}' - wildcards are not supported and would "
                    f"never match. A rule on 'example.com' already covers every "
                    f"subdomain."
                )
            host = _host_of(origin)
            if not host:
                raise ConfigError(
                    f"{path}: '{section}' has no hostname, so it can never match"
                )
            # Two keys that normalise to the same host would resolve by declaration
            # order, and order-dependent config is config you cannot reason about.
            if host in seen_hosts:
                raise ConfigError(
                    f"{path}: '{section}' and 'origins.{seen_hosts[host]}' are the "
                    f"same host ('{host}'). Keep one."
                )
            seen_hosts[host] = str(origin)
            origins[origin] = _build(OriginRule, rule, path, section)

        _validate(lanes, state, caps, path)
        return cls(lanes=lanes, state=state, caps=caps, origins=origins,
                   root_dir=path.parent)

    # --- derived paths -------------------------------------------------------

    @property
    def root_state_path(self) -> Path:
        """Absolute path to ``root.json``."""
        return (self.root_dir / self.state.root).resolve()

    @property
    def learned_path(self) -> Path:
        """Absolute path to the loop's learned facts."""
        return (self.root_dir / STATE_DIRNAME / LEARNED_FILENAME).resolve()

    def rule_for(self, origin: str) -> OriginRule:
        """Rule for *origin*, matched by host.

        Hosts compare case-insensitively with scheme, userinfo, port, trailing dot and
        path stripped, because those are all the same host and someone writing
        ``example.com`` means every one of them.

        When more than one rule matches, the **most specific** wins:
        ``app.example.com`` beats ``example.com`` whichever is declared first.
        Order-dependent config is config you cannot reason about.
        """
        host = _host_of(origin)
        if not host:
            return OriginRule()

        best: tuple[int, OriginRule] | None = None
        for pattern, rule in self.origins.items():
            candidate = _host_of(pattern)
            if not candidate:
                continue
            matches = host == candidate or host.endswith("." + candidate)
            if matches and (best is None or len(candidate) > best[0]):
                best = (len(candidate), rule)
        return best[1] if best else OriginRule()


def _build(klass: type, data: Any, path: Path, section: str):
    """Construct a config dataclass, rejecting unknown keys and wrong types by name."""
    if data is None:
        return klass()
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path}: '{section}' must be a table, got {type(data).__name__}"
        )

    spec = _FIELD_TYPES[klass]
    for key, value in data.items():
        if key not in spec:
            raise ConfigError(
                f"{path}: unknown key '{section}.{key}'. "
                f"Expected one of: {', '.join(f'{section}.{f}' for f in sorted(spec))}"
            )
        expected, optional = spec[key]
        _check_type(value, expected, optional, f"{path}: '{section}.{key}'")
    return klass(**data)


def _validate(lanes: LaneConfig, state: StateConfig, caps: CapsConfig,
              path: Path) -> None:
    """Range checks, once types are known good.

    Caps get the same treatment as ``lanes.max``. A zero cap is not a tight budget, it
    is an instruction to throw everything away, and a typo should not reach it.
    """
    if lanes.max < 1:
        raise ConfigError(f"{path}: 'lanes.max' must be at least 1, got {lanes.max}")
    if lanes.browser not in _BROWSERS:
        raise ConfigError(
            f"{path}: 'lanes.browser' must be one of {', '.join(_BROWSERS)} "
            f"- got '{lanes.browser}'"
        )
    if not lanes.mcp_version.strip():
        raise ConfigError(f"{path}: 'lanes.mcp_version' cannot be empty")
    # This value is interpolated into the argv inside a generated agent file, so an
    # unconstrained string could inject extra arguments - '--user-data-dir' among
    # them, which conflicts with '--isolated' and fails at startup.
    if not re.fullmatch(r"[A-Za-z0-9._@+-]+", lanes.mcp_version):
        raise ConfigError(
            f"{path}: 'lanes.mcp_version' may contain only letters, digits and "
            f". _ @ + - characters, got '{lanes.mcp_version}'"
        )
    if lanes.max > MAX_LANES:
        raise ConfigError(
            f"{path}: 'lanes.max' is {lanes.max}, above the {MAX_LANES} this "
            f"generates files for. Claude Code runs at most 20 subagents "
            f"concurrently by default, and each lane is a whole browser."
        )
    if not state.root.strip():
        raise ConfigError(f"{path}: 'state.root' cannot be empty")

    for name, value in (
        ("caps.total_bytes", caps.total_bytes),
        ("caps.max_origins", caps.max_origins),
        ("caps.max_indexeddb_per_origin", caps.max_indexeddb_per_origin),
    ):
        if value < 1:
            raise ConfigError(f"{path}: '{name}' must be at least 1, got {value}")


def _host_of(origin: str) -> str:
    """Reduce an origin, URL or bare host to a comparable lowercase hostname."""
    s = str(origin).strip().lower()
    if "://" in s:
        s = s.split("://", 1)[1]
    elif s.startswith("//"):
        s = s[2:]
    for sep in ("/", "?", "#"):
        s = s.split(sep, 1)[0]
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    if s.startswith("["):                      # IPv6 literal
        s = s.split("]", 1)[0] + "]"
    elif ":" in s:
        s = s.split(":", 1)[0]
    return s.rstrip(".")


def _find_upward(start: Path, filename: str) -> Path | None:
    """Return the nearest *filename* at or above *start*, or None.

    Stops at a repository root (a directory holding ``.git``) or at the user's home
    directory. Without a boundary the walk reaches the filesystem root, and a file
    left there by anybody becomes every project's config.
    """
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):  # pragma: no cover - home undefined
        home = None

    for directory in [start, *start.parents]:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            return None
        if home is not None and directory == home:
            return None
    return None


# --- learned facts -----------------------------------------------------------


@dataclass
class Learned:
    """Facts the loop discovered. Machine-written. Never hand-edit.

    Written atomically: a temp file then a rename, so a crash mid-write leaves the
    previous version intact rather than a truncated one.
    """

    rotates: list[str] = field(default_factory=list)
    """Origins observed changing a cookie value mid-run. Single-lane only."""

    sticky: list[str] = field(default_factory=list)
    """Origins where eviction was observed to force a re-login. Never evict."""

    @classmethod
    def load(cls, path: Path) -> Learned:
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: learned facts are corrupt: {exc}") from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(f"{path}: learned facts cannot be read: {exc}") from exc

        if not isinstance(raw, dict):
            raise ConfigError(
                f"{path}: learned facts must be a JSON object, "
                f"got {type(raw).__name__}"
            )
        known = ("rotates", "sticky")
        for key in raw:
            if key not in known:
                raise ConfigError(
                    f"{path}: unknown key '{key}'. Expected one of: {', '.join(known)}"
                )
        # A bare string here iterates character by character and prints one "origin"
        # per letter. Check the shape, not only the keys.
        for key in known:
            value = raw.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ConfigError(
                    f"{path}: '{key}' must be a list of strings, "
                    f"got {type(value).__name__}"
                )
        return cls(rotates=list(raw.get("rotates", [])),
                   sticky=list(raw.get("sticky", [])))

    def save(self, path: Path) -> None:
        """Write atomically, leaving no debris if the rename fails.

        Every failure here becomes a ``ConfigError``, including the directory not
        being creatable and the cleanup itself failing. A writer that raises a raw
        ``PermissionError`` from its own error handler masks the error it was
        reporting.
        """
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass        # cleanup is best-effort; the original error is what matters
            raise ConfigError(f"{path}: cannot write learned facts: {exc}") from exc

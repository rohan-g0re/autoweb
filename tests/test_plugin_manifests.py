"""The plugin's manifests, skills and command, asserted rather than eyeballed.

`claude plugin validate . --strict` catches a malformed manifest. It does not catch the
things that have actually broken plugins in this family: a version that drifted from
`pyproject.toml` so users pinning the plugin got a different package than the one it
ships, a `hooks.json` missing its top-level `"hooks"` wrapper (which fails to load
silently, as a file), a skill whose `name` stopped matching its directory, a markdown
link to a bundled file (read as a cwd-relative path, so it resolves to nothing), an
unquoted `${CLAUDE_PLUGIN_ROOT}` that breaks on the first user with a space in their
home directory, and a machine-specific path or a person's name shipped to strangers.

Each of those is one line of metadata and none of them raises at install time, so each
one gets a test.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 reads TOML through the backport the package declares
    tomllib = pytest.importorskip("tomli")

REPO = Path(__file__).resolve().parent.parent
PLUGIN_JSON = REPO / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO / ".claude-plugin" / "marketplace.json"
HOOKS_JSON = REPO / "hooks" / "hooks.json"
PYPROJECT = REPO / "pyproject.toml"
SKILLS_DIR = REPO / "skills"
COMMANDS_DIR = REPO / "commands"

# Skills that orchestrate rather than inform. They are typed by a user as
# /autoweb:<name> and must not fire on their own.
USER_INVOKED = {"aw-setup", "aw-doctor", "aw-run"}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def bundled_text_files() -> list[Path]:
    """Every markdown file the plugin ships under skills/ and commands/."""
    return sorted(SKILLS_DIR.rglob("*.md")) + sorted(COMMANDS_DIR.rglob("*.md"))


def frontmatter(path: Path) -> dict:
    """Parse the YAML frontmatter block, the way Claude Code reads it.

    Parsed rather than grepped on purpose: a `description` holding a colon has to be
    quoted or the block is not the mapping it looks like, and grep cannot tell the
    difference between valid YAML and a string that happens to contain `name:`.
    """
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{path} has no frontmatter block"
    _, _, rest = text.partition("---")
    block, sep, _ = rest.partition("\n---")
    assert sep, f"{path} has an unterminated frontmatter block"
    data = yaml.safe_load(block)
    assert isinstance(data, dict), f"{path} frontmatter is not a mapping: {data!r}"
    return data


# --------------------------------------------------------------------------- manifests


def test_the_plugin_version_matches_the_package_version():
    """Two files carry the version and users pin on one of them.

    `pyproject.toml` decides what `uv tool install` puts on PATH; `plugin.json` decides
    what Claude Code reports and when it offers an update. Let them drift and the plugin
    advertises a release whose code is a different release, which is unfalsifiable from
    the outside.
    """
    plugin_version = _json(PLUGIN_JSON)["version"]
    package_version = tomllib.loads(PYPROJECT.read_bytes().decode("utf-8"))["project"]["version"]
    assert plugin_version == package_version, (
        f"plugin.json says {plugin_version}, pyproject.toml says {package_version}; "
        "a release bumps both in one commit"
    )


def test_the_plugin_manifest_carries_what_makes_it_load():
    """`homepage` is the one metadata field that stops the plugin loading when it is not
    a URL, and `name` is what `/autoweb:<skill>` is built from."""
    data = _json(PLUGIN_JSON)
    assert data["name"] == "autoweb"
    assert data["homepage"].startswith("https://")
    assert data["repository"].startswith("https://")
    assert data["license"]
    assert isinstance(data["keywords"], list) and data["keywords"]
    assert isinstance(data["author"], dict) and data["author"].get("name")


def test_the_marketplace_entry_points_at_this_repository():
    """This repository doubles as its own marketplace, so the single entry has to name
    the plugin that lives beside it and resolve to the repository root. A `source` that
    is not `./` installs something else, or nothing."""
    market = _json(MARKETPLACE_JSON)
    plugin_name = _json(PLUGIN_JSON)["name"]
    assert market["name"] == plugin_name
    assert market["owner"]["name"]
    entries = market["plugins"]
    assert len(entries) == 1, f"expected exactly one plugin entry, found {len(entries)}"
    (entry,) = entries
    assert entry["name"] == plugin_name
    assert entry["source"] == "./"
    assert entry["category"]
    assert entry["description"]


@pytest.mark.skipif(not HOOKS_JSON.is_file(), reason="hooks/hooks.json not written yet")
def test_the_hooks_file_is_wrapped_and_holds_no_stop_hook():
    """Two failures in one file.

    The unwrapped event map does not load, and it does not complain either: Claude Code
    reads the file, finds no `hooks` key, and registers nothing. The symptom is a hook
    that never runs.

    A Stop hook would be a loop, and AutoWeb deliberately has none: a goal is done when
    `/autoweb:aw-run` reports every assertion passed, and re-running is the user's call.
    """
    data = _json(HOOKS_JSON)
    assert set(data) == {"hooks"}, f"hooks.json must hold exactly a 'hooks' key, got {set(data)}"
    events = data["hooks"]
    assert "Stop" not in events, "a Stop hook is a loop; AutoWeb makes one pass"
    assert "SessionStart" in events


# ------------------------------------------------------------------------------ skills


def test_there_are_skills_to_check():
    """A glob that matches nothing makes every parametrised test below vacuous."""
    assert skill_files(), "no skills/*/SKILL.md found"


@pytest.mark.parametrize("skill", skill_files(), ids=lambda p: p.parent.name)
def test_every_skill_names_its_own_directory_and_says_when_to_use_it(skill):
    """`name` has to equal the directory: the validator rejects a mismatch, and the
    directory is what the invocation path is built from. `description` is what every
    session pays for and the only thing triggering depends on."""
    data = frontmatter(skill)
    assert data.get("name") == skill.parent.name, (
        f"{skill} declares name {data.get('name')!r} in directory {skill.parent.name!r}"
    )
    description = data.get("description")
    assert isinstance(description, str) and description.strip(), f"{skill} has no description"


@pytest.mark.parametrize("name", sorted(USER_INVOKED))
def test_the_orchestrating_skills_do_not_fire_on_their_own(name):
    """These three run commands, dispatch browser lanes and write to `runs/`. They are
    typed deliberately; a model deciding to start one is a browser opening unasked."""
    skill = SKILLS_DIR / name / "SKILL.md"
    assert skill.is_file(), f"{skill} is missing"
    data = frontmatter(skill)
    assert data.get("disable-model-invocation") is True, (
        f"{name} is user-invoked and must set disable-model-invocation: true"
    )


def test_no_skill_links_to_a_bundled_file():
    """A markdown link to a bundled path is read as cwd-relative, so it resolves against
    whatever directory the session is in rather than the plugin. Backtick paths work;
    links do not."""
    pattern = re.compile(r"\]\((?:\./)?(?:references|scripts)/")
    offenders = []
    for path in sorted(SKILLS_DIR.rglob("*.md")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, "markdown links to bundled files:\n" + "\n".join(offenders)


def test_every_plugin_root_reference_is_quoted():
    """`${CLAUDE_PLUGIN_ROOT}` is substituted inside a skill body, and an installed
    plugin lives under a path that very often contains a space. Unquoted, the command
    splits on it and runs the wrong thing."""
    offenders = []
    for path in bundled_text_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"\$\{CLAUDE_PLUGIN_ROOT\}", line):
                if match.start() == 0 or line[match.start() - 1] != '"':
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, "unquoted ${CLAUDE_PLUGIN_ROOT}:\n" + "\n".join(offenders)


def test_nothing_shipped_names_a_person_a_machine_or_another_project():
    """Everything under skills/ and commands/ is read by strangers on their own
    machines. A home path is wrong there by construction, a person's name outlives the
    person, and a name carried over from the repository this shape was copied from is a
    leak of a project the reader has no business knowing about.
    """
    forbidden = {
        "a Windows home path": re.compile(r"[cC]:[\\/]+[Uu]sers[\\/]+"),
        "a Linux home path": re.compile(r"/home/"),
        "a macOS home path": re.compile(r"/Users/"),
        "a personal name": re.compile(r"rohan", re.IGNORECASE),
        "another project's name": re.compile(r"pursuit|sinhgad", re.IGNORECASE),
    }
    offenders = []
    for path in bundled_text_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for label, pattern in forbidden.items():
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO)}:{lineno}: {label}: {line.strip()}"
                    )
    assert not offenders, "machine- or person-specific content:\n" + "\n".join(offenders)

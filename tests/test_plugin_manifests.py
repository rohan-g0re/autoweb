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
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PLUGIN_JSON = REPO / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO / ".claude-plugin" / "marketplace.json"
HOOKS_JSON = REPO / "hooks" / "hooks.json"
PYPROJECT = REPO / "pyproject.toml"
SKILLS_DIR = REPO / "skills"
COMMANDS_DIR = REPO / "commands"
LOOP_DIR = REPO / "plugins" / "autoweb-loop"

# The marketplace's plugins, by entry name, and where each one's source must point.
MARKETPLACE_PLUGINS = {"autoweb": "./", "autoweb-loop": "./plugins/autoweb-loop"}

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


def test_the_marketplace_lists_exactly_the_two_plugins_of_this_repository():
    """This repository is the marketplace for two plugins: `autoweb` at the root and
    `autoweb-loop` under `plugins/`. A `source` that points anywhere else installs
    something else, or nothing, and an extra or missing entry is a plugin users can or
    cannot install by accident."""
    market = _json(MARKETPLACE_JSON)
    assert market["name"] == _json(PLUGIN_JSON)["name"]
    assert market["owner"]["name"]
    entries = {entry["name"]: entry for entry in market["plugins"]}
    assert len(entries) == len(market["plugins"]), "two entries share a name"
    assert {name: e["source"] for name, e in entries.items()} == MARKETPLACE_PLUGINS
    for entry in entries.values():
        assert entry["category"]
        assert entry["description"]
    assert entries["autoweb-loop"]["description"].endswith(
        "Experimental: not yet proven in a live session."
    )


@pytest.mark.parametrize("name", sorted(MARKETPLACE_PLUGINS))
def test_each_marketplace_source_holds_a_manifest_of_the_same_name(name):
    """The entry name is the install id and the manifest name is the skill prefix. When
    they differ, installing by the manifest name fails with `not found in marketplace`."""
    source = REPO / MARKETPLACE_PLUGINS[name]
    manifest = source / ".claude-plugin" / "plugin.json"
    assert manifest.is_file(), f"{MARKETPLACE_PLUGINS[name]} has no .claude-plugin/plugin.json"
    assert _json(manifest)["name"] == name
    if source != REPO:
        assert not (source / ".claude-plugin" / "marketplace.json").exists(), (
            "only the repository root is a marketplace"
        )


@pytest.mark.parametrize("name", sorted(MARKETPLACE_PLUGINS))
def test_every_hook_command_names_a_script_inside_its_own_plugin(name):
    """A hook names its scripts through `${CLAUDE_PLUGIN_ROOT}`, which is that plugin's
    own directory. A script that exists only in the other plugin is a hook that fails on
    every event it is registered for."""
    source = REPO / MARKETPLACE_PLUGINS[name]
    hooks = source / "hooks" / "hooks.json"
    if not hooks.is_file():
        pytest.skip(f"{name} has no hooks")
    named = []
    for matchers in _json(hooks)["hooks"].values():
        for matcher in matchers:
            for hook in matcher["hooks"]:
                named += re.findall(r'"\$\{CLAUDE_PLUGIN_ROOT\}/([^"]+)"', hook["command"])
    assert named, f"{name}: no hook command names a script through ${{CLAUDE_PLUGIN_ROOT}}"
    for rel in named:
        assert (source / rel).is_file(), f"{name}: hooks.json names {rel}, which is not there"


def test_the_root_plugin_needs_no_exclusion_for_the_loop_plugin():
    """The root plugin's source is `./`, so its cache copy holds `plugins/autoweb-loop`
    too. Those files are inert there: Claude Code reads components only from the plugin
    root's own `skills/`, `commands/`, `agents/` and `hooks/hooks.json` (and the paths
    its plugin.json names), never from a nested `.claude-plugin/plugin.json`. No way to
    exclude files from a plugin's copy is documented: the marketplace reference
    (code.claude.com/docs/en/plugins/marketplace-reference, read 2026-11-01) offers
    `sparsePaths` only on the user's marketplace source, not on a plugin. So nothing is
    excluded, and this test holds the condition that makes that safe: the root plugin
    declares no component path that reaches into `plugins/`."""
    manifest = _json(PLUGIN_JSON)
    for key in ("commands", "agents", "skills", "hooks", "mcpServers", "outputStyles"):
        assert "plugins/" not in json.dumps(manifest.get(key, "")), key
    assert "plugins/" not in HOOKS_JSON.read_text(encoding="utf-8")
    assert not (REPO / "skills" / "aw-loop").exists(), "aw-loop belongs to autoweb-loop"


def test_the_loop_py_sh_is_the_root_py_sh_but_for_its_message_prefix():
    """Two plugins, two copies of the Python finder, because `${CLAUDE_PLUGIN_ROOT}` is a
    different directory for each and neither may reach into the other. They are kept one
    file in all but name: every code line must match once `autoweb-loop:` is read as
    `autoweb:`. Comments may differ, so each can say where its remedy lives. A fix to one
    that is not made to the other fails here."""
    root = (REPO / "scripts" / "py.sh").read_text(encoding="utf-8").splitlines()
    loop = (LOOP_DIR / "scripts" / "py.sh").read_text(encoding="utf-8").splitlines()

    def code(lines):
        return [line for line in lines if not line.lstrip().startswith("#")]

    assert root[0] == loop[0] == "#!/bin/sh"
    assert [line.replace("'autoweb-loop: ", "'autoweb: ") for line in code(loop)] == code(root)
    messages = [line for line in code(loop) if "autoweb-loop: " in line]
    assert len(messages) == 3 and all("printf" in line for line in messages), messages
    assert not [line for line in code(loop) if "'autoweb: " in line]
    assert "aw_setup.py" not in "\n".join(loop), "aw_setup.py ships only with autoweb"


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


def test_the_plugin_root_ships_no_mcp_server_because_it_would_start_in_every_session():
    """Claude Code loads a `.mcp.json` at a plugin's root in every session on the machine.

    Shipped here, it started `plugin:autoweb:playwright` - an npx Playwright server - in
    every project of every user who installed the plugin, including the ones that never
    ran AutoWeb. `/autoweb:aw-setup` writes the server into each project instead, this
    repository included, so the file may exist on disk here but must never be tracked,
    and `git add .` must not be able to track it.
    """
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    listed = subprocess.run([git, "ls-files", "--", ".mcp.json"], cwd=REPO,
                            capture_output=True, text=True, check=False)
    if listed.returncode != 0:
        pytest.skip("not a git checkout")
    assert listed.stdout.strip() == "", "a tracked .mcp.json ships an MCP server to everyone"
    ignored = subprocess.run([git, "check-ignore", "-q", "--no-index", "--", ".mcp.json"],
                             cwd=REPO, capture_output=True, text=True, check=False)
    assert ignored.returncode == 0, ".gitignore must keep the generated .mcp.json out of git"


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

    Names are not spelled out here, because a guard that lists them is itself the
    leak. They come from the gitignored `.privacy-words.local` at the repository root,
    one case-insensitive literal per line, `#` for comments. tests/test_plugin_privacy.py
    applies the same file to every tracked file.
    """
    forbidden = {
        "a Windows home path": re.compile(r"[cC]:[\\/]+[Uu]sers[\\/]+"),
        "a Linux home path": re.compile(r"/home/"),
        "a macOS home path": re.compile(r"/Users/"),
    }
    words_file = REPO / ".privacy-words.local"
    if words_file.is_file():
        for word in words_file.read_text(encoding="utf-8").splitlines():
            word = word.strip()
            if word and not word.startswith("#"):
                forbidden[f"a private word ({len(word)} chars)"] = re.compile(
                    re.escape(word), re.IGNORECASE
                )
    offenders = []
    for path in bundled_text_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for label, pattern in forbidden.items():
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO)}:{lineno}: {label}: {line.strip()}"
                    )
    assert not offenders, "machine- or person-specific content:\n" + "\n".join(offenders)

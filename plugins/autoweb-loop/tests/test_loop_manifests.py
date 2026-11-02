"""Manifests, the hook file, the skill and the tree, asserted rather than eyeballed.

`claude plugin validate` catches a malformed manifest. It does not catch a `hooks.json`
without its top-level `"hooks"` wrapper (which loads as nothing, silently), a hook command
naming a script that is not there, a skill that fires on its own, or an unquoted
`${CLAUDE_PLUGIN_ROOT}`. Each is one line of metadata and none raises at install time.

The marketplace entry, the `py.sh` twin and personal data are checked at the repository
root, by tests/test_plugin_manifests.py and tests/test_plugin_privacy.py, because they are
facts about the repository rather than about this plugin.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
PLUGIN_JSON = PLUGIN / ".claude-plugin" / "plugin.json"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
CHANGELOG = PLUGIN / "CHANGELOG.md"
SKILLS_DIR = PLUGIN / "skills"
NAME = "autoweb-loop"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def frontmatter(path: Path) -> dict:
    """The frontmatter's top-level `key: value` lines. Values that are quoted are
    unquoted; the skill needs no nesting, so no YAML parser is needed here."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no frontmatter block"
    block, sep, _ = text[4:].partition("\n---\n")
    assert sep, f"{path} has an unterminated frontmatter block"
    data = {}
    for line in block.splitlines():
        key, colon, value = line.partition(":")
        assert colon and key == key.strip(), f"{path}: not a top-level key: {line!r}"
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        elif value[:1] != '"' and ": " in value:
            pytest.fail(f"{path}: {key} holds ': ' and must be quoted")
        data[key] = {"true": True, "false": False}.get(value, value)
    return data


# ---------------------------------------------------------------------- manifests


def test_the_plugin_manifest_names_the_plugin_and_carries_what_makes_it_load():
    data = _json(PLUGIN_JSON)
    assert data["name"] == NAME
    assert data["version"] == "0.1.0"
    assert data["homepage"].startswith("https://"), "a non-URL homepage stops the plugin loading"
    assert data["repository"].startswith("https://")
    assert data["license"] == "Apache-2.0"
    assert data["author"]["name"]
    assert isinstance(data["keywords"], list) and data["keywords"]


def test_the_version_has_a_changelog_entry():
    """The version lives in plugin.json alone now, so the changelog is what a release
    has to agree with."""
    version = _json(PLUGIN_JSON)["version"]
    text = CHANGELOG.read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", text,
                     re.MULTILINE), f"CHANGELOG.md has no entry for {version}"


def test_the_plugin_carries_no_marketplace_or_project_files_of_its_own():
    """The repository root is the marketplace and holds the one pyproject, lock file,
    ignore rules and authoring standard. A second copy here would be read by nobody and
    disagree with the first."""
    for rel in (".claude-plugin/marketplace.json", "pyproject.toml", "uv.lock", ".gitignore",
                ".gitattributes", "AGENTS.md"):
        assert not (PLUGIN / rel).exists(), f"{rel} belongs at the repository root"


# -------------------------------------------------------------------------- hooks


def test_hooks_json_is_wrapped_and_holds_one_stop_hook_only():
    data = _json(HOOKS_JSON)
    assert list(data) == ["hooks"], "the unwrapped event map loads as nothing"
    assert list(data["hooks"]) == ["Stop"]
    (matcher,) = data["hooks"]["Stop"]
    (hook,) = matcher["hooks"]
    assert hook["type"] == "command"
    assert hook["timeout"] == 10
    assert hook["command"] == (
        'bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/loop_stop.py"'
    )


def test_every_script_the_hook_names_exists():
    command = _json(HOOKS_JSON)["hooks"]["Stop"][0]["hooks"][0]["command"]
    named = re.findall(r'"\$\{CLAUDE_PLUGIN_ROOT\}/([^"]+)"', command)
    assert named == ["scripts/py.sh", "scripts/loop_stop.py"]
    for rel in named:
        assert (PLUGIN / rel).is_file(), f"hooks.json names {rel}, which is not there"


def test_py_sh_has_lf_endings_and_keeps_the_311_floor():
    raw = (PLUGIN / "scripts" / "py.sh").read_bytes()
    assert b"\r" not in raw
    assert b"sys.version_info >= (3, 11)" in raw
    attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"^\*\.sh\s+text\s+eol=lf$", attrs, re.MULTILINE)


# ------------------------------------------------------------------------- skills


def test_there_is_exactly_one_skill_and_it_is_aw_loop():
    skills = sorted(p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md"))
    assert skills == ["aw-loop"]
    assert not (PLUGIN / "commands").exists(), "the one command is the aw-loop skill"
    assert not (PLUGIN / "agents").exists()


def test_the_skill_is_user_invoked_and_names_its_directory():
    data = frontmatter(SKILLS_DIR / "aw-loop" / "SKILL.md")
    assert data["name"] == "aw-loop"
    assert data["name"].startswith("aw-")
    assert data["disable-model-invocation"] is True
    assert data["description"].strip()
    assert data["argument-hint"].strip()


def test_every_plugin_root_reference_in_the_skill_is_quoted():
    offenders = []
    for path in SKILLS_DIR.rglob("*.md"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"\$\{CLAUDE_PLUGIN_ROOT\}", line):
                if match.start() == 0 or line[match.start() - 1] != '"':
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "unquoted ${CLAUDE_PLUGIN_ROOT}:\n" + "\n".join(offenders)


def test_no_skill_links_to_a_bundled_file():
    pattern = re.compile(r"\]\((?:\./)?(?:references|scripts)/")
    for path in SKILLS_DIR.rglob("*.md"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path

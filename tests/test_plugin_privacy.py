"""Nothing personal and nothing browser-related may ever be tracked.

A plugin install clones this repository, so the tracked tree is exactly what
every user receives. These tests run `git ls-files` and fail the suite the
moment an identity file, a browser profile, a run artifact, a local settings
file or a personal reference lands in it. They are the durable form of the
one-time scrub done for 0.2.0.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Paths (git-style, forward slashes) that must never be tracked.
FORBIDDEN_PATHS = [
    "root.json", "root.json.*", "lane-*.json", "*.storagestate.json",
    ".playwright-mcp/*", "runs/*", "personal_docs/*", "transcripts/*",
    "screenshots/*", "traces/*", "browser-profile*/*", "profiles/*",
    ".autoweb/*", "CLAUDE.local.md", ".mcp.local.json",
    ".claude/settings.local.json", ".claude/agents/lane-*.md",
    ".env", ".env.*", "*.pem", "*.key", "credentials.json", "**/secrets/*",
    "graphify-out/*", "__pycache__/*", "*.pyc",
]

# Strings that identify the author's own accounts, machines or data. GitHub
# URLs naming the repository owner are allowed: they name the repo, not data.
FORBIDDEN_TEXT = [
    (re.compile(r"(?i)pursuit"), "the author's profile directory names"),
    (re.compile(r"(?i)sinhgad|sihgad"), "the author's school"),
    (re.compile(r"/home/rohan|C:[\\/]+Users[\\/]+rohan|[\\/]Users[\\/]rohan"), "a home path"),
    (re.compile(r"(?<![\w/\-])rohan(?![\w\-])"), "a personal name outside a GitHub URL"),
]
TEXT_SUFFIXES = {".md", ".py", ".toml", ".json", ".yml", ".yaml", ".sh", ".txt", ".cfg", ".ini"}
# Files whose only "rohan" is the repository owner in a URL are checked line by
# line; a line that contains the owner only inside github.com/rohan-g0re passes.
GITHUB_OWNER = re.compile(r"github\.com/rohan-g0re")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def test_no_identity_profile_run_or_local_file_is_tracked():
    bad = [f for f in tracked_files()
           if any(fnmatch.fnmatch(f, pat) for pat in FORBIDDEN_PATHS)]
    assert not bad, f"tracked files that must never ship: {bad}"


def test_no_personal_reference_in_any_tracked_text_file():
    hits = []
    me = Path(__file__).resolve()
    for rel in tracked_files():
        path = REPO / rel
        if path.suffix.lower() not in TEXT_SUFFIXES or rel == "uv.lock":
            continue
        if path.resolve() == me:
            continue  # this file holds the patterns themselves
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            for pattern, what in FORBIDDEN_TEXT:
                if pattern.search(line) and not GITHUB_OWNER.search(line):
                    hits.append(f"{rel}:{n}: {what}: {line.strip()[:80]}")
    assert not hits, "personal references in tracked files:\n" + "\n".join(hits)


def test_gitignore_still_covers_every_browser_and_identity_artifact():
    """The ignore rules are the first line of defence; keep them."""
    text = (REPO / ".gitignore").read_text(encoding="utf-8", errors="replace")
    for needle in ("root.json", "lane-*.json", ".playwright-mcp/", "runs/",
                   "personal_docs/", ".autoweb/", "CLAUDE.local.md",
                   ".claude/settings.local.json", ".claude/agents/lane-*.md"):
        assert needle in text, f".gitignore no longer lists {needle}"

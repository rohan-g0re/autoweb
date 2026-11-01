"""Nothing personal and nothing browser-related may ever be tracked.

A plugin install clones this repository, so the tracked tree is exactly what
every user receives. These tests run `git ls-files` and fail the suite the
moment an identity file, a browser profile, a run artifact, a local settings
file or a personal reference lands in it. They are the durable form of the
one-time scrub done for 0.2.0.

The checks are generic on purpose. A guard that spells out the author's name,
school or old project names is itself the leak, so this file names nobody:

  * a home path (`/home/<name>/`, `/Users/<name>/`, `C:/Users/<name>/`) fails
    unless `<name>` is a placeholder in HOME_PLACEHOLDERS or is written in
    angle brackets, which no real account name can contain;
  * an email address fails unless its domain is in ALLOWED_EMAIL_DOMAINS;
  * a long base64 or hex run on a line holding `"value"` fails, because that is
    what a cookie or storage value pasted from a state file looks like;
  * every word in the optional file `.privacy-words.local` fails.

`.privacy-words.local` sits at the repository root and is gitignored, so each
developer keeps their own private words on their own machine and the list never
ships. One literal per line, matched case-insensitively anywhere in any tracked
file. Blank lines and lines starting with `#` are skipped. Without the file the
word check does nothing and the generic checks still run.

Every tracked file is scanned, whatever its extension. Only `uv.lock` (hashes
and wheel URLs) and `LICENSE` (a copyright line) are skipped, by name.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path, PurePosixPath

import pytest

REPO = Path(__file__).resolve().parent.parent
WORDS_FILE = ".privacy-words.local"
SKIPPED_FILES = {"uv.lock", "LICENSE"}

# A tracked path fails if any of its directory components matches one of these.
FORBIDDEN_DIRS = [
    ".playwright-mcp", "runs", "graphify-out", "personal_docs", "transcripts",
    "screenshots", "traces", "browser-profile*", "*browser-profile*", "profiles",
    ".autoweb", "__pycache__", "secrets",
]
# A tracked path fails if its file name matches one of these, at any depth.
FORBIDDEN_NAMES = [
    "root.json", "root.json.*", "lane-*.json", "lane-*.json.*", "*.storagestate.json",
    "snap*.md", "*.har", "*.png", "CLAUDE.local.md", "AGENTS.local.md",
    ".mcp.local.json", "*.local", ".env", ".env.*", "*.pem", "*.key",
    "credentials.json", "*.pyc",
]
# A tracked path fails if the whole path matches one of these.
FORBIDDEN_PATHS = [".claude/settings.local.json", ".claude/agents/lane-*.md"]
ALLOWED_NAMES = {".env.example"}

# The placeholder account names the tree already uses in example paths.
HOME_PLACEHOLDERS = ["example", "John Smith"]
HOME_PATH = re.compile(r"/home/|/Users/|(?i:[a-z]:[\\/]+users[\\/]+)")
PLACEHOLDER = re.compile(
    "(?:" + "|".join(re.escape(p) for p in HOME_PLACEHOLDERS) + r")(?![\w.\-])"
)
# A home prefix followed by one of these characters is followed by an account name.
# Anything else after it - an <angle-bracket> name, a $VARIABLE, a quote, the end of
# the line - names nobody.
ACCOUNT_NAME_START = re.compile(r"[\w.\-@]")

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
ALLOWED_EMAIL_DOMAINS = ("example.com", "users.noreply.github.com")
ALLOWED_EMAILS = {"noreply@anthropic.com"}

COOKIE_LINE = re.compile(r"\"value\"")
COOKIE_VALUE = re.compile(r"[A-Za-z0-9+/_\-]{32,}={0,2}")


def tracked_files(repo: Path = REPO) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=False,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def private_words(repo: Path = REPO) -> list[str]:
    path = repo / WORDS_FILE
    if not path.is_file():
        return []
    words = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        word = line.strip()
        if word and not word.startswith("#"):
            words.append(word)
    return words


def forbidden_tracked_paths(repo: Path = REPO) -> list[str]:
    bad = []
    for rel in tracked_files(repo):
        parts = PurePosixPath(rel).parts
        name = parts[-1]
        if name in ALLOWED_NAMES:
            continue
        if (any(fnmatch.fnmatch(d, pat) for d in parts[:-1] for pat in FORBIDDEN_DIRS)
                or any(fnmatch.fnmatch(name, pat) for pat in FORBIDDEN_NAMES)
                or any(fnmatch.fnmatch(rel, pat) for pat in FORBIDDEN_PATHS)):
            bad.append(rel)
    return bad


def email_is_allowed(address: str, domain: str) -> bool:
    if address.lower() in ALLOWED_EMAILS:
        return True
    domain = domain.lower()
    return any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)


def line_problems(line: str, words: list[str]) -> list[str]:
    found = []
    for m in HOME_PATH.finditer(line):
        if (ACCOUNT_NAME_START.match(line, m.end())
                and not PLACEHOLDER.match(line, m.end())):
            found.append("a home path naming an account")
            break
    for m in EMAIL.finditer(line):
        if not email_is_allowed(m.group(0), m.group(1)):
            found.append("an email address")
            break
    if COOKIE_LINE.search(line):
        for m in COOKIE_VALUE.finditer(line):
            run = m.group(0)
            if re.search(r"[A-Za-z]", run) and re.search(r"[0-9]", run):
                found.append("a cookie-shaped value")
                break
    lowered = line.lower()
    found.extend(f"a private word from {WORDS_FILE}" for w in words if w.lower() in lowered)
    return found


def personal_references(repo: Path = REPO) -> list[str]:
    words = private_words(repo)
    hits = []
    for rel in tracked_files(repo):
        if rel in SKIPPED_FILES:
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for what in line_problems(line, words):
                hits.append(f"{rel}:{n}: {what}: {line.strip()[:80]}")
    return hits


def test_no_identity_profile_run_or_local_file_is_tracked():
    bad = forbidden_tracked_paths()
    assert not bad, f"tracked files that must never ship: {bad}"


def test_no_personal_reference_in_any_tracked_file():
    hits = personal_references()
    assert not hits, "personal references in tracked files:\n" + "\n".join(hits)


def test_gitignore_still_covers_every_browser_and_identity_artifact():
    """The ignore rules are the first line of defence; keep them."""
    text = (REPO / ".gitignore").read_text(encoding="utf-8", errors="replace")
    for needle in ("root.json", "lane-*.json", ".playwright-mcp/", "runs/",
                   "personal_docs/", ".autoweb/", "CLAUDE.local.md",
                   ".claude/settings.local.json", ".claude/agents/lane-*.md",
                   WORDS_FILE, "graphify-out/", "snap*.md", "*.har", "*.png"):
        assert needle in text, f".gitignore no longer lists {needle}"


# --- the guards themselves, proven against a scratch repository ---------------

def _scratch_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "--", *files], cwd=tmp_path, check=True)
    return tmp_path


def test_a_word_from_the_private_words_file_fails_wherever_it_is_tracked(tmp_path):
    word = "zebra" + "cornflake"
    repo = _scratch_repo(tmp_path, {"notes.txt": f"see {word.title()} here\n",
                                    "data.bin": "nothing\n"})
    assert personal_references(repo) == []
    (repo / WORDS_FILE).write_text(f"# private words\n\n{word}\n", encoding="utf-8")
    hits = personal_references(repo)
    assert len(hits) == 1 and hits[0].startswith("notes.txt:1:"), hits


def test_a_comment_line_in_the_private_words_file_is_not_a_word(tmp_path):
    repo = _scratch_repo(tmp_path, {"notes.txt": "a private words list\n"})
    (repo / WORDS_FILE).write_text("# private words list\n", encoding="utf-8")
    assert personal_references(repo) == []


def test_a_home_path_naming_an_account_fails_and_a_placeholder_passes(tmp_path):
    repo = _scratch_repo(tmp_path, {
        "a.md": "lives in " + "/" + "home/alice/project\n",
        "b.md": "C:" + "\\Users\\bob\\AppData\n",
        "c.md": "/" + "Users/carol/Library\n",
        "ok.md": "C:/" + "Users/John Smith/AppData and C:\\" + "Users\\example\\x"
                 " and /" + "home/<you>/project and /" + "home/$USER/x\n",
    })
    assert sorted(h.split(":")[0] for h in personal_references(repo)) == ["a.md", "b.md", "c.md"]


def test_an_email_address_fails_unless_it_is_a_placeholder_or_a_noreply(tmp_path):
    at = "@"
    repo = _scratch_repo(tmp_path, {
        "bad.md": f"mail alice{at}mail.test\n",
        "ok.md": f"pw{at}example.com noreply{at}anthropic.com 1+x{at}users.noreply.github.com"
                 " @playwright/mcp@0.0.83\n",
    })
    assert [h.split(":")[0] for h in personal_references(repo)] == ["bad.md"]


def test_a_cookie_shaped_value_fails_on_a_value_line(tmp_path):
    secret = "AQEDAT" + "x9Kq" * 9
    repo = _scratch_repo(tmp_path, {
        "bad.json": '{"name": "li_at", "value": "' + secret + '"}\n',
        "ok.json": '{"name": "t", "value": "1"}\n' + "no value key " + secret + "\n",
    })
    assert [h.split(":")[0] for h in personal_references(repo)] == ["bad.json"]


def test_a_browser_artifact_or_the_private_words_file_fails_when_tracked(tmp_path):
    repo = _scratch_repo(tmp_path, {
        "sub/root.json": "{}", "lane-2.json": "{}", "runs/x.md": "", "graphify-out/g.json": "",
        ".playwright-mcp/p.yml": "", "snap1.md": "", "trace.har": "", "docs/shot.png": "",
        WORDS_FILE: "", ".env.example": "", "docs/ok.md": "",
    })
    assert sorted(forbidden_tracked_paths(repo)) == sorted([
        "sub/root.json", "lane-2.json", "runs/x.md", "graphify-out/g.json",
        ".playwright-mcp/p.yml", "snap1.md", "trace.har", "docs/shot.png", WORDS_FILE,
    ])

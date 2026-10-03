"""Lane agent file tests.

The property that matters is not "a file was written". It is that each lane gets an
**inline** `mcpServers` block, because that is the single thing standing between five
independent browsers and five subagents fighting over one tab.
"""

from __future__ import annotations

import pytest

from autoweb.config import Config
from autoweb.lanes import (
    AGENTS_DIRNAME,
    GENERATED_MARKER,
    existing,
    lane_markdown,
    mcp_args,
    sync,
)


def config_at(tmp_path, body: str = "") -> Config:
    (tmp_path / "autoweb.toml").write_text(body, encoding="utf-8")
    return Config.load(tmp_path)


# --- the isolation property -------------------------------------------------


def test_server_is_defined_inline_not_referenced(tmp_path):
    """An inline definition starts a fresh server; a string reference shares the
    parent's, which would put every lane on one browser and one current tab."""
    body = lane_markdown(1, config_at(tmp_path))
    assert "mcpServers:" in body
    assert "command: npx" in body
    assert "args: [" in body


def test_every_lane_gets_its_own_file(tmp_path):
    cfg = config_at(tmp_path, "[lanes]\nmax = 4\n")
    sync(cfg)
    files = sorted((tmp_path / AGENTS_DIRNAME).glob("lane-*.md"))
    assert [f.stem for f in files] == ["lane-1", "lane-2", "lane-3", "lane-4"]


def test_lane_names_match_their_filenames(tmp_path):
    """Claude Code addresses a subagent by the `name` in its frontmatter."""
    cfg = config_at(tmp_path, "[lanes]\nmax = 3\n")
    sync(cfg)
    for index in (1, 2, 3):
        text = (tmp_path / AGENTS_DIRNAME / f"lane-{index}.md").read_text()
        assert f"name: lane-{index}" in text


# --- the argv ---------------------------------------------------------------


def test_isolated_is_passed_by_default(tmp_path):
    """--isolated is what makes lanes safe in parallel: no shared profile, no lock."""
    assert "--isolated" in mcp_args(config_at(tmp_path))


def test_isolated_can_be_turned_off(tmp_path):
    cfg = config_at(tmp_path, "[lanes]\nisolated = false\n")
    assert "--isolated" not in mcp_args(cfg)


def test_mcp_version_is_pinned_from_config(tmp_path):
    cfg = config_at(tmp_path, '[lanes]\nmcp_version = "0.0.99"\n')
    assert mcp_args(cfg)[0] == "@playwright/mcp@0.0.99"


def test_no_latest_tag_anywhere(tmp_path):
    """`@latest` is how a first run hangs and a working setup breaks later."""
    assert "@latest" not in " ".join(mcp_args(config_at(tmp_path)))


def test_browser_choice_is_passed_through(tmp_path):
    """ARM Linux has no Google Chrome, so this has to be settable."""
    cfg = config_at(tmp_path, '[lanes]\nbrowser = "chromium"\n')
    args = mcp_args(cfg)
    assert args[args.index("--browser") + 1] == "chromium"


def test_storage_state_path_is_absolute(tmp_path):
    """A relative path would resolve against the MCP server's working directory,
    which the agent file cannot see. Silently seeding nothing is the worst outcome."""
    cfg = config_at(tmp_path)
    args = mcp_args(cfg)
    state = args[args.index("--storage-state") + 1]
    assert state.endswith("root.json")
    assert ":" in state or state.startswith("/")        # drive letter or posix root


# --- sync behaviour ---------------------------------------------------------


def test_second_sync_reports_unchanged(tmp_path):
    cfg = config_at(tmp_path, "[lanes]\nmax = 2\n")
    sync(cfg)
    assert {r.action for r in sync(cfg)} == {"unchanged"}


def test_lowering_the_ceiling_retires_extra_lanes(tmp_path):
    (tmp_path / "autoweb.toml").write_text("[lanes]\nmax = 5\n", encoding="utf-8")
    sync(Config.load(tmp_path))
    (tmp_path / "autoweb.toml").write_text("[lanes]\nmax = 2\n", encoding="utf-8")
    results = sync(Config.load(tmp_path))
    assert sorted(r.index for r in results if r.action == "removed") == [3, 4, 5]
    assert not (tmp_path / AGENTS_DIRNAME / "lane-5.md").exists()


def test_a_hand_written_lane_file_is_never_deleted(tmp_path):
    """Config is not permission to delete somebody's work."""
    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    agents = tmp_path / AGENTS_DIRNAME
    agents.mkdir(parents=True)
    mine = agents / "lane-9.md"
    mine.write_text("---\nname: lane-9\n---\nmy own agent\n", encoding="utf-8")

    sync(cfg)
    assert mine.exists()
    assert mine.read_text() == "---\nname: lane-9\n---\nmy own agent\n"


def test_generated_files_carry_a_marker(tmp_path):
    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    sync(cfg)
    assert GENERATED_MARKER in (tmp_path / AGENTS_DIRNAME / "lane-1.md").read_text()


def test_changing_config_updates_existing_files(tmp_path):
    (tmp_path / "autoweb.toml").write_text("[lanes]\nmax = 1\n", encoding="utf-8")
    sync(Config.load(tmp_path))
    (tmp_path / "autoweb.toml").write_text(
        '[lanes]\nmax = 1\nmcp_version = "0.0.99"\n', encoding="utf-8")
    results = sync(Config.load(tmp_path))
    assert [r.action for r in results] == ["updated"]
    assert "0.0.99" in (tmp_path / AGENTS_DIRNAME / "lane-1.md").read_text()


# --- listing ----------------------------------------------------------------


def test_listing_empty_directory(tmp_path):
    assert existing(config_at(tmp_path)) == []


def test_listing_distinguishes_generated_from_hand_written(tmp_path):
    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    sync(cfg)
    agents = tmp_path / AGENTS_DIRNAME
    (agents / "lane-7.md").write_text("mine\n", encoding="utf-8")
    actions = {lane.path.name: lane.action for lane in existing(cfg)}
    assert actions == {"lane-1.md": "generated", "lane-7.md": "hand-written"}


def test_listing_is_ordered_numerically(tmp_path):
    """lane-10 sorts after lane-9, not between lane-1 and lane-2."""
    cfg = config_at(tmp_path, "[lanes]\nmax = 11\n")
    sync(cfg)
    assert [lane.index for lane in existing(cfg)] == list(range(1, 12))


@pytest.mark.parametrize("name", ["lane-.md", "lane-x.md", "lanes-1.md", "lane.md"])
def test_files_that_only_look_like_lanes_are_ignored(tmp_path, name):
    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    sync(cfg)
    (tmp_path / AGENTS_DIRNAME / name).write_text("x\n", encoding="utf-8")
    assert [lane.path.name for lane in existing(cfg)] == ["lane-1.md"]

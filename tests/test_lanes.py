"""Lane agent file tests.

The property that matters is not "a file was written". It is that each lane gets an
**inline** `mcpServers` block, because that is the single thing standing between five
independent browsers and five subagents fighting over one tab.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from autoweb.config import Config
from autoweb.lanes import (
    AGENTS_DIRNAME,
    GENERATED_MARKER,
    SERVER_NAME,
    existing,
    lane_markdown,
    mcp_args,
    sync,
)


def config_at(tmp_path, body: str = "") -> Config:
    (tmp_path / "autoweb.toml").write_text(body, encoding="utf-8")
    return Config.load(tmp_path)


# --- the isolation property -------------------------------------------------


def frontmatter(body: str) -> dict:
    """Parse the YAML frontmatter the way Claude Code will."""
    marker = "---\n"
    assert body.startswith(marker)
    return yaml.safe_load(body.split(marker, 2)[1])


def test_frontmatter_is_valid_yaml(tmp_path):
    fm = frontmatter(lane_markdown(1, config_at(tmp_path)))
    assert fm["name"] == "lane-1"
    assert isinstance(fm["description"], str) and fm["description"]


def test_mcp_servers_is_a_list_not_a_mapping(tmp_path):
    """THE load-bearing assertion, and the one an earlier grep-based test missed.

    Claude Code documents `mcpServers` as a sequence of single-key mappings. Given a
    mapping it logs nothing and silently ignores the block, so the lane falls back to
    the session's shared server: one browser, one current tab, every lane fighting
    over it. That is the exact failure lanes exist to prevent, and a test that greps
    for "mcpServers:" passes while it happens.
    """
    fm = frontmatter(lane_markdown(1, config_at(tmp_path)))
    assert isinstance(fm["mcpServers"], list), (
        "mcpServers must be a list of single-key mappings; a mapping is ignored "
        "silently and the lane shares the parent's browser"
    )


def test_inline_server_round_trips_to_the_exact_argv(tmp_path):
    """What YAML parsing yields must equal what mcp_args() produced."""
    cfg = config_at(tmp_path)
    entry = frontmatter(lane_markdown(1, cfg))["mcpServers"][0]
    assert list(entry) == [SERVER_NAME]
    server = entry[SERVER_NAME]
    assert server["command"] == "npx"
    assert server["type"] == "stdio"
    assert server["args"] == mcp_args(cfg)


def test_lane_server_is_not_called_playwright(tmp_path):
    """The orchestrator's own .mcp.json already connects a server named
    `playwright`. Which of two identically-named servers a subagent resolves is
    undocumented, so lanes use a distinct name and there is nothing to resolve.

    `.claude/settings.json` must allow that name, or every browser call prompts.
    """
    entry = frontmatter(lane_markdown(1, config_at(tmp_path)))["mcpServers"][0]
    assert SERVER_NAME != "playwright"
    assert list(entry) == [SERVER_NAME]

    settings = json.loads(
        (Path(__file__).parent.parent / ".claude/settings.json").read_text())
    allowed = settings["permissions"]["allow"]
    assert any(rule == f"mcp__{SERVER_NAME}" or rule.startswith(f"mcp__{SERVER_NAME}__")
               for rule in allowed), f"settings.json does not allow mcp__{SERVER_NAME}"


def test_isolated_and_user_data_dir_are_never_both_passed(tmp_path):
    """Playwright-mcp refuses that combination at startup, not at first use."""
    for name, body in (("iso", ""), ("persistent", "[lanes]\nisolated = false\n")):
        root = tmp_path / name
        root.mkdir()
        args = mcp_args(config_at(root, body))
        assert not ("--isolated" in args and "--user-data-dir" in args)


@pytest.mark.parametrize("path_fragment", ["with space", "unicode_éü"])
def test_paths_with_spaces_and_unicode_survive_yaml(tmp_path, path_fragment):
    """A path that breaks YAML quoting would seed the lane with nothing."""
    nested = tmp_path / path_fragment
    nested.mkdir()
    (nested / "autoweb.toml").write_text("", encoding="utf-8")
    cfg = Config.load(nested)
    entry = frontmatter(lane_markdown(1, cfg))["mcpServers"][0]
    assert entry[SERVER_NAME]["args"] == mcp_args(cfg)


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


def test_mcp_version_cannot_inject_extra_arguments(tmp_path):
    """mcp_version is interpolated into a YAML argv, so an unconstrained string
    could smuggle in `--user-data-dir`, which conflicts with `--isolated` and makes
    the server fail at startup."""
    from autoweb.config import ConfigError

    (tmp_path / "autoweb.toml").write_text(
        '[lanes]\nmcp_version = \'0.0.83", "--user-data-dir", "C:/x\'\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="mcp_version"):
        Config.load(tmp_path)


def test_lane_ceiling_is_enforced(tmp_path):
    """A four-digit lanes.max is a typo, not an intention."""
    from autoweb.config import ConfigError

    (tmp_path / "autoweb.toml").write_text("[lanes]\nmax = 1000000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="lanes.max"):
        Config.load(tmp_path)


def test_hand_written_lane_inside_the_ceiling_is_not_overwritten(tmp_path):
    """Raising lanes.max is not permission to destroy an agent somebody wrote.

    The marker guarded only the removal loop, so a marker-less lane-3.md inside
    1..max was silently replaced and reported as "updated".
    """
    cfg = config_at(tmp_path, "[lanes]\nmax = 5\n")
    agents = tmp_path / AGENTS_DIRNAME
    agents.mkdir(parents=True)
    mine = agents / "lane-3.md"
    original = "---\nname: lane-3\n---\nmy own agent\n"
    mine.write_text(original, encoding="utf-8")

    results = sync(cfg)
    assert mine.read_text() == original
    assert [r.action for r in results if r.index == 3] == ["skipped"]


def test_leading_zero_names_are_not_lanes(tmp_path):
    """lane-01.md would otherwise claim index 1 alongside lane-1.md, so one of them
    could never be retired."""
    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    sync(cfg)
    (tmp_path / AGENTS_DIRNAME / "lane-01.md").write_text("x\n", encoding="utf-8")
    assert [lane.path.name for lane in existing(cfg)] == ["lane-1.md"]


def test_unreadable_lane_file_is_left_alone(tmp_path):
    """A directory where a lane file should be must not crash sync, and must not be
    mistaken for a generated file and deleted."""
    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    agents = tmp_path / AGENTS_DIRNAME
    agents.mkdir(parents=True)
    (agents / "lane-4.md").mkdir()

    sync(cfg)
    assert (agents / "lane-4.md").is_dir()


def test_agents_path_blocked_by_a_file_is_a_clean_error(tmp_path):
    from autoweb.lanes import LaneError

    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    (tmp_path / ".claude").write_text("not a directory", encoding="utf-8")
    with pytest.raises(LaneError, match="is a file, not a directory"):
        sync(cfg)


# --- the persistent-mode escape hatch ---------------------------------------


def _persistent(tmp_path, max_lanes=1):
    return config_at(tmp_path, f"[lanes]\nisolated = false\nmax = {max_lanes}\n")


def test_persistent_lanes_get_one_profile_directory_each(tmp_path):
    """Without --isolated every lane would share the one default profile directory,
    and a second Chromium on a profile already in use fails silently on Windows."""
    cfg = _persistent(tmp_path, 3)
    dirs = [mcp_args(cfg, i)[mcp_args(cfg, i).index("--user-data-dir") + 1]
            for i in (1, 2, 3)]
    assert len(set(dirs)) == 3, dirs


def test_persistent_lanes_do_not_pass_storage_state(tmp_path):
    """--storage-state applies to isolated sessions. In persistent mode it is accepted
    and silently discarded, so the lane would look seeded without being seeded."""
    assert "--storage-state" not in mcp_args(_persistent(tmp_path))


def test_isolated_lanes_do_not_pass_user_data_dir(tmp_path):
    """The two flags are mutually exclusive; the server refuses both at startup."""
    assert "--user-data-dir" not in mcp_args(config_at(tmp_path))


def test_the_generated_file_carries_this_lanes_own_profile(tmp_path):
    """The frontmatter is what ships. A call site that dropped the index would hand
    every lane lane-1's directory and the silent lock comes back."""
    cfg = _persistent(tmp_path, 2)
    seen = set()
    for index in (1, 2):
        args = frontmatter(lane_markdown(index, cfg))["mcpServers"][0][SERVER_NAME]["args"]
        seen.add(args[args.index("--user-data-dir") + 1])
    assert len(seen) == 2, seen

"""Lane agent file tests.

The property that matters is not "a file was written". It is that each lane gets an
**inline** `mcpServers` block, because that is the single thing standing between five
independent browsers and five subagents fighting over one tab.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

from autoweb.config import Config
from autoweb.lanes import (
    AGENTS_DIRNAME,
    GENERATED_MARKER,
    LOCAL_SETTINGS_PATH,
    SERVER_NAME_PREFIX,
    LaneError,
    existing,
    lane_markdown,
    mcp_args,
    server_name,
    sync,
    sync_permissions,
    workspace_is_trusted,
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
    assert list(entry) == [server_name(1)]
    server = entry[server_name(1)]
    assert server["command"] == "npx"
    assert server["type"] == "stdio"
    assert server["args"] == mcp_args(cfg)


def test_lane_server_is_not_called_playwright(tmp_path):
    """The orchestrator's own .mcp.json already connects a server named `playwright`,
    and sharing it puts every lane in one browser."""
    entry = frontmatter(lane_markdown(1, config_at(tmp_path)))["mcpServers"][0]
    assert SERVER_NAME_PREFIX != "playwright"
    assert list(entry) == [server_name(1)]


def test_every_lane_declares_a_DIFFERENT_server_name(tmp_path):
    """The defect this phase exists to prevent, in its second form.

    Claude Code de-duplicates inline servers by name across subagents running at the
    same time. When every lane file declared a server called `lane`, lane 2 resolved
    to lane 1's process: one browser, one current tab, lanes navigating each other's
    pages, and `Error: No open pages available.` when the first lane closed. Measured,
    then measured again with per-lane names to confirm the fix.
    """
    cfg = config_at(tmp_path, "[lanes]\nmax = 5\n")
    names = [next(iter(frontmatter(lane_markdown(i, cfg))["mcpServers"][0]))
             for i in range(1, 6)]
    assert len(set(names)) == 5, names


def test_a_lane_server_name_is_safe_inside_a_tool_name(tmp_path):
    """The name becomes `mcp__<name>__browser_click`. `lane1` is the spelling that was
    verified end to end, so keep it to letters and digits."""
    for index in (1, 10, 50):
        assert re.fullmatch(r"[A-Za-z0-9]+", server_name(index)), server_name(index)


def test_sync_grants_every_lane_server_in_local_settings(tmp_path):
    """A permission rule names one server, so `mcp__lane` does not cover `mcp__lane1`.
    An ungranted lane stops for approval nobody is watching for, which reads as a
    hang."""
    cfg = config_at(tmp_path, "[lanes]\nmax = 3\n")
    granted = sync_permissions(cfg)
    data = json.loads((tmp_path / LOCAL_SETTINGS_PATH).read_text(encoding="utf-8"))
    allow = data["permissions"]["allow"]
    assert granted == ["mcp__lane1", "mcp__lane2", "mcp__lane3"]
    for rule in granted:
        assert rule in allow


def test_granting_permissions_keeps_what_the_user_already_had(tmp_path):
    """settings.local.json is the user's file. Only `mcp__lane<digits>` is ours."""
    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    path = tmp_path / LOCAL_SETTINGS_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "permissions": {"allow": ["Bash(ls)", "mcp__lane9"], "deny": ["Bash(rm)"]},
        "model": "opus",
    }), encoding="utf-8")
    sync_permissions(cfg)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert data["permissions"]["deny"] == ["Bash(rm)"]
    assert "Bash(ls)" in data["permissions"]["allow"]
    assert "mcp__lane1" in data["permissions"]["allow"]
    # lane 9 is above the ceiling now, and it is a rule this function owns.
    assert "mcp__lane9" not in data["permissions"]["allow"]


def test_malformed_local_settings_is_a_clear_error_not_a_traceback(tmp_path):
    cfg = config_at(tmp_path)
    path = tmp_path / LOCAL_SETTINGS_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LaneError, match="not valid JSON"):
        sync_permissions(cfg)


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
    assert entry[server_name(1)]["args"] == mcp_args(cfg)


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
        entry = frontmatter(lane_markdown(index, cfg))["mcpServers"][0]
        args = entry[server_name(index)]["args"]
        seen.add(args[args.index("--user-data-dir") + 1])
    assert len(seen) == 2, seen


def test_the_lane_profile_path_is_absolute(tmp_path):
    """The MCP server resolves a relative path against its own working directory,
    which the agent file cannot see, so two lanes could land on one profile.

    Absoluteness is the property that matters, and it is all this asserts. Dropping the
    `.resolve()` in `mcp_args` leaves this passing, because `Config.load` already gives
    an absolute `root_dir` - the call is defensive, not load-bearing, and a test that
    pretended otherwise would be testing the implementation.
    """
    cfg = config_at(tmp_path, "[lanes]\nisolated = false\n")
    args = mcp_args(cfg, 1)
    assert pathlib.Path(args[args.index("--user-data-dir") + 1]).is_absolute()


def test_a_lane_is_told_not_to_use_the_orchestrators_browser(tmp_path):
    """`mcp__playwright__*` is visible inside a lane. Using it lands the lane in the
    one shared tab, which is the failure this phase exists to prevent."""
    body = lane_markdown(1, config_at(tmp_path))
    assert "mcp__playwright__*" in body
    assert "mcp__lane1__" in body


def test_permission_ownership_does_not_reach_other_mcp_servers(tmp_path):
    """`mcp__lane<digits>` is the whole of what sync owns.

    An over-greedy pattern here would retire the user's other MCP grants, which is how
    a tool that was working yesterday starts asking for approval today.
    """
    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    path = tmp_path / LOCAL_SETTINGS_PATH
    path.parent.mkdir(parents=True)
    keep = ["mcp__playwright__browser_navigate", "mcp__playwright", "mcp__laneX",
            "mcp__lane1__browser_click", "mcp__tiger__db_schema", "Bash(ls)"]
    path.write_text(json.dumps({"permissions": {"allow": list(keep)}}), encoding="utf-8")
    sync_permissions(cfg)
    allow = json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"]
    for rule in keep:
        assert rule in allow, f"sync retired a rule it does not own: {rule}"


def test_a_settings_file_we_cannot_decode_is_never_silently_replaced(tmp_path):
    """UTF-16 is what PowerShell's `>` writes by default. Decoding it as empty would
    replace every permission, hook and model the user had with lane rules alone."""
    cfg = config_at(tmp_path)
    path = tmp_path / LOCAL_SETTINGS_PATH
    path.parent.mkdir(parents=True)
    original = json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "model": "opus"})
    path.write_bytes(original.encode("utf-16"))
    with pytest.raises(LaneError, match="not UTF-8"):
        sync_permissions(cfg)
    assert path.read_bytes() == original.encode("utf-16"), "the file was modified"


def test_a_byte_order_mark_is_tolerated(tmp_path):
    """Notepad writes one. config.py already reads TOML with utf-8-sig, so a user who
    edits one file in Notepad and not the other should not see different behaviour."""
    cfg = config_at(tmp_path, "[lanes]\nmax = 1\n")
    path = tmp_path / LOCAL_SETTINGS_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(json.dumps({"model": "opus"}).encode("utf-8-sig"))
    sync_permissions(cfg)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    assert data["model"] == "opus"
    assert "mcp__lane1" in data["permissions"]["allow"]


def test_granting_twice_does_not_rewrite_the_file(tmp_path):
    """It is the user's file. A no-op run should not reformat it or touch its mtime."""
    cfg = config_at(tmp_path, "[lanes]\nmax = 2\n")
    path = tmp_path / LOCAL_SETTINGS_PATH
    sync_permissions(cfg)
    before = path.read_bytes(), path.stat().st_mtime_ns
    sync_permissions(cfg)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_a_lane_body_tells_the_truth_about_how_it_is_seeded(tmp_path):
    """Both halves, because inverting the condition passes a test that checks one.

    A persistent lane told it starts logged in goes hunting for a session it does not
    have; an isolated lane told to expect its own profile ignores the identity it was
    given.
    """
    iso = tmp_path / "iso"
    iso.mkdir()
    body = lane_markdown(1, config_at(iso))
    assert "root.json" in body
    assert "starts logged out" not in body

    persistent = tmp_path / "persistent"
    persistent.mkdir()
    body = lane_markdown(1, config_at(persistent, "[lanes]\nisolated = false\n"))
    assert "starts logged out" in body
    assert "own profile on disk" in body


def test_a_non_object_settings_file_is_a_clear_error(tmp_path):
    cfg = config_at(tmp_path)
    path = tmp_path / LOCAL_SETTINGS_PATH
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(LaneError, match="expected a JSON object"):
        sync_permissions(cfg)


def test_a_settings_file_with_a_non_object_permissions_key_is_a_clear_error(tmp_path):
    cfg = config_at(tmp_path)
    path = tmp_path / LOCAL_SETTINGS_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"permissions": "all"}), encoding="utf-8")
    with pytest.raises(LaneError, match="not an object"):
        sync_permissions(cfg)


def test_a_settings_file_whose_allow_is_not_a_list_is_a_clear_error(tmp_path):
    cfg = config_at(tmp_path)
    path = tmp_path / LOCAL_SETTINGS_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"permissions": {"allow": "everything"}}),
                    encoding="utf-8")
    with pytest.raises(LaneError, match="not a list"):
        sync_permissions(cfg)


def test_a_lane_is_told_to_verify_its_own_page(tmp_path):
    """The one failure a lane cannot otherwise see.

    Under a broken isolation the trailing page report of `browser_wait_for` was observed
    naming the page the lane expected while the tab had already moved, so the lane
    reported one site's content under another site's name with no error anywhere.
    """
    body = lane_markdown(1, config_at(tmp_path))
    assert "location.href" in body
    assert "browser_wait_for" in body


def test_every_lane_can_read_its_own_storage(tmp_path):
    """Without `--caps=storage` the server ships 25 tools and not one of them can read
    a cookie, so the lane has nothing to hand back at teardown. Measured over stdio:
    25 tools bare, 42 with the flag."""
    for body in ("", "[lanes]\nisolated = false\n"):
        root = tmp_path / ("iso" if not body else "persistent")
        root.mkdir()
        assert "--caps=storage" in mcp_args(config_at(root, body))


def test_the_storage_capability_reaches_the_generated_file(tmp_path):
    """The frontmatter is what the server is actually started with."""
    entry = frontmatter(lane_markdown(1, config_at(tmp_path)))["mcpServers"][0]
    assert "--caps=storage" in entry[server_name(1)]["args"]


# --- the trusted-workspace requirement --------------------------------------
#
# Measured on a fresh clone: four lanes dispatched correctly, a naive agent found them
# unaided, and zero browsers ever started. Claude Code will not start the mcpServers in
# an agent file from an untrusted folder, and the only trace is a debug-log line.


def _claude_json(tmp_path, body):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(json.dumps(body), encoding="utf-8")
    return home


def test_a_trusted_folder_is_recognised(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    home = _claude_json(tmp_path, {"projects": {
        project.resolve().as_posix(): {"hasTrustDialogAccepted": True}}})
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert workspace_is_trusted(project) is True


def test_an_untrusted_folder_is_recognised(tmp_path, monkeypatch):
    """False, not None. This is the case that silently costs you every browser."""
    project = tmp_path / "proj"
    project.mkdir()
    home = _claude_json(tmp_path, {"projects": {
        project.resolve().as_posix(): {"hasTrustDialogAccepted": False}}})
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert workspace_is_trusted(project) is False


def test_a_folder_claude_has_never_seen_is_untrusted(tmp_path, monkeypatch):
    """A fresh clone is exactly this, and it is the shape the real failure took."""
    project = tmp_path / "proj"
    project.mkdir()
    home = _claude_json(tmp_path, {"projects": {"/somewhere/else": {}}})
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert workspace_is_trusted(project) is False


def test_project_keys_are_matched_whatever_the_slashes(tmp_path, monkeypatch):
    """Claude Code stores these keys with forward slashes on every platform, so a
    Windows path has to be normalised before comparing or the answer is always False."""
    project = tmp_path / "proj"
    project.mkdir()
    key = project.resolve().as_posix().replace("/", B + B)
    home = _claude_json(tmp_path, {"projects": {key: {"hasTrustDialogAccepted": True}}})
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert workspace_is_trusted(project) is True


def test_an_unreadable_config_is_unknown_rather_than_untrusted(tmp_path, monkeypatch):
    """None, not False. A false alarm here teaches people to ignore the warning, and the
    warning is the only thing standing between them and a silent hour of debugging."""
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert workspace_is_trusted(project) is None


def test_a_missing_config_is_unknown(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert workspace_is_trusted(project) is None


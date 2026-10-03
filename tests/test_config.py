"""Config tests.

The contract under test is not "config parses". It is: **defaults work without a
config file, and a malformed file fails loudly naming the offending key.** A config
error that makes you grep for the problem is the bug.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from autoweb.config import Config, ConfigError, Learned, OriginRule


def write(tmp_path, body: str):
    (tmp_path / "autoweb.toml").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


# --- defaults ---------------------------------------------------------------


def test_missing_config_is_not_an_error(tmp_path):
    """AutoWeb must run out of the box with no config at all."""
    cfg = Config.load(tmp_path)
    assert cfg.lanes.max == 5
    assert cfg.state.indexeddb is True


def test_indexeddb_defaults_to_true(tmp_path):
    """Playwright defaults this to False; False quietly loses most real logins."""
    assert Config.load(tmp_path).state.indexeddb is True


def test_partial_config_keeps_other_defaults(tmp_path):
    cfg = Config.load(write(tmp_path, """
        [lanes]
        max = 3
    """))
    assert cfg.lanes.max == 3
    assert cfg.lanes.browser == "chrome"       # untouched
    assert cfg.caps.max_origins == 50          # untouched


# --- failing loudly ---------------------------------------------------------


def test_unknown_top_level_key_names_itself(tmp_path):
    with pytest.raises(ConfigError, match="lanez"):
        Config.load(write(tmp_path, """
            [lanez]
            max = 3
        """))


def test_unknown_nested_key_names_itself(tmp_path):
    with pytest.raises(ConfigError, match=r"lanes\.maxx"):
        Config.load(write(tmp_path, """
            [lanes]
            maxx = 3
        """))


def test_unknown_key_suggests_valid_ones(tmp_path):
    """Naming the bad key is half the job; the other half is saying what was allowed."""
    with pytest.raises(ConfigError, match="browser"):
        Config.load(write(tmp_path, """
            [lanes]
            brower = "chromium"
        """))


def test_invalid_browser_rejected(tmp_path):
    with pytest.raises(ConfigError, match="lanes.browser"):
        Config.load(write(tmp_path, """
            [lanes]
            browser = "netscape"
        """))


def test_zero_lanes_rejected(tmp_path):
    with pytest.raises(ConfigError, match="lanes.max"):
        Config.load(write(tmp_path, """
            [lanes]
            max = 0
        """))


def test_malformed_toml_names_the_file(tmp_path):
    (tmp_path / "autoweb.toml").write_text("[lanes\nmax = 3", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        Config.load(tmp_path)


def test_origin_rule_must_be_a_table(tmp_path):
    with pytest.raises(ConfigError, match="must be a table"):
        Config.load(write(tmp_path, """
            [origins]
            "example.com" = true
        """))


# --- origin rules -----------------------------------------------------------


def test_origin_rule_exact_match(tmp_path):
    cfg = Config.load(write(tmp_path, """
        [origins."example.com"]
        rotates = true
    """))
    assert cfg.rule_for("example.com").rotates is True


def test_origin_rule_matches_subdomain(tmp_path):
    """A rule on example.com must cover app.example.com — sites split auth across hosts."""
    cfg = Config.load(write(tmp_path, """
        [origins."example.com"]
        sticky = true
    """))
    assert cfg.rule_for("https://app.example.com/path").sticky is True


def test_origin_rule_does_not_match_unrelated_suffix(tmp_path):
    """notexample.com must not match example.com."""
    cfg = Config.load(write(tmp_path, """
        [origins."example.com"]
        sticky = true
    """))
    assert cfg.rule_for("https://notexample.com").sticky is False


def test_unknown_origin_gets_empty_rule(tmp_path):
    assert Config.load(tmp_path).rule_for("https://nowhere.test") == OriginRule()


# --- learned facts ----------------------------------------------------------


def test_learned_missing_is_empty(tmp_path):
    assert Learned.load(tmp_path / "learned.json").rotates == []


def test_learned_roundtrip(tmp_path):
    path = tmp_path / ".autoweb" / "learned.json"
    Learned(rotates=["a.test"], sticky=["b.test"]).save(path)
    assert Learned.load(path).rotates == ["a.test"]


def test_learned_save_is_atomic(tmp_path):
    """A crash mid-write must not leave a truncated file where the old one was."""
    path = tmp_path / ".autoweb" / "learned.json"
    Learned(rotates=["a.test"]).save(path)
    Learned(rotates=["b.test"]).save(path)
    assert json.loads(path.read_text())["rotates"] == ["b.test"]
    assert not path.with_suffix(".json.tmp").exists()


def test_corrupt_learned_file_is_an_error(tmp_path):
    path = tmp_path / "learned.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="corrupt"):
        Learned.load(path)


# --- derived paths ----------------------------------------------------------


def test_paths_resolve_against_config_location(tmp_path):
    """Running from a subdirectory must still find the project's root.json."""
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    write(tmp_path, """
        [state]
        root = "state/root.json"
    """)
    cfg = Config.load(nested)
    assert cfg.root_state_path == (tmp_path / "state" / "root.json").resolve()


# --- regressions from the Phase 1 Fable gate --------------------------------
#
# Every test below corresponds to a defect found by independent testing. TOML is
# typed and dataclasses are not, so `Klass(**data)` accepted anything the key name
# matched. The damage was invisible: `indexeddb = "false"` is a truthy string.


@pytest.mark.parametrize("body,needle", [
    ('[lanes]\nmax = "five"\n', "lanes.max"),
    ('[lanes]\nmax = true\n', "lanes.max"),
    ('[lanes]\nmax = 2.5\n', "lanes.max"),
    ('[lanes]\nisolated = "yes"\n', "lanes.isolated"),
    ('[lanes]\nmcp_version = 83\n', "lanes.mcp_version"),
    ('[state]\nroot = 5\n', "state.root"),
    ('[state]\nindexeddb = "false"\n', "state.indexeddb"),
    ('[caps]\ntotal_bytes = "big"\n', "caps.total_bytes"),
    ('[origins."a.test"]\nrotates = "yes"\n', "origins.a.test.rotates"),
    ('[origins."a.test"]\nindexeddb = 1\n', "origins.a.test.indexeddb"),
])
def test_wrong_type_names_the_key(tmp_path, body, needle):
    """A wrong type must name the key, not raise a TypeError from a later comparison."""
    (tmp_path / "autoweb.toml").write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Config.load(tmp_path)
    assert needle in str(exc.value)


def test_string_false_does_not_silently_enable_indexeddb(tmp_path):
    """`indexeddb = "false"` is truthy. Accepting it inverts the user's intent."""
    (tmp_path / "autoweb.toml").write_text('[state]\nindexeddb = "false"\n',
                                           encoding="utf-8")
    with pytest.raises(ConfigError, match="boolean"):
        Config.load(tmp_path)


@pytest.mark.parametrize("body", [
    "lanes = 5\n",
    'lanes = "x"\n',
    "[[lanes]]\nmax = 3\n",
    'origins = ["a.test"]\n',
    'origins = "a.test"\n',
])
def test_wrong_shape_sections_are_config_errors(tmp_path, body):
    """A section given as a scalar or array must not reach dict iteration."""
    (tmp_path / "autoweb.toml").write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(tmp_path)


@pytest.mark.parametrize("key", ["total_bytes", "max_origins", "max_indexeddb_per_origin"])
@pytest.mark.parametrize("value", [0, -1])
def test_caps_must_be_positive(tmp_path, key, value):
    """A zero cap is not a tight budget, it is 'discard everything'."""
    (tmp_path / "autoweb.toml").write_text(f"[caps]\n{key} = {value}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=key):
        Config.load(tmp_path)


def test_empty_root_rejected(tmp_path):
    (tmp_path / "autoweb.toml").write_text('[state]\nroot = ""\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="state.root"):
        Config.load(tmp_path)


def test_utf8_bom_is_tolerated(tmp_path):
    """Notepad writes a BOM by default; that should not be a config error."""
    (tmp_path / "autoweb.toml").write_bytes(b"\xef\xbb\xbf[lanes]\nmax = 3\n")
    assert Config.load(tmp_path).lanes.max == 3


def test_undecodable_file_is_a_config_error(tmp_path):
    (tmp_path / "autoweb.toml").write_bytes(b"[lanes]\nmax = 3\n\xff\xfe")
    with pytest.raises(ConfigError, match="UTF-8"):
        Config.load(tmp_path)


def test_dir_that_does_not_exist_is_rejected(tmp_path):
    """A typo in -C must not silently resolve to someone else's config."""
    with pytest.raises(ConfigError, match="not a directory"):
        Config.load(tmp_path / "nope" / "nowhere")


def test_file_passed_as_dir_is_rejected(tmp_path):
    target = tmp_path / "autoweb.toml"
    target.write_text("[lanes]\nmax = 3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a directory"):
        Config.load(target)


def test_upward_search_stops_at_repo_root(tmp_path):
    """A stray config above the repo must not become the project's config."""
    (tmp_path / "autoweb.toml").write_text("[lanes]\nmax = 99\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert Config.load(nested).lanes.max == 5          # default, not 99


@pytest.mark.parametrize("origin", [
    "https://example.com:443",
    "https://example.com:8443/x",
    "https://EXAMPLE.com",
    "https://Example.COM/x",
    "https://example.com.",
    "http://user:pw@example.com/",
    "//example.com",
    "https://app.example.com",
    "example.com",
])
def test_host_matching_is_forgiving(tmp_path, origin):
    """Scheme, case, port, userinfo and trailing dot are all the same host."""
    (tmp_path / "autoweb.toml").write_text(
        '[origins."example.com"]\nsticky = true\n', encoding="utf-8")
    assert Config.load(tmp_path).rule_for(origin).sticky is True


@pytest.mark.parametrize("declared_first", ["example.com", "app.example.com"])
def test_most_specific_rule_wins_regardless_of_order(tmp_path, declared_first):
    """Same rules, same query, same answer - whatever order the file lists them."""
    other = "app.example.com" if declared_first == "example.com" else "example.com"
    flags = {"example.com": "sticky = true", "app.example.com": "rotates = true"}
    (tmp_path / "autoweb.toml").write_text(
        f'[origins."{declared_first}"]\n{flags[declared_first]}\n'
        f'[origins."{other}"]\n{flags[other]}\n', encoding="utf-8")
    rule = Config.load(tmp_path).rule_for("https://app.example.com/x")
    assert (rule.rotates, rule.sticky) == (True, False)


@pytest.mark.parametrize("raw", [
    '{"rotates": "a.test"}',
    '{"rotates": null}',
    '{"rotates": [1, 2]}',
    '{"rotates": {"a": 1}}',
])
def test_learned_rejects_wrong_value_shapes(tmp_path, raw):
    """A bare string iterates per character and prints one 'origin' per letter."""
    path = tmp_path / "learned.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ConfigError, match="list of strings"):
        Learned.load(path)


def test_learned_save_failure_leaves_no_temp_file(tmp_path, monkeypatch):
    """A failed rename must not leave debris beside the real file."""
    path = tmp_path / ".autoweb" / "learned.json"
    path.parent.mkdir(parents=True)

    def boom(self, target):
        raise OSError("access denied")

    monkeypatch.setattr("pathlib.Path.replace", boom)
    with pytest.raises(ConfigError, match="cannot write"):
        Learned(rotates=["a.test"]).save(path)
    assert list(path.parent.iterdir()) == []

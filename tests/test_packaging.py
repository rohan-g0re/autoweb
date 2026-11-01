"""A clone must work. Design rule 4, asserted rather than hoped for.

This file exists because the rule was already broken once, in a way no amount of reading
the code would have caught: the dev tools lived in an optional-dependency *extra*, `uv
sync` installs *groups*, so a fresh clone got no test runner and `uv run pytest` silently
fell back to whatever pytest was on PATH. That one cannot import `autoweb`, so it failed
with a ModuleNotFoundError that reads like a packaging bug.

Packaging metadata is exactly the kind of thing that rots without noticing, because the
machine you develop on already has everything installed. So the invariants get tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 reads TOML through the backport
    tomllib = pytest.importorskip("tomli")

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


@pytest.fixture(scope="module")
def meta() -> dict:
    return tomllib.loads(PYPROJECT.read_bytes().decode("utf-8"))


def test_the_dev_extra_and_the_dev_group_say_the_same_thing(meta):
    """Both have to exist, and they have to agree.

    `uv sync` installs the group and ignores the extra; pip cannot install a PEP 735
    group without a recent version and an explicit flag. Listing the dev tools in both
    is what lets either installer set up a clone on its own. If they drift, one of the
    two paths starts missing a tool, and the failure looks like a broken package rather
    than a missing dependency.
    """
    extra = sorted(meta["project"]["optional-dependencies"]["dev"])
    group = sorted(meta["dependency-groups"]["dev"])
    assert extra == group, (
        "the dev extra and the dev group have drifted. Whichever installer reads the "
        "shorter list will produce a clone that cannot test itself."
    )


@pytest.mark.parametrize("tool", ["pytest", "ruff", "pyyaml"])
def test_the_tools_this_suite_needs_are_declared(meta, tool):
    """pyyaml is the easy one to forget: only one test file imports it, to parse the
    generated agent frontmatter instead of grepping it, and grepping is how a dead
    feature passed 121 tests."""
    declared = " ".join(meta["dependency-groups"]["dev"])
    assert tool in declared


def test_the_package_is_importable_from_a_clone():
    """Hatchling is told which directory to ship. A flat layout makes it easy to publish
    a wheel containing nothing, and the symptom is an install that succeeds followed by
    an import that fails."""
    packages = meta_packages()
    assert packages == ["autoweb"], packages


def meta_packages() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_bytes().decode("utf-8"))
    return data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]


def test_the_console_script_points_at_something_real(meta):
    """`autoweb = "autoweb.cli:main"` is a string until someone runs it. A typo here is
    invisible until a user installs the package and types the command."""
    target = meta["project"]["scripts"]["autoweb"]
    module_path, _, func_name = target.partition(":")
    module = __import__(module_path, fromlist=[func_name])
    assert callable(getattr(module, func_name))


def test_the_python_floor_matches_the_syntax_actually_used(meta):
    """`requires-python` is a promise. Breaking it means an install that works and a
    crash on first run, on exactly the machines least able to debug it."""
    assert meta["project"]["requires-python"] == ">=3.10"
    # tomllib arrived in 3.11, so 3.10 needs the backport declared conditionally.
    deps = " ".join(meta["project"]["dependencies"])
    assert "tomli>=2.0" in deps and "python_version < '3.11'" in deps


def test_nothing_in_the_wheel_config_ships_the_tests(meta):
    """Tests are not part of the product, and shipping them puts a `tests` package on
    the user's import path where it can shadow their own."""
    assert "tests" not in meta_packages()

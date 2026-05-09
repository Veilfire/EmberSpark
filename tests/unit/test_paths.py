"""Tests for spark.utils.paths — path traversal defense."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from spark.utils.paths import PathDenied, PathPolicy


def test_allowed_path_within_base(tmp_path: Path) -> None:
    base = tmp_path / "data"
    base.mkdir()
    target = base / "note.md"
    target.write_text("hi")
    policy = PathPolicy.from_strings([str(base)])
    assert policy.check(target) == target.resolve()


def test_traversal_is_blocked(tmp_path: Path) -> None:
    base = tmp_path / "data"
    base.mkdir()
    (tmp_path / "secret").write_text("nope")
    policy = PathPolicy.from_strings([str(base)])
    with pytest.raises(PathDenied):
        policy.check(base / ".." / "secret")


def test_symlink_escape_blocked(tmp_path: Path) -> None:
    base = tmp_path / "data"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = base / "link"
    os.symlink(outside, link)
    policy = PathPolicy.from_strings([str(base)])
    with pytest.raises(PathDenied):
        policy.check(link)


def test_deny_overrides_allow(tmp_path: Path) -> None:
    base = tmp_path / "data"
    base.mkdir()
    (base / "private").mkdir()
    policy = PathPolicy.from_strings([str(base)], [str(base / "private")])
    with pytest.raises(PathDenied):
        policy.check(base / "private" / "x.txt")


def test_empty_allow_fails_closed(tmp_path: Path) -> None:
    policy = PathPolicy.from_strings([])
    with pytest.raises(PathDenied, match="No allow paths"):
        policy.check(tmp_path / "anything")

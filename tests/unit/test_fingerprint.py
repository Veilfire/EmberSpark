"""Tests for stable objective fingerprints."""

from __future__ import annotations

from spark.learning.fingerprint import compute_fingerprint


def test_identical_inputs_same_fingerprint() -> None:
    a = compute_fingerprint("Summarize the repo README", ["http_client", "filesystem"])
    b = compute_fingerprint("Summarize the repo README", ["http_client", "filesystem"])
    assert a == b


def test_stop_words_dont_change_fingerprint() -> None:
    a = compute_fingerprint("Summarize the repo README", ["http_client"])
    b = compute_fingerprint("Summarize repo README", ["http_client"])
    assert a == b


def test_tool_order_does_not_matter() -> None:
    a = compute_fingerprint("do thing", ["a", "b"])
    b = compute_fingerprint("do thing", ["b", "a"])
    assert a == b


def test_different_tools_change_fingerprint() -> None:
    a = compute_fingerprint("do thing", ["a"])
    b = compute_fingerprint("do thing", ["a", "b"])
    assert a != b

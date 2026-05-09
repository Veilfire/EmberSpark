"""Tests for the trusted-documentation policy."""

from __future__ import annotations

from spark.skills.sources import DEFAULT_TRUSTED_DOC_HOSTS, TrustedDocPolicy


def test_default_policy_allows_known_hosts() -> None:
    policy = TrustedDocPolicy.default()
    assert policy.allows("core.telegram.org")
    assert policy.allows("CORE.TELEGRAM.ORG")
    assert not policy.allows("evil.example.com")


def test_default_hosts_include_big_apis() -> None:
    for host in ("api.slack.com", "docs.github.com", "api.notion.com"):
        assert host in DEFAULT_TRUSTED_DOC_HOSTS


def test_with_additions_extends_without_mutation() -> None:
    base = TrustedDocPolicy.default()
    extended = base.with_additions(["docs.example.com"])
    assert extended.allows("docs.example.com")
    assert not base.allows("docs.example.com")

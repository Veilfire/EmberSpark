"""Tests for spark.privacy.redaction — secret scrubbing must be truthful."""

from __future__ import annotations

import pytest

from spark.privacy.redaction import (
    disable_presidio,
    redact,
    redact_structure,
)

disable_presidio()  # keep unit tests fast and deterministic


def test_openai_key_scrubbed() -> None:
    text = "use sk-abcdefghijklmnopqrstuvwxyz1234567890 to authenticate"
    result = redact(text, use_presidio=False)
    assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in result.text
    assert "OPENAI_KEY" in result.applied


def test_aws_key_id_scrubbed() -> None:
    text = "AKIAIOSFODNN7EXAMPLE here"
    result = redact(text, use_presidio=False)
    assert "AKIAIOSFODNN7EXAMPLE" not in result.text
    assert "AWS_ACCESS_KEY_ID" in result.applied


def test_jwt_scrubbed() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmb28ifQ.AbC_Dsig-here"
    result = redact(f"bearer {jwt}", use_presidio=False)
    assert jwt not in result.text
    assert "JWT" in result.applied


def test_pem_block_scrubbed() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----"
    result = redact(pem, use_presidio=False)
    assert "ABC" not in result.text
    assert "PRIVATE_KEY_PEM" in result.applied


def test_cloud_metadata_url_scrubbed() -> None:
    text = "fetch http://169.254.169.254/latest/meta-data/ for fun"
    result = redact(text, use_presidio=False)
    assert "169.254.169.254" not in result.text
    assert "CLOUD_METADATA_URL" in result.applied


def test_structure_walk() -> None:
    data = {
        "api_key": "sk-abcdefghijklmnopqrstuvwxyz1234567890",
        "nested": {"jwt": "eyJa.b.c", "safe": "hello"},
    }
    scrubbed, applied = redact_structure(data, use_presidio=False)
    assert "sk-" not in scrubbed["api_key"]
    assert "OPENAI_KEY" in applied
    assert scrubbed["nested"]["safe"] == "hello"


@pytest.mark.parametrize(
    "nonsecret",
    ["hello world", "plain text", "the quick brown fox"],
)
def test_non_secrets_untouched(nonsecret: str) -> None:
    result = redact(nonsecret, use_presidio=False)
    assert result.text == nonsecret
    assert result.applied == ()

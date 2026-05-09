"""Tests for the credential generator + persistence."""

from __future__ import annotations

from pathlib import Path

from spark.web.credentials import (
    MIN_PASSWORD_LEN,
    SPECIALS,
    ensure_credentials,
    generate_credentials,
    generate_password,
    generate_username,
    hash_password,
    verify_password,
)


def test_username_shape() -> None:
    for _ in range(50):
        username = generate_username()
        # word + 4 digits, last 4 chars are digits
        assert username[-4:].isdigit()
        assert username[:-4].isalpha()
        assert 4 < len(username) < 20


def test_password_shape() -> None:
    for _ in range(100):
        pw = generate_password()
        assert len(pw) == MIN_PASSWORD_LEN
        assert sum(1 for c in pw if c.isupper()) == 1
        assert any(c.isdigit() for c in pw)
        assert any(c in SPECIALS for c in pw)
        # No whitespace, no control chars.
        assert all(c.isprintable() and not c.isspace() for c in pw)


def test_hash_and_verify_roundtrip() -> None:
    pw = generate_password()
    h = hash_password(pw)
    assert verify_password(pw, h)
    assert not verify_password(pw + "x", h)


def test_ensure_credentials_generates_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "creds.json"
    stored, fresh = ensure_credentials(path=path, rotate=False)
    assert fresh is not None
    assert stored.username == fresh.username
    assert verify_password(fresh.password, stored.password_hash)
    # file is 0600
    assert path.stat().st_mode & 0o777 == 0o600


def test_ensure_credentials_no_rotate_returns_existing(tmp_path: Path) -> None:
    path = tmp_path / "creds.json"
    stored1, fresh1 = ensure_credentials(path=path, rotate=False)
    stored2, fresh2 = ensure_credentials(path=path, rotate=False)
    assert fresh2 is None
    assert stored1.password_hash == stored2.password_hash


def test_ensure_credentials_rotate_replaces(tmp_path: Path) -> None:
    path = tmp_path / "creds.json"
    stored1, _ = ensure_credentials(path=path, rotate=False)
    stored2, fresh2 = ensure_credentials(path=path, rotate=True)
    assert fresh2 is not None
    assert stored1.password_hash != stored2.password_hash

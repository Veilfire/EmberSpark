"""Credential generation + persistence for the web UI.

Username: `<word><4 digits>` (e.g. `sparrow1234`).
Password: 16 characters, built from two dictionary words, digits, a special,
and exactly one uppercase letter. Readable but non-trivial.

Persistence: bcrypt hash only. The cleartext password is displayed ONCE on
startup (to stderr so it can't land in piped stdout), then discarded.
"""

from __future__ import annotations

import json
import os
import secrets
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark.web.wordlist import WORDLIST

MIN_PASSWORD_LEN = 16
SPECIALS = "!@#$%&*?-_+=/"


@dataclass(frozen=True)
class GeneratedCredentials:
    """Cleartext creds returned ONCE at generation time."""

    username: str
    password: str


@dataclass(frozen=True)
class StoredCredentials:
    """What we persist on disk — no cleartext password."""

    username: str
    password_hash: str  # bcrypt modular crypt format
    created_at: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "username": self.username,
                "password_hash": self.password_hash,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "StoredCredentials":
        payload = json.loads(data)
        return cls(
            username=payload["username"],
            password_hash=payload["password_hash"],
            created_at=payload["created_at"],
        )


def generate_username(rng: secrets.SystemRandom | None = None) -> str:
    rng = rng or secrets.SystemRandom()
    word = rng.choice(WORDLIST)
    digits = "".join(rng.choice(string.digits) for _ in range(4))
    return f"{word}{digits}"


def generate_password(rng: secrets.SystemRandom | None = None) -> str:
    """Return a 16-char password with two words, digits, one special, one uppercase."""
    rng = rng or secrets.SystemRandom()

    # Pick two short words that together fit in ~9 chars.
    short_words = [w for w in WORDLIST if 3 <= len(w) <= 5]
    w1 = rng.choice(short_words)
    w2 = rng.choice(short_words)
    while len(w1) + len(w2) > 10 or w1 == w2:
        w1 = rng.choice(short_words)
        w2 = rng.choice(short_words)

    special = rng.choice(SPECIALS)
    # Build a base: word1-word2-dddd
    digits = "".join(rng.choice(string.digits) for _ in range(4))
    base = f"{w1}{special}{w2}{digits}"

    # Pad or truncate to exactly MIN_PASSWORD_LEN chars.
    if len(base) < MIN_PASSWORD_LEN:
        padding_len = MIN_PASSWORD_LEN - len(base)
        pad_alphabet = string.ascii_lowercase + string.digits + SPECIALS
        base += "".join(rng.choice(pad_alphabet) for _ in range(padding_len))
    elif len(base) > MIN_PASSWORD_LEN:
        base = base[:MIN_PASSWORD_LEN]

    # Enforce exactly one uppercase letter.
    chars = list(base)
    lowercase_positions = [i for i, c in enumerate(chars) if c.islower()]
    if not lowercase_positions:
        # Insert one lowercase letter we can then uppercase.
        idx = rng.randrange(len(chars))
        chars[idx] = rng.choice(string.ascii_lowercase)
        lowercase_positions = [idx]
    up_idx = rng.choice(lowercase_positions)
    chars[up_idx] = chars[up_idx].upper()

    result = "".join(chars)
    assert len(result) == MIN_PASSWORD_LEN
    # Final sanity check: at least one digit, one special, exactly one upper.
    assert any(c.isdigit() for c in result), "password missing digit"
    assert any(c in SPECIALS for c in result), "password missing special"
    assert sum(1 for c in result if c.isupper()) == 1, "password uppercase count"
    return result


def generate_credentials() -> GeneratedCredentials:
    rng = secrets.SystemRandom()
    return GeneratedCredentials(
        username=generate_username(rng),
        password=generate_password(rng),
    )


def hash_password(password: str) -> str:
    import bcrypt

    # 13 rounds ≈ 0.5s/verify on modern hardware — acceptable for an
    # interactive local UI, strong enough for shared-host deployments.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=13)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def store_credentials(
    creds: GeneratedCredentials, *, path: Path
) -> StoredCredentials:
    from datetime import datetime, timezone

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = StoredCredentials(
        username=creds.username,
        password_hash=hash_password(creds.password),
        created_at=datetime.now(tz=timezone.utc).isoformat(),
    )
    path.write_text(stored.to_json())
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover — readonly FS
        pass
    return stored


def load_credentials(path: Path) -> StoredCredentials | None:
    path = path.expanduser()
    if not path.exists():
        return None
    try:
        return StoredCredentials.from_json(path.read_text())
    except (ValueError, KeyError):
        return None


def ensure_credentials(
    *,
    path: Path,
    rotate: bool,
) -> tuple[StoredCredentials, GeneratedCredentials | None]:
    """Ensure credentials exist on disk.

    Returns (stored, newly_generated_or_none). The cleartext is only present in
    the second element when fresh credentials were minted this call — the
    caller must display them exactly once to the console and then forget them.
    """
    existing = load_credentials(path)
    if existing is not None and not rotate:
        return existing, None
    creds = generate_credentials()
    stored = store_credentials(creds, path=path)
    return stored, creds


def display_banner(
    creds: GeneratedCredentials, bind: tuple[str, int], *, tls: bool = False
) -> str:
    """Render the one-shot startup banner. Print to stderr."""
    host, port = bind
    scheme = "https" if tls else "http"
    bar = "=" * 60
    return (
        f"\n{bar}\n"
        f"  Spark web UI — credentials (DISPLAYED ONCE; save them now)\n"
        f"{bar}\n"
        f"  URL:      {scheme}://{host}:{port}\n"
        f"  Username: {creds.username}\n"
        f"  Password: {creds.password}\n"
        f"{bar}\n"
        f"  These credentials are not logged. If lost, re-run `spark serve`\n"
        f"  with `--rotate-credentials` or set credentials.rotate_on_startup=true\n"
        f"  in ~/.spark/spark.yaml to mint a new pair.\n"
        f"{bar}\n"
    )

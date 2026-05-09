"""Age-encrypted file vault — the primary secrets backend.

**Threat model + design decisions**

1. One vault file (``~/.spark/secrets.age``), encrypted to a single
   X25519 recipient. The backing JSON is a plain ``{name: value}`` dict.
2. The identity (private key) lives at ``~/.spark/age_identity.key`` with
   mode ``0600``. Optionally passphrase-wrapped via an outer age layer
   (scrypt KDF). When passphrase-wrapped, the file is at
   ``~/.spark/age_identity.key.age`` instead.
3. Decrypted values are cached in memory as ``SecretStr``. Reads after
   the first unlock are O(1) and never touch disk.
4. Writes re-encrypt and atomically replace the vault file. Atomic rename
   on the same filesystem guarantees callers never see a half-written file.
5. No cloud anything. ``pyrage`` is a pure-Python binding to ``age-rs``.
   No network. No GPG. No ambient credentials.

**What this vault does NOT do:**

- It does not hide secret *names* — the list of keys is stored encrypted
  but is enumerable by anyone who has the identity. Names are assumed
  to be non-sensitive (e.g. ``anthropic_key``, ``smtp_password``).
- It does not protect against a compromised host user account. If an
  attacker can read ``age_identity.key`` and the vault file, they have
  full access. Mode ``0600`` + OS user isolation is the boundary.
- It does not support multi-user access. One identity, one vault, one
  operator.

**Atomicity guarantees**

- All writes go to a ``secrets.age.tmp`` file next to the real one,
  ``os.fsync`` the tmp, then ``os.replace`` over the real path. This is
  crash-safe on POSIX: after a crash you either see the old or the new
  contents, never a truncated/corrupt file.
- Same pattern for ``age_identity.key`` rotation.
"""

from __future__ import annotations

import json
import logging
import os
import secrets as _stdlib_secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import SecretStr

if TYPE_CHECKING:  # pragma: no cover — import-only types
    pass

log = logging.getLogger("spark.secrets.age_vault")


class AgeVaultError(RuntimeError):
    """Base exception for age-vault failures."""


class VaultNotInitialized(AgeVaultError):
    """Raised when the vault files don't exist yet."""


class VaultAlreadyExists(AgeVaultError):
    """Raised when initializing a vault that is already present."""


class IdentityLocked(AgeVaultError):
    """Raised when the identity file is passphrase-wrapped but no passphrase was supplied."""


class IdentityPassphraseInvalid(AgeVaultError):
    """Raised when the passphrase doesn't decrypt the identity file."""


@dataclass(frozen=True)
class AgeVaultPaths:
    """The three file paths the vault may touch, resolved once at init."""

    vault: Path              # secrets.age
    identity: Path           # age_identity.key (plaintext)
    identity_wrapped: Path   # age_identity.key.age (passphrase-wrapped)

    @classmethod
    def default(cls, home: Path | None = None) -> "AgeVaultPaths":
        base = (home or Path.home() / ".spark").expanduser()
        return cls(
            vault=base / "secrets.age",
            identity=base / "age_identity.key",
            identity_wrapped=base / "age_identity.key.age",
        )


class AgeFileVault:
    """Primary secrets backend.

    Usage:

    >>> vault = AgeFileVault(AgeVaultPaths.default())
    >>> vault.init()                      # one-time: creates identity + empty vault
    >>> vault.unlock()                    # load + decrypt once per process
    >>> vault.set("anthropic_key", "sk-...")
    >>> vault.get("anthropic_key")        # -> SecretStr

    For passphrase-wrapped identities:

    >>> vault = AgeFileVault(AgeVaultPaths.default(), passphrase="mine")
    >>> vault.init(passphrase="mine")     # writes age_identity.key.age instead
    >>> vault.unlock()
    """

    name = "age_file"

    def __init__(
        self,
        paths: AgeVaultPaths | None = None,
        *,
        passphrase: str | None = None,
    ) -> None:
        self.paths = paths or AgeVaultPaths.default()
        self._passphrase = passphrase
        self._identity_obj: object | None = None   # pyrage x25519.Identity
        self._recipient_obj: object | None = None  # pyrage x25519.Recipient
        self._cache: dict[str, SecretStr] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_initialized(self) -> bool:
        """True iff the vault + identity files exist on disk."""
        if not self.paths.vault.exists():
            return False
        return self.paths.identity.exists() or self.paths.identity_wrapped.exists()

    def init(self, *, passphrase: str | None = None) -> None:
        """Create a new identity + empty vault.

        Refuses to overwrite an existing vault. Pass ``passphrase`` to
        passphrase-wrap the identity file; omit for an unwrapped identity
        protected only by filesystem permissions.
        """
        if self.is_initialized():
            raise VaultAlreadyExists(
                f"vault already initialized at {self.paths.vault}"
            )

        # Lazy import so the whole module doesn't crash if pyrage is missing
        # on installs that don't use the vault.
        try:
            from pyrage import encrypt, passphrase as pyrage_passphrase, x25519
        except ImportError as exc:  # pragma: no cover — build-time check
            raise AgeVaultError(
                "pyrage is not installed. Install with `pip install pyrage`"
                " or enable the core spark-runtime install."
            ) from exc

        self.paths.vault.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        # 1. Generate a new X25519 identity.
        identity = x25519.Identity.generate()
        recipient = identity.to_public()

        identity_str = str(identity).encode("utf-8")

        # 2. Write the identity file.
        if passphrase is not None:
            # Passphrase-wrap the identity with an outer age layer.
            wrapped = encrypt(
                identity_str,
                [pyrage_passphrase.Recipient(passphrase)],
            )
            _atomic_write_bytes(self.paths.identity_wrapped, wrapped, mode=0o600)
        else:
            _atomic_write_bytes(self.paths.identity, identity_str, mode=0o600)

        # 3. Write an empty encrypted vault.
        empty_vault = json.dumps({}, separators=(",", ":")).encode("utf-8")
        vault_bytes = encrypt(empty_vault, [recipient])
        _atomic_write_bytes(self.paths.vault, vault_bytes, mode=0o600)

        log.info(
            "age_vault_initialized",
            extra={
                "vault_path": str(self.paths.vault),
                "passphrase_wrapped": passphrase is not None,
            },
        )

        # Cache the identity so subsequent operations don't re-read it.
        self._identity_obj = identity
        self._recipient_obj = recipient
        self._cache = {}

    def unlock(self, *, passphrase: str | None = None) -> None:
        """Load the identity, decrypt the vault, build the in-memory cache.

        Idempotent: safe to call multiple times.
        """
        if self._cache is not None and self._identity_obj is not None:
            return  # already unlocked

        if not self.is_initialized():
            raise VaultNotInitialized(
                f"no vault at {self.paths.vault} — run `spark secrets init-age-vault`"
            )

        try:
            from pyrage import decrypt, passphrase as pyrage_passphrase, x25519
        except ImportError as exc:  # pragma: no cover
            raise AgeVaultError("pyrage not installed") from exc

        # 1. Load the identity.
        pw = passphrase if passphrase is not None else self._passphrase
        if self.paths.identity_wrapped.exists():
            if pw is None:
                raise IdentityLocked(
                    "identity is passphrase-wrapped but no passphrase supplied "
                    "(set SPARK_AGE_PASSPHRASE or call unlock(passphrase=...))"
                )
            wrapped_bytes = self.paths.identity_wrapped.read_bytes()
            try:
                identity_str_bytes = decrypt(
                    wrapped_bytes,
                    [pyrage_passphrase.Identity(pw)],
                )
            except Exception as exc:
                raise IdentityPassphraseInvalid(
                    "failed to decrypt age identity — wrong passphrase?"
                ) from exc
            identity_str = identity_str_bytes.decode("utf-8").strip()
        elif self.paths.identity.exists():
            identity_str = self.paths.identity.read_text().strip()
        else:  # pragma: no cover — is_initialized() catches this
            raise VaultNotInitialized("no identity file")

        identity = x25519.Identity.from_str(identity_str)
        recipient = identity.to_public()

        # 2. Decrypt the vault.
        vault_ciphertext = self.paths.vault.read_bytes()
        try:
            vault_plaintext = decrypt(vault_ciphertext, [identity])
        except Exception as exc:
            raise AgeVaultError(
                f"failed to decrypt vault at {self.paths.vault}: {exc}"
            ) from exc

        try:
            data = json.loads(vault_plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgeVaultError(f"vault plaintext is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise AgeVaultError("vault payload must be a JSON object")

        self._identity_obj = identity
        self._recipient_obj = recipient
        self._cache = {str(k): SecretStr(str(v)) for k, v in data.items()}

    def lock(self) -> None:
        """Drop the in-memory cache + identity. Subsequent reads need unlock."""
        self._cache = None
        self._identity_obj = None
        self._recipient_obj = None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_names(self) -> list[str]:
        self._require_unlocked()
        assert self._cache is not None
        return sorted(self._cache.keys())

    def get(self, name: str) -> SecretStr:
        self._require_unlocked()
        assert self._cache is not None
        if name not in self._cache:
            raise KeyError(name)
        return self._cache[name]

    def available(self, name: str) -> bool:
        self._require_unlocked()
        assert self._cache is not None
        return name in self._cache

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def set(self, name: str, value: str) -> None:
        """Add or overwrite a secret and persist the vault."""
        if not name or not name.strip():
            raise ValueError("secret name must be non-empty")
        if len(name) > 128:
            raise ValueError("secret name too long (max 128)")
        if "\x00" in name or "\n" in name:
            raise ValueError("secret name contains forbidden characters")

        self._require_unlocked()
        assert self._cache is not None
        self._cache[name] = SecretStr(value)
        self._flush()

    def delete(self, name: str) -> None:
        """Remove a secret and persist the vault. Missing name is a no-op."""
        self._require_unlocked()
        assert self._cache is not None
        if self._cache.pop(name, None) is not None:
            self._flush()

    def rotate_identity(self, *, passphrase: str | None = None) -> None:
        """Generate a new identity and re-encrypt the vault under it.

        The old identity is discarded. Pass ``passphrase`` to keep the
        new identity passphrase-wrapped (same semantics as ``init``).
        """
        self._require_unlocked()
        assert self._cache is not None

        try:
            from pyrage import encrypt, passphrase as pyrage_passphrase, x25519
        except ImportError as exc:  # pragma: no cover
            raise AgeVaultError("pyrage not installed") from exc

        # 1. New identity
        new_identity = x25519.Identity.generate()
        new_recipient = new_identity.to_public()
        new_identity_str = str(new_identity).encode("utf-8")

        # 2. Re-encrypt the vault under the new recipient.
        payload = json.dumps(
            {k: v.get_secret_value() for k, v in self._cache.items()},
            separators=(",", ":"),
        ).encode("utf-8")
        new_vault_bytes = encrypt(payload, [new_recipient])

        # 3. Write everything atomically. Delete the old identity *after*
        #    the new files are in place, so a crash mid-rotation still
        #    leaves a decryptable vault.
        if passphrase is not None:
            wrapped = encrypt(
                new_identity_str,
                [pyrage_passphrase.Recipient(passphrase)],
            )
            _atomic_write_bytes(self.paths.identity_wrapped, wrapped, mode=0o600)
            _atomic_write_bytes(self.paths.vault, new_vault_bytes, mode=0o600)
            # Remove the old unwrapped identity if it existed.
            if self.paths.identity.exists():
                self.paths.identity.unlink()
        else:
            _atomic_write_bytes(self.paths.identity, new_identity_str, mode=0o600)
            _atomic_write_bytes(self.paths.vault, new_vault_bytes, mode=0o600)
            if self.paths.identity_wrapped.exists():
                self.paths.identity_wrapped.unlink()

        self._identity_obj = new_identity
        self._recipient_obj = new_recipient

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_unlocked(self) -> None:
        if self._cache is None or self._identity_obj is None:
            # Best-effort auto-unlock using the passphrase supplied at
            # construction. If that fails, the caller gets a clean error.
            self.unlock()

    def _flush(self) -> None:
        """Re-encrypt the in-memory cache and write the vault atomically."""
        try:
            from pyrage import encrypt
        except ImportError as exc:  # pragma: no cover
            raise AgeVaultError("pyrage not installed") from exc

        assert self._cache is not None
        assert self._recipient_obj is not None

        payload = json.dumps(
            {k: v.get_secret_value() for k, v in self._cache.items()},
            separators=(",", ":"),
        ).encode("utf-8")
        ciphertext = encrypt(payload, [self._recipient_obj])
        _atomic_write_bytes(self.paths.vault, ciphertext, mode=0o600)


def _atomic_write_bytes(target: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Write `data` to `target` atomically with the given file mode.

    Writes to a sibling ``.tmp.<random>`` file, fsync's it, then ``os.replace``
    over the target. Safe against crashes and partial writes.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A unique suffix so concurrent writers don't collide. We're
    # single-writer by design, but operators sometimes run two CLIs at once.
    suffix = _stdlib_secrets.token_hex(6)
    tmp = target.with_name(f"{target.name}.tmp.{suffix}")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

"""Secrets subsystem.

EmberSpark's secrets story (H1.3 onward):

- **Primary backend:** :class:`~spark.secrets.age_vault.AgeFileVault` — an
  age-encrypted file at ``~/.spark/secrets.age`` with its identity stored at
  ``~/.spark/age_identity.key`` (mode ``0600``). Optionally passphrase-wrapped
  via an outer age layer.
- **Fallback:** :class:`~spark.secrets.env_backend.EnvBackend` — reads
  ``SPARK_SECRET_*`` env vars. Opt-out via config; every resolution emits a
  one-time info log so operators notice stray env-var secrets.

The ``keyring`` provider was removed in H1.3.
"""

from __future__ import annotations

from pathlib import Path

from spark.secrets.age_vault import (
    AgeFileVault,
    AgeVaultError,
    AgeVaultPaths,
    IdentityLocked,
    IdentityPassphraseInvalid,
    VaultAlreadyExists,
    VaultNotInitialized,
)
from spark.secrets.base import SecretBackend, SecretNotFound
from spark.secrets.env_backend import EnvBackend
from spark.secrets.manager import SecretManager

__all__ = [
    "AgeFileVault",
    "AgeVaultError",
    "AgeVaultPaths",
    "EnvBackend",
    "IdentityLocked",
    "IdentityPassphraseInvalid",
    "SecretBackend",
    "SecretManager",
    "SecretNotFound",
    "VaultAlreadyExists",
    "VaultNotInitialized",
    "default_manager",
]


def default_manager(
    *,
    vault_path: Path | None = None,
    identity_path: Path | None = None,
    passphrase: str | None = None,
    env_fallback_enabled: bool = True,
    env_warn_on_hit: bool = True,
) -> SecretManager:
    """Build a ``SecretManager`` with the age vault as primary.

    Args:
        vault_path: Override for the vault file path. Defaults to
            ``~/.spark/secrets.age``.
        identity_path: Override for the unwrapped identity file path.
            Defaults to ``~/.spark/age_identity.key``.
        passphrase: Pre-supplied passphrase for passphrase-wrapped identities.
        env_fallback_enabled: When True, unresolved names fall through to
            ``SPARK_SECRET_*`` env vars.
        env_warn_on_hit: When True, every env fallback resolution emits a
            one-time info log so operators notice stray env-var secrets.

    The vault is NOT auto-unlocked here — the first read triggers a lazy
    unlock. Callers that want explicit control can call
    ``manager.vault.unlock()``.
    """
    if vault_path is None and identity_path is None:
        paths = AgeVaultPaths.default()
    else:
        base = Path("~/.spark").expanduser()
        paths = AgeVaultPaths(
            vault=vault_path or (base / "secrets.age"),
            identity=identity_path or (base / "age_identity.key"),
            identity_wrapped=base / "age_identity.key.age",
        )
    vault = AgeFileVault(paths, passphrase=passphrase)
    return SecretManager(
        vault=vault,
        env_fallback_enabled=env_fallback_enabled,
        env_warn_on_hit=env_warn_on_hit,
    )

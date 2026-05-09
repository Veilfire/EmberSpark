"""Secret manager — `age_file` primary + optional `env` fallback.

**Contract (unchanged from v1)**

- ``get(name)`` returns a ``SecretStr`` or raises ``SecretNotFound``. Never
  returns a plain ``str``.
- ``list_names()`` returns only names. Never values.
- Values never touch prompts, model context, memory, or logs by default.
  The structlog scrub processor redacts any value registered via
  ``known_values()``.

**Resolution order**

1. :class:`~spark.secrets.age_vault.AgeFileVault` (primary) — if unlocked
   and the name is present, return it.
2. :class:`~spark.secrets.env_backend.EnvBackend` (fallback) — if
   ``env_fallback_enabled`` and the name is present as
   ``SPARK_SECRET_<UPPERCASE_NAME>``, return it AND emit a one-time
   ``info`` log entry so operators notice they've left a secret in an
   env var. ``env_warn_on_hit`` controls whether this fires.

The ``keyring`` provider was removed in H1.3. It is no longer supported.
"""

from __future__ import annotations

import logging

from pydantic import SecretStr

from spark.secrets.age_vault import (
    AgeFileVault,
    AgeVaultError,
    VaultNotInitialized,
)
from spark.secrets.base import SecretBackend, SecretNotFound
from spark.secrets.env_backend import EnvBackend

log = logging.getLogger("spark.secrets")


class SecretManager:
    """Top-level secret manager.

    Two backends, in order:

    1. :class:`~spark.secrets.age_vault.AgeFileVault` — primary.
    2. :class:`~spark.secrets.env_backend.EnvBackend` — optional fallback.

    The vault is optional — if ``vault is None``, only the env fallback is
    consulted. This lets unit tests build a manager without writing a
    real age vault to disk.
    """

    def __init__(
        self,
        *,
        vault: AgeFileVault | None = None,
        env_fallback_enabled: bool = True,
        env_warn_on_hit: bool = True,
    ) -> None:
        self.vault = vault
        self.env_fallback_enabled = env_fallback_enabled
        self.env_warn_on_hit = env_warn_on_hit
        self._env = EnvBackend(silence_warning=True) if env_fallback_enabled else None
        self._known_values: set[str] = set()
        # Names we've already warned about, to keep `warn_on_hit` one-time.
        self._env_warned_for: set[str] = set()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, name: str) -> SecretStr:
        # 1. Primary: age vault
        if self.vault is not None:
            try:
                if self.vault.available(name):
                    value = self.vault.get(name)
                    self._track(value)
                    return value
            except VaultNotInitialized:
                # Vault wasn't initialized — fall through to env only.
                log.debug("vault_not_initialized_falling_back")
            except AgeVaultError as exc:  # pragma: no cover — boot path
                log.warning("vault_error_falling_back", extra={"error": str(exc)})

        # 2. Fallback: env var
        if (
            self._env is not None
            and self.env_fallback_enabled
            and self._env.available(name)
        ):
            value = self._env.get(name)
            self._track(value)
            if self.env_warn_on_hit and name not in self._env_warned_for:
                self._env_warned_for.add(name)
                log.info(
                    "env_secret_fallback",
                    extra={
                        "event": "secrets.env_fallback_hit",
                        "name": name,
                        "severity": "info",
                        "remediation": (
                            f"Move {name!r} into the age vault via "
                            f"`spark secrets set {name}`"
                        ),
                    },
                )
            return value

        raise SecretNotFound(name)

    def get_scoped(self, names: set[str]) -> dict[str, SecretStr]:
        return {n: self.get(n) for n in names}

    def available(self, name: str) -> bool:
        try:
            self.get(name)
        except SecretNotFound:
            return False
        return True

    def list_names(self) -> list[str]:
        names: set[str] = set()
        if self.vault is not None:
            try:
                names.update(self.vault.list_names())
            except VaultNotInitialized:
                pass
            except AgeVaultError:  # pragma: no cover
                pass
        if self._env is not None and self.env_fallback_enabled:
            names.update(self._env.list_names())
        return sorted(names)

    # ------------------------------------------------------------------
    # Writes (delegate to vault only — env is not writable)
    # ------------------------------------------------------------------

    def set(self, name: str, value: str) -> None:
        if self.vault is None:
            raise AgeVaultError(
                "no age vault configured — cannot set secrets. "
                "Run `spark secrets init-age-vault` first."
            )
        self.vault.set(name, value)

    def delete(self, name: str) -> None:
        if self.vault is None:
            raise AgeVaultError(
                "no age vault configured — cannot delete secrets."
            )
        self.vault.delete(name)

    # ------------------------------------------------------------------
    # Log scrubbing support
    # ------------------------------------------------------------------

    def known_values(self) -> frozenset[str]:
        """Tracked secret values — consumed by the logging scrub processor."""
        return frozenset(self._known_values)

    def _track(self, value: SecretStr) -> None:
        self._known_values.add(value.get_secret_value())

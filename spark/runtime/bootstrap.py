"""Runtime bootstrap — the single side-effecting wrapper around `load_runtime`.

Every CLI entry point and the web app should call :func:`bootstrap` exactly
once during startup. It:

1. Loads the SparkRuntime YAML.
2. If the ``data_volume`` block is enabled, creates the root + subdirs on
   disk (mode 0700) and records the resolved config via
   :func:`spark.config.runtime_config.set_data_volume`.
3. If ``secrets.age_file.auto_init`` is True and the vault is missing,
   initializes a fresh age identity + empty vault (mode 0600).
4. Builds the process-scoped :class:`SecretManager`.
5. Returns the ``SparkRuntime`` instance.

Subsystems that need the data volume or secret manager read them through
:func:`spark.config.runtime_config.get_data_volume` and
:func:`get_secret_manager`. Nothing else in the tree should call the
underlying `ensure_*` or `AgeFileVault.init` helpers directly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from spark.config.runtime_config import (
    DataVolumeConfig,
    SecretsConfig,
    SparkRuntime,
    ensure_data_volume_dirs,
    load_runtime,
    set_data_volume,
)
from spark.secrets import (
    AgeFileVault,
    AgeVaultError,
    AgeVaultPaths,
    SecretManager,
    VaultAlreadyExists,
)

log = logging.getLogger("spark.runtime.bootstrap")


# Process-scoped singleton, same pattern as `get_data_volume`.
_secret_manager: SecretManager | None = None


def bootstrap(config_path: Path | None = None) -> SparkRuntime:
    """Load a SparkRuntime YAML and apply its side effects.

    Safe to call more than once per process — idempotent. The resolved
    data volume is recorded in the process-scoped singleton so later
    ``get_data_volume()`` calls from any subsystem see the same view.
    """
    cfg = load_runtime(config_path)

    dv = cfg.spec.data_volume
    if dv.enabled:
        ensure_data_volume_dirs(dv)
        set_data_volume(dv)
    else:
        set_data_volume(None)

    _bootstrap_secrets(cfg.spec.secrets)

    return cfg


def _bootstrap_secrets(secrets_cfg: SecretsConfig) -> None:
    """Ensure the age vault exists and build a process-scoped SecretManager."""
    global _secret_manager

    paths = AgeVaultPaths(
        vault=Path(secrets_cfg.age_file.vault_path).expanduser(),
        identity=Path(secrets_cfg.age_file.identity_path).expanduser(),
        identity_wrapped=(
            Path(secrets_cfg.age_file.identity_path).expanduser().with_name(
                Path(secrets_cfg.age_file.identity_path).name + ".age"
            )
        ),
    )

    # Resolve passphrase from env if configured.
    passphrase: str | None = None
    if secrets_cfg.age_file.passphrase_env:
        passphrase = os.environ.get(secrets_cfg.age_file.passphrase_env)
        if passphrase is None:
            log.warning(
                "age_passphrase_env_unset",
                extra={"env_var": secrets_cfg.age_file.passphrase_env},
            )

    vault = AgeFileVault(paths, passphrase=passphrase)

    # Auto-init on first boot if configured and vault is missing.
    if secrets_cfg.age_file.auto_init and not vault.is_initialized():
        try:
            vault.init(passphrase=passphrase)
            log.info(
                "age_vault_auto_initialized",
                extra={
                    "vault_path": str(paths.vault),
                    "passphrase_wrapped": passphrase is not None,
                },
            )
        except VaultAlreadyExists:
            pass  # raced with another process — fine
        except AgeVaultError as exc:  # pragma: no cover — boot path
            log.warning("age_vault_auto_init_failed", extra={"error": str(exc)})

    _secret_manager = SecretManager(
        vault=vault,
        env_fallback_enabled=secrets_cfg.env_fallback.enabled,
        env_warn_on_hit=secrets_cfg.env_fallback.warn_on_hit,
    )


def get_secret_manager() -> SecretManager:
    """Return the process-scoped SecretManager built by bootstrap().

    Falls back to a default-configured manager (env fallback only, no
    vault) when bootstrap hasn't been called — this keeps unit tests
    that touch the manager without going through bootstrap working.
    """
    global _secret_manager
    if _secret_manager is None:
        _secret_manager = SecretManager(
            vault=None,
            env_fallback_enabled=True,
            env_warn_on_hit=True,
        )
    return _secret_manager


def set_secret_manager(manager: SecretManager | None) -> None:
    """Test helper: override the process-scoped manager."""
    global _secret_manager
    _secret_manager = manager


def effective_sqlite_path(dv: DataVolumeConfig | None) -> Path | None:
    """Return the SQLite path implied by the data volume, or ``None``.

    ``None`` means "use the persistence layer's legacy default"
    (``~/.spark/spark.db``). This is a thin convenience wrapper that
    factors out the ``dv is None or not dv.sqlite_on_volume`` branches
    that every caller would otherwise repeat.
    """
    if dv is None or not dv.enabled or not dv.sqlite_on_volume:
        return None
    return dv.sqlite_path


def effective_chroma_path(dv: DataVolumeConfig | None) -> Path | None:
    """Return the Chroma persist path implied by the data volume, or ``None``.

    ``None`` means "use whatever the agent's ``LongTermMemoryConfig``
    specifies" — the caller is responsible for its own fallback.
    """
    if dv is None or not dv.enabled:
        return None
    return dv.chroma_path

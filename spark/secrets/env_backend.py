"""Environment-variable secret backend — dev fallback only.

Requires the `SPARK_SECRET_` prefix so only explicitly-scoped env vars are
considered. A warning is emitted at first access so operators notice.
"""

from __future__ import annotations

import os
import warnings

from pydantic import SecretStr

from spark.secrets.base import SecretBackend, SecretNotFound

_PREFIX = "SPARK_SECRET_"


class EnvBackend(SecretBackend):
    name = "env"

    def __init__(self, *, silence_warning: bool = False) -> None:
        self._warned = silence_warning

    def _warn_once(self) -> None:
        if not self._warned:
            warnings.warn(
                "EmberSpark env secret fallback in use — dev/CI only. Move "
                "the secret into the age vault via `spark secrets set <name>`.",
                stacklevel=2,
            )
            self._warned = True

    def _env_key(self, name: str) -> str:
        safe = name.upper().replace("-", "_").replace(".", "_")
        return f"{_PREFIX}{safe}"

    def get(self, name: str) -> SecretStr:
        self._warn_once()
        key = self._env_key(name)
        value = os.environ.get(key)
        if value is None:
            raise SecretNotFound(name)
        return SecretStr(value)

    def list_names(self) -> list[str]:
        return [
            k[len(_PREFIX) :].lower() for k in os.environ if k.startswith(_PREFIX)
        ]

    def available(self, name: str) -> bool:
        return self._env_key(name) in os.environ

"""Safe YAML loading with env-var interpolation and Pydantic validation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError
from ruamel.yaml import YAML

from spark.config.models import Agent, Task

T = TypeVar("T", Agent, Task)

_ENV_PATTERN = re.compile(r"\$\{(SPARK_[A-Z0-9_]+)\}")


class ConfigLoadError(Exception):
    def __init__(self, path: Path, errors: list[str]) -> None:
        self.path = path
        self.errors = errors
        super().__init__(f"{path}: {errors}")


def _yaml() -> YAML:
    # typ='safe' refuses Python-object tags like !!python/object
    y = YAML(typ="safe")
    y.preserve_quotes = True
    return y


def _interp(value: Any) -> Any:
    """Substitute `${SPARK_*}` env vars inside string leaves only.

    Only `SPARK_*` vars are allowed so YAML can't spray arbitrary environment
    contents into config.
    """
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            key = m.group(1)
            env = os.environ.get(key)
            if env is None:
                raise KeyError(f"Unresolved env var {key} in config")
            return env

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [_interp(v) for v in value]
    if isinstance(value, dict):
        return {k: _interp(v) for k, v in value.items()}
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        data = _yaml().load(f)
    if not isinstance(data, dict):
        raise ConfigLoadError(path, [f"Top-level YAML must be a mapping, got {type(data).__name__}"])
    return _interp(data)  # type: ignore[no-any-return]


def _load_model(path: Path, model_cls: type[T]) -> T:
    raw = _load_yaml(path)
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']} (type={e['type']})"
            for e in exc.errors()
        ]
        raise ConfigLoadError(path, errors) from exc


def load_agent(path: Path) -> Agent:
    return _load_model(path, Agent)


def load_task(path: Path) -> Task:
    return _load_model(path, Task)

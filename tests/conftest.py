"""Shared test fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Ensure tests never pick up a user's real spark DB or logs.
@pytest.fixture(autouse=True)
def _isolated_spark_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "spark-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Force any module caching of default paths to re-resolve.
    from spark.config import runtime_config as rc_module
    from spark.persistence import db as db_module

    db_module.LEGACY_DB_PATH = home / ".spark/spark.db"
    # Reset the process-scoped data volume so tests don't leak a real one.
    rc_module.set_data_volume(None)
    return home

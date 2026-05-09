"""Tests for the SparkRuntime config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.config.runtime_config import (
    SparkRuntime,
    WebBindLan,
    WebBindLoopback,
    WebBindPublic,
    dump_example,
    load_runtime,
    write_example,
)


def test_default_disables_web() -> None:
    cfg = SparkRuntime.default()
    assert cfg.spec.web.enabled is False
    assert isinstance(cfg.spec.web.bind, WebBindLoopback)


def test_write_example_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "spark.yaml"
    target = write_example(path)
    assert target.exists()
    assert target.stat().st_mode & 0o777 == 0o600
    content = target.read_text()
    assert "kind: SparkRuntime" in content
    assert "enabled: false" in content


def test_load_runtime_loopback(tmp_path: Path) -> None:
    path = tmp_path / "spark.yaml"
    path.write_text(
        """
apiVersion: spark.veilfire.dev/v1alpha1
kind: SparkRuntime
metadata:
  name: test
spec:
  web:
    enabled: true
    bind:
      mode: loopback
      host: 127.0.0.1
      port: 7777
""".strip()
    )
    cfg = load_runtime(path)
    assert cfg.spec.web.enabled is True
    assert isinstance(cfg.spec.web.bind, WebBindLoopback)


def test_load_runtime_lan_requires_cidrs(tmp_path: Path) -> None:
    path = tmp_path / "spark.yaml"
    path.write_text(
        """
apiVersion: spark.veilfire.dev/v1alpha1
kind: SparkRuntime
spec:
  web:
    enabled: true
    bind:
      mode: lan
      allowed_cidrs: []
""".strip()
    )
    with pytest.raises(Exception):
        load_runtime(path)


def test_load_runtime_lan_happy_path(tmp_path: Path) -> None:
    path = tmp_path / "spark.yaml"
    path.write_text(
        """
apiVersion: spark.veilfire.dev/v1alpha1
kind: SparkRuntime
spec:
  web:
    enabled: true
    bind:
      mode: lan
      host: 0.0.0.0
      port: 8000
      allowed_cidrs:
        - 192.168.1.0/24
        - 10.0.0.0/8
""".strip()
    )
    cfg = load_runtime(path)
    assert isinstance(cfg.spec.web.bind, WebBindLan)
    assert "192.168.1.0/24" in cfg.spec.web.bind.allowed_cidrs


def test_load_runtime_public_requires_tls(tmp_path: Path) -> None:
    path = tmp_path / "spark.yaml"
    path.write_text(
        """
apiVersion: spark.veilfire.dev/v1alpha1
kind: SparkRuntime
spec:
  web:
    enabled: true
    bind:
      mode: public
      host: 203.0.113.10
""".strip()
    )
    with pytest.raises(Exception):
        load_runtime(path)


def test_wrong_kind_rejected(tmp_path: Path) -> None:
    path = tmp_path / "spark.yaml"
    path.write_text("kind: Agent\nspec: {}\n")
    with pytest.raises(ValueError):
        load_runtime(path)


def test_missing_file_returns_default(tmp_path: Path) -> None:
    cfg = load_runtime(tmp_path / "nonexistent.yaml")
    assert cfg.spec.web.enabled is False


def test_example_yaml_roundtrips() -> None:
    example = dump_example()
    assert "kind: SparkRuntime" in example
    assert "enabled: false" in example

"""Structured logging subsystem."""

from __future__ import annotations

from spark.logging.events import EventType
from spark.logging.writer import configure_logging, get_logger

__all__ = ["EventType", "configure_logging", "get_logger"]

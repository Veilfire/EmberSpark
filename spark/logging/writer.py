"""JSONL rotating writer + structlog setup + hash-chain headers."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Callable

import structlog

from spark.logging.events import EventType
from spark.logging.processors import event_enum_processor, make_scrub_processor
from spark.logging.redaction_stats import make_aggregator_processor
from spark.logging.retention import latest_hash, rotate_and_bucket

DEFAULT_LOG_DIR = Path("~/.spark/logs").expanduser()
DEFAULT_LOG_FILE = "spark.jsonl"


def _ensure_dir(path: Path) -> Path:
    path = path.expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


class _ChainedFileHandler(logging.handlers.TimedRotatingFileHandler):
    """TimedRotatingFileHandler that writes a ``file.header`` on every new
    file, chaining it to the previous rotated file's sha256.
    """

    def __init__(self, filename: str, log_root: Path, **kwargs: object) -> None:
        super().__init__(filename=filename, **kwargs)  # type: ignore[arg-type]
        self._log_root = log_root
        # On first init, if the file is empty, write the header immediately.
        path = Path(self.baseFilename)
        if path.exists() and path.stat().st_size == 0:
            self._write_header()

    def doRollover(self) -> None:  # pragma: no cover — runs on rotation boundary
        super().doRollover()
        self._write_header()
        try:
            rotate_and_bucket(self._log_root)
        except Exception:
            pass

    def _write_header(self) -> None:
        import json
        from datetime import datetime, timezone

        prev = latest_hash(self._log_root) or ""
        header = json.dumps(
            {
                "event_type": EventType.FILE_HEADER.value,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "prev_sha256": prev,
                "version": "1",
            },
            sort_keys=True,
        )
        try:
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(header + "\n")
            self.stream.flush()
        except Exception:  # pragma: no cover
            pass


def configure_logging(
    *,
    log_dir: Path | None = None,
    level: str = "info",
    tracked_secret_values: Callable[[], frozenset[str]] | None = None,
) -> None:
    """Configure root logging + structlog with privacy processors.

    Idempotent: calling twice replaces handlers on the root logger.
    """
    resolved_dir = _ensure_dir(log_dir or DEFAULT_LOG_DIR)
    log_path = resolved_dir / DEFAULT_LOG_FILE

    # Run retention once at startup so aged files end up in the right bucket.
    try:
        rotate_and_bucket(resolved_dir)
    except Exception:
        pass

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    file_handler = _ChainedFileHandler(
        filename=str(log_path),
        log_root=resolved_dir,
        when="midnight",
        backupCount=14,
        encoding="utf-8",
        utc=True,
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(file_handler)

    scrub = make_scrub_processor(tracked_secret_values or (lambda: frozenset()))
    aggregator = make_aggregator_processor()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            event_enum_processor,
            scrub,
            aggregator,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "spark") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)

"""SQLAlchemy type helpers for timezone-aware datetime round-trips.

SQLite has no native timezone storage. SQLAlchemy's default ``DateTime``
type writes ISO strings and returns naive ``datetime`` objects on load —
even when the caller wrote ``datetime.now(tz=timezone.utc)``. Everything
in Spark is UTC, so we attach ``timezone.utc`` on the way in and on the
way out. Reads become tz-aware, comparisons with ``utcnow()`` work
correctly, and ``.isoformat()`` naturally produces ``+00:00``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """``DateTime`` that keeps every value in UTC, both directions."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        # Normalize to UTC, then strip tzinfo so SQLAlchemy's default
        # SQLite DateTime string-formatter sees a plain wall-clock value
        # and writes it unambiguously. All Spark code treats SQLite-stored
        # datetimes as UTC by convention.
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def promote_datetime_columns(metadata: Any) -> int:
    """Swap every plain ``DateTime`` column type for ``UTCDateTime``.

    Call once after models are imported and before the engine binds
    (or before ``create_all``). Returns the number of columns promoted.
    """
    promoted = 0
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, UTCDateTime):
                continue
            if isinstance(column.type, DateTime):
                column.type = UTCDateTime()
                promoted += 1
    return promoted

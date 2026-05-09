"""Log retention classes + hash-chain file headers.

Retention layout under `~/.spark/logs/`:
  hot/       —   0–7 days  (plain JSONL, active)
  warm/      —   7–30 days (plain JSONL, closed)
  cold/      —  30–365 days (gzipped)
  archive/   —  365+ days (gzipped, operator-pruned only)

Each rotated file's first line is a ``file.header`` event whose ``prev_sha256``
field chains to the previous file, so ``spark logs verify`` can detect
tampering or deletion.
"""

from __future__ import annotations

import gzip
import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from spark.utils.hashing import sha256_file


@dataclass(frozen=True)
class RetentionBucket:
    name: str
    min_age: timedelta
    max_age: timedelta | None  # None = no upper bound
    compressed: bool


BUCKETS = (
    RetentionBucket("hot", timedelta(days=0), timedelta(days=7), False),
    RetentionBucket("warm", timedelta(days=7), timedelta(days=30), False),
    RetentionBucket("cold", timedelta(days=30), timedelta(days=365), True),
    RetentionBucket("archive", timedelta(days=365), None, True),
)


def bucket_for_age(age: timedelta) -> RetentionBucket:
    for bucket in BUCKETS:
        if bucket.max_age is None or age < bucket.max_age:
            if age >= bucket.min_age:
                return bucket
    return BUCKETS[-1]


def _ensure_subdirs(root: Path) -> None:
    for bucket in BUCKETS:
        (root / bucket.name).mkdir(parents=True, exist_ok=True)


def latest_hash(root: Path) -> str | None:
    """Return the sha256 of the most-recently-rotated log file, or None."""
    candidates: list[Path] = []
    for bucket in BUCKETS:
        d = root / bucket.name
        if not d.exists():
            continue
        candidates.extend(d.iterdir())
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return sha256_file(candidates[0])


def rotate_and_bucket(root: Path) -> dict[str, int]:
    """Sort existing rotated files into the correct retention bucket.

    Called at startup and nightly. Moves files across buckets as they age
    and compresses files entering `cold` / `archive`.
    """
    root = root.expanduser()
    _ensure_subdirs(root)
    now = datetime.now(tz=timezone.utc)
    moved: dict[str, int] = {}

    # Walk every file under the root (including the un-bucketed top level,
    # where the TimedRotatingFileHandler writes new rotations initially).
    for path in list(root.rglob("*")):
        if not path.is_file():
            continue
        # Skip the currently-active file if present directly under root.
        if path.parent == root and path.name == "spark.jsonl":
            continue

        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        age = now - mtime
        target_bucket = bucket_for_age(age)
        target_dir = root / target_bucket.name

        # Move into the target bucket if not already there.
        if path.parent != target_dir:
            dest = target_dir / path.name
            if dest.exists():
                dest.unlink()
            shutil.move(str(path), dest)
            moved[target_bucket.name] = moved.get(target_bucket.name, 0) + 1
            path = dest

        # Compress if entering a gzipped bucket and not already .gz.
        if target_bucket.compressed and not path.name.endswith(".gz"):
            gz_path = path.with_suffix(path.suffix + ".gz")
            with path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            path.unlink()

    return moved


@dataclass(frozen=True)
class ChainVerdict:
    ok: bool
    broken_file: str | None
    expected_hash: str | None
    actual_hash: str | None
    message: str


def verify_chain(root: Path) -> ChainVerdict:
    """Walk every log file in age order and verify the prev_sha256 chain."""
    root = root.expanduser()
    files: list[Path] = []
    for bucket in BUCKETS:
        d = root / bucket.name
        if d.exists():
            files.extend(sorted(d.iterdir(), key=lambda p: p.stat().st_mtime))

    previous_hash: str | None = None
    for path in files:
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as f:  # type: ignore[operator]
                first_line = f.readline()
        except OSError:
            continue
        if not first_line:
            continue
        try:
            header = json.loads(first_line)
        except json.JSONDecodeError:
            return ChainVerdict(
                ok=False,
                broken_file=str(path),
                expected_hash=previous_hash,
                actual_hash=None,
                message="first line is not JSON",
            )
        if header.get("event_type") != "file.header":
            # Legacy file — skip but don't break the chain.
            previous_hash = sha256_file(path)
            continue
        declared_prev = header.get("prev_sha256")
        if previous_hash is not None and declared_prev != previous_hash:
            return ChainVerdict(
                ok=False,
                broken_file=str(path),
                expected_hash=previous_hash,
                actual_hash=declared_prev,
                message="prev_sha256 mismatch — file may have been inserted, removed, or tampered with",
            )
        previous_hash = sha256_file(path)

    return ChainVerdict(
        ok=True,
        broken_file=None,
        expected_hash=previous_hash,
        actual_hash=previous_hash,
        message="chain OK",
    )

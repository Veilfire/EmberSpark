"""Deliverables endpoints.

Serves the data volume's ``deliverables`` subdirectory as an operator-
facing Downloads page. Paths are validated against the volume root so
no traversal can escape it.

The filesystem is the source of truth for file *presence* (so manually
dropped files still appear), but the ``deliverables`` table enriches
each row with run-id / source metadata when available — enabling the
"from run X" cross-link on the Downloads page and the run-replay's
"Deliverables" sidebar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from spark.config.runtime_config import get_data_volume
from spark.persistence.db import session_scope
from spark.persistence.models import DeliverableRow
from spark.web.auth import Principal, require_viewer

router = APIRouter()


class DeliverableFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relative_path: str
    size_bytes: int
    modified_at: datetime
    run_id: str | None = None
    task_name: str | None = None
    source: str | None = None
    kind: str | None = None


class DeliverableListing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str
    files: list[DeliverableFile]
    total_size_bytes: int


#: Maximum number of files the listing endpoint will return. Hard cap
#: to prevent DoS via a million-file deliverables directory.
_LIST_MAX_FILES = 1000

#: Maximum recursion depth when walking the deliverables tree.
_LIST_MAX_DEPTH = 6


@router.get("/", response_model=DeliverableListing)
async def list_deliverables(
    _: Principal = Depends(require_viewer),
    run_id: str | None = Query(default=None, max_length=64),
) -> DeliverableListing:
    root = _deliverables_root_or_404()

    # If a run_id filter is supplied, return only the rows the engine
    # recorded against that run. Skips the filesystem walk entirely.
    if run_id:
        async with session_scope() as session:
            stmt = select(DeliverableRow).where(DeliverableRow.run_id == run_id)
            rows = list((await session.execute(stmt)).scalars().all())
        files: list[DeliverableFile] = []
        total = 0
        for r in rows:
            abs_path = root / r.relative_path
            try:
                stat = abs_path.stat()
                modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            except OSError:
                modified = r.created_at
            files.append(
                DeliverableFile(
                    relative_path=r.relative_path,
                    size_bytes=r.size_bytes,
                    modified_at=modified,
                    run_id=r.run_id,
                    task_name=r.task_name,
                    source=r.source,
                    kind=r.kind,
                )
            )
            total += r.size_bytes
        files.sort(key=lambda f: f.modified_at, reverse=True)
        return DeliverableListing(root=str(root), files=files, total_size_bytes=total)

    # Build the path → row index once so each filesystem hit can be
    # enriched without N round-trips.
    async with session_scope() as session:
        result = await session.execute(select(DeliverableRow))
        index: dict[str, DeliverableRow] = {
            r.relative_path: r for r in result.scalars().all()
        }

    files = []
    total = 0
    collected = 0

    # Walk with a manual queue so we can cap depth, cap file count, and
    # (critically) resolve each file's path to refuse anything that
    # resolves outside the deliverables root. `rglob` follows symlinks
    # by default, so we can't use it naively.
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue:
        if collected >= _LIST_MAX_FILES:
            break
        current, depth = queue.pop(0)
        if depth > _LIST_MAX_DEPTH:
            continue
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if collected >= _LIST_MAX_FILES:
                break
            # Refuse symlinks entirely — a symlink in deliverables is
            # an unexpected state and could exfiltrate metadata of
            # files outside the root.
            try:
                if entry.is_symlink():
                    continue
            except OSError:
                continue
            if entry.is_dir():
                queue.append((entry, depth + 1))
                continue
            if not entry.is_file():
                continue
            # Defensive: after resolve, the entry must still be inside
            # root. `iterdir` shouldn't escape root (we refused
            # symlinks), but belt + suspenders.
            try:
                resolved_entry = entry.resolve()
                resolved_entry.relative_to(root)
            except (OSError, ValueError):
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            rel = entry.relative_to(root)
            rel_str = str(rel)
            row = index.get(rel_str)
            files.append(
                DeliverableFile(
                    relative_path=rel_str,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    run_id=row.run_id if row else None,
                    task_name=row.task_name if row else None,
                    source=row.source if row else "external",
                    kind=row.kind if row else None,
                )
            )
            total += stat.st_size
            collected += 1

    files.sort(key=lambda f: f.modified_at, reverse=True)
    return DeliverableListing(root=str(root), files=files, total_size_bytes=total)


@router.get("/{file_path:path}")
async def download_file(
    file_path: Annotated[str, "Relative path inside the deliverables root"],
    _: Principal = Depends(require_viewer),
) -> FileResponse:
    root = _deliverables_root_or_404()
    # Reject any absolute, empty, or traversing path.
    if not file_path or file_path.startswith("/") or ".." in file_path.split("/"):
        raise HTTPException(status_code=400, detail="invalid path")
    # Reject CR/LF/NUL that could break Content-Disposition headers.
    if any(ch in file_path for ch in ("\r", "\n", "\x00", "\\")):
        raise HTTPException(status_code=400, detail="invalid path")
    # Walk the path segment-by-segment, refusing any intermediate
    # symlink — a symlink pointing outside the root would otherwise be
    # followed by `resolve()` and escape the root. We refuse it before
    # reaching the final file so the operator can't accidentally bind a
    # sensitive host path into the download surface.
    target = root
    for segment in Path(file_path).parts:
        target = target / segment
        try:
            if target.is_symlink():
                raise HTTPException(status_code=400, detail="symlinks in deliverables are refused")
        except OSError as exc:
            raise HTTPException(status_code=404, detail="file not found") from exc
    resolved = target.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path escapes deliverables root") from exc
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(
        path=str(resolved),
        filename=resolved.name,
        media_type="application/octet-stream",
    )


def _deliverables_root_or_404() -> Path:
    dv = get_data_volume()
    if dv is None:
        raise HTTPException(
            status_code=404,
            detail="data volume not enabled; set spec.data_volume.enabled in SparkRuntime",
        )
    return dv.deliverables_path

"""PDF reader plugin.

Extracts text (and optional metadata) from PDF files under an operator-
allowlisted path tree. Pure offline, no network, uses `pypdf` (standard
library dep in the web-runtime extras).

Configuration is deliberately minimal: a path allow/deny list (same
semantics as the filesystem plugin), a per-page char cap, and a page count
cap so a 10k-page PDF doesn't blow up the context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity


class PdfReaderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_paths: list[Path] = Field(default_factory=list)
    deny_paths: list[Path] = Field(default_factory=list)
    max_pages: int = Field(default=200, ge=1, le=10_000)
    max_chars_per_page: int = Field(default=20_000, ge=100, le=1_000_000)
    include_metadata: bool = True


class PdfReaderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path = Field(
        description="Path to the PDF. Must be inside the operator's allow_paths.",
    )
    pages: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Optional page range, 1-indexed. Examples: '1-10', '3', '5-'. "
            "None reads all pages (up to max_pages)."
        ),
    )


class PdfPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_num: int
    text: str
    char_count: int


class PdfMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    creator: str | None = None
    producer: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    page_count: int


class PdfReaderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    metadata: PdfMetadata | None
    pages: list[PdfPage]
    truncated_page_count: bool
    truncated_text: bool


class PdfReaderPlugin:
    name: ClassVar[str] = "pdf_reader"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Read text (and metadata) from a PDF file under an operator-allowlisted "
        "path tree. No network, no writes."
    )
    input_schema: ClassVar[type[BaseModel]] = PdfReaderArgs
    output_schema: ClassVar[type[BaseModel]] = PdfReaderResponse
    config_schema: ClassVar[type[BaseModel]] = PdfReaderConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset({Permission.FS_READ})
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: PdfReaderArgs, ctx: Any) -> PdfReaderResponse:
        from spark.utils.paths import PathPolicy

        cfg = getattr(ctx, "plugin_config", {}) or {}
        allow_paths = [Path(p) for p in (cfg.get("allow_paths") or [])]
        deny_paths = [Path(p) for p in (cfg.get("deny_paths") or [])]
        if not allow_paths:
            # Working default: data-volume scratch + deliverables. Same
            # rationale as csv_io / markdown_writer.
            for attr in ("scratch_path", "deliverables_path"):
                p = getattr(ctx, attr, None)
                if p:
                    allow_paths.append(Path(p))
        max_pages = int(cfg.get("max_pages") or 200)
        max_chars_per_page = int(cfg.get("max_chars_per_page") or 20_000)
        include_metadata = bool(cfg.get("include_metadata", True))

        policy = PathPolicy.from_strings(
            [str(p) for p in allow_paths],
            [str(p) for p in deny_paths],
        )
        resolved = policy.check(args.path)
        if not resolved.exists():
            raise FileNotFoundError(f"pdf_reader: {resolved} does not exist")
        if not resolved.is_file():
            raise PermissionError(f"pdf_reader: {resolved} is not a regular file")

        try:
            import pypdf  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "pdf_reader requires the `pypdf` package. Install it with "
                "`pip install pypdf` or enable the `pdf` extra on spark-runtime."
            ) from exc

        reader = pypdf.PdfReader(str(resolved))
        total_pages = len(reader.pages)

        start_idx, end_idx = _parse_page_range(args.pages, total_pages)
        selected_idx = list(range(start_idx, end_idx))

        truncated_page_count = False
        if len(selected_idx) > max_pages:
            selected_idx = selected_idx[:max_pages]
            truncated_page_count = True

        pages: list[PdfPage] = []
        truncated_text = False
        for i in selected_idx:
            try:
                text = reader.pages[i].extract_text() or ""
            except Exception:
                text = ""
            if len(text) > max_chars_per_page:
                text = text[:max_chars_per_page]
                truncated_text = True
            pages.append(PdfPage(page_num=i + 1, text=text, char_count=len(text)))

        metadata: PdfMetadata | None = None
        if include_metadata:
            info = reader.metadata or {}
            metadata = PdfMetadata(
                title=_pdf_str(info.get("/Title")),
                author=_pdf_str(info.get("/Author")),
                subject=_pdf_str(info.get("/Subject")),
                creator=_pdf_str(info.get("/Creator")),
                producer=_pdf_str(info.get("/Producer")),
                created_at=_pdf_str(info.get("/CreationDate")),
                modified_at=_pdf_str(info.get("/ModDate")),
                page_count=total_pages,
            )

        return PdfReaderResponse(
            path=str(resolved),
            metadata=metadata,
            pages=pages,
            truncated_page_count=truncated_page_count,
            truncated_text=truncated_text,
        )


def _parse_page_range(spec: str | None, total: int) -> tuple[int, int]:
    """Return (start_idx, end_idx) half-open, 0-indexed.

    Accepts: ``None`` (all pages), ``"N"``, ``"N-M"``, ``"N-"``, ``"-M"``.
    Raises ``PermissionError`` (never a raw ``ValueError``) on garbage
    input so the error surface shown to the model is clean.
    """
    if not spec:
        return 0, total
    spec = spec.strip()
    try:
        if "-" in spec:
            left, _, right = spec.partition("-")
            left = left.strip()
            right = right.strip()
            start = max(1, int(left)) if left else 1
            end = min(total, int(right)) if right else total
        else:
            start = end = int(spec)
    except ValueError as exc:
        raise PermissionError(f"pdf_reader: invalid page range {spec!r}") from exc
    if start < 1 or end < start:
        raise PermissionError(f"pdf_reader: invalid page range {spec!r}")
    return start - 1, end


def _pdf_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)

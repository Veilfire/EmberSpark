"""RSS / Atom feed reader plugin.

A tool an agent calls during a run to fetch and parse a feed into structured
entries. Distinct from the scheduler's ``http_new_row`` event source — that
one polls on a cron; this one is synchronous, model-driven.

Uses ``feedparser`` (pure Python, the standard RSS/Atom library) and
reuses the SSRF defense from ``http_client``.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from spark.config.enums import Permission, Sensitivity


class RssReaderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_hosts: list[str] = Field(default_factory=list)
    max_items: int = Field(default=50, ge=1, le=500)
    include_content: bool = True
    max_content_chars: int = Field(default=5_000, ge=1, le=100_000)
    max_response_bytes: int = Field(default=10_000_000, ge=1, le=100_000_000)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    user_agent: str = Field(default="spark-runtime-rss/0.1", max_length=256)


class RssReaderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(
        description="Feed URL (RSS or Atom). Host must be in the operator's allow_hosts.",
    )
    # See WebSearchArgs for why we clamp via a validator instead of
    # ``Field(ge=1, le=500)`` — Bedrock's tool-binding JSON Schema
    # subset rejects ``minimum``/``maximum`` on ``number`` types.
    max_items: int = Field(
        default=50,
        description="Maximum entries to return (clamped to 1..500, capped further by operator config).",
    )

    @field_validator("max_items")
    @classmethod
    def _clamp_max_items(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > 500:
            return 500
        return v


class RssItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    link: str | None = None
    published: str | None = None
    summary: str | None = None
    content: str | None = None
    id: str | None = None


class RssReaderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feed_title: str | None
    feed_link: str | None
    items: list[RssItem]
    item_count: int


class RssReaderPlugin:
    name: ClassVar[str] = "rss_reader"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Fetch and parse an RSS/Atom feed. SSRF-hardened, host-allowlisted."
    )
    input_schema: ClassVar[type[BaseModel]] = RssReaderArgs
    output_schema: ClassVar[type[BaseModel]] = RssReaderResponse
    config_schema: ClassVar[type[BaseModel]] = RssReaderConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset({Permission.NET_HTTP})
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: RssReaderArgs, ctx: Any) -> RssReaderResponse:
        from spark.utils.net import HostPolicy, pin_dns, validate_url

        cfg = getattr(ctx, "plugin_config", {}) or {}
        allow_hosts = list(cfg.get("allow_hosts") or [])
        max_items_cfg = int(cfg.get("max_items") or 50)
        include_content = bool(cfg.get("include_content", True))
        max_content_chars = int(cfg.get("max_content_chars") or 5_000)
        max_response_bytes = int(cfg.get("max_response_bytes") or 10_000_000)
        connect_timeout = float(cfg.get("connect_timeout_seconds") or 5.0)
        read_timeout = float(cfg.get("read_timeout_seconds") or 15.0)
        user_agent = cfg.get("user_agent") or "spark-runtime-rss/0.1"
        max_items = min(args.max_items, max_items_cfg)

        policy = HostPolicy.from_list(allow_hosts, allow_http=False, allow_redirects=False)
        target = validate_url(args.url, policy)

        headers = {"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"}
        timeout = httpx.Timeout(
            connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout
        )
        with pin_dns(target):
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, verify=True, trust_env=False
            ) as client, client.stream("GET", args.url, headers=headers) as response:
                response.raise_for_status()
                body_bytes = bytearray()
                async for chunk in response.aiter_bytes():
                    body_bytes.extend(chunk)
                    if len(body_bytes) > max_response_bytes:
                        raise PermissionError(
                            f"rss_reader: response exceeded max_response_bytes={max_response_bytes}"
                        )
                body = bytes(body_bytes)

        try:
            import feedparser  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "rss_reader requires the `feedparser` package. Install with "
                "`pip install feedparser`."
            ) from exc

        parsed_feed = feedparser.parse(body)
        feed_title = (parsed_feed.feed.get("title") if parsed_feed.feed else None) or None
        feed_link = (parsed_feed.feed.get("link") if parsed_feed.feed else None) or None

        items: list[RssItem] = []
        for entry in parsed_feed.entries[:max_items]:
            content = None
            if include_content:
                # feedparser stores content in a list-of-dicts under `content`.
                content_entries = entry.get("content") or []
                if content_entries and isinstance(content_entries, list):
                    first = content_entries[0]
                    value = first.get("value") if isinstance(first, dict) else None
                    if value:
                        content = value[:max_content_chars]
            summary = entry.get("summary")
            if summary:
                summary = summary[:max_content_chars]
            items.append(
                RssItem(
                    title=entry.get("title"),
                    link=entry.get("link"),
                    published=entry.get("published"),
                    summary=summary,
                    content=content,
                    id=entry.get("id") or entry.get("guid"),
                )
            )

        return RssReaderResponse(
            feed_title=feed_title,
            feed_link=feed_link,
            items=items,
            item_count=len(items),
        )

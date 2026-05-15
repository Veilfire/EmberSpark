"""Wikipedia plugin — search + article extract.

Free, no auth. Cleaner than scraping with `http_tool` for canonical
references.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity
from spark.errors import ErrorCode, SparkError
from spark.plugins._http_base import build_client, classify_connect_error


class WikipediaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(default="en", max_length=8)
    max_summary_chars: int = Field(default=4000, gt=0, le=20000)
    max_section_chars: int = Field(default=8000, gt=0, le=40000)
    user_agent: str = Field(
        default="spark-agent/0.1 (https://github.com/Veilfire/EmberSpark)",
        max_length=256,
    )
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    verify_ssl: bool = True


class _WikiArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["search", "summary", "section"] = Field(
        description=(
            "Which Wikipedia op. 'search' (title fuzzy match), "
            "'summary' (lede + infobox for one article), 'section' "
            "(fetch a named section by title)."
        ),
    )
    query: str | None = Field(default=None, max_length=512)
    title: str | None = Field(default=None, max_length=256)
    section: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=10, gt=0, le=50)


class WikiResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    ok: bool
    hits: list[dict[str, Any]] | None = None
    title: str | None = None
    extract: str | None = None
    url: str | None = None
    error: str | None = None


class WikipediaPlugin:
    name: ClassVar[str] = "wikipedia"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Wikipedia search + article extract. Free, no auth. Cleaner "
        "than search-and-scrape for canonical references."
    )
    input_schema: ClassVar[type[BaseModel]] = _WikiArgs
    output_schema: ClassVar[type[BaseModel]] = WikiResult
    config_schema: ClassVar[type[BaseModel]] = WikipediaConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: _WikiArgs, ctx: Any) -> WikiResult:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        lang = (cfg.get("language") or "en").strip()
        ua = cfg.get("user_agent") or "spark-agent/0.1"
        max_sum = int(cfg.get("max_summary_chars") or 4000)
        max_sec = int(cfg.get("max_section_chars") or 8000)

        async with build_client(cfg) as client:
            if args.action == "search":
                if not args.query:
                    raise SparkError(
                        ErrorCode.INPUT_SCHEMA_INVALID,
                        "wikipedia: search requires query",
                        detail={"plugin": "wikipedia"},
                    )
                return await _do_search(client, lang, ua, args.query, args.limit)
            if args.action == "summary":
                if not args.title:
                    raise SparkError(
                        ErrorCode.INPUT_SCHEMA_INVALID,
                        "wikipedia: summary requires title",
                        detail={"plugin": "wikipedia"},
                    )
                return await _do_summary(client, lang, ua, args.title, max_sum)
            if args.action == "section":
                if not args.title or not args.section:
                    raise SparkError(
                        ErrorCode.INPUT_SCHEMA_INVALID,
                        "wikipedia: section requires title and section",
                        detail={"plugin": "wikipedia"},
                    )
                return await _do_section(
                    client, lang, ua, args.title, args.section, max_sec
                )
            raise SparkError(
                ErrorCode.INPUT_SCHEMA_INVALID,
                f"wikipedia: unknown action {args.action!r}",
                detail={"plugin": "wikipedia"},
            )


def _api(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def _rest(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/api/rest_v1"


async def _do_search(
    client: httpx.AsyncClient, lang: str, ua: str, query: str, limit: int
) -> WikiResult:
    url = _api(lang)
    headers = {"User-Agent": ua, "Accept": "application/json"}
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(limit),
        "format": "json",
        "formatversion": "2",
    }
    try:
        resp = await client.get(url, headers=headers, params=params)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=url, plugin_name="wikipedia") from exc
    if resp.status_code >= 400:
        return WikiResult(action="search", ok=False, error=resp.text[:200])
    data = resp.json()
    hits = data.get("query", {}).get("search", []) or []
    out = [
        {
            "title": h.get("title"),
            "snippet": _strip_html(h.get("snippet", "")),
            "wordcount": h.get("wordcount"),
            "url": f"https://{lang}.wikipedia.org/wiki/{quote(h.get('title', '').replace(' ', '_'))}",
        }
        for h in hits
    ]
    return WikiResult(action="search", ok=True, hits=out)


async def _do_summary(
    client: httpx.AsyncClient, lang: str, ua: str, title: str, max_chars: int
) -> WikiResult:
    url = f"{_rest(lang)}/page/summary/{quote(title.replace(' ', '_'))}"
    headers = {"User-Agent": ua, "Accept": "application/json"}
    try:
        resp = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=url, plugin_name="wikipedia") from exc
    if resp.status_code == 404:
        return WikiResult(
            action="summary", ok=False, error=f"article {title!r} not found"
        )
    if resp.status_code >= 400:
        return WikiResult(action="summary", ok=False, error=resp.text[:200])
    data = resp.json()
    extract = (data.get("extract") or "")[:max_chars]
    return WikiResult(
        action="summary",
        ok=True,
        title=data.get("title"),
        extract=extract,
        url=(data.get("content_urls") or {}).get("desktop", {}).get("page"),
    )


async def _do_section(
    client: httpx.AsyncClient,
    lang: str,
    ua: str,
    title: str,
    section: str,
    max_chars: int,
) -> WikiResult:
    # Use MediaWiki action=parse to get one named section's wikitext as html.
    url = _api(lang)
    headers = {"User-Agent": ua, "Accept": "application/json"}
    # 1) get sections list to find the index
    params = {
        "action": "parse",
        "page": title,
        "prop": "sections",
        "format": "json",
        "formatversion": "2",
    }
    try:
        resp = await client.get(url, headers=headers, params=params)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=url, plugin_name="wikipedia") from exc
    if resp.status_code >= 400:
        return WikiResult(action="section", ok=False, error=resp.text[:200])
    data = resp.json()
    sections = (data.get("parse") or {}).get("sections") or []
    match = None
    target = section.lower()
    for s in sections:
        if (s.get("line") or "").lower() == target:
            match = s.get("index")
            break
    if match is None:
        return WikiResult(
            action="section", ok=False, error=f"section {section!r} not found"
        )
    # 2) fetch that section's wikitext
    params2 = {
        "action": "parse",
        "page": title,
        "section": str(match),
        "prop": "wikitext",
        "format": "json",
        "formatversion": "2",
    }
    try:
        resp2 = await client.get(url, headers=headers, params=params2)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=url, plugin_name="wikipedia") from exc
    if resp2.status_code >= 400:
        return WikiResult(action="section", ok=False, error=resp2.text[:200])
    body = (
        (resp2.json().get("parse") or {}).get("wikitext", "")
        if resp2.json()
        else ""
    )[:max_chars]
    return WikiResult(action="section", ok=True, title=title, extract=body)


def _strip_html(s: str) -> str:
    """Trivial HTML strip for the search-snippet field."""
    import re  # noqa: PLC0415

    return re.sub(r"<[^>]+>", "", s).strip()

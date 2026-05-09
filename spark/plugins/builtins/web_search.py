"""Web search plugin.

Thin, provider-agnostic wrapper over the main search APIs. Operators pick a
provider (``brave``, ``serper``, ``tavily``, ``ddg_html``, ``bing``) and
supply an API key (age-vault secret reference) — except for ``ddg_html`` which
scrapes the no-key HTML results page.

The plugin normalizes every provider's response into a single ``SearchResult``
schema so the agent doesn't have to care about provider-specific JSON shapes.

All network calls run through the same SSRF defense as ``http_client``: IDN
normalization → IP validation against a per-provider host allowlist → IP
pinned transport. No redirects followed.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from spark.config.enums import Permission, Sensitivity

# Default endpoints + their allowed hostnames. These are baked in because
# the operator is picking the provider by name, not by URL — the whole
# point of the plugin is to abstract the URL away.
_PROVIDER_ENDPOINTS: dict[str, tuple[str, str]] = {
    # provider -> (endpoint url, host for allowlist)
    "brave": ("https://api.search.brave.com/res/v1/web/search", "api.search.brave.com"),
    "serper": ("https://google.serper.dev/search", "google.serper.dev"),
    "tavily": ("https://api.tavily.com/search", "api.tavily.com"),
    "ddg_html": ("https://html.duckduckgo.com/html/", "html.duckduckgo.com"),
    "bing": ("https://api.bing.microsoft.com/v7.0/search", "api.bing.microsoft.com"),
}


class WebSearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["brave", "serper", "tavily", "ddg_html", "bing"] = "ddg_html"
    api_key_secret: str = Field(
        default="web_search_key",
        description=(
            "Name of the secret (in the age vault) holding the provider API key. "
            "Unused for the 'ddg_html' provider, which scrapes results without authentication."
        ),
    )
    max_results: int = Field(default=10, ge=1, le=50)
    safe_search: Literal["off", "moderate", "strict"] = "moderate"
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    user_agent: str = Field(default="spark-runtime-web-search/0.1", max_length=256)


class WebSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        min_length=1,
        max_length=500,
        description="Search query string. Plain text; provider handles its own quoting.",
    )
    # Don't use ``Field(ge=1, le=50)`` — that emits ``minimum``/``maximum``
    # in the JSON Schema, and Bedrock's tool-calling subset rejects them
    # on ``number`` types. The model would then see *no* tools at all.
    # Same posture as the reflection schema: clamp post-parse instead.
    max_results: int = Field(
        default=10,
        description="Maximum results to return (clamped to 1..50).",
    )
    language: str | None = Field(
        default=None,
        max_length=16,
        description="ISO 639 language hint (e.g. 'en', 'es'). Optional.",
    )
    country: str | None = Field(
        default=None,
        max_length=8,
        description="ISO 3166 country hint (e.g. 'us', 'gb'). Optional.",
    )

    @field_validator("max_results")
    @classmethod
    def _clamp_max_results(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > 50:
            return 50
        return v


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    url: str
    snippet: str | None = None
    published_at: str | None = None


class WebSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    query: str
    results: list[SearchResult]
    result_count: int


class WebSearchPlugin:
    name: ClassVar[str] = "web_search"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Provider-agnostic web search. Operator picks Brave, Serper, Tavily, "
        "DuckDuckGo HTML, or Bing and supplies an API key via a secret reference."
    )
    input_schema: ClassVar[type[BaseModel]] = WebSearchArgs
    output_schema: ClassVar[type[BaseModel]] = WebSearchResponse
    config_schema: ClassVar[type[BaseModel]] = WebSearchConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()  # resolved dynamically below
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: WebSearchArgs, ctx: Any) -> WebSearchResponse:
        from spark.utils.net import HostPolicy, pin_dns, validate_url

        cfg = getattr(ctx, "plugin_config", {}) or {}
        provider = cfg.get("provider") or "brave"
        if provider not in _PROVIDER_ENDPOINTS:
            raise PermissionError(f"unknown web_search provider {provider!r}")

        endpoint, host = _PROVIDER_ENDPOINTS[provider]
        api_key_secret = cfg.get("api_key_secret") or "web_search_key"

        # API key lookup — None is allowed for ddg_html.
        secrets = getattr(ctx, "secrets", {}) or {}
        api_key = secrets.get(api_key_secret)
        if provider != "ddg_html" and not api_key:
            raise PermissionError(
                f"web_search provider {provider!r} requires secret "
                f"{api_key_secret!r} but it was not injected into the context"
            )

        max_results = min(args.max_results, int(cfg.get("max_results") or 10))
        user_agent = cfg.get("user_agent") or "spark-runtime-web-search/0.1"
        connect_timeout = float(cfg.get("connect_timeout_seconds") or 5.0)
        read_timeout = float(cfg.get("read_timeout_seconds") or 15.0)

        policy = HostPolicy.from_list([host], allow_http=False, allow_redirects=False)

        # Build the per-provider request.
        method, params, headers, body = self._build_request(
            provider=provider,
            endpoint=endpoint,
            query=args.query,
            max_results=max_results,
            language=args.language,
            country=args.country,
            api_key=api_key,
            user_agent=user_agent,
            safe_search=cfg.get("safe_search") or "moderate",
        )

        request_url = endpoint
        if params:
            from urllib.parse import urlencode

            sep = "&" if "?" in endpoint else "?"
            request_url = f"{endpoint}{sep}{urlencode(params)}"
        target = validate_url(request_url, policy)
        headers.setdefault("User-Agent", user_agent)

        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, verify=True, trust_env=False
        ) as client:
            with pin_dns(target):
                response = await client.request(
                    method, request_url, headers=headers, content=body
                )
            response.raise_for_status()
            results = self._parse_response(provider, response.text, max_results)

        return WebSearchResponse(
            provider=provider,
            query=args.query,
            results=results,
            result_count=len(results),
        )

    # ------------------------------------------------------------------
    # Per-provider request/response plumbing
    # ------------------------------------------------------------------

    def _build_request(
        self,
        *,
        provider: str,
        endpoint: str,
        query: str,
        max_results: int,
        language: str | None,
        country: str | None,
        api_key: str | None,
        user_agent: str,
        safe_search: str,
    ) -> tuple[str, dict[str, str], dict[str, str], bytes | None]:
        """Return (method, query_params, headers, body_bytes)."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if provider == "brave":
            headers["X-Subscription-Token"] = api_key or ""
            params = {
                "q": query,
                "count": str(max_results),
                "safesearch": safe_search,
            }
            if language:
                params["search_lang"] = language
            if country:
                params["country"] = country
            return "GET", params, headers, None
        if provider == "serper":
            headers["X-API-KEY"] = api_key or ""
            headers["Content-Type"] = "application/json"
            body = json.dumps({"q": query, "num": max_results}).encode("utf-8")
            return "POST", {}, headers, body
        if provider == "tavily":
            headers["Content-Type"] = "application/json"
            body = json.dumps(
                {
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                }
            ).encode("utf-8")
            return "POST", {}, headers, body
        if provider == "bing":
            headers["Ocp-Apim-Subscription-Key"] = api_key or ""
            params = {"q": query, "count": str(max_results), "safeSearch": safe_search.capitalize()}
            if language:
                params["mkt"] = language
            return "GET", params, headers, None
        if provider == "ddg_html":
            # No API key, HTML response — ddg_html is the escape hatch when
            # you don't want to supply any secret.
            headers["Accept"] = "text/html"
            headers["User-Agent"] = user_agent
            params = {"q": query}
            return "GET", params, headers, None
        raise PermissionError(f"unsupported provider {provider!r}")

    def _parse_response(
        self, provider: str, body: str, max_results: int
    ) -> list[SearchResult]:
        if provider == "brave":
            data = json.loads(body)
            web = (data.get("web") or {}).get("results") or []
            return [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description"),
                    published_at=item.get("age"),
                )
                for item in web[:max_results]
            ]
        if provider == "serper":
            data = json.loads(body)
            organic = data.get("organic") or []
            return [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet"),
                    published_at=item.get("date"),
                )
                for item in organic[:max_results]
            ]
        if provider == "tavily":
            data = json.loads(body)
            items = data.get("results") or []
            return [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content"),
                    published_at=None,
                )
                for item in items[:max_results]
            ]
        if provider == "bing":
            data = json.loads(body)
            pages = (data.get("webPages") or {}).get("value") or []
            return [
                SearchResult(
                    title=item.get("name", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet"),
                    published_at=item.get("dateLastCrawled"),
                )
                for item in pages[:max_results]
            ]
        if provider == "ddg_html":
            return _parse_ddg_html(body, max_results)
        return []


def _parse_ddg_html(body: str, max_results: int) -> list[SearchResult]:
    """Minimal DuckDuckGo HTML results parser.

    DDG's HTML layout is stable enough for a plain regex extraction. We
    deliberately do NOT pull in BeautifulSoup for one plugin.
    """
    import re

    # Each result is <a class="result__a" href="...">TITLE</a> followed by
    # <a class="result__snippet">SNIPPET</a>. DDG URL-wraps the target in
    # /l/?uddg=... so we need to strip that.
    link_re = re.compile(
        r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_re = re.compile(
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    tag_re = re.compile(r"<[^>]+>")

    links = link_re.findall(body)
    snippets = snippet_re.findall(body)

    def _unescape(text: str) -> str:
        import html as _html

        return _html.unescape(tag_re.sub("", text)).strip()

    def _unwrap(url: str) -> str:
        if url.startswith("/l/?"):
            from urllib.parse import parse_qs
            from urllib.parse import urlparse as _urlparse

            parsed = _urlparse(url)
            uddg = parse_qs(parsed.query).get("uddg")
            if uddg:
                return uddg[0]
        return url

    results: list[SearchResult] = []
    for idx, (href, title_html) in enumerate(links[:max_results]):
        snippet = snippets[idx] if idx < len(snippets) else None
        results.append(
            SearchResult(
                title=_unescape(title_html),
                url=_unwrap(href),
                snippet=_unescape(snippet) if snippet else None,
                published_at=None,
            )
        )
    return results

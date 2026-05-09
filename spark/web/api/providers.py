"""Provider model listing routes.

Fetches available models from each LLM provider using the operator's
API key from the secrets vault. Results are cached for 5 minutes to
avoid hammering upstream APIs on every dropdown open.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from spark.logging import get_logger
from spark.web.auth import Principal, require_viewer

router = APIRouter()
log = get_logger("spark.providers.api")

# Simple in-memory cache: {provider: (timestamp, [models])}
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cached(provider: str) -> list[dict[str, str]] | None:
    entry = _cache.get(provider)
    if entry and time.time() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _set_cache(provider: str, models: list[dict[str, str]]) -> None:
    _cache[provider] = (time.time(), models)


@router.post("/{provider}/test")
async def test_provider(
    provider: str, _: Principal = Depends(require_viewer)
) -> dict[str, Any]:
    """Test that the stored API key for a provider is valid."""
    if provider not in ("openrouter", "openai", "anthropic", "ollama"):
        raise HTTPException(status_code=400, detail=f"unknown provider {provider!r}")

    import httpx  # noqa: PLC0415
    from spark.runtime import get_secret_manager  # noqa: PLC0415

    mgr = get_secret_manager()

    try:
        if provider == "openrouter":
            key = mgr.get("openrouter_key").get_secret_value()
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {key}"},
                )
            if r.status_code == 200:
                return {"ok": True, "detail": "Valid OpenRouter key"}
            return {"ok": False, "detail": f"HTTP {r.status_code}"}
        elif provider == "openai":
            key = mgr.get("openai_key").get_secret_value()
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
            if r.status_code == 200:
                return {"ok": True, "detail": "Valid OpenAI key"}
            return {"ok": False, "detail": f"HTTP {r.status_code}"}
        elif provider == "anthropic":
            key = mgr.get("anthropic_key").get_secret_value()
            # Anthropic doesn't have a cheap ping endpoint — smallest tokens request
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
            if r.status_code in (200, 400):
                # 400 can mean invalid model but key works.
                return {"ok": True, "detail": "Valid Anthropic key"}
            return {"ok": False, "detail": f"HTTP {r.status_code}"}
        elif provider == "ollama":
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get("http://localhost:11434/api/tags")
            if r.status_code == 200:
                count = len(r.json().get("models", []))
                return {"ok": True, "detail": f"Ollama running, {count} models"}
            return {"ok": False, "detail": "Ollama not reachable"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": False, "detail": "unknown provider"}


@router.get("/{provider}/models")
async def list_models(
    provider: str,
    q: str = "",
    _: Principal = Depends(require_viewer),
) -> list[dict[str, str]]:
    """List models for a provider. Optionally filter by `q` (substring match)."""
    if provider not in ("openrouter", "openai", "anthropic", "ollama"):
        raise HTTPException(status_code=400, detail=f"unknown provider {provider!r}")

    cached = _cached(provider)
    if cached is None:
        try:
            if provider == "openrouter":
                cached = await _fetch_openrouter()
            elif provider == "openai":
                cached = await _fetch_openai()
            elif provider == "anthropic":
                cached = _static_anthropic()
            elif provider == "ollama":
                cached = await _fetch_ollama()
            else:
                cached = []
            _set_cache(provider, cached)
        except Exception as exc:
            log.warning("provider.models_fetch_failed", provider=provider, error=str(exc))
            raise HTTPException(
                status_code=502, detail=f"failed to fetch models from {provider}: {exc}"
            ) from exc

    if q:
        q_lower = q.lower()
        cached = [m for m in cached if q_lower in m["id"].lower()]

    return cached


async def _fetch_openrouter() -> list[dict[str, str]]:
    """Fetch from https://openrouter.ai/api/v1/models (public, no key needed)."""
    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get("https://openrouter.ai/api/v1/models")
        resp.raise_for_status()
        data = resp.json()

    models: list[dict[str, str]] = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        name = m.get("name", model_id)
        if model_id:
            models.append({"id": model_id, "name": name})
    models.sort(key=lambda m: m["id"])
    return models


async def _fetch_openai() -> list[dict[str, str]]:
    """Fetch from OpenAI /v1/models using the stored API key."""
    import httpx  # noqa: PLC0415
    from spark.runtime import get_secret_manager  # noqa: PLC0415

    mgr = get_secret_manager()
    try:
        key = mgr.get("openai_key").get_secret_value()
    except Exception:
        return _static_openai()

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        resp.raise_for_status()
        data = resp.json()

    models: list[dict[str, str]] = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        if model_id and not model_id.startswith("ft:"):
            models.append({"id": model_id, "name": model_id})
    models.sort(key=lambda m: m["id"])
    return models


def _static_openai() -> list[dict[str, str]]:
    """Fallback when no API key is available."""
    ids = [
        "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4",
        "o1", "o1-mini", "o1-pro",
        "gpt-3.5-turbo",
    ]
    return [{"id": i, "name": i} for i in ids]


def _static_anthropic() -> list[dict[str, str]]:
    """Anthropic doesn't have a public models list API."""
    ids = [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ]
    return [{"id": i, "name": i} for i in ids]


async def _fetch_ollama() -> list[dict[str, str]]:
    """Fetch from local Ollama instance at /api/tags."""
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return _static_ollama()

    models: list[dict[str, str]] = []
    for m in data.get("models", []):
        name = m.get("name", "")
        if name:
            models.append({"id": name, "name": name})
    models.sort(key=lambda m: m["id"])
    return models


def _static_ollama() -> list[dict[str, str]]:
    """Fallback when Ollama isn't reachable."""
    ids = ["llama3.1", "llama3.2", "mistral", "codellama", "gemma2", "phi3"]
    return [{"id": i, "name": i} for i in ids]

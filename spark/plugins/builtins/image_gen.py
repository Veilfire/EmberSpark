"""Image generation plugin.

Wraps provider image-generation APIs (OpenAI / Stability / Replicate) and
writes the generated image file to the data volume's deliverables
directory, where the web UI's Downloads page surfaces it and the
notification bell (Phase G3) fires a ``download_ready`` event.

Operator picks the provider by name; the plugin handles the provider-
specific request shape. The API key is a secret reference.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from spark.config.enums import Permission, Sensitivity

Provider = Literal["openai", "stability", "replicate"]
OutputFormat = Literal["png", "webp", "jpeg"]


_PROVIDER_ENDPOINTS: dict[str, tuple[str, str]] = {
    "openai": ("https://api.openai.com/v1/images/generations", "api.openai.com"),
    "stability": (
        "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
        "api.stability.ai",
    ),
    "replicate": ("https://api.replicate.com/v1/predictions", "api.replicate.com"),
}


class ImageGenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Provider = "openai"
    api_key_secret: str = Field(default="image_gen_key", max_length=128)
    default_model: str = Field(default="dall-e-3", max_length=128)
    default_size: Literal[
        "512x512", "1024x1024", "1792x1024", "1024x1792"
    ] = "1024x1024"
    max_prompt_chars: int = Field(default=4000, ge=1, le=16_000)
    max_images_per_call: int = Field(default=4, ge=1, le=10)
    output_format: OutputFormat = "png"
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    subdirectory: str = Field(
        default="generated",
        max_length=64,
        description="Subdirectory inside the data volume's deliverables path.",
    )

    @field_validator("subdirectory")
    @classmethod
    def _subdirectory_is_safe(cls, v: str) -> str:
        # Must be a single path segment: no slashes, no parent refs,
        # no leading dot, no NUL bytes.
        if not v:
            raise ValueError("subdirectory must be non-empty")
        if "/" in v or "\\" in v:
            raise ValueError("subdirectory must be a single path segment")
        if v.startswith(".") or v in {".", ".."}:
            raise ValueError("subdirectory must not start with '.' or be '..'")
        if "\x00" in v:
            raise ValueError("subdirectory must not contain NUL bytes")
        return v


class ImageGenArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(
        min_length=1,
        max_length=16_000,
        description="Natural-language image prompt sent to the configured provider.",
    )
    # See WebSearchArgs for why we clamp via a validator instead of
    # ``Field(ge=1, le=10)`` — Bedrock's tool-binding JSON Schema
    # subset rejects ``minimum``/``maximum`` on ``number`` types.
    n: int = Field(
        default=1,
        description="Number of images to generate (clamped to 1..10, capped further by operator config).",
    )
    size: Literal["512x512", "1024x1024", "1792x1024", "1024x1792"] | None = Field(
        default=None,
        description="Image size. Falls back to the operator's default_size when omitted.",
    )
    model: str | None = Field(
        default=None,
        max_length=128,
        description="Provider-specific model name override (e.g. 'dall-e-3', 'stability-xl-1.0').",
    )

    @field_validator("n")
    @classmethod
    def _clamp_n(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > 10:
            return 10
        return v


class GeneratedImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    filename: str
    size_bytes: int
    provider_id: str | None = None


class ImageGenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str
    prompt: str
    images: list[GeneratedImage]
    image_count: int


class ImageGenPlugin:
    name: ClassVar[str] = "image_gen"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Provider-agnostic image generation. Writes output files to the "
        "data volume's deliverables directory."
    )
    input_schema: ClassVar[type[BaseModel]] = ImageGenArgs
    output_schema: ClassVar[type[BaseModel]] = ImageGenResponse
    config_schema: ClassVar[type[BaseModel]] = ImageGenConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ, Permission.FS_WRITE}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: ImageGenArgs, ctx: Any) -> ImageGenResponse:
        from spark.utils.net import HostPolicy, pin_dns, validate_url

        deliverables = getattr(ctx, "deliverables_path", None)
        if deliverables is None:
            raise PermissionError(
                "image_gen: deliverables_path is None — enable the data volume "
                "in SparkRuntime config so the plugin has somewhere to write."
            )

        cfg = getattr(ctx, "plugin_config", {}) or {}
        provider = cfg.get("provider") or "openai"
        if provider not in _PROVIDER_ENDPOINTS:
            raise PermissionError(f"image_gen: unknown provider {provider!r}")
        endpoint, host = _PROVIDER_ENDPOINTS[provider]
        api_key_secret = cfg.get("api_key_secret") or "image_gen_key"
        model_name = args.model or cfg.get("default_model") or "dall-e-3"
        size = args.size or cfg.get("default_size") or "1024x1024"
        max_prompt = int(cfg.get("max_prompt_chars") or 4000)
        max_n = int(cfg.get("max_images_per_call") or 4)
        output_format = cfg.get("output_format") or "png"
        connect_timeout = float(cfg.get("connect_timeout_seconds") or 10.0)
        read_timeout = float(cfg.get("read_timeout_seconds") or 60.0)
        subdir = cfg.get("subdirectory") or "generated"

        if len(args.prompt) > max_prompt:
            raise PermissionError(
                f"image_gen: prompt is {len(args.prompt)} chars (max {max_prompt})"
            )
        n = min(args.n, max_n)

        secrets = getattr(ctx, "secrets", {}) or {}
        api_key = secrets.get(api_key_secret)
        if not api_key:
            raise PermissionError(
                f"image_gen: secret {api_key_secret!r} not injected into context"
            )

        # Build + send request.
        policy = HostPolicy.from_list([host], allow_http=False, allow_redirects=False)
        method, headers, body = self._build_request(
            provider=provider,
            api_key=api_key,
            model=model_name,
            prompt=args.prompt,
            n=n,
            size=size,
            output_format=output_format,
        )

        target = validate_url(endpoint, policy)

        timeout = httpx.Timeout(
            connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout
        )
        with pin_dns(target):
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, verify=True, trust_env=False
            ) as client:
                response = await client.request(
                    method, endpoint, headers=headers, content=body
                )
                response.raise_for_status()
                image_payloads = self._parse_response(provider, response)

        # Write to deliverables/<subdir>/<uuid>.<ext>.
        # Defensive validation: even with the config-level `field_validator`,
        # re-check that the computed target directory is INSIDE the resolved
        # deliverables root. Guards against raw dict config bypass and
        # against operator-side manipulation of the row between save and use.
        deliverables_root = Path(deliverables).expanduser().resolve()
        if "/" in subdir or "\\" in subdir or subdir in {"", ".", ".."} or subdir.startswith("."):
            raise PermissionError(
                f"image_gen: subdirectory {subdir!r} is not a safe path segment"
            )
        target_dir = (deliverables_root / subdir).resolve()
        try:
            target_dir.relative_to(deliverables_root)
        except ValueError as exc:
            raise PermissionError(
                f"image_gen: subdirectory {subdir!r} escapes deliverables root"
            ) from exc
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        images: list[GeneratedImage] = []
        for payload in image_payloads:
            fid = uuid.uuid4().hex
            filename = f"{fid}.{output_format}"
            file_path = (target_dir / filename).resolve()
            try:
                file_path.relative_to(deliverables_root)
            except ValueError as exc:
                raise PermissionError(
                    "image_gen: refused to write outside deliverables root"
                ) from exc
            file_path.write_bytes(payload.data)
            images.append(
                GeneratedImage(
                    path=str(file_path),
                    filename=filename,
                    size_bytes=len(payload.data),
                    provider_id=payload.provider_id,
                )
            )

        return ImageGenResponse(
            provider=provider,
            model=model_name,
            prompt=args.prompt,
            images=images,
            image_count=len(images),
        )

    # ------------------------------------------------------------------
    # Per-provider request/response plumbing
    # ------------------------------------------------------------------

    def _build_request(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        prompt: str,
        n: int,
        size: str,
        output_format: str,
    ) -> tuple[str, dict[str, str], bytes]:
        if provider == "openai":
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            body = json.dumps(
                {
                    "model": model,
                    "prompt": prompt,
                    "n": n,
                    "size": size,
                    "response_format": "b64_json",
                }
            ).encode("utf-8")
            return "POST", headers, body
        if provider == "stability":
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            w, h = size.split("x")
            body = json.dumps(
                {
                    "text_prompts": [{"text": prompt, "weight": 1}],
                    "samples": n,
                    "width": int(w),
                    "height": int(h),
                    "steps": 30,
                }
            ).encode("utf-8")
            return "POST", headers, body
        if provider == "replicate":
            headers = {
                "Authorization": f"Token {api_key}",
                "Content-Type": "application/json",
            }
            body = json.dumps(
                {
                    "version": model,
                    "input": {
                        "prompt": prompt,
                        "num_outputs": n,
                    },
                }
            ).encode("utf-8")
            return "POST", headers, body
        raise PermissionError(f"image_gen: unsupported provider {provider!r}")

    def _parse_response(
        self, provider: str, response: httpx.Response
    ) -> list[_ImagePayload]:
        data = response.json()
        results: list[_ImagePayload] = []
        if provider == "openai":
            for item in data.get("data", []):
                if "b64_json" in item:
                    results.append(
                        _ImagePayload(
                            data=base64.b64decode(item["b64_json"]),
                            provider_id=None,
                        )
                    )
                elif "url" in item:
                    # We deliberately do NOT follow the URL — that would
                    # require a second outbound network call against a
                    # non-allowlisted CDN host. If operators need URL-mode
                    # output, they should widen their `http_tool` config.
                    raise PermissionError(
                        "image_gen[openai]: URL-mode responses not supported; "
                        "provider must return base64 (`response_format: b64_json`)"
                    )
            return results
        if provider == "stability":
            for artifact in data.get("artifacts", []):
                if "base64" in artifact:
                    results.append(
                        _ImagePayload(
                            data=base64.b64decode(artifact["base64"]),
                            provider_id=artifact.get("seed"),
                        )
                    )
            return results
        if provider == "replicate":
            # Replicate is async — the initial response returns a prediction
            # id and the caller is expected to poll. For this v1 plugin we
            # only support providers that return images synchronously, so
            # we surface a clear error instead of silently long-polling.
            raise PermissionError(
                "image_gen[replicate]: synchronous response not supported; "
                "use OpenAI or Stability for now"
            )
        return results


class _ImagePayload:
    __slots__ = ("data", "provider_id")

    def __init__(self, data: bytes, provider_id: Any = None) -> None:
        self.data = data
        self.provider_id = str(provider_id) if provider_id is not None else None

"""Chat model factory.

Builds a `BaseChatModel` instance for each provider. Secrets are resolved at
build time through the SecretManager and held as `SecretStr`. The model
instance is created fresh per task and discarded on completion.

If the required provider SDK is not installed, we raise a clear error pointing
at the optional extra.
"""

from __future__ import annotations

from typing import Any

from spark.config.models import (
    AnthropicProviderConfig,
    OllamaProviderConfig,
    OpenAIProviderConfig,
    OpenRouterProviderConfig,
    ProviderConfig,
)
from spark.secrets import SecretManager


# Identifier sent to providers that support an application-label header
# (OpenRouter's X-Title, anything else that honors a UA-style brand).
# Agents can still override via their YAML provider block.
DEFAULT_APP_TITLE = "Veilfire EmberSpark"
DEFAULT_REFERER = "https://veilfire.io/emberspark"


class ProviderNotInstalled(RuntimeError):
    def __init__(self, provider: str, extra: str) -> None:
        super().__init__(
            f"Provider {provider!r} requires the optional extra: "
            f"pip install spark-runtime[{extra}]"
        )


def build_chat_model(config: ProviderConfig, secrets: SecretManager) -> Any:
    if isinstance(config, OpenAIProviderConfig):
        return _build_openai(config, secrets)
    if isinstance(config, AnthropicProviderConfig):
        return _build_anthropic(config, secrets)
    if isinstance(config, OpenRouterProviderConfig):
        return _build_openrouter(config, secrets)
    if isinstance(config, OllamaProviderConfig):
        return _build_ollama(config, secrets)
    raise TypeError(f"Unknown provider config: {type(config).__name__}")


def _build_openai(config: OpenAIProviderConfig, secrets: SecretManager) -> Any:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ProviderNotInstalled("openai", "openai") from exc

    api_key = secrets.get(config.api_key_ref)
    return ChatOpenAI(
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout_seconds,
        api_key=api_key,
        base_url=config.base_url,
        organization=config.organization,
    )


def _build_anthropic(config: AnthropicProviderConfig, secrets: SecretManager) -> Any:
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise ProviderNotInstalled("anthropic", "anthropic") from exc

    api_key = secrets.get(config.api_key_ref)
    return ChatAnthropic(
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens or 1024,
        timeout=config.timeout_seconds,
        api_key=api_key,
    )


def _build_openrouter(config: OpenRouterProviderConfig, secrets: SecretManager) -> Any:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ProviderNotInstalled("openrouter", "openrouter") from exc

    api_key = secrets.get(config.api_key_ref)
    # OpenRouter honors HTTP-Referer + X-Title to attribute requests in
    # its rankings / analytics. We default both so every deployment
    # shows up as "Veilfire EmberSpark" unless the operator chooses to
    # override in the agent YAML.
    default_headers = {
        "HTTP-Referer": config.referer or DEFAULT_REFERER,
        "X-Title": config.app_title or DEFAULT_APP_TITLE,
    }
    # ``extra_body={"usage": {"include": True}}`` is the OpenRouter-specific
    # opt-in that adds ``usage.cost`` (USD float) and ``cost_details`` to
    # the response. langchain-openai's ``extra_body`` field is forwarded
    # verbatim into the request JSON; using ``model_kwargs`` instead would
    # leak the kwarg into the OpenAI SDK call signature and fail with
    # ``TypeError: ... got an unexpected keyword argument 'usage'``.
    return ChatOpenAI(
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout_seconds,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers=default_headers,
        extra_body={"usage": {"include": True}},
    )


def _build_ollama(config: OllamaProviderConfig, secrets: SecretManager) -> Any:
    try:
        from langchain_ollama import ChatOllama
    except ImportError as exc:
        raise ProviderNotInstalled("ollama", "ollama") from exc

    # Ollama typically needs no key; base_url validation uses SSRF rules only
    # when the URL is non-local.
    return ChatOllama(
        model=config.model,
        temperature=config.temperature,
        num_predict=config.max_tokens,
        base_url=config.base_url,
    )

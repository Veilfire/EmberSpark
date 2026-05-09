"""Redaction pipeline.

Chain of processors:
  1. Secret-pattern regex (AWS, GCP, Stripe, JWT, PEM, GitHub, Slack, ...).
  2. High-entropy string detector (for token-like blobs).
  3. Presidio PII recognizer (names, emails, credit cards, SSN, IBAN, ...).

Each processor returns (text, applied_rules). The caller can log
`redaction_applied` = union of rules without leaking the raw content.

Presidio is loaded lazily and is *on* by default. Pass `use_presidio=False`
to skip it (e.g., for `privacy_mode: regex_only` configurations).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

_PLACEHOLDER = "[REDACTED:{label}]"

# Ordered: more specific first. Label becomes part of the placeholder token so
# downstream (model, memory) sees shape.
_REGEX_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS_ACCESS_KEY_ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS_SECRET_ACCESS_KEY", re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])")),
    ("OPENAI_KEY", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("OPENROUTER_KEY", re.compile(r"sk-or-[A-Za-z0-9-]{20,}")),
    ("ANTHROPIC_KEY", re.compile(r"sk-ant-[A-Za-z0-9-]{20,}")),
    ("GITHUB_TOKEN", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("SLACK_TOKEN", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("STRIPE_KEY", re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{24,}")),
    # Telegram bot tokens have the shape ``<digits>:<35 chars>`` — the
    # number before the colon is the bot user_id and isn't sensitive on
    # its own, but the suffix is the bearer credential. Match the whole
    # thing so we don't leak partial context.
    ("TELEGRAM_BOT_TOKEN", re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{35,}\b")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("PRIVATE_KEY_PEM", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("CLOUD_METADATA_URL", re.compile(r"https?://169\.254\.169\.254[^\s\"']*")),
)

# Lowered from 24 to 16 chars to catch shorter API-key-like tokens. The
# entropy threshold makes false positives on ordinary English rare.
_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9_\-+/=]{16,}")
_ENTROPY_THRESHOLD = 4.0  # bits/char


def _shannon(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(s)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


@dataclass(frozen=True)
class RedactionResult:
    text: str
    applied: tuple[str, ...]

    @property
    def any(self) -> bool:
        return bool(self.applied)


def _apply_regex(text: str) -> tuple[str, list[str]]:
    applied: list[str] = []
    out = text
    for label, pattern in _REGEX_RULES:
        new, n = pattern.subn(_PLACEHOLDER.format(label=label), out)
        if n:
            applied.append(label)
            out = new
    return out, applied


def _apply_entropy(text: str) -> tuple[str, list[str]]:
    applied: list[str] = []

    def repl(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if _shannon(candidate) >= _ENTROPY_THRESHOLD:
            applied.append("HIGH_ENTROPY")
            return _PLACEHOLDER.format(label="HIGH_ENTROPY")
        return candidate

    out = _ENTROPY_CANDIDATE.sub(repl, text)
    return out, applied


class _PresidioLazy:
    """Lazy holder around Presidio so import cost is deferred to first call."""

    _analyzer: Any | None = None
    _anonymizer: Any | None = None
    _disabled: bool = False

    @classmethod
    def get(cls) -> tuple[Any, Any] | None:
        if cls._disabled:
            return None
        if cls._analyzer is not None and cls._anonymizer is not None:
            return cls._analyzer, cls._anonymizer
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine
        except ImportError:  # pragma: no cover
            cls._disabled = True
            return None
        try:
            cls._analyzer = AnalyzerEngine()
            cls._anonymizer = AnonymizerEngine()
        except Exception:  # pragma: no cover — missing spaCy model etc.
            cls._disabled = True
            return None
        return cls._analyzer, cls._anonymizer

    @classmethod
    def disable(cls) -> None:
        cls._disabled = True


def disable_presidio() -> None:
    """Explicitly disable Presidio for regex_only mode."""
    _PresidioLazy.disable()


def _apply_presidio(text: str, *, score_threshold: float) -> tuple[str, list[str]]:
    pair = _PresidioLazy.get()
    if pair is None:
        return text, []
    analyzer, anonymizer = pair
    try:
        results = analyzer.analyze(text=text, language="en", score_threshold=score_threshold)
        if not results:
            return text, []
        anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
        labels = sorted({r.entity_type for r in results})
        return str(anonymized.text), labels
    except Exception:  # pragma: no cover — NER is best-effort
        return text, []


def redact(
    text: str,
    *,
    use_presidio: bool = True,
    presidio_threshold: float = 0.5,
) -> RedactionResult:
    """Apply the full redaction chain to a single string."""
    applied: list[str] = []
    out = text

    out, regex_applied = _apply_regex(out)
    applied.extend(regex_applied)

    out, entropy_applied = _apply_entropy(out)
    applied.extend(entropy_applied)

    if use_presidio:
        out, pres_applied = _apply_presidio(out, score_threshold=presidio_threshold)
        applied.extend(pres_applied)

    return RedactionResult(text=out, applied=tuple(applied))


def redact_structure(
    obj: Any,
    *,
    use_presidio: bool = True,
    presidio_threshold: float = 0.5,
) -> tuple[Any, list[str]]:
    """Walk a JSON-shaped structure and redact every string leaf."""
    all_applied: list[str] = []

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            result = redact(
                value,
                use_presidio=use_presidio,
                presidio_threshold=presidio_threshold,
            )
            all_applied.extend(result.applied)
            return result.text
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, tuple):
            return tuple(walk(v) for v in value)
        return value

    return walk(obj), all_applied

"""Data-class detectors used by the guardrail engine.

Each concrete classifier implements :class:`DataClassifier` and returns
zero-or-more :class:`DetectorHit` records tagged with the
:class:`DataClass` they identified, the byte span inside the scanned
text, a confidence score, and the rule id that fired. The guardrail
resolver decides what to *do* with the hits (allow / warn / redact /
block) — classifiers themselves stay dumb and fast.

Adding a new detector is one class + one registration call. Operators
can extend the CLI pattern catalog in ``cli_patterns.py`` without
touching this file.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Protocol, runtime_checkable

from spark.config.enums import DataClass
from spark.privacy.cli_patterns import CLI_PATTERNS, PROMPT_INJECTION_PATTERNS


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectorHit:
    """A single detector match inside a scanned string.

    ``tier`` marks which layer produced the hit. The fusion step uses
    this to give consensus bonuses when tier-1 (deterministic, usually
    checksummed) and tier-2 (statistical / NER) detectors agree, and to
    apply per-class policy knobs like ``require_consensus``.

    The ``start``/``end`` offsets reference the ORIGINAL input text.
    After ``apply_guardrails`` redacts, the returned ``text`` diverges;
    the hits list still carries pre-redaction offsets for auditing.
    """

    data_class: DataClass
    start: int
    end: int
    confidence: float  # 0..1
    rule_id: str
    # Pre-computed replacement text — redaction/enforcement uses this
    # directly instead of the raw matched substring.
    redaction: str = ""
    # "tier1" = deterministic / regex / checksummed classifier.
    # "tier2" = statistical / NER classifier (Presidio today).
    tier: str = "tier1"

    @property
    def length(self) -> int:
        return self.end - self.start


@runtime_checkable
class DataClassifier(Protocol):
    """Scan input text and yield hits for the classes this detector owns."""

    classes: frozenset[DataClass]

    def scan(self, text: str) -> list[DetectorHit]: ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _placeholder(cls: DataClass) -> str:
    return f"[REDACTED:{cls.value}]"


def _shannon(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(s)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


# ---------------------------------------------------------------------------
# Classifier: Luhn-validated credit cards
# ---------------------------------------------------------------------------


_CC_CANDIDATE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if d < 0 or d > 9:
            return False
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class LuhnCardClassifier:
    classes = frozenset({DataClass.FINANCIAL_CARD})

    def scan(self, text: str) -> list[DetectorHit]:
        hits: list[DetectorHit] = []
        for m in _CC_CANDIDATE.finditer(text):
            raw = m.group(0)
            digits = re.sub(r"[ -]", "", raw)
            if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                hits.append(
                    DetectorHit(
                        data_class=DataClass.FINANCIAL_CARD,
                        start=m.start(),
                        end=m.end(),
                        confidence=0.98,
                        rule_id="luhn",
                        redaction=_placeholder(DataClass.FINANCIAL_CARD),
                    )
                )
        return hits


# ---------------------------------------------------------------------------
# Classifier: IBAN + US routing (financial.bank)
# ---------------------------------------------------------------------------


_IBAN_CANDIDATE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_US_ROUTING = re.compile(r"(?<!\d)\d{9}(?!\d)")
_SWIFT_BIC = re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b")


def _iban_checksum_ok(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(
        str(ord(c) - 55) if c.isalpha() else c for c in rearranged.upper()
    )
    try:
        return int(numeric) % 97 == 1
    except ValueError:
        return False


def _aba_routing_ok(n: str) -> bool:
    if len(n) != 9 or not n.isdigit():
        return False
    d = [int(c) for c in n]
    check = (
        3 * (d[0] + d[3] + d[6])
        + 7 * (d[1] + d[4] + d[7])
        + (d[2] + d[5] + d[8])
    ) % 10
    return check == 0


_ROUTING_CONTEXT = re.compile(
    r"\b(?:routing|aba|rtn|rt\s*#|ach|bank|wire|transit)\b",
    re.IGNORECASE,
)


def _has_routing_context(text: str, start: int, end: int, window: int = 48) -> bool:
    """Routing numbers are 9-digit sequences — a shape too common to flag
    without a nearby keyword. Checks ``window`` chars before and after.
    """
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    return bool(_ROUTING_CONTEXT.search(text[lo:hi]))


class BankClassifier:
    classes = frozenset({DataClass.FINANCIAL_BANK})

    def scan(self, text: str) -> list[DetectorHit]:
        hits: list[DetectorHit] = []
        for m in _IBAN_CANDIDATE.finditer(text):
            if _iban_checksum_ok(m.group(0)):
                hits.append(
                    DetectorHit(
                        data_class=DataClass.FINANCIAL_BANK,
                        start=m.start(),
                        end=m.end(),
                        confidence=0.97,
                        rule_id="iban",
                        redaction=_placeholder(DataClass.FINANCIAL_BANK),
                    )
                )
        for m in _US_ROUTING.finditer(text):
            # Plain 9-digit runs are too common (zip+4, phone without
            # dashes, invoice numbers). Require BOTH the ABA checksum AND
            # a routing-related keyword nearby.
            if not _aba_routing_ok(m.group(0)):
                continue
            if not _has_routing_context(text, m.start(), m.end()):
                continue
            hits.append(
                DetectorHit(
                    data_class=DataClass.FINANCIAL_BANK,
                    start=m.start(),
                    end=m.end(),
                    confidence=0.85,
                    rule_id="us-routing-aba",
                    redaction=_placeholder(DataClass.FINANCIAL_BANK),
                )
            )
        for m in _SWIFT_BIC.finditer(text):
            hits.append(
                DetectorHit(
                    data_class=DataClass.FINANCIAL_BANK,
                    start=m.start(),
                    end=m.end(),
                    confidence=0.55,
                    rule_id="swift-bic",
                    redaction=_placeholder(DataClass.FINANCIAL_BANK),
                )
            )
        return hits


# ---------------------------------------------------------------------------
# Classifier: Government IDs (pii.gov_id)
# ---------------------------------------------------------------------------


# SSN (US): ###-##-####; exclude known-invalid area codes.
_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
# US Passport: single letter + 8 digits.
_US_PASSPORT = re.compile(r"(?<![A-Z0-9])[A-Z]\d{8}(?![A-Z0-9])")
# ITIN: starts with 9, 2nd segment 70-88 / 90-92 / 94-99.
_ITIN = re.compile(r"\b9\d{2}-(?:7\d|8[0-8]|9[0-24-9])-\d{4}\b")


class GovIdClassifier:
    classes = frozenset({DataClass.PII_GOV_ID})

    def scan(self, text: str) -> list[DetectorHit]:
        hits: list[DetectorHit] = []
        for pattern, rule in (
            (_SSN, "us-ssn"),
            (_ITIN, "us-itin"),
            (_US_PASSPORT, "us-passport"),
        ):
            for m in pattern.finditer(text):
                hits.append(
                    DetectorHit(
                        data_class=DataClass.PII_GOV_ID,
                        start=m.start(),
                        end=m.end(),
                        confidence=0.9 if rule != "us-passport" else 0.6,
                        rule_id=rule,
                        redaction=_placeholder(DataClass.PII_GOV_ID),
                    )
                )
        return hits


# ---------------------------------------------------------------------------
# Classifier: API credentials (credentials.api) + PEM (credentials.pem)
# ---------------------------------------------------------------------------


_API_KEY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("openai", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("openrouter", re.compile(r"sk-or-[A-Za-z0-9-]{20,}")),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9-]{20,}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("slack", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("stripe", re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{24,}")),
    ("telegram-bot-token", re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{35,}\b")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
]

_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")
_ENTROPY_THRESHOLD = 4.2  # stricter than redaction.py — lower FP rate here


class ApiKeyClassifier:
    classes = frozenset({DataClass.CREDENTIALS_API})

    def scan(self, text: str) -> list[DetectorHit]:
        hits: list[DetectorHit] = []
        seen_spans: set[tuple[int, int]] = set()
        for rule_id, pattern in _API_KEY_RULES:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                hits.append(
                    DetectorHit(
                        data_class=DataClass.CREDENTIALS_API,
                        start=span[0],
                        end=span[1],
                        confidence=0.97,
                        rule_id=rule_id,
                        redaction=_placeholder(DataClass.CREDENTIALS_API),
                    )
                )
        # High-entropy catch-all for "key=sk_something" generic tokens.
        for m in _ENTROPY_CANDIDATE.finditer(text):
            span = (m.start(), m.end())
            if span in seen_spans:
                continue
            if _shannon(m.group(0)) >= _ENTROPY_THRESHOLD:
                seen_spans.add(span)
                hits.append(
                    DetectorHit(
                        data_class=DataClass.CREDENTIALS_API,
                        start=span[0],
                        end=span[1],
                        confidence=0.6,
                        rule_id="high-entropy",
                        redaction=_placeholder(DataClass.CREDENTIALS_API),
                    )
                )
        return hits


_PEM_MARKER = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    r"[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)


class PemKeyClassifier:
    classes = frozenset({DataClass.CREDENTIALS_PEM})

    def scan(self, text: str) -> list[DetectorHit]:
        return [
            DetectorHit(
                data_class=DataClass.CREDENTIALS_PEM,
                start=m.start(),
                end=m.end(),
                confidence=0.99,
                rule_id="pem-block",
                redaction=_placeholder(DataClass.CREDENTIALS_PEM),
            )
            for m in _PEM_MARKER.finditer(text)
        ]


# ---------------------------------------------------------------------------
# Classifier: Secrets vault — exact-match against known values
# ---------------------------------------------------------------------------


class SecretsVaultClassifier:
    """Flag any substring that equals a value tracked by ``SecretManager``.

    Callers pass in the current known-values snapshot (the resolver owns
    invalidation). The classifier never logs or retains the values.
    """

    classes = frozenset({DataClass.SECRETS_VAULT})

    def __init__(self, values_provider: "callable[[], frozenset[str]]") -> None:
        self._values_provider = values_provider

    # Skip absurdly short "secrets" — they cause false positives on
    # common substrings. Any real secret is a dozen+ characters.
    MIN_VALUE_LEN = 12

    def scan(self, text: str) -> list[DetectorHit]:
        values = self._values_provider()
        if not values or not text:
            return []
        hits: list[DetectorHit] = []
        for value in values:
            if not value or len(value) < self.MIN_VALUE_LEN:
                continue
            start = 0
            while True:
                idx = text.find(value, start)
                if idx < 0:
                    break
                hits.append(
                    DetectorHit(
                        data_class=DataClass.SECRETS_VAULT,
                        start=idx,
                        end=idx + len(value),
                        confidence=1.0,
                        rule_id="vault-exact",
                        redaction=_placeholder(DataClass.SECRETS_VAULT),
                    )
                )
                start = idx + len(value)
        return hits


# ---------------------------------------------------------------------------
# Classifier: Dangerous CLI patterns
# ---------------------------------------------------------------------------


class DangerousCliClassifier:
    classes = frozenset(
        {
            DataClass.CLI_DESTRUCTIVE,
            DataClass.CLI_PRIVILEGE,
            DataClass.CLI_PIPE_EXEC,
            DataClass.CLI_EXFILTRATION,
        }
    )

    def scan(self, text: str) -> list[DetectorHit]:
        hits: list[DetectorHit] = []
        for rule_id, cls, pattern in CLI_PATTERNS:
            for m in pattern.finditer(text):
                hits.append(
                    DetectorHit(
                        data_class=cls,
                        start=m.start(),
                        end=m.end(),
                        confidence=0.9,
                        rule_id=rule_id,
                        redaction=_placeholder(cls),
                    )
                )
        return hits


# ---------------------------------------------------------------------------
# Classifier: Prompt injection heuristics
# ---------------------------------------------------------------------------


class PromptInjectionClassifier:
    classes = frozenset({DataClass.PROMPT_INJECTION})

    def scan(self, text: str) -> list[DetectorHit]:
        return [
            DetectorHit(
                data_class=DataClass.PROMPT_INJECTION,
                start=m.start(),
                end=m.end(),
                confidence=0.7,
                rule_id=rule_id,
                redaction=_placeholder(DataClass.PROMPT_INJECTION),
            )
            for rule_id, pattern in PROMPT_INJECTION_PATTERNS
            for m in pattern.finditer(text)
        ]


# ---------------------------------------------------------------------------
# Classifier: Presidio wrapper — pii.basic / pii.name
# ---------------------------------------------------------------------------


# Map Presidio entity_type strings to our DataClass enum. Missing keys
# are skipped (no class to assign to).
_PRESIDIO_ENTITY_MAP: dict[str, DataClass] = {
    "EMAIL_ADDRESS": DataClass.PII_BASIC,
    "PHONE_NUMBER": DataClass.PII_BASIC,
    "LOCATION": DataClass.PII_BASIC,
    "IP_ADDRESS": DataClass.PII_BASIC,
    "URL": DataClass.PII_BASIC,
    "DATE_TIME": DataClass.PII_BASIC,
    "PERSON": DataClass.PII_NAME,
    "NRP": DataClass.PII_NAME,
    "MEDICAL_LICENSE": DataClass.PII_MEDICAL,
    # Financial / gov ids are caught by our stricter rule-based
    # classifiers above; we still accept Presidio as a backup.
    "CREDIT_CARD": DataClass.FINANCIAL_CARD,
    "IBAN_CODE": DataClass.FINANCIAL_BANK,
    "US_SSN": DataClass.PII_GOV_ID,
    "US_PASSPORT": DataClass.PII_GOV_ID,
    "US_DRIVER_LICENSE": DataClass.PII_GOV_ID,
    "US_ITIN": DataClass.PII_GOV_ID,
    "US_BANK_NUMBER": DataClass.FINANCIAL_BANK,
    "UK_NHS": DataClass.PII_GOV_ID,
    "AU_ABN": DataClass.PII_GOV_ID,
    "AU_ACN": DataClass.PII_GOV_ID,
    "AU_TFN": DataClass.PII_GOV_ID,
    "AU_MEDICARE": DataClass.PII_GOV_ID,
    "CRYPTO": DataClass.FINANCIAL_CRYPTO,
}


class PresidioClassifier:
    classes = frozenset(set(_PRESIDIO_ENTITY_MAP.values()))

    def __init__(self, threshold: float = 0.5) -> None:
        self._threshold = threshold

    def scan(self, text: str) -> list[DetectorHit]:
        from spark.privacy.redaction import _PresidioLazy  # noqa: PLC0415

        pair = _PresidioLazy.get()
        if pair is None:
            return []
        analyzer, _ = pair
        try:
            results = analyzer.analyze(
                text=text, language="en", score_threshold=self._threshold
            )
        except Exception:  # pragma: no cover — NER is best-effort
            return []
        hits: list[DetectorHit] = []
        for r in results:
            cls = _PRESIDIO_ENTITY_MAP.get(r.entity_type)
            if cls is None:
                continue
            hits.append(
                DetectorHit(
                    data_class=cls,
                    start=int(r.start),
                    end=int(r.end),
                    confidence=float(r.score),
                    rule_id=f"presidio:{r.entity_type}",
                    redaction=_placeholder(cls),
                    tier="tier2",
                )
            )
        return hits


# ---------------------------------------------------------------------------
# Registry + dispatch
# ---------------------------------------------------------------------------


@dataclass
class ClassifierRegistry:
    """Holds the active classifier set. Lazily instantiated."""

    _all: list[DataClassifier] = field(default_factory=list)

    def register(self, classifier: DataClassifier) -> None:
        self._all.append(classifier)

    def classifiers_for(
        self, classes: Iterable[DataClass]
    ) -> list[DataClassifier]:
        wanted = set(classes)
        return [c for c in self._all if c.classes & wanted]


_registry: ClassifierRegistry | None = None


def get_registry() -> ClassifierRegistry:
    global _registry
    if _registry is not None:
        return _registry
    reg = ClassifierRegistry()
    reg.register(LuhnCardClassifier())
    reg.register(BankClassifier())
    reg.register(GovIdClassifier())
    reg.register(ApiKeyClassifier())
    reg.register(PemKeyClassifier())
    reg.register(DangerousCliClassifier())
    reg.register(PromptInjectionClassifier())
    # Presidio is optional: wrapped in a try/except at scan-time so
    # it's safe to register even when the extra isn't installed.
    reg.register(PresidioClassifier())
    # Vault classifier is registered by the runtime (it needs the
    # secret manager's known_values callback). See
    # ``runtime.bootstrap`` — if the secret manager is present there,
    # it calls ``register_vault_classifier`` at boot.
    _registry = reg
    return reg


def register_vault_classifier(values_provider: "callable[[], frozenset[str]]") -> None:
    """Register the secrets-vault classifier with the runtime's provider.

    Idempotent — calling twice replaces the previous registration."""
    reg = get_registry()
    reg._all = [c for c in reg._all if not isinstance(c, SecretsVaultClassifier)]
    reg.register(SecretsVaultClassifier(values_provider))


def run_classifiers(
    text: str, *, enabled_classes: frozenset[DataClass]
) -> list[DetectorHit]:
    """Run every classifier that owns at least one of ``enabled_classes``.

    Hits that land in an un-enabled class are filtered out — a single
    classifier can own multiple classes (e.g. Presidio) and the caller
    only wants the ones their policy asked about.
    """
    if not text or not enabled_classes:
        return []
    reg = get_registry()
    out: list[DetectorHit] = []
    for c in reg.classifiers_for(enabled_classes):
        for hit in c.scan(text):
            if hit.data_class in enabled_classes:
                out.append(hit)
    return out

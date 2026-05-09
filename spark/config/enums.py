"""Shared enums used across config, privacy, memory, and logging."""

from __future__ import annotations

from enum import Enum


class PrivacyMode(str, Enum):
    STRICT = "strict"
    BALANCED = "balanced"
    REGEX_ONLY = "regex_only"


class Sensitivity(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    RESTRICTED = "restricted"


class TaskMode(str, Enum):
    ONE_SHOT = "one_shot"
    RECURRING = "recurring"
    PERPETUAL = "perpetual"
    EVENT = "event"


class TaskState(str, Enum):
    CREATED = "created"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    SLEEPING = "sleeping"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class MemoryType(str, Enum):
    FACT = "fact"
    LESSON = "lesson"
    PATTERN = "pattern"
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    RESULT = "result"


class SourceType(str, Enum):
    REFLECTION = "reflection"
    TOOL_RESULT = "tool_result"
    USER_INPUT = "user_input"
    MANUAL_NOTE = "manual_note"
    SESSION_SUMMARY = "session_summary"


class RetentionClass(str, Enum):
    PERSISTENT = "persistent"
    REVIEW = "review"
    TEMPORARY = "temporary"
    EXPIRING = "expiring"


class SandboxBackend(str, Enum):
    AUTO = "auto"
    BUBBLEWRAP = "bubblewrap"
    NSJAIL = "nsjail"
    SEATBELT = "seatbelt"


class Permission(str, Enum):
    """Permissions a tool may request and an agent may grant."""

    FS_READ = "fs.read"
    FS_WRITE = "fs.write"
    FS_LIST = "fs.list"
    NET_HTTP = "net.http"
    SUBPROCESS = "subprocess"
    SECRETS_READ = "secrets.read"


class DataClass(str, Enum):
    """Named, namespaced content categories the guardrail engine detects."""

    PII_BASIC = "pii.basic"              # email, phone, address
    PII_NAME = "pii.name"                # PERSON
    PII_GOV_ID = "pii.gov_id"            # SSN, passport, DL, ITIN, NHS
    PII_MEDICAL = "pii.medical"          # medical license, ICD codes
    FINANCIAL_CARD = "financial.card"    # PAN + CVV (Luhn-validated)
    FINANCIAL_BANK = "financial.bank"    # IBAN, routing, SWIFT
    FINANCIAL_CRYPTO = "financial.crypto"  # wallet addrs, seed phrases
    CREDENTIALS_API = "credentials.api"  # API keys, bearer tokens, JWTs
    CREDENTIALS_PEM = "credentials.pem"  # BEGIN PRIVATE KEY markers
    SECRETS_VAULT = "secrets.vault"      # exact-match on vault values
    CLI_DESTRUCTIVE = "cli.destructive"  # rm -rf /, dd, mkfs, shred
    CLI_PRIVILEGE = "cli.privilege"      # sudo, su, chmod 777
    CLI_PIPE_EXEC = "cli.pipe_exec"      # curl|sh, wget|bash
    CLI_EXFILTRATION = "cli.exfiltration"  # nc, scp, ssh to external
    PROMPT_INJECTION = "prompt.injection"  # "ignore previous instructions"


class DataScope(str, Enum):
    """Where in the pipeline a guardrail applies."""

    USER_INPUT = "user_input"        # incoming chat / task args
    TOOL_OUTPUT = "tool_output"      # plugin result pre-model
    MODEL_OUTPUT = "model_output"    # assistant generation pre-user/persist
    MEMORY_WRITE = "memory_write"    # candidates entering LTM
    SHELL_ARGS = "shell_args"        # the assembled argv for shell-like plugins


class DataClassLevel(str, Enum):
    """Enforcement level for a (class, scope) pair.

    ``shadow_block`` is a calibration tool — audits AS IF the operation
    was blocked but passes through. Operators flip a class to shadow,
    run real workloads for a few days, look at the rollup, then
    promote to ``block`` once they're satisfied with the FP rate.
    """

    ALLOW = "allow"                # detect, count, move on
    WARN = "warn"                  # detect, audit info, pass through
    REDACT = "redact"              # replace hits with [REDACTED:<class>]
    SHADOW_BLOCK = "shadow_block"  # audit elevated as if blocked, pass through
    BLOCK = "block"                # raise SparkError; operation aborts


class MaskStyle(str, Enum):
    """How a redacted hit is rendered when ``DataClassLevel.REDACT`` fires.

    Operators pick a style per category on the Filtering page so the
    output format matches the reviewer's intent — full mask for
    secrets, last-4 reveal for cards (still useful for support), name
    initials for human-readable PII, deterministic hash when log
    correlation matters more than human readability, hard strip for
    prompt-injection where leaving any trace risks re-exposing the
    payload.
    """

    PLACEHOLDER_CLASS = "placeholder_class"   # [REDACTED:credentials.api]
    PLACEHOLDER_PLAIN = "placeholder_plain"   # [REDACTED]
    LAST_4 = "last_4"                          # ****-****-****-1234
    FIRST_4 = "first_4"                        # 4111-****-****-****
    INITIAL = "initial"                        # "Jane Doe" -> "J. D."
    HASH_SHORT = "hash_short"                  # [#abc123de]
    STRIP = "strip"                            # remove the match entirely

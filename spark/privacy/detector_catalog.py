"""Catalog of every detector grouped by data class.

The Filtering page's Advanced drawer renders one toggle per entry so
operators can surgically disable a single rule (e.g. switching off the
``high-entropy`` catch-all for ``credentials.api`` while keeping the
named-vendor regexes on). Rule ids are the same string the
classifiers emit on a hit, so disabling here lines up exactly with
what gets dropped in :func:`spark.privacy.guardrails.apply_guardrails`.

Adding a new detector ⇒ add an entry here so the UI surfaces it. The
classifier itself stays the source of truth for the actual matching
logic; this file is just the human-readable index.
"""

from __future__ import annotations

from dataclasses import dataclass

from spark.config.enums import DataClass


@dataclass(frozen=True)
class DetectorEntry:
    rule_id: str
    label: str
    description: str
    # tier1 = deterministic / regex / checksum; tier2 = statistical / NER.
    # Mirrors DetectorHit.tier so the UI can group named-vendor regexes
    # vs. the Presidio NER catch-all.
    tier: str = "tier1"


DETECTOR_CATALOG: dict[DataClass, list[DetectorEntry]] = {
    DataClass.PII_BASIC: [
        DetectorEntry("presidio:EMAIL_ADDRESS", "Email", "Presidio email recognizer.", "tier2"),
        DetectorEntry("presidio:PHONE_NUMBER", "Phone", "Presidio phone recognizer.", "tier2"),
        DetectorEntry("presidio:LOCATION", "Location", "Presidio location/address NER.", "tier2"),
        DetectorEntry("presidio:IP_ADDRESS", "IP address", "Presidio IPv4/IPv6 recognizer.", "tier2"),
        DetectorEntry("presidio:URL", "URL", "Presidio URL recognizer.", "tier2"),
        DetectorEntry("presidio:DATE_TIME", "Date/time", "Presidio date/time recognizer.", "tier2"),
    ],
    DataClass.PII_NAME: [
        DetectorEntry("presidio:PERSON", "Person name", "Presidio NER for person names (high false-positive rate).", "tier2"),
        DetectorEntry("presidio:NRP", "Nationality/religion", "Presidio NER for nationality, religion, political affiliation.", "tier2"),
    ],
    DataClass.PII_GOV_ID: [
        DetectorEntry("us-ssn", "US SSN", "Strict US Social Security Number with validity ranges."),
        DetectorEntry("us-itin", "US ITIN", "Individual Taxpayer Identification Number."),
        DetectorEntry("us-passport", "US passport", "9-character US passport pattern."),
        DetectorEntry("presidio:US_SSN", "Presidio: US SSN", "Presidio US SSN recognizer (backup).", "tier2"),
        DetectorEntry("presidio:US_PASSPORT", "Presidio: US passport", "Presidio US passport recognizer.", "tier2"),
        DetectorEntry("presidio:US_DRIVER_LICENSE", "Presidio: US driver license", "Presidio US DL recognizer.", "tier2"),
        DetectorEntry("presidio:US_ITIN", "Presidio: US ITIN", "Presidio US ITIN recognizer.", "tier2"),
        DetectorEntry("presidio:UK_NHS", "Presidio: UK NHS", "Presidio UK NHS recognizer.", "tier2"),
        DetectorEntry("presidio:AU_ABN", "Presidio: AU ABN", "Australian Business Number.", "tier2"),
        DetectorEntry("presidio:AU_ACN", "Presidio: AU ACN", "Australian Company Number.", "tier2"),
        DetectorEntry("presidio:AU_TFN", "Presidio: AU TFN", "Australian Tax File Number.", "tier2"),
        DetectorEntry("presidio:AU_MEDICARE", "Presidio: AU Medicare", "Australian Medicare number.", "tier2"),
    ],
    DataClass.PII_MEDICAL: [
        DetectorEntry("presidio:MEDICAL_LICENSE", "Medical license", "Presidio medical license recognizer.", "tier2"),
    ],
    DataClass.FINANCIAL_CARD: [
        DetectorEntry("luhn", "Luhn-validated PAN", "Credit card numbers passing the Luhn checksum."),
        DetectorEntry("presidio:CREDIT_CARD", "Presidio: credit card", "Presidio credit card NER (backup).", "tier2"),
    ],
    DataClass.FINANCIAL_BANK: [
        DetectorEntry("iban", "IBAN", "International Bank Account Number with checksum."),
        DetectorEntry("us-routing-aba", "US routing (ABA)", "US bank routing number with ABA checksum AND nearby keyword."),
        DetectorEntry("swift-bic", "SWIFT/BIC", "SWIFT or BIC code shape (lower confidence)."),
        DetectorEntry("presidio:IBAN_CODE", "Presidio: IBAN", "Presidio IBAN recognizer.", "tier2"),
        DetectorEntry("presidio:US_BANK_NUMBER", "Presidio: US bank number", "Presidio US bank account recognizer.", "tier2"),
    ],
    DataClass.FINANCIAL_CRYPTO: [
        DetectorEntry("presidio:CRYPTO", "Crypto wallet", "Presidio cryptocurrency wallet address recognizer.", "tier2"),
    ],
    DataClass.CREDENTIALS_API: [
        DetectorEntry("aws-access-key", "AWS access key", "AKIA-prefixed access key id."),
        DetectorEntry("openai", "OpenAI key", "sk-prefixed OpenAI key."),
        DetectorEntry("openrouter", "OpenRouter key", "sk-or-prefixed OpenRouter key."),
        DetectorEntry("anthropic", "Anthropic key", "sk-ant-prefixed Anthropic key."),
        DetectorEntry("github-token", "GitHub token", "GitHub PAT/app/refresh token."),
        DetectorEntry("slack", "Slack token", "xox-prefixed Slack token."),
        DetectorEntry("stripe", "Stripe key", "sk_live / sk_test Stripe key."),
        DetectorEntry("telegram-bot-token", "Telegram bot token", "Telegram bot bearer token."),
        DetectorEntry("jwt", "JWT", "Three-segment JSON Web Token."),
        DetectorEntry("high-entropy", "High-entropy catch-all", "Fallback Shannon-entropy detector for unknown keys."),
    ],
    DataClass.CREDENTIALS_PEM: [
        DetectorEntry("pem-block", "PEM private key", "BEGIN/END PRIVATE KEY block."),
    ],
    DataClass.SECRETS_VAULT: [
        DetectorEntry("vault-exact", "Vault exact match", "Any string equal to a value tracked by SecretManager."),
    ],
    DataClass.CLI_DESTRUCTIVE: [
        DetectorEntry("cli.rm_rf_root", "rm -rf /", "Recursive force-remove rooted at /."),
        DetectorEntry("cli.rm_rf_anywhere", "rm -rf <path>", "Recursive force-remove of any path."),
        DetectorEntry("cli.dd_to_device", "dd to device", "dd writing to /dev/sd*, /dev/nvme*, /dev/null, etc."),
        DetectorEntry("cli.mkfs", "mkfs", "Filesystem creation — destroys existing data."),
        DetectorEntry("cli.shred", "shred", "Multi-pass overwrite — irrecoverable deletion."),
        DetectorEntry("cli.fork_bomb", "Fork bomb", "Classic ``:(){ :|:& };:`` shape."),
        DetectorEntry("cli.chown_root_recursive", "chown -R / target", "Recursive chown rooted at /."),
    ],
    DataClass.CLI_PRIVILEGE: [
        DetectorEntry("cli.sudo", "sudo", "Privilege escalation via sudo."),
        DetectorEntry("cli.su", "su", "User switch via su."),
        DetectorEntry("cli.doas", "doas", "OpenBSD privilege escalation."),
        DetectorEntry("cli.chmod_world_writable", "chmod 777", "World-writable mode bits."),
        DetectorEntry("cli.setuid_bit", "chmod +s", "Setuid/setgid bit."),
    ],
    DataClass.CLI_PIPE_EXEC: [
        DetectorEntry("cli.curl_pipe_sh", "curl | sh", "Curl piped to a shell."),
        DetectorEntry("cli.wget_pipe_sh", "wget | sh", "Wget piped to a shell."),
        DetectorEntry("cli.powershell_iex", "iwr | iex", "PowerShell download-and-execute."),
        DetectorEntry("cli.eval_base64", "eval base64", "Eval/exec of a base64 blob."),
    ],
    DataClass.CLI_EXFILTRATION: [
        DetectorEntry("cli.netcat_listener", "netcat -e/-c", "Netcat with command execution flag."),
        DetectorEntry("cli.reverse_shell_bash", "Bash reverse shell", "``bash -i >& /dev/tcp/...``."),
        DetectorEntry("cli.scp_outbound", "scp outbound", "scp to a remote user@host:/path."),
    ],
    DataClass.PROMPT_INJECTION: [
        DetectorEntry("prompt.ignore_prior", "Ignore prior", "``ignore previous instructions`` family."),
        DetectorEntry("prompt.system_override", "System override", "Inline ``system:`` / ``assistant:`` re-prompts."),
        DetectorEntry("prompt.role_flip", "Role flip", "``you are now a different AI`` family."),
        DetectorEntry("prompt.jailbreak_marker", "Jailbreak marker", "DAN, jailbreak, developer mode."),
        DetectorEntry("prompt.prompt_leak", "Prompt-leak request", "``reveal the system prompt`` family."),
    ],
}


# Family grouping used by the Filtering page UI. Order is significant —
# this is the order rendered top-to-bottom on the page.
FAMILIES: list[tuple[str, str, list[DataClass]]] = [
    ("pii", "PII", [
        DataClass.PII_BASIC,
        DataClass.PII_NAME,
        DataClass.PII_GOV_ID,
        DataClass.PII_MEDICAL,
    ]),
    ("financial", "Financial", [
        DataClass.FINANCIAL_CARD,
        DataClass.FINANCIAL_BANK,
        DataClass.FINANCIAL_CRYPTO,
    ]),
    ("credentials", "Credentials", [
        DataClass.CREDENTIALS_API,
        DataClass.CREDENTIALS_PEM,
        DataClass.SECRETS_VAULT,
    ]),
    ("cli", "CLI safety", [
        DataClass.CLI_DESTRUCTIVE,
        DataClass.CLI_PRIVILEGE,
        DataClass.CLI_PIPE_EXEC,
        DataClass.CLI_EXFILTRATION,
    ]),
    ("prompt", "Prompt safety", [
        DataClass.PROMPT_INJECTION,
    ]),
]


def family_of(data_class: DataClass) -> str:
    for fam_id, _, members in FAMILIES:
        if data_class in members:
            return fam_id
    return "other"

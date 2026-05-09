"""Regex catalog for the dangerous-CLI classifier.

Data-only module. Each entry is ``(rule_id, data_class, compiled_regex)``.
New patterns land here; the classifier picks them up on import with no
code changes.

Patterns are matched against the **joined argv** of a shell-style
invocation (see ``DangerousCliClassifier.scan``) or against any string
scope (``user_input``, ``model_output``, etc.). They are intentionally
conservative — false positives cost workflow; false negatives cost
security. A pattern here should be something that is *almost never*
legitimate without an explicit grant.
"""

from __future__ import annotations

import re
from re import Pattern

from spark.config.enums import DataClass


# Each tuple: (rule_id, class, compiled pattern).
# rule_ids are stable strings — the UI + audit log keys on them.
CLI_PATTERNS: list[tuple[str, DataClass, Pattern[str]]] = [
    # --- Destructive ---------------------------------------------------
    (
        "cli.rm_rf_root",
        DataClass.CLI_DESTRUCTIVE,
        re.compile(r"\brm\s+(?:-[a-zA-Z]*[rRfF][a-zA-Z]*\s+)+(?:--no-preserve-root\s+)?/\s*(?:$|\s)"),
    ),
    (
        "cli.rm_rf_anywhere",
        DataClass.CLI_DESTRUCTIVE,
        re.compile(r"\brm\s+(?:-[a-zA-Z]*[rRfF][a-zA-Z]*\s+)+\S"),
    ),
    (
        "cli.dd_to_device",
        DataClass.CLI_DESTRUCTIVE,
        re.compile(r"\bdd\b.*\bof=/dev/(?:sd[a-z]|nvme|disk|null|zero)", re.IGNORECASE),
    ),
    (
        "cli.mkfs",
        DataClass.CLI_DESTRUCTIVE,
        re.compile(r"\bmkfs(?:\.\w+)?\b"),
    ),
    (
        "cli.shred",
        DataClass.CLI_DESTRUCTIVE,
        re.compile(r"\bshred\s+(?:-[a-zA-Z]+\s+)*\S"),
    ),
    (
        "cli.fork_bomb",
        DataClass.CLI_DESTRUCTIVE,
        re.compile(r":\s*\(\s*\)\s*\{.*:\s*\|\s*:"),
    ),
    (
        "cli.chown_root_recursive",
        DataClass.CLI_DESTRUCTIVE,
        re.compile(r"\bchown\s+(?:-[a-zA-Z]*R[a-zA-Z]*\s+)+\S+\s+/"),
    ),
    # --- Privilege escalation ------------------------------------------
    (
        "cli.sudo",
        DataClass.CLI_PRIVILEGE,
        re.compile(r"(?:^|[\s|;&])sudo\b"),
    ),
    (
        "cli.su",
        DataClass.CLI_PRIVILEGE,
        re.compile(r"(?:^|[\s|;&])su\s+(?:-\s+)?\S"),
    ),
    (
        "cli.doas",
        DataClass.CLI_PRIVILEGE,
        re.compile(r"(?:^|[\s|;&])doas\b"),
    ),
    (
        "cli.chmod_world_writable",
        DataClass.CLI_PRIVILEGE,
        re.compile(r"\bchmod\s+(?:-[a-zA-Z]+\s+)*[0-7]*7(?:77|$|\s)"),
    ),
    (
        "cli.setuid_bit",
        DataClass.CLI_PRIVILEGE,
        re.compile(r"\bchmod\s+(?:-[a-zA-Z]+\s+)*[+=][us]"),
    ),
    # --- Pipe-to-shell execution ---------------------------------------
    (
        "cli.curl_pipe_sh",
        DataClass.CLI_PIPE_EXEC,
        re.compile(r"\bcurl\b[^|;]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.IGNORECASE),
    ),
    (
        "cli.wget_pipe_sh",
        DataClass.CLI_PIPE_EXEC,
        re.compile(r"\bwget\b[^|;]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.IGNORECASE),
    ),
    (
        "cli.powershell_iex",
        DataClass.CLI_PIPE_EXEC,
        re.compile(r"\b(?:iwr|invoke-webrequest|invoke-restmethod)\b[^|]*\|\s*iex", re.IGNORECASE),
    ),
    (
        "cli.eval_base64",
        DataClass.CLI_PIPE_EXEC,
        re.compile(r"\b(?:eval|exec)\s+(?:\$\()?(?:echo\s+)?['\"]?[A-Za-z0-9+/=]{40,}['\"]?"),
    ),
    # --- Exfiltration heuristics ---------------------------------------
    (
        "cli.netcat_listener",
        DataClass.CLI_EXFILTRATION,
        re.compile(r"\b(?:nc|ncat|netcat)\b\s+(?:-[a-zA-Z]+\s+)*(?:-e|-c)\b"),
    ),
    (
        "cli.reverse_shell_bash",
        DataClass.CLI_EXFILTRATION,
        re.compile(r"bash\s+-i\s+>&\s*/dev/tcp/"),
    ),
    (
        "cli.scp_outbound",
        DataClass.CLI_EXFILTRATION,
        re.compile(r"\bscp\s+(?:-[a-zA-Z]+\s+)*\S+\s+\S+@[^:\s]+:"),
    ),
]


# --- Prompt-injection patterns --------------------------------------------
# These target the most common published attack strings. Matches trigger
# `warn` by default (the class ships at level=warn); operators can raise
# to `redact` or `block` per class or per agent.
PROMPT_INJECTION_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (
        "prompt.ignore_prior",
        re.compile(r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|rules)", re.IGNORECASE),
    ),
    (
        "prompt.system_override",
        re.compile(r"(?:^|\n)\s*(?:system|assistant)\s*(?:prompt|message)?\s*:\s*", re.IGNORECASE | re.MULTILINE),
    ),
    (
        "prompt.role_flip",
        re.compile(r"you\s+are\s+now\s+(?:a\s+)?(?:different|new)\s+(?:ai|assistant|model|system)", re.IGNORECASE),
    ),
    (
        "prompt.jailbreak_marker",
        re.compile(r"\b(?:DAN|jailbreak|developer\s+mode|do\s+anything\s+now)\b", re.IGNORECASE),
    ),
    (
        "prompt.prompt_leak",
        re.compile(r"(?:show|reveal|print|output)\s+(?:the\s+)?(?:full\s+)?(?:system|original)\s+prompt", re.IGNORECASE),
    ),
]

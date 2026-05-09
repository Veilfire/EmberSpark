"""Sensitivity policy table.

Sensitivity classes gate three decisions:
- whether a value can be shown to the model;
- whether it can be stored in long-term memory;
- whether it can appear in logs (even after redaction).

`strict` mode is the most conservative; `balanced` relaxes model exposure slightly.
"""

from __future__ import annotations

from dataclasses import dataclass

from spark.config.enums import PrivacyMode, Sensitivity


@dataclass(frozen=True)
class SensitivityDecision:
    allow_model: bool
    allow_long_term: bool
    allow_log: bool


# strict mode table
_STRICT: dict[Sensitivity, SensitivityDecision] = {
    Sensitivity.LOW: SensitivityDecision(True, True, True),
    Sensitivity.MODERATE: SensitivityDecision(True, True, True),
    Sensitivity.HIGH: SensitivityDecision(True, False, False),
    Sensitivity.RESTRICTED: SensitivityDecision(False, False, False),
}

_BALANCED: dict[Sensitivity, SensitivityDecision] = {
    Sensitivity.LOW: SensitivityDecision(True, True, True),
    Sensitivity.MODERATE: SensitivityDecision(True, True, True),
    Sensitivity.HIGH: SensitivityDecision(True, True, False),
    Sensitivity.RESTRICTED: SensitivityDecision(False, False, False),
}


def decide(mode: PrivacyMode, sensitivity: Sensitivity) -> SensitivityDecision:
    if mode == PrivacyMode.STRICT:
        return _STRICT[sensitivity]
    if mode == PrivacyMode.BALANCED:
        return _BALANCED[sensitivity]
    # regex_only — treated as balanced for gating purposes
    return _BALANCED[sensitivity]

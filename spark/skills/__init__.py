"""Agent-developed skills — structured knowledge about external APIs.

A `Skill` is not executable code. It is a Pydantic record describing how to
talk to an external service (auth method, base URL, endpoints, required
secrets). The existing `http_client` plugin is the only code path; skills feed
it structured knowledge retrieved into the planner's context.

New skills go through a mandatory review queue by default.
"""

from __future__ import annotations

from spark.skills.catalog import SkillCatalog
from spark.skills.discovery import SkillDiscovery, SkillDiscoveryError
from spark.skills.schemas import (
    ApiSkill,
    SkillAuthMethod,
    SkillEndpoint,
    SkillReviewDecision,
)
from spark.skills.sources import (
    DEFAULT_TRUSTED_DOC_HOSTS,
    TrustedDocPolicy,
)

__all__ = [
    "ApiSkill",
    "DEFAULT_TRUSTED_DOC_HOSTS",
    "SkillAuthMethod",
    "SkillCatalog",
    "SkillDiscovery",
    "SkillDiscoveryError",
    "SkillEndpoint",
    "SkillReviewDecision",
    "TrustedDocPolicy",
]

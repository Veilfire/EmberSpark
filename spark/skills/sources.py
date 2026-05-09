"""Trusted documentation source policy.

When an agent discovers a capability gap and wants to learn a new API, it is
only permitted to fetch documentation from hosts in the *trusted docs
allowlist*. This is a second, tighter allowlist distinct from the agent's
general `network.allow_hosts` — a skill discovery session should not piggyback
on the agent's normal network grants.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_TRUSTED_DOC_HOSTS: frozenset[str] = frozenset(
    {
        # Platform docs
        "docs.github.com",
        "developer.github.com",
        "api.slack.com",
        "core.telegram.org",
        "developers.facebook.com",
        "developer.whatsapp.com",
        "developers.google.com",
        "cloud.google.com",
        "docs.aws.amazon.com",
        "learn.microsoft.com",
        "docs.stripe.com",
        "stripe.com",
        "docs.twilio.com",
        "www.twilio.com",
        "api.notion.com",
        "developers.notion.com",
        "developers.cloudflare.com",
        "docs.anthropic.com",
        "platform.openai.com",
        # Standards bodies
        "www.rfc-editor.org",
        "datatracker.ietf.org",
    }
)


@dataclass(frozen=True)
class TrustedDocPolicy:
    hosts: frozenset[str]

    @classmethod
    def default(cls) -> "TrustedDocPolicy":
        return cls(hosts=DEFAULT_TRUSTED_DOC_HOSTS)

    def with_additions(self, extra: list[str]) -> "TrustedDocPolicy":
        return TrustedDocPolicy(
            hosts=frozenset(self.hosts | {h.strip().lower() for h in extra if h.strip()})
        )

    def allows(self, host: str) -> bool:
        host = host.strip().lower()
        return host in self.hosts

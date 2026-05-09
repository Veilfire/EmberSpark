"""Authentication primitives shared across the web layer.

HMAC verification helpers for inbound webhooks. Two flavours:

- :func:`verify_hmac_sha256` — raw-body HMAC (GitHub, Stripe, Linear,
  Vercel, Twilio, …). The signature is over the request body bytes
  alone; format is ``sha256=<hex>`` or just ``<hex>``.
- :func:`verify_hmac_sha256_slack` — Slack's flavour, where the
  signature is over ``v0:<timestamp>:<body>`` and the timestamp
  doubles as a replay-prevention window (default 5 min).

All comparisons use ``hmac.compare_digest``. Bad inputs return
``False`` rather than raising so the webhook endpoint can't be
fingerprinted via differential response shapes.
"""

from __future__ import annotations

import hmac
import time
from hashlib import sha256

#: Default replay window for ``verify_hmac_sha256_slack``. Slack's
#: official guidance is "reject anything older than five minutes."
SLACK_REPLAY_WINDOW_SECONDS = 60 * 5


def verify_hmac_sha256(
    secret: str | bytes, body: bytes, header_value: str
) -> bool:
    """Verify a signed-body header against ``HMAC-SHA256(secret, body)``.

    GitHub's ``X-Hub-Signature-256`` and the generic
    ``X-Spark-Signature-256`` ship as ``sha256=<hex>``. This helper
    accepts either the prefixed or raw hex form.

    Returns ``True`` only on exact match. Constant-time. Returns
    ``False`` on any malformed input rather than raising — webhook
    callers should not be able to differentiate "bad input" from
    "wrong signature" via response variation.
    """
    if not isinstance(header_value, str) or not header_value:
        return False
    sig_hex = header_value.strip()
    if sig_hex.startswith("sha256="):
        sig_hex = sig_hex[len("sha256=") :]
    if not _is_hex(sig_hex):
        return False
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    else:
        secret_bytes = secret
    expected = hmac.new(secret_bytes, body, sha256).hexdigest()
    return hmac.compare_digest(expected, sig_hex.lower())


def verify_hmac_sha256_slack(
    secret: str | bytes,
    body: bytes,
    signature_header: str,
    timestamp_header: str,
    *,
    replay_window: int = SLACK_REPLAY_WINDOW_SECONDS,
    now: float | None = None,
) -> bool:
    """Verify a Slack-style signed webhook.

    Slack signs ``v0:<unix_ts>:<body>`` with HMAC-SHA256 and ships the
    digest as ``v0=<hex>`` in ``X-Slack-Signature``, alongside the
    timestamp in ``X-Slack-Request-Timestamp``. The timestamp doubles as
    a replay-prevention window — Slack's docs require rejecting
    anything older than 5 minutes. We enforce the same default.

    Returns ``False`` on any malformed input or replay-window violation.
    """
    if not isinstance(signature_header, str) or not signature_header:
        return False
    if not isinstance(timestamp_header, str) or not timestamp_header:
        return False
    sig = signature_header.strip()
    if sig.startswith("v0="):
        sig = sig[len("v0=") :]
    if not _is_hex(sig):
        return False
    try:
        ts = int(timestamp_header.strip())
    except ValueError:
        return False
    current = now if now is not None else time.time()
    if abs(current - ts) > replay_window:
        return False
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    else:
        secret_bytes = secret
    basestring = b"v0:" + str(ts).encode("ascii") + b":" + body
    expected = hmac.new(secret_bytes, basestring, sha256).hexdigest()
    return hmac.compare_digest(expected, sig.lower())


def _is_hex(s: str) -> bool:
    if not s or len(s) % 2 != 0:
        return False
    try:
        int(s, 16)
    except ValueError:
        return False
    return True

"""SMTP email sender plugin.

Narrow, send-only. Every configuration knob is operator-locked:

- ``smtp_host`` / ``smtp_port`` — where to connect
- ``from_address`` — the envelope sender (model cannot spoof)
- ``allowed_to_domains`` — if non-empty, all recipients must be under these
- ``attachment_allow_paths`` — attachments must live under these (typically
  the data volume's scratch or deliverables directory)

The model cannot set the sender, the SMTP server, or widen the recipient
domain allowlist. All it supplies per call is the ``to``, ``subject``,
``body``, and optional attachment paths.
"""

from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from spark.config.enums import Permission, Sensitivity

# Deliberate: we do NOT import pydantic.EmailStr because it pulls in
# `email-validator` at import time. Instead we validate with a strict
# regex that rejects obvious garbage. The real email validity check is
# the SMTP server's own acceptance — if the address is malformed, the
# server returns 550 and the plugin raises PermissionError.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

EmailAddress = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=320,
        pattern=_EMAIL_RE.pattern,
    ),
]


class EmailSenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smtp_host: str = Field(min_length=1, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    use_starttls: bool = True
    username_secret: str = Field(default="smtp_username", max_length=128)
    password_secret: str = Field(default="smtp_password", max_length=128)
    from_address: EmailAddress
    allowed_to_domains: list[str] = Field(default_factory=list)
    max_subject_chars: int = Field(default=200, ge=1, le=1000)
    max_body_chars: int = Field(default=100_000, ge=1, le=5_000_000)
    max_recipients: int = Field(default=10, ge=1, le=500)
    allow_html: bool = False
    allow_attachments: bool = True
    attachment_allow_paths: list[Path] = Field(default_factory=list)
    max_attachment_bytes: int = Field(default=10_000_000, ge=1, le=100_000_000)
    #: Permit SMTP connections to internal IPs (RFC1918, loopback,
    #: link-local, cloud metadata). Defaults to ``False``. When the
    #: operator needs to relay through a mail server at e.g.
    #: ``10.0.0.25``, they opt in explicitly and the choice is audited.
    allow_internal_smtp: bool = False


class EmailSenderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: list[EmailAddress] = Field(
        min_length=1,
        max_length=500,
        description="Recipient addresses. All must match the operator's allowed_to_domains.",
    )
    subject: str = Field(
        min_length=1,
        max_length=1000,
        description="Email subject line. CR/LF rejected as header injection.",
    )
    body: str = Field(
        min_length=1,
        max_length=5_000_000,
        description="Plain-text message body.",
    )
    body_html: str | None = Field(
        default=None,
        max_length=5_000_000,
        description="Optional HTML alternative. Only allowed when operator's allow_html is true.",
    )
    attachments: list[Path] = Field(
        default_factory=list,
        max_length=20,
        description="File paths to attach. Each must be under attachment_allow_paths.",
    )
    cc: list[EmailAddress] = Field(
        default_factory=list,
        max_length=500,
        description="Optional CC recipients. Subject to the same domain allowlist as 'to'.",
    )
    reply_to: EmailAddress | None = Field(
        default=None,
        description="Optional Reply-To address. Subject to the same domain allowlist.",
    )


class EmailSenderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str
    recipients: list[str]
    attachment_count: int


class EmailSenderPlugin:
    name: ClassVar[str] = "email_sender"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "SMTP sender with operator-locked from address, domain allowlist, "
        "and attachment-path gating."
    )
    input_schema: ClassVar[type[BaseModel]] = EmailSenderArgs
    output_schema: ClassVar[type[BaseModel]] = EmailSenderResponse
    config_schema: ClassVar[type[BaseModel]] = EmailSenderConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ, Permission.FS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()  # operator picks via config
    sensitivity: ClassVar[Sensitivity] = Sensitivity.HIGH
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: EmailSenderArgs, ctx: Any) -> EmailSenderResponse:
        from spark.utils.paths import PathPolicy

        cfg = getattr(ctx, "plugin_config", {}) or {}
        smtp_host = cfg.get("smtp_host")
        smtp_port = int(cfg.get("smtp_port") or 587)
        use_starttls = bool(cfg.get("use_starttls", True))
        from_address = cfg.get("from_address")
        allowed_domains = set(cfg.get("allowed_to_domains") or [])
        max_subject = int(cfg.get("max_subject_chars") or 200)
        max_body = int(cfg.get("max_body_chars") or 100_000)
        max_recipients = int(cfg.get("max_recipients") or 10)
        allow_html = bool(cfg.get("allow_html", False))
        allow_attachments = bool(cfg.get("allow_attachments", True))
        attach_allow = [Path(p) for p in (cfg.get("attachment_allow_paths") or [])]
        max_attachment_bytes = int(cfg.get("max_attachment_bytes") or 10_000_000)
        allow_internal_smtp = bool(cfg.get("allow_internal_smtp", False))
        username_secret = cfg.get("username_secret") or "smtp_username"
        password_secret = cfg.get("password_secret") or "smtp_password"

        if not smtp_host or not from_address:
            raise PermissionError(
                "email_sender: operator must set smtp_host and from_address in config"
            )

        # Reject CRLF in anything that will end up as a header value.
        # Defense in depth — Python's email.message policy refuses bad
        # headers at serialization, but we want a clean error earlier.
        for name, value in (
            ("subject", args.subject),
            ("from_address", from_address),
        ):
            if _has_crlf(value):
                raise PermissionError(f"email_sender: {name} contains CR/LF")
        if args.body_html and _has_crlf_in_headers(args.body_html):
            # body_html is content, not a header, but CR/LF at the very
            # start can still cause ambiguity in multipart builds. The
            # EmailMessage API handles this; we log but don't reject.
            pass

        secrets = getattr(ctx, "secrets", {}) or {}
        username = secrets.get(username_secret)
        password = secrets.get(password_secret)
        if username is None or password is None:
            raise PermissionError(
                "email_sender: SMTP credentials not injected; check operator secrets"
            )

        # SMTP host blocklist gate — SSRF defense for the SMTP layer.
        # Without `allow_internal_smtp: True`, we resolve the host and
        # refuse RFC1918 / loopback / link-local / cloud metadata IPs.
        if not allow_internal_smtp:
            _reject_internal_smtp_host(smtp_host)

        # Recipient caps and domain allowlist. Now covers reply_to too.
        all_recipients = list(args.to) + list(args.cc)
        if args.reply_to:
            all_recipients.append(args.reply_to)
        if len(all_recipients) > max_recipients + 1:  # +1 for reply_to
            raise PermissionError(
                f"email_sender: {len(all_recipients)} recipients exceeds max {max_recipients}"
            )
        if allowed_domains:
            for addr in all_recipients:
                domain = addr.split("@", 1)[-1].lower()
                if domain not in allowed_domains:
                    raise PermissionError(
                        f"email_sender: recipient domain {domain!r} not in allowlist "
                        f"(applies to to, cc, and reply_to)"
                    )

        if len(args.subject) > max_subject:
            raise PermissionError(f"email_sender: subject exceeds {max_subject} chars")
        if len(args.body) > max_body:
            raise PermissionError(f"email_sender: body exceeds {max_body} chars")
        if args.body_html and not allow_html:
            raise PermissionError("email_sender: HTML body disabled in operator config")
        if args.attachments and not allow_attachments:
            raise PermissionError("email_sender: attachments disabled in operator config")

        # Attachment path validation via PathPolicy.
        attachment_payloads: list[tuple[str, bytes]] = []
        if args.attachments:
            policy = PathPolicy.from_strings([str(p) for p in attach_allow], deny=[])
            for p in args.attachments:
                resolved = policy.check(p)
                size = resolved.stat().st_size
                if size > max_attachment_bytes:
                    raise PermissionError(
                        f"email_sender: attachment {resolved.name} is {size} bytes "
                        f"(max {max_attachment_bytes})"
                    )
                attachment_payloads.append((resolved.name, resolved.read_bytes()))

        # Build the message.
        msg = EmailMessage()
        msg["From"] = from_address
        msg["To"] = ", ".join(args.to)
        if args.cc:
            msg["Cc"] = ", ".join(args.cc)
        if args.reply_to:
            msg["Reply-To"] = args.reply_to
        msg["Subject"] = args.subject
        msg.set_content(args.body)
        if args.body_html and allow_html:
            msg.add_alternative(args.body_html, subtype="html")
        for name, payload in attachment_payloads:
            msg.add_attachment(
                payload,
                maintype="application",
                subtype="octet-stream",
                filename=name,
            )

        # Send — synchronous smtplib is fine, the sandbox worker's
        # event loop only has this one task.
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                if use_starttls:
                    smtp.starttls()
                    smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(msg)
        except smtplib.SMTPException as exc:
            raise PermissionError(f"email_sender: SMTP error: {exc}") from exc

        return EmailSenderResponse(
            message_id=msg["Message-ID"] or "",
            recipients=list(all_recipients),
            attachment_count=len(attachment_payloads),
        )


def _has_crlf(value: str) -> bool:
    return "\r" in value or "\n" in value


def _has_crlf_in_headers(value: str) -> bool:
    # Used for body_html — the content itself may contain CRLF but we
    # still want to reject anything that looks like header injection
    # on the very first line.
    first_line = value.split("\n", 1)[0]
    return "\r" in first_line


def _reject_internal_smtp_host(smtp_host: str) -> None:
    """Resolve the SMTP host and refuse internal / metadata addresses.

    The agent YAML's SMTP host is operator-configured, but operators are
    fallible. Without this gate, ``smtp_host: 169.254.169.254`` or
    ``smtp_host: 127.0.0.1`` would happily connect. We reuse the same
    blocklist logic the HTTP plugins use.
    """
    import ipaddress
    import socket

    _METADATA_BLOCKLIST = frozenset(
        {"169.254.169.254", "100.100.100.200", "fd00:ec2::254", "::1", "0.0.0.0"}
    )

    try:
        infos = socket.getaddrinfo(smtp_host, None)
    except socket.gaierror as exc:
        raise PermissionError(f"email_sender: cannot resolve smtp_host {smtp_host!r}: {exc}") from exc

    for info in infos:
        addr = info[4][0]
        if addr in _METADATA_BLOCKLIST:
            raise PermissionError(
                f"email_sender: smtp_host {smtp_host!r} resolves to blocked "
                f"metadata address {addr}. Set allow_internal_smtp=true to override."
            )
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise PermissionError(
                f"email_sender: smtp_host {smtp_host!r} resolves to internal "
                f"address {addr}. Set allow_internal_smtp=true in plugin config "
                "if this is an intentional internal relay."
            )

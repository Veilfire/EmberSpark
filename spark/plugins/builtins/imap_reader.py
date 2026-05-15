"""IMAP reader plugin — read inbound email.

Pairs with the existing ``email_sender`` so an agent has the full
inbox loop (send + read). Uses stdlib ``imaplib`` + ``email`` — no
new dependencies.

Five guard rails:

1. **Mailbox allowlist** — every action checks the targeted mailbox
   against ``allowed_mailboxes``. The empty default refuses
   everything; the default ``["INBOX"]`` for new installs is
   permissive only on the main inbox. ``[Gmail]/All Mail``,
   ``[Gmail]/Trash`` etc. must be explicitly enabled.
2. **Sensitivity = HIGH** — email bodies are the densest PII surface
   on the host. Strict-mode agents won't see bodies; balanced will.
3. **Body cap** — `max_body_bytes` (default 256 KB) truncates long
   threads so a single tool call can't blow the prompt window.
4. **Attachments off by default** — even when allowed, attachments
   are routed through ``deliverables_path`` (surface in the Downloads
   page + notification) rather than streamed into the prompt.
5. **Read-only by default** — ``mark_seen_on_read=false`` keeps the
   plugin from mutating the operator's inbox state.
"""

from __future__ import annotations

import asyncio
import email
import email.header
import email.utils
import imaplib
from email.message import Message
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity
from spark.errors import ErrorCode, SparkError


# Server-special mailboxes carry an elevated/danger risk chip in the
# editor so operators see the warning before allowing them.
_DANGER_MAILBOXES = frozenset(
    {
        "[Gmail]/All Mail",
        "[Gmail]/Trash",
        "[Gmail]/Spam",
    }
)
_ELEVATED_MAILBOXES = frozenset(
    {
        "[Gmail]/Sent Mail",
        "[Gmail]/Drafts",
        "[Gmail]/Important",
        "[Gmail]/Starred",
        "Sent",
        "Drafts",
        "Trash",
        "Junk",
        "Junk Email",
        "Archive",
    }
)


def mailbox_risk(name: str) -> Literal["safe", "elevated", "danger"]:
    if name in _DANGER_MAILBOXES:
        return "danger"
    if name in _ELEVATED_MAILBOXES:
        return "elevated"
    return "safe"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ImapReaderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="", max_length=256)
    port: int = Field(default=993, gt=0, le=65535)
    use_ssl: bool = Field(default=True)
    username: str = Field(default="", max_length=256)
    password_secret: str = Field(default="imap_password", max_length=128)
    allowed_mailboxes: list[str] = Field(
        default_factory=lambda: ["INBOX"],
        description=(
            "Mailbox path allowlist. Empty = refuse. Default permits "
            "only INBOX — operator opts each provider-specific "
            "mailbox in via discovery."
        ),
    )
    max_messages_returned: int = Field(default=50, gt=0, le=500)
    max_body_bytes: int = Field(default=262_144, gt=0)
    download_attachments: bool = Field(
        default=False,
        description=(
            "When true, attachments flow to deliverables_path and "
            "surface in the Downloads page. Otherwise attachments "
            "are never read."
        ),
    )
    body_format: Literal["text", "html", "both"] = Field(default="text")
    mark_seen_on_read: bool = Field(default=False)
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)


# ---------------------------------------------------------------------------
# Action surface — discriminated union on `action`
# ---------------------------------------------------------------------------


class _ListMailboxesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list_mailboxes"] = "list_mailboxes"


class _SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["search"] = "search"
    mailbox: str = Field(default="INBOX", max_length=256)
    since: str | None = Field(default=None, description="ISO date lower bound")
    before: str | None = Field(default=None, description="ISO date upper bound")
    from_address: str | None = Field(default=None, max_length=256)
    to_address: str | None = Field(default=None, max_length=256)
    subject: str | None = Field(default=None, max_length=512)
    body: str | None = Field(default=None, max_length=512)
    unseen: bool = False
    flagged: bool = False


class _GetMessageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["get_message"] = "get_message"
    mailbox: str = Field(default="INBOX", max_length=256)
    uid: str = Field(min_length=1, max_length=64)


class _ImapReaderArgsWrapper(BaseModel):
    """Permissive wrapper; inner ``_*Args`` validates per action."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["list_mailboxes", "search", "get_message"] = Field(
        description=(
            "Which IMAP op to run. 'list_mailboxes' (discovery), "
            "'search' (IMAP SEARCH criteria), 'get_message' (one "
            "message by UID)."
        ),
    )
    mailbox: str | None = None
    since: str | None = None
    before: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    subject: str | None = None
    body: str | None = None
    unseen: bool | None = None
    flagged: bool | None = None
    uid: str | None = None


class ImapMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uid: str
    mailbox: str
    subject: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    date: str | None = None
    flags: list[str] = Field(default_factory=list)
    snippet: str | None = None
    body: str | None = None
    body_html: str | None = None
    has_attachments: bool = False


class ImapResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    ok: bool
    mailboxes: list[dict[str, Any]] | None = None
    messages: list[ImapMessage] | None = None
    message: ImapMessage | None = None
    truncated: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class ImapMailboxEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    delimiter: str | None = None
    attributes: list[str] = Field(default_factory=list)
    risk: Literal["safe", "elevated", "danger"] = "safe"


class ImapDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    error: str | None = None
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None
    mailboxes: list[ImapMailboxEntry] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    server: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_password(cfg: dict[str, Any], ctx: Any) -> str:
    secret_name = (cfg.get("password_secret") or "imap_password").strip()
    secrets = getattr(ctx, "secrets", {}) or {}
    value = secrets.get(secret_name) if isinstance(secrets, dict) else None
    if not value:
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            f"imap_reader: secret {secret_name!r} not injected",
            detail={"plugin": "imap_reader", "secret_name": secret_name},
        )
    return str(value)


def _refuse_mailbox(name: str) -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        f"imap_reader: mailbox {name!r} not in allowed_mailboxes",
        detail={
            "plugin": "imap_reader",
            "missing_allowlist_item": name,
            "field": "allowed_mailboxes",
            "risk": mailbox_risk(name),
        },
    )


def _decode_header(value: str | None) -> str | None:
    if not value:
        return None
    parts = email.header.decode_header(value)
    out: list[str] = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _classify_imap_error(exc: Exception, *, host: str) -> SparkError:
    import ipaddress  # noqa: PLC0415

    msg = str(exc)
    if isinstance(exc, imaplib.IMAP4.error) and (
        "authenticationfailed" in msg.lower()
        or "auth" in msg.lower()
        or "login failed" in msg.lower()
    ):
        return SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            f"imap_reader: auth refused by {host}",
            detail={"plugin": "imap_reader", "secret_name": "imap_password"},
        )
    # Try IP-private check for connect-time failures.
    try:
        ip = ipaddress.ip_address(host)
        is_private = (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        is_private = False
    if "Connection" in msg or "refused" in msg.lower() or "resolve" in msg.lower():
        code = ErrorCode.URL_PRIVATE_IP if is_private else ErrorCode.URL_DENIED
        return SparkError(
            code,
            f"imap_reader: cannot reach {host}: {exc}",
            detail={"plugin": "imap_reader", "host": host},
        )
    return SparkError(
        ErrorCode.PLUGIN_RAISED,
        f"imap_reader: IMAP error: {exc}",
        detail={"plugin": "imap_reader"},
    )


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class ImapReaderPlugin:
    name: ClassVar[str] = "imap_reader"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Read inbound email over IMAP. Pairs with email_sender. "
        "Per-mailbox allowlist + body cap + attachments off by "
        "default."
    )
    input_schema: ClassVar[type[BaseModel]] = _ImapReaderArgsWrapper
    output_schema: ClassVar[type[BaseModel]] = ImapResult
    config_schema: ClassVar[type[BaseModel]] = ImapReaderConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.HIGH
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(
        self, args: _ImapReaderArgsWrapper, ctx: Any
    ) -> ImapResult:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        host = (cfg.get("host") or "").strip()
        if not host:
            raise SparkError(
                ErrorCode.OPERATOR_OVERRIDE_REFUSED,
                "imap_reader: host not set in operator config",
                detail={"plugin": "imap_reader", "field": "host"},
            )
        return await asyncio.to_thread(_execute_sync, args, cfg, ctx)


def _execute_sync(
    args: _ImapReaderArgsWrapper, cfg: dict[str, Any], ctx: Any
) -> ImapResult:
    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 993)
    use_ssl = bool(cfg.get("use_ssl", True))
    username = (cfg.get("username") or "").strip()
    password = _resolve_password(cfg, ctx)
    allowed = set(cfg.get("allowed_mailboxes") or [])
    max_messages = int(cfg.get("max_messages_returned") or 50)
    max_body_bytes = int(cfg.get("max_body_bytes") or 262_144)
    body_format = cfg.get("body_format") or "text"
    mark_seen = bool(cfg.get("mark_seen_on_read", False))
    timeout = float(cfg.get("connect_timeout_seconds") or 10.0)

    try:
        if use_ssl:
            conn = imaplib.IMAP4_SSL(host=host, port=port, timeout=timeout)
        else:
            conn = imaplib.IMAP4(host=host, port=port, timeout=timeout)
    except Exception as exc:
        raise _classify_imap_error(exc, host=host) from exc

    try:
        conn.login(username, password)
    except Exception as exc:
        try:
            conn.logout()
        except Exception:  # pragma: no cover
            pass
        raise _classify_imap_error(exc, host=host) from exc

    try:
        if args.action == "list_mailboxes":
            return _do_list_mailboxes(conn)
        if args.action == "search":
            mailbox = (args.mailbox or "INBOX").strip()
            if mailbox not in allowed:
                raise _refuse_mailbox(mailbox)
            return _do_search(
                conn, args, mailbox, max_messages=max_messages
            )
        if args.action == "get_message":
            mailbox = (args.mailbox or "INBOX").strip()
            if mailbox not in allowed:
                raise _refuse_mailbox(mailbox)
            if not args.uid:
                raise SparkError(
                    ErrorCode.INPUT_SCHEMA_INVALID,
                    "imap_reader: get_message requires uid",
                    detail={"plugin": "imap_reader"},
                )
            return _do_get_message(
                conn, mailbox, args.uid,
                max_body_bytes=max_body_bytes,
                body_format=body_format,
                mark_seen=mark_seen,
            )
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            f"imap_reader: unknown action {args.action!r}",
            detail={"plugin": "imap_reader", "action": args.action},
        )
    finally:
        try:
            conn.logout()
        except Exception:  # pragma: no cover
            pass


def _do_list_mailboxes(conn: imaplib.IMAP4) -> ImapResult:
    typ, data = conn.list()
    if typ != "OK":
        return ImapResult(action="list_mailboxes", ok=False, error="LIST failed")
    out: list[dict[str, Any]] = []
    for entry in data:
        if entry is None:
            continue
        raw = entry.decode("utf-8", errors="replace") if isinstance(entry, bytes) else str(entry)
        # Parse:   (\HasNoChildren) "/" "INBOX"
        try:
            attr_end = raw.index(")")
            attrs_raw = raw[1:attr_end]
            rest = raw[attr_end + 1 :].strip()
            delim_end = rest.index('" "', 1) + 1
            delimiter = rest[1:delim_end - 1]
            name = rest[delim_end + 1 :].strip().strip('"')
        except ValueError:
            continue
        attributes = [a.strip("()\\") for a in attrs_raw.split()]
        out.append({"name": name, "delimiter": delimiter, "attributes": attributes})
    return ImapResult(action="list_mailboxes", ok=True, mailboxes=out)


def _build_search_criteria(args: _ImapReaderArgsWrapper) -> list[str]:
    criteria: list[str] = []
    if args.unseen:
        criteria.append("UNSEEN")
    if args.flagged:
        criteria.append("FLAGGED")
    if args.since:
        criteria.extend(["SINCE", _imap_date(args.since)])
    if args.before:
        criteria.extend(["BEFORE", _imap_date(args.before)])
    if args.from_address:
        criteria.extend(["FROM", f'"{args.from_address}"'])
    if args.to_address:
        criteria.extend(["TO", f'"{args.to_address}"'])
    if args.subject:
        criteria.extend(["SUBJECT", f'"{args.subject}"'])
    if args.body:
        criteria.extend(["BODY", f'"{args.body}"'])
    return criteria or ["ALL"]


def _imap_date(iso: str) -> str:
    """Convert ISO date / datetime to IMAP date format ('1-Jan-2026')."""
    from datetime import datetime  # noqa: PLC0415

    s = iso.strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    dt = datetime.fromisoformat(s)
    return dt.strftime("%d-%b-%Y")


def _do_search(
    conn: imaplib.IMAP4,
    args: _ImapReaderArgsWrapper,
    mailbox: str,
    *,
    max_messages: int,
) -> ImapResult:
    typ, _ = conn.select(mailbox, readonly=True)
    if typ != "OK":
        return ImapResult(
            action="search", ok=False, error=f"select {mailbox!r} failed"
        )
    criteria = _build_search_criteria(args)
    typ, data = conn.uid("SEARCH", None, *criteria)
    if typ != "OK" or not data or data[0] is None:
        return ImapResult(action="search", ok=True, messages=[])
    uids = data[0].decode("ascii", errors="ignore").split()
    truncated = False
    if len(uids) > max_messages:
        uids = uids[-max_messages:]
        truncated = True
    # FETCH envelope info for each uid.
    out: list[ImapMessage] = []
    for uid in reversed(uids):
        typ, data = conn.uid("FETCH", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
        if typ != "OK" or not data:
            continue
        flags: list[str] = []
        headers_raw = b""
        for part in data:
            if isinstance(part, tuple) and len(part) >= 2:
                hdr_blob = part[0] or b""
                body_blob = part[1] or b""
                if isinstance(hdr_blob, bytes):
                    text = hdr_blob.decode("ascii", errors="ignore")
                    # FLAGS appear in the response header chunk.
                    if "FLAGS" in text:
                        a = text.index("(", text.index("FLAGS")) + 1
                        b = text.index(")", a)
                        flags = text[a:b].split()
                if isinstance(body_blob, bytes):
                    headers_raw += body_blob
        msg = email.message_from_bytes(headers_raw)
        out.append(
            ImapMessage(
                uid=str(uid),
                mailbox=mailbox,
                subject=_decode_header(msg.get("Subject")),
                from_address=_decode_header(msg.get("From")),
                to_address=_decode_header(msg.get("To")),
                date=msg.get("Date"),
                flags=flags,
            )
        )
    return ImapResult(action="search", ok=True, messages=out, truncated=truncated)


def _do_get_message(
    conn: imaplib.IMAP4,
    mailbox: str,
    uid: str,
    *,
    max_body_bytes: int,
    body_format: str,
    mark_seen: bool,
) -> ImapResult:
    typ, _ = conn.select(mailbox, readonly=not mark_seen)
    if typ != "OK":
        return ImapResult(
            action="get_message", ok=False, error=f"select {mailbox!r} failed"
        )
    fetch_what = "(RFC822)"
    typ, data = conn.uid("FETCH", uid, fetch_what)
    if typ != "OK" or not data or not data[0]:
        return ImapResult(action="get_message", ok=False, error="message not found")
    part = data[0]
    raw = b""
    if isinstance(part, tuple) and len(part) >= 2:
        raw = part[1] or b""
    if not isinstance(raw, bytes):
        raw = bytes(raw)  # type: ignore[arg-type]
    msg: Message = email.message_from_bytes(raw)

    body_text: str | None = None
    body_html: str | None = None
    has_attachments = False
    for part_msg in msg.walk():
        ct = part_msg.get_content_type()
        cd = (part_msg.get("Content-Disposition") or "").lower()
        if "attachment" in cd:
            has_attachments = True
            continue
        if ct == "text/plain" and body_text is None:
            payload = part_msg.get_payload(decode=True) or b""
            try:
                body_text = payload.decode(
                    part_msg.get_content_charset() or "utf-8",
                    errors="replace",
                )
            except LookupError:
                body_text = payload.decode("utf-8", errors="replace")
        elif ct == "text/html" and body_html is None:
            payload = part_msg.get_payload(decode=True) or b""
            try:
                body_html = payload.decode(
                    part_msg.get_content_charset() or "utf-8",
                    errors="replace",
                )
            except LookupError:
                body_html = payload.decode("utf-8", errors="replace")

    if body_text and len(body_text) > max_body_bytes:
        body_text = body_text[:max_body_bytes]
    if body_html and len(body_html) > max_body_bytes:
        body_html = body_html[:max_body_bytes]

    keep_text = body_format in {"text", "both"}
    keep_html = body_format in {"html", "both"}

    return ImapResult(
        action="get_message",
        ok=True,
        message=ImapMessage(
            uid=str(uid),
            mailbox=mailbox,
            subject=_decode_header(msg.get("Subject")),
            from_address=_decode_header(msg.get("From")),
            to_address=_decode_header(msg.get("To")),
            date=msg.get("Date"),
            body=body_text if keep_text else None,
            body_html=body_html if keep_html else None,
            has_attachments=has_attachments,
        ),
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


async def discover(cfg: dict[str, Any], ctx: Any) -> ImapDiscovery:
    host = (cfg.get("host") or "").strip()
    if not host:
        return ImapDiscovery(
            ok=False,
            error="host not set",
            error_code=ErrorCode.OPERATOR_OVERRIDE_REFUSED.value,
            error_detail={"plugin": "imap_reader", "field": "host"},
        )
    try:
        password = _resolve_password(cfg, ctx)
    except SparkError as exc:
        return ImapDiscovery(
            ok=False,
            error=exc.message,
            error_code=exc.code.value,
            error_detail=exc.detail,
        )
    return await asyncio.to_thread(_discover_sync, cfg, password)


def _discover_sync(cfg: dict[str, Any], password: str) -> ImapDiscovery:
    host = cfg["host"].strip()
    port = int(cfg.get("port") or 993)
    use_ssl = bool(cfg.get("use_ssl", True))
    username = (cfg.get("username") or "").strip()
    timeout = float(cfg.get("connect_timeout_seconds") or 10.0)

    try:
        if use_ssl:
            conn = imaplib.IMAP4_SSL(host=host, port=port, timeout=timeout)
        else:
            conn = imaplib.IMAP4(host=host, port=port, timeout=timeout)
        conn.login(username, password)
    except Exception as exc:
        err = _classify_imap_error(exc, host=host)
        return ImapDiscovery(
            ok=False,
            error=err.message,
            error_code=err.code.value,
            error_detail=err.detail,
        )

    try:
        typ, data = conn.list()
        if typ != "OK":
            return ImapDiscovery(
                ok=False, error="IMAP LIST failed",
                error_code=ErrorCode.PLUGIN_RAISED.value,
            )
        caps = list(conn.capabilities) if hasattr(conn, "capabilities") else []
    finally:
        try:
            conn.logout()
        except Exception:  # pragma: no cover
            pass

    entries: list[ImapMailboxEntry] = []
    for entry in data or []:
        if entry is None:
            continue
        raw = (
            entry.decode("utf-8", errors="replace")
            if isinstance(entry, bytes)
            else str(entry)
        )
        try:
            attr_end = raw.index(")")
            attrs_raw = raw[1:attr_end]
            rest = raw[attr_end + 1 :].strip()
            delim_end = rest.index('" "', 1) + 1
            delimiter = rest[1:delim_end - 1]
            name = rest[delim_end + 1 :].strip().strip('"')
        except ValueError:
            continue
        attributes = [a.strip("()\\") for a in attrs_raw.split()]
        entries.append(
            ImapMailboxEntry(
                name=name,
                delimiter=delimiter,
                attributes=attributes,
                risk=mailbox_risk(name),
            )
        )
    entries.sort(key=lambda e: e.name.lower())
    return ImapDiscovery(
        ok=True,
        server=host,
        capabilities=caps,
        mailboxes=entries,
    )

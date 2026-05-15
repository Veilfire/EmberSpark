"""IMAP reader plugin — config + allowlist gates + error mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from spark.errors.codes import ErrorCode, SparkError
from spark.plugins.builtins.imap_reader import (
    ImapReaderConfig,
    ImapReaderPlugin,
    _ImapReaderArgsWrapper,
    _classify_imap_error,
    _refuse_mailbox,
    discover,
    mailbox_risk,
)


def _ctx(secrets, cfg):
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.secrets = secrets
    ctx.plugin_config = cfg
    return ctx


def _wrapper(action: str, **kw):
    return _ImapReaderArgsWrapper(action=action, **kw)


# ---------------------------------------------------------------------------
# Risk classification + config defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("INBOX", "safe"),
        ("Work", "safe"),
        ("Personal/Receipts", "safe"),
        ("Sent", "elevated"),
        ("Drafts", "elevated"),
        ("Trash", "elevated"),
        ("Junk", "elevated"),
        ("[Gmail]/Sent Mail", "elevated"),
        ("[Gmail]/All Mail", "danger"),
        ("[Gmail]/Trash", "danger"),
        ("[Gmail]/Spam", "danger"),
    ],
)
def test_mailbox_risk(name, expected):
    assert mailbox_risk(name) == expected


def test_config_defaults():
    cfg = ImapReaderConfig()
    assert cfg.port == 993
    assert cfg.use_ssl is True
    assert cfg.allowed_mailboxes == ["INBOX"]
    assert cfg.password_secret == "imap_password"
    assert cfg.body_format == "text"
    assert cfg.download_attachments is False
    assert cfg.mark_seen_on_read is False


# ---------------------------------------------------------------------------
# Failure inspector wiring
# ---------------------------------------------------------------------------


def test_refuse_mailbox_shape():
    err = _refuse_mailbox("[Gmail]/All Mail")
    assert err.code is ErrorCode.PERMISSION_MISSING
    assert err.detail["plugin"] == "imap_reader"
    assert err.detail["missing_allowlist_item"] == "[Gmail]/All Mail"
    assert err.detail["field"] == "allowed_mailboxes"
    assert err.detail["risk"] == "danger"


def test_refuse_mailbox_emits_failure_inspector_options():
    err = SparkError(
        ErrorCode.PERMISSION_MISSING,
        "imap_reader: refused",
        detail={
            "plugin": "imap_reader",
            "missing_allowlist_item": "[Gmail]/All Mail",
            "field": "allowed_mailboxes",
            "risk": "danger",
        },
    )
    payload = err.to_dict()
    actionable = [t for t in payload["tuning"] if t["deep_link"]]
    assert actionable
    first = actionable[0]
    assert first["prefill"]["kind"] == "plugin_allowlist_grant"
    assert first["prefill"]["plugin"] == "imap_reader"
    assert first["prefill"]["add_item"] == "[Gmail]/All Mail"
    assert first["prefill"]["field"] == "allowed_mailboxes"
    # Danger items get the critical chip.
    assert first["severity"] == "critical"


# ---------------------------------------------------------------------------
# Execute — refusal paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refuses_when_host_empty():
    plugin = ImapReaderPlugin()
    ctx = _ctx({"imap_password": "p"}, {})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_wrapper("list_mailboxes"), ctx)
    assert exc.value.code is ErrorCode.OPERATOR_OVERRIDE_REFUSED
    assert exc.value.detail["field"] == "host"


@pytest.mark.asyncio
async def test_refuses_when_secret_missing():
    plugin = ImapReaderPlugin()
    ctx = _ctx({}, {"host": "imap.example.com", "username": "u"})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_wrapper("list_mailboxes"), ctx)
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND
    assert exc.value.detail["secret_name"] == "imap_password"


@pytest.mark.asyncio
async def test_search_refuses_disallowed_mailbox():
    plugin = ImapReaderPlugin()
    ctx = _ctx(
        {"imap_password": "p"},
        {
            "host": "imap.example.com",
            "username": "u",
            "allowed_mailboxes": ["INBOX"],
        },
    )
    fake_imap = MagicMock()
    fake_imap.login = MagicMock(return_value=("OK", []))
    fake_imap.logout = MagicMock()
    with patch("imaplib.IMAP4_SSL", return_value=fake_imap):
        with pytest.raises(SparkError) as exc:
            await plugin.execute(_wrapper("search", mailbox="Archive"), ctx)
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_allowlist_item"] == "Archive"
    assert exc.value.detail["field"] == "allowed_mailboxes"


@pytest.mark.asyncio
async def test_get_message_refuses_disallowed_mailbox():
    plugin = ImapReaderPlugin()
    ctx = _ctx(
        {"imap_password": "p"},
        {
            "host": "imap.example.com",
            "username": "u",
            "allowed_mailboxes": ["INBOX"],
        },
    )
    fake_imap = MagicMock()
    fake_imap.login = MagicMock(return_value=("OK", []))
    fake_imap.logout = MagicMock()
    with patch("imaplib.IMAP4_SSL", return_value=fake_imap):
        with pytest.raises(SparkError) as exc:
            await plugin.execute(
                _wrapper("get_message", mailbox="Trash", uid="1"), ctx
            )
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_allowlist_item"] == "Trash"


@pytest.mark.asyncio
async def test_get_message_requires_uid():
    plugin = ImapReaderPlugin()
    ctx = _ctx(
        {"imap_password": "p"},
        {
            "host": "imap.example.com",
            "username": "u",
            "allowed_mailboxes": ["INBOX"],
        },
    )
    fake_imap = MagicMock()
    fake_imap.login = MagicMock(return_value=("OK", []))
    fake_imap.logout = MagicMock()
    with patch("imaplib.IMAP4_SSL", return_value=fake_imap):
        with pytest.raises(SparkError) as exc:
            await plugin.execute(_wrapper("get_message", mailbox="INBOX"), ctx)
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def test_classify_auth_error():
    import imaplib

    exc = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    err = _classify_imap_error(exc, host="imap.example.com")
    assert err.code is ErrorCode.SECRET_NOT_FOUND


def test_classify_connect_error_private_ip():
    exc = Exception("Connection refused")
    err = _classify_imap_error(exc, host="192.168.1.10")
    assert err.code is ErrorCode.URL_PRIVATE_IP


def test_classify_connect_error_public_host():
    exc = Exception("Connection timed out")
    err = _classify_imap_error(exc, host="imap.example.com")
    assert err.code is ErrorCode.URL_DENIED


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_returns_error_when_host_empty():
    result = await discover({}, _ctx({}, {}))
    assert result.ok is False
    assert result.error_code == ErrorCode.OPERATOR_OVERRIDE_REFUSED.value


@pytest.mark.asyncio
async def test_discover_returns_error_when_secret_missing():
    cfg = {"host": "imap.example.com", "username": "u"}
    result = await discover(cfg, _ctx({}, cfg))
    assert result.ok is False
    assert result.error_code == ErrorCode.SECRET_NOT_FOUND.value


@pytest.mark.asyncio
async def test_discover_happy_path():
    cfg = {"host": "imap.example.com", "username": "u"}
    ctx = _ctx({"imap_password": "p"}, cfg)
    fake_imap = MagicMock()
    fake_imap.login = MagicMock(return_value=("OK", []))
    fake_imap.list = MagicMock(
        return_value=(
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "Sent"',
                b'(\\HasNoChildren) "/" "[Gmail]/All Mail"',
            ],
        )
    )
    fake_imap.logout = MagicMock()
    fake_imap.capabilities = ["IMAP4REV1", "STARTTLS"]
    with patch("imaplib.IMAP4_SSL", return_value=fake_imap):
        result = await discover(cfg, ctx)
    assert result.ok is True
    names = [m.name for m in result.mailboxes]
    assert "INBOX" in names
    assert "Sent" in names
    assert "[Gmail]/All Mail" in names
    # Risk surfaces.
    risks = {m.name: m.risk for m in result.mailboxes}
    assert risks["INBOX"] == "safe"
    assert risks["Sent"] == "elevated"
    assert risks["[Gmail]/All Mail"] == "danger"

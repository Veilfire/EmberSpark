"""cloud_drive plugin — provider-centric config, gates, synthesis, auto-share.

These tests cover the v2 schema (providers list, per-provider auth +
allowed_paths + auto_share, global read_only / max_file_bytes /
file_type_allowlist). The plugin owns the credential store; rclone is
an implementation detail invoked via a per-call temp config file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from spark.errors.codes import ErrorCode, SparkError
from spark.plugins.builtins.cloud_drive import (
    AutoShareSpec,
    CloudDriveConfig,
    CloudDrivePlugin,
    DropboxAuth,
    GoogleDriveAuth,
    OneDriveAuth,
    ProtonDriveAuth,
    ProviderSpec,
    _auto_share,
    _CloudDriveArgsWrapper,
    _check_file_type,
    _extract_access_token,
    _file_extension,
    _normalize_path,
    _path_allowed,
    _persist_refreshed_tokens,
    _refuse_file_type,
    _refuse_path,
    _refuse_provider,
    _refuse_read_only,
    _resolve_secret,
    _synthesize_rclone_config,
    _token_secret_name,
    discover,
)


def _ctx(cfg, scratch_path=None, secrets=None):
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.secrets = secrets or {}
    ctx.plugin_config = cfg
    ctx.scratch_path = scratch_path
    return ctx


def _w(action: str, **kw):
    return _CloudDriveArgsWrapper(action=action, **kw)


def _gdrive_provider(name="gdrive_work", **overrides):
    return ProviderSpec(
        name=name,
        enabled=True,
        auth=GoogleDriveAuth(token_secret=f"{name}_token"),
        allowed_paths=["Spark-agent"],
        **overrides,
    ).model_dump()


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


def test_config_defaults():
    cfg = CloudDriveConfig()
    assert cfg.providers == []
    assert cfg.read_only is True
    assert cfg.max_file_bytes == 52_428_800
    # Default extensions match the spec the operator approved.
    assert "pdf" in cfg.file_type_allowlist
    assert "docx" in cfg.file_type_allowlist
    assert "exe" not in cfg.file_type_allowlist


def test_provider_name_must_be_slug():
    with pytest.raises(ValidationError):
        ProviderSpec(
            name="Invalid Name!",
            auth=GoogleDriveAuth(token_secret="t"),
        )


def test_duplicate_provider_names_rejected():
    p = _gdrive_provider("gdrive_work")
    with pytest.raises(ValidationError):
        CloudDriveConfig(providers=[p, p])


def test_auth_discriminator_routes_to_right_subclass():
    p = ProviderSpec.model_validate(
        {
            "name": "drop",
            "auth": {"kind": "dropbox", "token_secret": "drop_token"},
            "allowed_paths": ["Spark-agent"],
        }
    )
    assert isinstance(p.auth, DropboxAuth)
    p2 = ProviderSpec.model_validate(
        {
            "name": "od",
            "auth": {"kind": "onedrive", "token_secret": "od_token", "drive_type": "business", "drive_id": "abc"},
            "allowed_paths": ["Reports"],
        }
    )
    assert isinstance(p2.auth, OneDriveAuth)
    assert p2.auth.drive_type == "business"


def test_proton_drive_auth_has_username_and_pw():
    p = ProviderSpec.model_validate(
        {
            "name": "proton",
            "auth": {"kind": "protondrive", "username": "u@p.me", "password_secret": "pw"},
            "allowed_paths": ["Spark-agent"],
        }
    )
    assert isinstance(p.auth, ProtonDriveAuth)
    assert p.auth.username == "u@p.me"


def test_extra_auth_field_rejected():
    with pytest.raises(ValidationError):
        ProviderSpec.model_validate(
            {
                "name": "g",
                "auth": {"kind": "drive", "token_secret": "t", "rogue_field": "x"},
                "allowed_paths": ["Spark-agent"],
            }
        )


def test_auto_share_recipients_must_be_emails():
    with pytest.raises(ValidationError):
        AutoShareSpec(enabled=True, recipients=["not-an-email"])
    # valid passes
    s = AutoShareSpec(enabled=True, recipients=["op@example.com"], permission="writer")
    assert s.permission == "writer"


# ---------------------------------------------------------------------------
# Path + file-type gates
# ---------------------------------------------------------------------------


def test_path_allowed_under_root():
    assert _path_allowed("Spark-agent/file.pdf", ["Spark-agent"]) is True
    assert _path_allowed("Spark-agent", ["Spark-agent"]) is True
    assert _path_allowed("Spark-agent/sub/deep.pdf", ["Spark-agent"]) is True


def test_path_allowed_multiple_roots():
    assert _path_allowed("Reports/q1.pdf", ["Spark-agent", "Reports"]) is True
    assert _path_allowed("Other/x.pdf", ["Spark-agent", "Reports"]) is False


def test_path_root_refused_when_no_match():
    assert _path_allowed("", ["Spark-agent"]) is False
    assert _path_allowed("Personal/secrets.pdf", ["Spark-agent"]) is False


def test_path_traversal_refused():
    with pytest.raises(SparkError) as ei:
        _normalize_path("Spark-agent/../etc/passwd")
    assert ei.value.code is ErrorCode.PATH_TRAVERSAL


def test_path_prefix_match_not_subpath():
    # "Spark-agent2" must NOT match because "Spark-agent" is its prefix.
    assert _path_allowed("Spark-agent2/file.pdf", ["Spark-agent"]) is False


def test_file_extension_lowercases():
    assert _file_extension("Report.PDF") == "pdf"
    assert _file_extension("archive.tar.gz") == "gz"
    assert _file_extension("README") == ""


def test_check_file_type_allowlist():
    _check_file_type("file.pdf", ["pdf", "txt"])
    with pytest.raises(SparkError) as ei:
        _check_file_type("malware.exe", ["pdf", "txt"])
    assert ei.value.code is ErrorCode.FILE_TYPE_DENIED
    assert ei.value.detail["extension"] == "exe"


def test_check_file_type_no_extension_refused():
    with pytest.raises(SparkError) as ei:
        _check_file_type("README", ["pdf"])
    assert ei.value.code is ErrorCode.FILE_TYPE_DENIED


# ---------------------------------------------------------------------------
# Refusal shapes wire to Failure Inspector
# ---------------------------------------------------------------------------


def test_refuse_provider_emits_inspector_shape():
    err = _refuse_provider("gdrive_work")
    assert err.code is ErrorCode.PERMISSION_MISSING
    assert err.detail["plugin"] == "cloud_drive"
    assert err.detail["missing_allowlist_item"] == "gdrive_work"
    assert err.detail["field"] == "providers"
    # Catalogue produces a plugin_allowlist_grant prefill targeting the editor
    tuning = err.to_dict()["tuning"]
    labels = [t["label"] for t in tuning]
    assert any("Allow `gdrive_work` on cloud_drive" in label for label in labels)


def test_refuse_path_emits_provider_scoped_prefill():
    err = _refuse_path("gdrive_work", "OtherFolder/file.pdf")
    tuning = err.to_dict()["tuning"]
    # The cloud_drive-specific PATH_DENIED branch should produce a
    # tuning option that deep-links to /plugins?plugin=cloud_drive
    assert any("allowed_paths" in t["label"] for t in tuning)
    deep_link = next(t["deep_link"] for t in tuning if t.get("deep_link"))
    assert "plugin=cloud_drive" in deep_link


def test_refuse_file_type_emits_extension_prefill():
    err = _refuse_file_type("exe")
    tuning = err.to_dict()["tuning"]
    labels = [t["label"] for t in tuning]
    assert any(".exe" in label and "file_type_allowlist" in label for label in labels)


def test_refuse_read_only_shape():
    err = _refuse_read_only()
    assert err.code is ErrorCode.PERMISSION_MISSING
    assert err.detail["missing_toggle"] == "read_only"


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------


def test_resolve_secret_returns_value():
    ctx = _ctx({}, secrets={"my_token": "value123"})
    assert _resolve_secret("my_token", ctx) == "value123"


def test_resolve_secret_empty_name_returns_empty():
    ctx = _ctx({})
    assert _resolve_secret("", ctx) == ""


def test_resolve_secret_missing_raises():
    ctx = _ctx({}, secrets={})
    with pytest.raises(SparkError) as ei:
        _resolve_secret("missing", ctx)
    assert ei.value.code is ErrorCode.SECRET_NOT_FOUND


# ---------------------------------------------------------------------------
# rclone config synthesis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_drive_config():
    providers = [
        ProviderSpec.model_validate(_gdrive_provider("gdrive_work"))
    ]
    ctx = _ctx({}, secrets={"gdrive_work_token": '{"access_token":"X"}'})
    txt = await _synthesize_rclone_config(providers, "rclone", ctx)
    assert "[gdrive_work]" in txt
    assert "type = drive" in txt
    assert 'token = {"access_token":"X"}' in txt


@pytest.mark.asyncio
async def test_synthesize_skips_disabled_providers():
    providers = [
        ProviderSpec.model_validate(
            {**_gdrive_provider("gdrive_work"), "enabled": False}
        )
    ]
    ctx = _ctx({}, secrets={"gdrive_work_token": "X"})
    txt = await _synthesize_rclone_config(providers, "rclone", ctx)
    assert "[gdrive_work]" not in txt


@pytest.mark.asyncio
async def test_synthesize_onedrive_drive_id_for_business():
    providers = [
        ProviderSpec.model_validate(
            {
                "name": "od_biz",
                "auth": {
                    "kind": "onedrive",
                    "token_secret": "od_token",
                    "drive_type": "business",
                    "drive_id": "abc-123",
                },
                "allowed_paths": ["Spark-agent"],
            }
        )
    ]
    ctx = _ctx({}, secrets={"od_token": '{"access_token":"Y"}'})
    txt = await _synthesize_rclone_config(providers, "rclone", ctx)
    assert "drive_type = business" in txt
    assert "drive_id = abc-123" in txt


@pytest.mark.asyncio
async def test_synthesize_protondrive_obscures_password():
    providers = [
        ProviderSpec.model_validate(
            {
                "name": "proton",
                "auth": {
                    "kind": "protondrive",
                    "username": "u@p.me",
                    "password_secret": "proton_pw",
                },
                "allowed_paths": ["Spark-agent"],
            }
        )
    ]
    ctx = _ctx({}, secrets={"proton_pw": "plaintext-pw"})

    async def fake_subprocess(*args, **kw):
        mock = MagicMock()
        mock.returncode = 0
        mock.communicate = AsyncMock(return_value=(b"OBSCURED_BLOB\n", b""))
        return mock

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        txt = await _synthesize_rclone_config(providers, "rclone", ctx)
    assert "[proton]" in txt
    assert "type = protondrive" in txt
    assert "username = u@p.me" in txt
    assert "password = OBSCURED_BLOB" in txt
    # plaintext must NOT appear
    assert "plaintext-pw" not in txt


# ---------------------------------------------------------------------------
# Execute — refusal paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_unknown_provider_refused():
    cfg = CloudDriveConfig(providers=[ProviderSpec.model_validate(_gdrive_provider())])
    ctx = _ctx(cfg.model_dump())
    plugin = CloudDrivePlugin()
    with pytest.raises(SparkError) as ei:
        await plugin.execute(_w("list", provider="not_configured", path="X"), ctx)
    assert ei.value.code is ErrorCode.PERMISSION_MISSING
    assert ei.value.detail["field"] == "providers"


@pytest.mark.asyncio
async def test_execute_disabled_provider_refused():
    p = _gdrive_provider()
    p["enabled"] = False
    cfg = CloudDriveConfig(providers=[ProviderSpec.model_validate(p)])
    ctx = _ctx(cfg.model_dump())
    plugin = CloudDrivePlugin()
    with pytest.raises(SparkError) as ei:
        await plugin.execute(_w("list", provider="gdrive_work", path="Spark-agent"), ctx)
    assert ei.value.code is ErrorCode.PERMISSION_MISSING


@pytest.mark.asyncio
async def test_execute_path_outside_allowlist_refused():
    cfg = CloudDriveConfig(providers=[ProviderSpec.model_validate(_gdrive_provider())])
    ctx = _ctx(cfg.model_dump())
    plugin = CloudDrivePlugin()
    with pytest.raises(SparkError) as ei:
        await plugin.execute(_w("list", provider="gdrive_work", path="OtherFolder"), ctx)
    assert ei.value.code is ErrorCode.PATH_DENIED
    assert ei.value.detail["provider"] == "gdrive_work"


@pytest.mark.asyncio
async def test_execute_empty_allowed_paths_refuses_all():
    p = _gdrive_provider()
    p["allowed_paths"] = []
    cfg = CloudDriveConfig(providers=[ProviderSpec.model_validate(p)])
    ctx = _ctx(cfg.model_dump())
    plugin = CloudDrivePlugin()
    with pytest.raises(SparkError) as ei:
        await plugin.execute(_w("list", provider="gdrive_work", path="anything"), ctx)
    assert ei.value.code is ErrorCode.PATH_DENIED


@pytest.mark.asyncio
async def test_execute_put_refused_when_read_only():
    cfg = CloudDriveConfig(
        providers=[ProviderSpec.model_validate(_gdrive_provider())],
        read_only=True,
    )
    ctx = _ctx(cfg.model_dump(), scratch_path="/tmp/scratch")
    plugin = CloudDrivePlugin()
    with pytest.raises(SparkError) as ei:
        await plugin.execute(
            _w("put", provider="gdrive_work", path="Spark-agent/x.pdf", local_path="/tmp/x.pdf"),
            ctx,
        )
    assert ei.value.code is ErrorCode.PERMISSION_MISSING
    assert ei.value.detail["missing_toggle"] == "read_only"


@pytest.mark.asyncio
async def test_execute_get_refuses_disallowed_file_type():
    cfg = CloudDriveConfig(
        providers=[ProviderSpec.model_validate(_gdrive_provider())],
        file_type_allowlist=["pdf"],
    )
    ctx = _ctx(cfg.model_dump(), scratch_path="/tmp/scratch")
    plugin = CloudDrivePlugin()
    with pytest.raises(SparkError) as ei:
        await plugin.execute(_w("get", provider="gdrive_work", path="Spark-agent/x.exe"), ctx)
    assert ei.value.code is ErrorCode.FILE_TYPE_DENIED


@pytest.mark.asyncio
async def test_execute_list_providers_lists_enabled_only():
    p1 = _gdrive_provider("gdrive_work")
    p2 = _gdrive_provider("gdrive_personal")
    p2["enabled"] = False
    cfg = CloudDriveConfig(
        providers=[
            ProviderSpec.model_validate(p1),
            ProviderSpec.model_validate(p2),
        ]
    )
    ctx = _ctx(cfg.model_dump())
    plugin = CloudDrivePlugin()
    res = await plugin.execute(_w("list_providers"), ctx)
    assert res.ok is True
    names = [p["name"] for p in res.providers]
    assert names == ["gdrive_work"]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_missing_rclone_returns_typed_error(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    res = await discover({"rclone_path": "rclone"}, _ctx({}))
    assert res.ok is False
    assert res.error_code == ErrorCode.SANDBOX_UNAVAILABLE.value
    assert res.rclone_available is False


def test_extract_access_token_from_rclone_blob():
    blob = '{"access_token":"abc","refresh_token":"r","expiry":"..."}'
    assert _extract_access_token(blob) == "abc"
    assert _extract_access_token("not json") is None
    assert _extract_access_token("") is None


def test_token_secret_name_per_provider():
    assert _token_secret_name(GoogleDriveAuth(token_secret="g")) == "g"
    assert _token_secret_name(OneDriveAuth(token_secret="od")) == "od"
    assert _token_secret_name(DropboxAuth(token_secret="dp")) == "dp"
    assert _token_secret_name(ProtonDriveAuth(username="u", password_secret="p")) == ""


# ---------------------------------------------------------------------------
# Auto-share dispatchers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_share_skipped_when_disabled():
    p = ProviderSpec.model_validate(_gdrive_provider())
    ctx = _ctx({}, secrets={"gdrive_work_token": '{"access_token":"X"}'})
    got = await _auto_share(p, "Spark-agent/x.pdf", ctx, {})
    assert got == []


@pytest.mark.asyncio
async def test_auto_share_drive_calls_permissions_api(monkeypatch):
    auth = GoogleDriveAuth(token_secret="gdrive_token")
    p = ProviderSpec(
        name="gdrive_work",
        auth=auth,
        allowed_paths=["Spark-agent"],
        auto_share=AutoShareSpec(
            enabled=True,
            recipients=["op@example.com", "ops@example.com"],
            permission="writer",
        ),
    )
    ctx = _ctx({}, secrets={"gdrive_token": '{"access_token":"AT123"}'})

    async def fake_lookup(*_args, **_kw):
        return "FILE_ID"

    monkeypatch.setattr(
        "spark.plugins.builtins.cloud_drive._rclone_lookup_file_id", fake_lookup
    )

    posted: list[dict] = []

    class FakeResp:
        status_code = 204

    class FakeClient:
        def __init__(self, *_a, **_k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, *, headers=None, params=None, json=None):
            posted.append({"url": url, "headers": headers, "params": params, "json": json})
            return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    got = await _auto_share(p, "Spark-agent/x.pdf", ctx, {})
    assert sorted(got) == sorted(["op@example.com", "ops@example.com"])
    assert len(posted) == 2
    assert posted[0]["json"]["role"] == "writer"
    assert posted[0]["headers"]["Authorization"] == "Bearer AT123"
    assert "files/FILE_ID/permissions" in posted[0]["url"]


@pytest.mark.asyncio
async def test_auto_share_drive_team_drive_passes_supports_all_drives(monkeypatch):
    auth = GoogleDriveAuth(token_secret="g", team_drive="0AABBCC")
    p = ProviderSpec(
        name="g_team",
        auth=auth,
        allowed_paths=["Reports"],
        auto_share=AutoShareSpec(enabled=True, recipients=["op@example.com"]),
    )
    ctx = _ctx({}, secrets={"g": '{"access_token":"AT"}'})

    async def fake_lookup(*_a, **_k): return "FID"
    monkeypatch.setattr(
        "spark.plugins.builtins.cloud_drive._rclone_lookup_file_id", fake_lookup
    )

    captured: list[dict] = []

    class FakeResp: status_code = 200
    class FakeClient:
        def __init__(self, *_a, **_k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, *, headers=None, params=None, json=None):
            captured.append(params or {})
            return FakeResp()
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    await _auto_share(p, "Reports/q1.pdf", ctx, {})
    assert captured[0].get("supportsAllDrives") == "true"


@pytest.mark.asyncio
async def test_auto_share_onedrive_business_uses_drive_id_endpoint(monkeypatch):
    auth = OneDriveAuth(
        token_secret="od_tok",
        drive_type="business",
        drive_id="DRIVE_ABC",
    )
    p = ProviderSpec(
        name="od_biz",
        auth=auth,
        allowed_paths=["Reports"],
        auto_share=AutoShareSpec(enabled=True, recipients=["op@example.com"], permission="writer"),
    )
    ctx = _ctx({}, secrets={"od_tok": '{"access_token":"AT"}'})

    async def fake_lookup(*_a, **_k): return "FILE_ID"
    monkeypatch.setattr(
        "spark.plugins.builtins.cloud_drive._rclone_lookup_file_id", fake_lookup
    )

    captured: list[str] = []
    captured_payload: list[dict] = []

    class FakeResp: status_code = 200
    class FakeClient:
        def __init__(self, *_a, **_k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, *, headers=None, json=None):
            captured.append(url)
            captured_payload.append(json)
            return FakeResp()
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    got = await _auto_share(p, "Reports/q1.pdf", ctx, {})
    assert got == ["op@example.com"]
    assert "drives/DRIVE_ABC/items/FILE_ID/invite" in captured[0]
    # writer → "write" (no "writer" role in OneDrive)
    assert captured_payload[0]["roles"] == ["write"]
    assert captured_payload[0]["sendInvitation"] is False


@pytest.mark.asyncio
async def test_auto_share_onedrive_personal_uses_me_drive(monkeypatch):
    auth = OneDriveAuth(token_secret="od_tok", drive_type="personal")
    p = ProviderSpec(
        name="od_p",
        auth=auth,
        allowed_paths=["X"],
        auto_share=AutoShareSpec(enabled=True, recipients=["op@example.com"]),
    )
    ctx = _ctx({}, secrets={"od_tok": '{"access_token":"AT"}'})

    async def fake_lookup(*_a, **_k): return "FID"
    monkeypatch.setattr(
        "spark.plugins.builtins.cloud_drive._rclone_lookup_file_id", fake_lookup
    )

    urls: list[str] = []
    class FakeResp: status_code = 200
    class FakeClient:
        def __init__(self, *_a, **_k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, *, headers=None, json=None):
            urls.append(url)
            return FakeResp()
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    await _auto_share(p, "X/y.pdf", ctx, {})
    assert "me/drive/items/FID/invite" in urls[0]


@pytest.mark.asyncio
async def test_auto_share_dropbox_uses_path_not_id(monkeypatch):
    auth = DropboxAuth(token_secret="db_tok")
    p = ProviderSpec(
        name="db",
        auth=auth,
        allowed_paths=["Spark-agent"],
        auto_share=AutoShareSpec(enabled=True, recipients=["op@example.com"], permission="writer"),
    )
    ctx = _ctx({}, secrets={"db_tok": '{"access_token":"AT"}'})

    captured: list[dict] = []
    class FakeResp: status_code = 200
    class FakeClient:
        def __init__(self, *_a, **_k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, *, headers=None, json=None):
            captured.append({"url": url, "json": json})
            return FakeResp()
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    got = await _auto_share(p, "Spark-agent/q1.pdf", ctx, {})
    assert got == ["op@example.com"]
    assert "add_file_member" in captured[0]["url"]
    # writer → editor in Dropbox parlance
    assert captured[0]["json"]["access_level"][".tag"] == "editor"
    # Path must have a leading slash
    assert captured[0]["json"]["file"] == "/Spark-agent/q1.pdf"


@pytest.mark.asyncio
async def test_auto_share_proton_returns_empty():
    p = ProviderSpec(
        name="pr",
        auth=ProtonDriveAuth(username="u@p.me", password_secret="pw"),
        allowed_paths=["X"],
        auto_share=AutoShareSpec(enabled=True, recipients=["op@example.com"]),
    )
    ctx = _ctx({}, secrets={"pw": "P"})
    got = await _auto_share(p, "X/y.pdf", ctx, {})
    assert got == []


@pytest.mark.asyncio
async def test_auto_share_handles_401_silently(monkeypatch):
    auth = GoogleDriveAuth(token_secret="g")
    p = ProviderSpec(
        name="g",
        auth=auth,
        allowed_paths=["X"],
        auto_share=AutoShareSpec(enabled=True, recipients=["op@example.com"]),
    )
    ctx = _ctx({}, secrets={"g": '{"access_token":"AT"}'})

    async def fake_lookup(*_a, **_k): return "FID"
    monkeypatch.setattr(
        "spark.plugins.builtins.cloud_drive._rclone_lookup_file_id", fake_lookup
    )

    class FakeResp: status_code = 401
    class FakeClient:
        def __init__(self, *_a, **_k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, *_a, **_kw): return FakeResp()
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    got = await _auto_share(p, "X/y.pdf", ctx, {})
    # Silent skip — no exception, just empty grant list
    assert got == []


# ---------------------------------------------------------------------------
# Token-refresh persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_refreshed_tokens_writes_back_changed_value(tmp_path, monkeypatch):
    cfg_path = tmp_path / "rclone.conf"
    cfg_path.write_text(
        """[gdrive_work]
type = drive
token = {"access_token":"NEW_TOKEN"}
"""
    )

    p = ProviderSpec(
        name="gdrive_work",
        auth=GoogleDriveAuth(token_secret="gdrive_token"),
        allowed_paths=["X"],
    )
    ctx = _ctx({}, secrets={"gdrive_token": '{"access_token":"OLD"}'})

    written: dict = {}

    class FakeMgr:
        def set(self, name, value):
            written[name] = value

    monkeypatch.setattr(
        "spark.runtime.get_secret_manager", lambda: FakeMgr()
    )
    await _persist_refreshed_tokens([p], str(cfg_path), ctx)
    assert written["gdrive_token"] == '{"access_token":"NEW_TOKEN"}'
    # ctx.secrets was updated too so a follow-up call sees the new value
    assert ctx.secrets["gdrive_token"] == '{"access_token":"NEW_TOKEN"}'


@pytest.mark.asyncio
async def test_persist_refreshed_tokens_skips_unchanged(tmp_path, monkeypatch):
    cfg_path = tmp_path / "rclone.conf"
    cfg_path.write_text(
        """[g]
type = drive
token = {"access_token":"SAME"}
"""
    )
    p = ProviderSpec(
        name="g",
        auth=GoogleDriveAuth(token_secret="g_tok"),
        allowed_paths=["X"],
    )
    ctx = _ctx({}, secrets={"g_tok": '{"access_token":"SAME"}'})

    written: dict = {}

    class FakeMgr:
        def set(self, name, value):
            written[name] = value

    monkeypatch.setattr(
        "spark.runtime.get_secret_manager", lambda: FakeMgr()
    )
    await _persist_refreshed_tokens([p], str(cfg_path), ctx)
    assert written == {}


@pytest.mark.asyncio
async def test_persist_refreshed_tokens_ignores_proton(tmp_path, monkeypatch):
    cfg_path = tmp_path / "rclone.conf"
    cfg_path.write_text(
        """[pr]
type = protondrive
username = u@p.me
password = OBSCURED
"""
    )
    p = ProviderSpec(
        name="pr",
        auth=ProtonDriveAuth(username="u@p.me", password_secret="pw"),
        allowed_paths=["X"],
    )
    ctx = _ctx({}, secrets={"pw": "plaintext"})

    written: dict = {}

    class FakeMgr:
        def set(self, name, value):
            written[name] = value

    monkeypatch.setattr(
        "spark.runtime.get_secret_manager", lambda: FakeMgr()
    )
    await _persist_refreshed_tokens([p], str(cfg_path), ctx)
    # No write — Proton has no token field; password isn't refreshed
    assert written == {}


@pytest.mark.asyncio
async def test_discover_returns_per_provider_health(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/rclone")

    p1 = _gdrive_provider("gdrive_work")
    p2 = _gdrive_provider("gdrive_personal")
    p2["enabled"] = False
    cfg = {
        "providers": [p1, p2],
        "rclone_path": "rclone",
        "file_type_allowlist": ["pdf"],
        "max_file_bytes": 52_428_800,
        "read_only": True,
        "timeout_seconds": 30,
    }

    async def fake_run(*args, **kw):
        return 0, '{"free": 100, "total": 1000}', ""

    with patch("spark.plugins.builtins.cloud_drive._run_rclone", side_effect=fake_run):
        res = await discover(cfg, _ctx(cfg, secrets={"gdrive_work_token": '{"access_token":"X"}'}))
    assert res.ok is True
    names = [p.name for p in res.providers]
    assert "gdrive_work" in names
    assert "gdrive_personal" in names
    enabled = next(p for p in res.providers if p.name == "gdrive_work")
    assert enabled.ok is True
    assert enabled.free_bytes == 100
    disabled = next(p for p in res.providers if p.name == "gdrive_personal")
    assert disabled.ok is False
    assert disabled.error == "disabled"

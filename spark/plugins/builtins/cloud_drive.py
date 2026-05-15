"""Cloud drive plugin — Google Drive / OneDrive / Dropbox / Proton Drive.

v2 schema: provider-centric. The plugin owns the credential store
(secrets in the vault); rclone is an implementation detail. Operator
configures providers in the Spark UI; on every action we synthesize
a fresh rclone config in a per-call temp file, resolving secrets at
call time so credential rotations land immediately.

Guard rails:

1. **Provider allowlist** — only `enabled: true` providers are usable
2. **Per-provider path allowlist** — `allowed_paths` lists root paths
   the agent can read/write under. Empty refuses all.
3. **Read-only default** — `read_only: true` blocks `put`/`delete`
4. **File-type allowlist** — `file_type_allowlist` gates `get`/`put`
   by extension
5. **Size cap** — `max_file_bytes` (default 50 MB) on `put`/`get`
6. **Auto-share** — optional per-provider; `put` auto-grants access
   to the configured recipients (Drive: native API; others: deferred)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spark.config.enums import Permission, Sensitivity
from spark.errors import ErrorCode, SparkError


# ---------------------------------------------------------------------------
# Per-provider auth shapes (discriminated union on ``kind``)
# ---------------------------------------------------------------------------


class _AuthBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoogleDriveAuth(_AuthBase):
    kind: Literal["drive"] = "drive"
    token_secret: str = Field(
        default="",
        description=(
            "Vault entry holding the OAuth token JSON from "
            "`rclone authorize \"drive\"`."
        ),
    )
    client_id: str = Field(
        default="",
        description=(
            "Optional Google OAuth client_id. Strongly recommended — "
            "rclone's shared client is rate-limited. See "
            "https://rclone.org/drive/#making-your-own-client-id."
        ),
    )
    client_secret_secret: str = Field(
        default="",
        description="Optional vault entry holding the OAuth client secret.",
    )
    team_drive: str = Field(
        default="",
        description="Shared Drive ID. Empty = personal Drive.",
    )


class OneDriveAuth(_AuthBase):
    kind: Literal["onedrive"] = "onedrive"
    token_secret: str = Field(default="")
    drive_type: Literal["personal", "business", "documentLibrary"] = "personal"
    drive_id: str = Field(default="", description="Required for business / SharePoint.")
    client_id: str = Field(default="")
    client_secret_secret: str = Field(default="")


class DropboxAuth(_AuthBase):
    kind: Literal["dropbox"] = "dropbox"
    token_secret: str = Field(default="")
    client_id: str = Field(default="")
    client_secret_secret: str = Field(default="")


class ProtonDriveAuth(_AuthBase):
    kind: Literal["protondrive"] = "protondrive"
    username: str = Field(default="")
    password_secret: str = Field(default="")
    twofa_secret: str = Field(
        default="",
        description="Optional vault entry holding the 2FA TOTP code/seed.",
    )


ProviderAuth = Annotated[
    Union[GoogleDriveAuth, OneDriveAuth, DropboxAuth, ProtonDriveAuth],
    Field(discriminator="kind"),
]

ProviderKind = Literal["drive", "onedrive", "dropbox", "protondrive"]


# ---------------------------------------------------------------------------
# Auto-share spec
# ---------------------------------------------------------------------------


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AutoShareSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    recipients: list[str] = Field(
        default_factory=list,
        description=(
            "Email addresses the plugin grants access to after every "
            "`put`. Strict allowlist — only these recipients receive shares."
        ),
    )
    permission: Literal["reader", "writer", "commenter"] = "reader"

    @model_validator(mode="after")
    def _validate_recipients(self) -> "AutoShareSpec":
        for r in self.recipients:
            if not _EMAIL_PATTERN.match(r):
                raise ValueError(f"invalid recipient email {r!r}")
        return self


# ---------------------------------------------------------------------------
# Provider spec
# ---------------------------------------------------------------------------


_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Operator-chosen slug. Becomes the rclone remote name. "
            "Lowercase letters, digits, underscores."
        ),
    )
    enabled: bool = True
    auth: ProviderAuth
    allowed_paths: list[str] = Field(
        default_factory=list,
        description=(
            "Root paths the agent may touch on this remote (e.g. "
            "['Spark-agent', 'Public/Reports']). Empty refuses all."
        ),
    )
    auto_share: AutoShareSpec = Field(default_factory=AutoShareSpec)

    @model_validator(mode="after")
    def _validate_name(self) -> "ProviderSpec":
        if not _SLUG_PATTERN.match(self.name):
            raise ValueError(
                f"provider name {self.name!r} must match {_SLUG_PATTERN.pattern}"
            )
        return self


# ---------------------------------------------------------------------------
# Plugin config
# ---------------------------------------------------------------------------


_DEFAULT_FILE_TYPES = [
    "pdf", "txt", "doc", "docx", "xls", "xlsx", "png", "jpeg",
]


class CloudDriveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: list[ProviderSpec] = Field(
        default_factory=list,
        description="Configured cloud-storage providers.",
    )
    read_only: bool = Field(default=True)
    max_file_bytes: int = Field(default=52_428_800, gt=0)  # 50 MB
    file_type_allowlist: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_FILE_TYPES),
        description=(
            "Lowercase file extensions (no leading dot) the agent may "
            "read or write. Empty refuses all file types."
        ),
    )
    rclone_path: str = Field(default="rclone", max_length=512)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)

    @model_validator(mode="after")
    def _validate_unique_provider_names(self) -> "CloudDriveConfig":
        seen: set[str] = set()
        for p in self.providers:
            if p.name in seen:
                raise ValueError(f"duplicate provider name {p.name!r}")
            seen.add(p.name)
        return self


# ---------------------------------------------------------------------------
# Action surface
# ---------------------------------------------------------------------------


class _CloudDriveArgsWrapper(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list_providers", "list", "get", "put", "search", "delete"] = Field(
        description=(
            "'list_providers' (discovery), 'list' (folder), 'get' "
            "(download), 'put' (upload, gated by read_only), 'search', "
            "'delete' (gated by read_only)."
        ),
    )
    provider: str | None = Field(default=None, max_length=64)
    path: str | None = Field(default=None, max_length=1024)
    local_path: str | None = Field(default=None, max_length=1024)
    query: str | None = Field(default=None, max_length=256)
    recursive: bool = False


class CloudFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    path: str
    size: int | None = None
    is_dir: bool = False
    modified: str | None = None
    mime_type: str | None = None


class CloudDriveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    ok: bool
    providers: list[dict[str, Any]] | None = None
    files: list[CloudFile] | None = None
    local_path: str | None = None
    bytes_transferred: int | None = None
    shared_with: list[str] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Discovery shape
# ---------------------------------------------------------------------------


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: ProviderKind
    enabled: bool
    ok: bool
    error: str | None = None
    free_bytes: int | None = None
    total_bytes: int | None = None
    allowed_paths: list[str] = Field(default_factory=list)


class CloudDriveDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    error: str | None = None
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None
    providers: list[ProviderHealth] = Field(default_factory=list)
    rclone_available: bool = True


# ---------------------------------------------------------------------------
# Refusal helpers
# ---------------------------------------------------------------------------


def _refuse_provider(name: str) -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        f"cloud_drive: provider {name!r} not enabled",
        detail={
            "plugin": "cloud_drive",
            "missing_allowlist_item": name,
            "field": "providers",
            "provider": name,
        },
    )


def _refuse_read_only() -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        "cloud_drive: read_only=true blocks write",
        detail={"plugin": "cloud_drive", "missing_toggle": "read_only"},
    )


def _refuse_path(provider: str, path: str) -> SparkError:
    return SparkError(
        ErrorCode.PATH_DENIED,
        f"cloud_drive: path {path!r} outside allowed_paths for {provider}",
        detail={
            "plugin": "cloud_drive",
            "provider": provider,
            "path": path,
            "field": "allowed_paths",
        },
    )


def _refuse_file_type(ext: str) -> SparkError:
    return SparkError(
        ErrorCode.FILE_TYPE_DENIED,
        f"cloud_drive: file type {ext!r} not in file_type_allowlist",
        detail={
            "plugin": "cloud_drive",
            "extension": ext,
            "field": "file_type_allowlist",
        },
    )


# ---------------------------------------------------------------------------
# Path + file-type gates
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    """Strip leading/trailing slashes; refuse traversal."""
    if ".." in path.split("/"):
        raise SparkError(
            ErrorCode.PATH_TRAVERSAL,
            f"cloud_drive: traversal in path {path!r}",
            detail={"plugin": "cloud_drive", "path": path},
        )
    return path.strip("/")


def _path_allowed(path: str, allowed: list[str]) -> bool:
    """Return True iff ``path`` is at or below at least one allowed root."""
    p = _normalize_path(path)
    if not p:
        # Listing the root only allowed when an allowed_paths root is empty,
        # which we refuse explicitly above (caller must check non-empty).
        return False
    for root in allowed:
        r = root.strip("/")
        if not r:
            continue
        if p == r or p.startswith(r + "/"):
            return True
    return False


def _file_extension(name: str) -> str:
    """Lowercase extension without the dot, or '' for none."""
    _, ext = os.path.splitext(name)
    return ext.lstrip(".").lower()


def _check_file_type(filename: str, allowlist: list[str]) -> None:
    ext = _file_extension(filename)
    if not ext:
        raise _refuse_file_type("")
    if ext not in {e.lower().lstrip(".") for e in allowlist}:
        raise _refuse_file_type(ext)


# ---------------------------------------------------------------------------
# rclone config synthesis
# ---------------------------------------------------------------------------


_SECRET_DEFAULT = ""


def _resolve_secret(name: str, ctx: Any) -> str:
    """Look up ``name`` in the vault via ``ctx.secrets``. Empty name returns ''."""
    if not name:
        return _SECRET_DEFAULT
    secrets = getattr(ctx, "secrets", None)
    if secrets is None:
        return _SECRET_DEFAULT
    try:
        # ``SecretsAccessor.get`` is the standard interface across the codebase
        val = secrets.get(name)
    except Exception as exc:
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            f"cloud_drive: secret {name!r} not found",
            detail={"plugin": "cloud_drive", "secret_name": name},
        ) from exc
    if val is None:
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            f"cloud_drive: secret {name!r} not found",
            detail={"plugin": "cloud_drive", "secret_name": name},
        )
    return str(val)


async def _rclone_obscure(binary: str, secret: str) -> str:
    """Rclone-obscure a password (required for protondrive)."""
    if not secret:
        return ""
    proc = await asyncio.create_subprocess_exec(
        binary, "obscure", secret,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise SparkError(
            ErrorCode.PLUGIN_RAISED,
            f"cloud_drive: rclone obscure failed: {err.decode(errors='replace')[:200]}",
            detail={"plugin": "cloud_drive"},
        )
    return out.decode().strip()


async def _synthesize_rclone_config(
    providers: list[ProviderSpec],
    binary: str,
    ctx: Any,
) -> str:
    """Build a complete rclone.conf body from the providers list.

    Resolves secrets at call time so credential rotations land
    immediately. Returns the INI text — caller writes it to a temp
    file and passes ``--config <tmpfile>`` to rclone.
    """
    sections: list[str] = []
    for p in providers:
        if not p.enabled:
            continue
        auth = p.auth
        lines = [f"[{p.name}]"]
        if isinstance(auth, GoogleDriveAuth):
            lines.append("type = drive")
            token = _resolve_secret(auth.token_secret, ctx)
            if token:
                lines.append(f"token = {token}")
            if auth.client_id:
                lines.append(f"client_id = {auth.client_id}")
                if auth.client_secret_secret:
                    lines.append(
                        f"client_secret = {_resolve_secret(auth.client_secret_secret, ctx)}"
                    )
            if auth.team_drive:
                lines.append(f"team_drive = {auth.team_drive}")
                lines.append("root_folder_id =")
        elif isinstance(auth, OneDriveAuth):
            lines.append("type = onedrive")
            token = _resolve_secret(auth.token_secret, ctx)
            if token:
                lines.append(f"token = {token}")
            lines.append(f"drive_type = {auth.drive_type}")
            if auth.drive_id:
                lines.append(f"drive_id = {auth.drive_id}")
            if auth.client_id:
                lines.append(f"client_id = {auth.client_id}")
                if auth.client_secret_secret:
                    lines.append(
                        f"client_secret = {_resolve_secret(auth.client_secret_secret, ctx)}"
                    )
        elif isinstance(auth, DropboxAuth):
            lines.append("type = dropbox")
            token = _resolve_secret(auth.token_secret, ctx)
            if token:
                lines.append(f"token = {token}")
            if auth.client_id:
                lines.append(f"client_id = {auth.client_id}")
                if auth.client_secret_secret:
                    lines.append(
                        f"client_secret = {_resolve_secret(auth.client_secret_secret, ctx)}"
                    )
        elif isinstance(auth, ProtonDriveAuth):
            lines.append("type = protondrive")
            lines.append(f"username = {auth.username}")
            pw = _resolve_secret(auth.password_secret, ctx)
            if pw:
                lines.append(f"password = {await _rclone_obscure(binary, pw)}")
            if auth.twofa_secret:
                lines.append(
                    f"2fa = {_resolve_secret(auth.twofa_secret, ctx)}"
                )
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + ("\n" if sections else "")


# ---------------------------------------------------------------------------
# rclone subprocess wrapper
# ---------------------------------------------------------------------------


def _token_secret_name(auth: ProviderAuth) -> str:
    """Return the vault entry name where this provider's OAuth token
    lives (Drive / OneDrive / Dropbox) — or empty string for Proton."""
    if isinstance(auth, (GoogleDriveAuth, OneDriveAuth, DropboxAuth)):
        return auth.token_secret
    return ""


async def _persist_refreshed_tokens(
    providers: list[ProviderSpec],
    config_path: str,
    ctx: Any,
) -> None:
    """If rclone refreshed any OAuth token during the call, persist
    the new value back to the vault.

    rclone edits its config file in place when it refreshes a token
    (Drive / OneDrive / Dropbox use 1-hour access_tokens; without
    write-back, auto-share would silently break after the first hour).
    We compare the post-call ``token = `` line for each provider
    against the value we wrote pre-call (held in ``ctx.secrets``) and
    write any change back via ``SecretManager.set``.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return

    import configparser  # noqa: PLC0415

    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return

    try:
        from spark.runtime import get_secret_manager  # noqa: PLC0415

        mgr = get_secret_manager()
    except Exception:  # pragma: no cover — bootstrap path
        return

    secrets_dict = getattr(ctx, "secrets", None)
    if not isinstance(secrets_dict, dict):
        secrets_dict = None

    for p in providers:
        if not p.enabled:
            continue
        secret_name = _token_secret_name(p.auth)
        if not secret_name or p.name not in parser:
            continue
        new_token = parser[p.name].get("token", "").strip()
        if not new_token:
            continue
        old_token = (secrets_dict or {}).get(secret_name, "")
        if new_token == old_token:
            continue
        try:
            mgr.set(secret_name, new_token)
            if secrets_dict is not None:
                secrets_dict[secret_name] = new_token
        except Exception:
            # A vault-write hiccup shouldn't fail the whole rclone call;
            # the operator just has to re-paste the token if it expires.
            continue


async def _run_rclone(
    cfg: dict[str, Any],
    args: list[str],
    *,
    ctx: Any,
    timeout: float | None = None,
) -> tuple[int, str, str]:
    """Spawn rclone with a fresh, secret-resolved config file.

    After the call, scan the (possibly-mutated) config file for token
    refreshes and persist them back to the vault. Without this step
    OAuth tokens expire after their (typically 1-hour) lifetime and
    every subsequent call until the operator re-pastes a fresh token
    fails.
    """
    binary = (cfg.get("rclone_path") or "rclone").strip()
    timeout = timeout or float(cfg.get("timeout_seconds") or 60.0)

    # Pydantic-validate the providers list so we get clean refusals on
    # malformed config rather than a confusing rclone error downstream.
    try:
        providers = [ProviderSpec.model_validate(p) for p in (cfg.get("providers") or [])]
    except Exception as exc:
        raise SparkError(
            ErrorCode.OPERATOR_OVERRIDE_REFUSED,
            f"cloud_drive: invalid provider config: {exc}",
            detail={"plugin": "cloud_drive"},
        ) from exc

    config_text = await _synthesize_rclone_config(providers, binary, ctx)

    # tempfile.mkstemp gives us 0600 perms by default on POSIX.
    fd, path = tempfile.mkstemp(prefix="rclone-", suffix=".conf")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(config_text)
        cmd = [binary, "--config", path, *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise SparkError(
                ErrorCode.SANDBOX_UNAVAILABLE,
                f"cloud_drive: rclone binary not found at {binary!r}",
                detail={"plugin": "cloud_drive", "binary": binary},
            ) from exc
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:  # pragma: no cover
                pass
            raise SparkError(
                ErrorCode.SANDBOX_TIMEOUT,
                f"cloud_drive: rclone timed out after {timeout}s",
                detail={"plugin": "cloud_drive"},
            )

        # Pull refreshed tokens (if any) back into the vault before
        # the tmpfile is unlinked. ``rclone obscure`` paths don't get
        # token write-back (they don't load any provider config), so
        # the no-op branch in `_persist_refreshed_tokens` covers them.
        await _persist_refreshed_tokens(providers, path, ctx)

        return (
            proc.returncode if proc.returncode is not None else 0,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )
    finally:
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# Auto-share post-step
# ---------------------------------------------------------------------------


_DRIVE_PERMISSION_ROLE = {
    "reader": "reader",
    "writer": "writer",
    "commenter": "commenter",
}

# OneDrive doesn't have a "commenter" role — Microsoft Graph
# accepts ``read`` and ``write`` only. We fold commenter into read.
_ONEDRIVE_ROLE = {
    "reader": "read",
    "writer": "write",
    "commenter": "read",
}

# Dropbox uses ``viewer`` / ``editor`` / ``viewer_no_comment``.
_DROPBOX_ACCESS = {
    "reader": "viewer",
    "writer": "editor",
    "commenter": "viewer",  # standard viewer can comment
}


def _extract_access_token(token_str: str) -> str | None:
    """Pull ``access_token`` out of rclone's stored OAuth JSON blob."""
    if not token_str:
        return None
    try:
        token = json.loads(token_str)
    except json.JSONDecodeError:
        return None
    val = token.get("access_token")
    return str(val) if val else None


async def _rclone_lookup_file_id(
    cfg: dict[str, Any], ctx: Any, provider_name: str, remote_path: str
) -> str | None:
    """Resolve a remote path to a provider file ID via rclone."""
    rc, stdout, _ = await _run_rclone(
        cfg,
        ["lsf", f"{provider_name}:{remote_path}", "--format", "i", "--files-only"],
        ctx=ctx,
    )
    if rc != 0 or not stdout.strip():
        return None
    first = stdout.strip().splitlines()[0].strip()
    return first or None


async def _auto_share_drive(
    auth: GoogleDriveAuth,
    provider_name: str,
    remote_path: str,
    spec: AutoShareSpec,
    ctx: Any,
    cfg: dict[str, Any],
) -> list[str]:
    """Google Drive — `/drive/v3/files/{id}/permissions`."""
    access_token = _extract_access_token(_resolve_secret(auth.token_secret, ctx))
    if not access_token:
        return []
    file_id = await _rclone_lookup_file_id(cfg, ctx, provider_name, remote_path)
    if not file_id:
        return []
    role = _DRIVE_PERMISSION_ROLE.get(spec.permission, "reader")

    import httpx  # noqa: PLC0415

    granted: list[str] = []
    params = {"sendNotificationEmail": "false"}
    if auth.team_drive:
        params["supportsAllDrives"] = "true"
    async with httpx.AsyncClient(timeout=15) as client:
        for email in spec.recipients:
            try:
                r = await client.post(
                    f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                    json={"type": "user", "role": role, "emailAddress": str(email)},
                )
                if r.status_code in (200, 204):
                    granted.append(str(email))
            except httpx.HTTPError:
                continue
    return granted


async def _auto_share_onedrive(
    auth: OneDriveAuth,
    provider_name: str,
    remote_path: str,
    spec: AutoShareSpec,
    ctx: Any,
    cfg: dict[str, Any],
) -> list[str]:
    """OneDrive — Graph API ``/me/drive/items/{id}/invite`` (personal)
    or ``/drives/{drive_id}/items/{id}/invite`` (business / SharePoint)."""
    access_token = _extract_access_token(_resolve_secret(auth.token_secret, ctx))
    if not access_token:
        return []
    file_id = await _rclone_lookup_file_id(cfg, ctx, provider_name, remote_path)
    if not file_id:
        return []

    if auth.drive_type in ("business", "documentLibrary") and auth.drive_id:
        endpoint = (
            f"https://graph.microsoft.com/v1.0/drives/{auth.drive_id}/items/{file_id}/invite"
        )
    else:
        endpoint = (
            f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}/invite"
        )

    role = _ONEDRIVE_ROLE.get(spec.permission, "read")

    import httpx  # noqa: PLC0415

    granted: list[str] = []
    payload = {
        "requireSignIn": True,
        "sendInvitation": False,
        "roles": [role],
        "recipients": [{"email": str(e)} for e in spec.recipients],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
            # Graph returns 200 with a per-recipient result list on success.
            if r.status_code in (200, 201):
                granted = [str(e) for e in spec.recipients]
        except httpx.HTTPError:
            return []
    return granted


async def _auto_share_dropbox(
    auth: DropboxAuth,
    provider_name: str,
    remote_path: str,
    spec: AutoShareSpec,
    ctx: Any,
    cfg: dict[str, Any],  # noqa: ARG001 — kept for signature uniformity
) -> list[str]:
    """Dropbox — ``/2/sharing/add_file_member``. Dropbox uses paths,
    not IDs, so the rclone lookup step is skipped."""
    access_token = _extract_access_token(_resolve_secret(auth.token_secret, ctx))
    if not access_token:
        return []
    access_level = _DROPBOX_ACCESS.get(spec.permission, "viewer")
    path = "/" + remote_path.lstrip("/")

    import httpx  # noqa: PLC0415

    granted: list[str] = []
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                "https://api.dropboxapi.com/2/sharing/add_file_member",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "file": path,
                    "members": [
                        {".tag": "email", "email": str(e)} for e in spec.recipients
                    ],
                    "access_level": {".tag": access_level},
                    "quiet": True,
                },
            )
            if r.status_code in (200, 204):
                granted = [str(e) for e in spec.recipients]
        except httpx.HTTPError:
            return []
    return granted


async def _auto_share(
    provider: ProviderSpec,
    remote_path: str,
    ctx: Any,
    cfg: dict[str, Any],
) -> list[str]:
    """Dispatch to the per-provider sharing implementation. Returns
    the list of recipients that actually got the grant. Proton Drive
    returns [] (no API-driven sharing); others 401-silently on
    expired tokens (operator must re-paste)."""
    spec = provider.auto_share
    if not spec.enabled or not spec.recipients:
        return []
    auth = provider.auth
    if isinstance(auth, GoogleDriveAuth):
        return await _auto_share_drive(auth, provider.name, remote_path, spec, ctx, cfg)
    if isinstance(auth, OneDriveAuth):
        return await _auto_share_onedrive(auth, provider.name, remote_path, spec, ctx, cfg)
    if isinstance(auth, DropboxAuth):
        return await _auto_share_dropbox(auth, provider.name, remote_path, spec, ctx, cfg)
    # ProtonDriveAuth — no API-driven sharing.
    return []


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class CloudDrivePlugin:
    name: ClassVar[str] = "cloud_drive"
    version: ClassVar[str] = "0.2.0"
    description: ClassVar[str] = (
        "Google Drive / OneDrive / Dropbox / Proton Drive. Per-provider "
        "auth + path allowlists + file-type gates + optional auto-share."
    )
    input_schema: ClassVar[type[BaseModel]] = _CloudDriveArgsWrapper
    output_schema: ClassVar[type[BaseModel]] = CloudDriveResult
    config_schema: ClassVar[type[BaseModel]] = CloudDriveConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.SUBPROCESS, Permission.FS_READ, Permission.FS_WRITE}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(
        self, args: _CloudDriveArgsWrapper, ctx: Any
    ) -> CloudDriveResult:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        read_only = bool(cfg.get("read_only", True))
        max_bytes = int(cfg.get("max_file_bytes") or 52_428_800)
        file_type_allowlist = cfg.get("file_type_allowlist") or []
        scratch = getattr(ctx, "scratch_path", None)

        try:
            providers = [
                ProviderSpec.model_validate(p) for p in (cfg.get("providers") or [])
            ]
        except Exception as exc:
            raise SparkError(
                ErrorCode.OPERATOR_OVERRIDE_REFUSED,
                f"cloud_drive: invalid provider config: {exc}",
                detail={"plugin": "cloud_drive"},
            ) from exc
        provider_by_name = {p.name: p for p in providers if p.enabled}

        if args.action == "list_providers":
            return CloudDriveResult(
                action="list_providers",
                ok=True,
                providers=[
                    {
                        "name": p.name,
                        "kind": p.auth.kind,
                        "allowed_paths": list(p.allowed_paths),
                    }
                    for p in providers
                    if p.enabled
                ],
            )

        if not args.provider:
            raise SparkError(
                ErrorCode.INPUT_SCHEMA_INVALID,
                f"cloud_drive: {args.action} requires provider",
                detail={"plugin": "cloud_drive"},
            )
        prov_name = args.provider.rstrip(":")
        provider = provider_by_name.get(prov_name)
        if provider is None:
            raise _refuse_provider(prov_name)

        if not provider.allowed_paths:
            raise _refuse_path(prov_name, args.path or "")
        path = args.path or ""
        if not _path_allowed(path, provider.allowed_paths):
            raise _refuse_path(prov_name, path)
        normalized_path = _normalize_path(path)

        if args.action == "list":
            return await _do_list(cfg, ctx, prov_name, normalized_path, recursive=args.recursive)
        if args.action == "search":
            return await _do_search(cfg, ctx, prov_name, normalized_path, query=args.query)
        if args.action == "get":
            if not scratch:
                raise SparkError(
                    ErrorCode.OPERATOR_OVERRIDE_REFUSED,
                    "cloud_drive: get requires the data volume's scratch dir",
                    detail={"plugin": "cloud_drive", "field": "data_volume"},
                )
            _check_file_type(os.path.basename(normalized_path), file_type_allowlist)
            return await _do_get(
                cfg, ctx, prov_name, normalized_path,
                scratch=Path(scratch), max_bytes=max_bytes,
            )
        if args.action == "put":
            if read_only:
                raise _refuse_read_only()
            if not args.local_path:
                raise SparkError(
                    ErrorCode.INPUT_SCHEMA_INVALID,
                    "cloud_drive: put requires local_path",
                    detail={"plugin": "cloud_drive"},
                )
            _check_file_type(os.path.basename(args.local_path), file_type_allowlist)
            return await _do_put(
                cfg, ctx, provider, normalized_path,
                local_path=args.local_path,
                scratch=Path(scratch) if scratch else None,
                max_bytes=max_bytes,
            )
        if args.action == "delete":
            if read_only:
                raise _refuse_read_only()
            return await _do_delete(cfg, ctx, prov_name, normalized_path)
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            f"cloud_drive: unknown action {args.action!r}",
            detail={"plugin": "cloud_drive", "action": args.action},
        )


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


async def _do_list(
    cfg: dict[str, Any], ctx: Any, provider: str, path: str, *, recursive: bool
) -> CloudDriveResult:
    target = f"{provider}:{path}"
    args = ["lsjson", target]
    if recursive:
        args.append("-R")
    rc, stdout, stderr = await _run_rclone(cfg, args, ctx=ctx)
    if rc != 0:
        return CloudDriveResult(action="list", ok=False, error=stderr[:200])
    try:
        entries = json.loads(stdout) if stdout.strip() else []
    except json.JSONDecodeError:
        return CloudDriveResult(action="list", ok=False, error="rclone lsjson returned invalid JSON")
    files = [
        CloudFile(
            name=e.get("Name", ""),
            path=e.get("Path", ""),
            size=e.get("Size"),
            is_dir=bool(e.get("IsDir")),
            modified=e.get("ModTime"),
            mime_type=e.get("MimeType"),
        )
        for e in entries
    ]
    return CloudDriveResult(action="list", ok=True, files=files)


async def _do_search(
    cfg: dict[str, Any], ctx: Any, provider: str, path: str, *, query: str | None
) -> CloudDriveResult:
    if not query:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "cloud_drive: search requires query",
            detail={"plugin": "cloud_drive"},
        )
    target = f"{provider}:{path}"
    rc, stdout, stderr = await _run_rclone(
        cfg, ["lsjson", target, "--include", f"*{query}*", "-R"], ctx=ctx
    )
    if rc != 0:
        return CloudDriveResult(action="search", ok=False, error=stderr[:200])
    try:
        entries = json.loads(stdout) if stdout.strip() else []
    except json.JSONDecodeError:
        return CloudDriveResult(action="search", ok=False, error="rclone lsjson returned invalid JSON")
    files = [
        CloudFile(
            name=e.get("Name", ""),
            path=e.get("Path", ""),
            size=e.get("Size"),
            is_dir=bool(e.get("IsDir")),
            modified=e.get("ModTime"),
        )
        for e in entries
    ]
    return CloudDriveResult(action="search", ok=True, files=files)


async def _do_get(
    cfg: dict[str, Any], ctx: Any, provider: str, path: str,
    *, scratch: Path, max_bytes: int,
) -> CloudDriveResult:
    name = os.path.basename(path) or "download"
    dest = scratch / "cloud_drive" / provider / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = f"{provider}:{path}"
    rc, stdout, stderr = await _run_rclone(
        cfg,
        ["copyto", target, str(dest), "--max-size", str(max_bytes)],
        ctx=ctx,
    )
    if rc != 0:
        return CloudDriveResult(action="get", ok=False, error=stderr[:200])
    size = dest.stat().st_size if dest.exists() else None
    return CloudDriveResult(
        action="get",
        ok=True,
        local_path=str(dest),
        bytes_transferred=size,
    )


async def _do_put(
    cfg: dict[str, Any],
    ctx: Any,
    provider: ProviderSpec,
    path: str,
    *,
    local_path: str,
    scratch: Path | None,
    max_bytes: int,
) -> CloudDriveResult:
    src = Path(local_path).expanduser().resolve()
    if scratch and not str(src).startswith(str(scratch)) and not str(src).startswith("/data/spark"):
        raise SparkError(
            ErrorCode.PATH_DENIED,
            f"cloud_drive: local_path {str(src)!r} outside the agent's workspace",
            detail={"plugin": "cloud_drive", "path": str(src)},
        )
    if not src.exists():
        raise SparkError(
            ErrorCode.FILE_NOT_FOUND,
            f"cloud_drive: local_path {str(src)!r} not found",
            detail={"plugin": "cloud_drive", "path": str(src)},
        )
    if src.stat().st_size > max_bytes:
        raise SparkError(
            ErrorCode.FILE_TOO_LARGE,
            f"cloud_drive: {src.stat().st_size} bytes exceeds max_file_bytes ({max_bytes})",
            detail={
                "plugin": "cloud_drive",
                "size": src.stat().st_size,
                "max_bytes": max_bytes,
            },
        )
    target = f"{provider.name}:{path}"
    rc, stdout, stderr = await _run_rclone(cfg, ["copyto", str(src), target], ctx=ctx)
    if rc != 0:
        return CloudDriveResult(action="put", ok=False, error=stderr[:200])
    shared = await _auto_share(provider, path, ctx, cfg)
    return CloudDriveResult(
        action="put",
        ok=True,
        local_path=str(src),
        bytes_transferred=src.stat().st_size,
        shared_with=shared or None,
    )


async def _do_delete(
    cfg: dict[str, Any], ctx: Any, provider: str, path: str
) -> CloudDriveResult:
    target = f"{provider}:{path}"
    rc, stdout, stderr = await _run_rclone(cfg, ["deletefile", target], ctx=ctx)
    if rc != 0:
        return CloudDriveResult(action="delete", ok=False, error=stderr[:200])
    return CloudDriveResult(action="delete", ok=True)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


async def discover(cfg: dict[str, Any], ctx: Any) -> CloudDriveDiscovery:
    """Per-provider health probe. ``rclone about`` reports free/total
    bytes for providers that support it (Drive, OneDrive, Dropbox).
    Protondrive doesn't, so we just confirm reachability via ``lsd``.
    """
    binary = (cfg.get("rclone_path") or "rclone").strip()
    if shutil.which(binary) is None:
        return CloudDriveDiscovery(
            ok=False,
            error=f"rclone binary not found at {binary!r}",
            error_code=ErrorCode.SANDBOX_UNAVAILABLE.value,
            error_detail={"plugin": "cloud_drive", "binary": binary},
            rclone_available=False,
        )

    try:
        providers = [
            ProviderSpec.model_validate(p) for p in (cfg.get("providers") or [])
        ]
    except Exception as exc:
        return CloudDriveDiscovery(
            ok=False,
            error=f"invalid provider config: {exc}",
            error_code=ErrorCode.OPERATOR_OVERRIDE_REFUSED.value,
            error_detail={"plugin": "cloud_drive"},
        )

    health: list[ProviderHealth] = []
    for p in providers:
        if not p.enabled:
            health.append(
                ProviderHealth(
                    name=p.name,
                    kind=p.auth.kind,
                    enabled=False,
                    ok=False,
                    error="disabled",
                    allowed_paths=list(p.allowed_paths),
                )
            )
            continue
        # Probe via `rclone about` (preferred) or fall back to `lsd`.
        probe_args = (
            ["about", f"{p.name}:", "--json"]
            if not isinstance(p.auth, ProtonDriveAuth)
            else ["lsd", f"{p.name}:", "--max-depth", "1"]
        )
        rc, stdout, stderr = await _run_rclone(cfg, probe_args, ctx=ctx)
        if rc != 0:
            health.append(
                ProviderHealth(
                    name=p.name,
                    kind=p.auth.kind,
                    enabled=True,
                    ok=False,
                    error=stderr[:200] or "rclone returned non-zero",
                    allowed_paths=list(p.allowed_paths),
                )
            )
            continue
        free = total = None
        if not isinstance(p.auth, ProtonDriveAuth):
            try:
                info = json.loads(stdout) if stdout.strip() else {}
                free = info.get("free")
                total = info.get("total")
            except json.JSONDecodeError:
                pass
        health.append(
            ProviderHealth(
                name=p.name,
                kind=p.auth.kind,
                enabled=True,
                ok=True,
                free_bytes=free,
                total_bytes=total,
                allowed_paths=list(p.allowed_paths),
            )
        )
    return CloudDriveDiscovery(ok=True, providers=health)

"""Web UI auth — username/password primary, token fallback for headless clients.

- **Primary:** `POST /api/auth/login` with `{"username", "password"}` against
  credentials minted at startup. bcrypt-verified. Returns signed session cookie.
- **Fallback:** `x-spark-token` header for headless clients (CI, scripts,
  internal API callers). The token lives at ``~/.spark/web-token`` with mode
  0600 and is generated on first `spark serve` invocation.
- **Roles:** viewer, operator, admin. The username/password account is
  `operator` by default; the token short-circuits to `admin` for power users.

All session cookies are signed via `itsdangerous.URLSafeTimedSerializer`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer

from spark.web.credentials import (
    GeneratedCredentials,
    StoredCredentials,
    ensure_credentials,
    verify_password,
)

TOKEN_FILE = Path("~/.spark/web-token").expanduser()
DEFAULT_SESSION_TTL_SECONDS = 3600

# Cookie lifetime when session timeouts are disabled. Signed-cookie
# verification uses ``max_age=None`` (no age check), but the browser-side
# cookie still needs an explicit Max-Age so it survives browser restarts.
# Ten years is effectively "forever" for this product.
DISABLED_COOKIE_MAX_AGE_SECONDS = 10 * 365 * 24 * 3600


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


@dataclass
class Principal:
    subject: str
    role: Role


def _role_rank(role: Role) -> int:
    return {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}[role]


def ensure_token(rotate: bool = False) -> str:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if rotate or not TOKEN_FILE.exists():
        token = secrets.token_urlsafe(32)
        TOKEN_FILE.write_text(token)
        try:
            TOKEN_FILE.chmod(0o600)
        except OSError:  # pragma: no cover
            pass
        return token
    return TOKEN_FILE.read_text().strip()


class AuthState:
    def __init__(
        self,
        *,
        token: str,
        credentials: StoredCredentials,
        session_ttl_seconds: int | None = DEFAULT_SESSION_TTL_SECONDS,
        cookie_secure: bool = False,
    ) -> None:
        self.token = token
        self.credentials = credentials
        # ``None`` means timeouts are disabled — signed-cookie verification
        # skips the age check entirely. Mutable: admins can update via
        # PUT /api/settings/session without restarting.
        self.session_ttl_seconds: int | None = session_ttl_seconds
        self.cookie_secure = cookie_secure
        self._serializer = URLSafeTimedSerializer(
            secret_key=token, salt="spark-web-session"
        )

    @property
    def cookie_max_age(self) -> int:
        """Browser cookie Max-Age. Large constant when TTL is disabled."""
        if self.session_ttl_seconds is None:
            return DISABLED_COOKIE_MAX_AGE_SECONDS
        return self.session_ttl_seconds

    def set_session_ttl(self, seconds: int | None) -> None:
        """Hot-swap the session TTL. ``None`` disables age checks."""
        self.session_ttl_seconds = seconds

    def issue_session(self, subject: str, role: Role) -> str:
        return self._serializer.dumps({"sub": subject, "role": role.value})

    def verify_session(self, cookie: str) -> Principal:
        try:
            payload = self._serializer.loads(
                cookie, max_age=self.session_ttl_seconds
            )
        except BadSignature as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session"
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=401, detail="malformed session")
        return Principal(
            subject=str(payload.get("sub", "unknown")),
            role=Role(payload.get("role", "viewer")),
        )

    def verify_user_password(self, username: str, password: str) -> bool:
        if not secrets.compare_digest(username, self.credentials.username):
            # Still do a bcrypt compare to keep timing uniform.
            verify_password(password, self.credentials.password_hash)
            return False
        return verify_password(password, self.credentials.password_hash)


_auth_state: AuthState | None = None


def init_auth(
    *,
    credentials_path: Path,
    session_ttl_seconds: int | None = DEFAULT_SESSION_TTL_SECONDS,
    rotate_credentials: bool = False,
    rotate_token: bool = False,
    cookie_secure: bool = False,
) -> tuple[AuthState, GeneratedCredentials | None]:
    """Initialize the web auth singleton.

    Returns the state plus any newly-generated credentials. If fresh
    credentials were minted, the caller MUST display them once and then
    forget them. `cookie_secure=True` MUST be passed for any bind mode that
    terminates TLS or sits behind an HTTPS proxy — otherwise the session
    cookie can be captured over plaintext.
    """
    global _auth_state
    token = ensure_token(rotate=rotate_token)
    stored, fresh = ensure_credentials(path=credentials_path, rotate=rotate_credentials)
    _auth_state = AuthState(
        token=token,
        credentials=stored,
        session_ttl_seconds=session_ttl_seconds,
        cookie_secure=cookie_secure,
    )
    return _auth_state, fresh


def get_auth() -> AuthState:
    if _auth_state is None:
        raise RuntimeError("auth not initialized; call init_auth() first")
    return _auth_state


def get_principal(
    request: Request,
    x_spark_token: Annotated[str | None, Header()] = None,
) -> Principal:
    auth = get_auth()
    if x_spark_token is not None:
        if not secrets.compare_digest(x_spark_token, auth.token):
            raise HTTPException(status_code=401, detail="bad token")
        return Principal(subject="token", role=Role.ADMIN)

    cookie = request.cookies.get("spark_session")
    if not cookie:
        raise HTTPException(status_code=401, detail="not authenticated")
    return auth.verify_session(cookie)


def require_role(min_role: Role):  # noqa: ANN201 — used as dependency
    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if _role_rank(principal.role) < _role_rank(min_role):
            raise HTTPException(
                status_code=403,
                detail=f"role {principal.role.value} < {min_role.value}",
            )
        return principal
    return _dep


require_viewer = require_role(Role.VIEWER)
require_operator = require_role(Role.OPERATOR)
require_admin = require_role(Role.ADMIN)

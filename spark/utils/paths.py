"""Path resolution with allow/deny enforcement.

Every filesystem-touching operation in Spark must route through `resolve_within`
to prevent path traversal, symlink escape, and TOCTOU races. The resolver always
calls `Path.resolve()` (which follows symlinks) before comparison, so a malicious
symlink pointing outside the allowlist will be caught.

For the actual `open()` call, plugins should pass `follow_symlinks=False` / use
`O_NOFOLLOW` where possible to prevent a subsequent symlink-swap race.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from spark.errors import ErrorCode, SparkError


class PathDenied(SparkError, PermissionError):
    """Raised when a path is outside the allowlist or inside the denylist.

    Defaults to ``PATH_DENIED``; callers can pass ``PATH_TRAVERSAL`` or
    ``PATH_SYMLINK_REFUSED`` for more specific failures.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.PATH_DENIED,
        detail: dict | None = None,
    ) -> None:
        SparkError.__init__(self, code, message, detail=detail or {})


@dataclass(frozen=True)
class PathPolicy:
    """Read/write path policy for a filesystem-touching plugin."""

    allow: tuple[Path, ...]
    deny: tuple[Path, ...]

    @classmethod
    def from_strings(
        cls, allow: list[str] | None, deny: list[str] | None = None
    ) -> "PathPolicy":
        allow_paths = tuple(
            Path(p).expanduser().resolve() for p in (allow or [])
        )
        deny_paths = tuple(
            Path(p).expanduser().resolve() for p in (deny or [])
        )
        return cls(allow=allow_paths, deny=deny_paths)

    def check(self, target: Path) -> Path:
        """Resolve `target` and ensure it's inside allow and outside deny.

        Returns the resolved absolute path or raises PathDenied.
        """
        # strict=False because the target may not exist yet for a write op
        resolved = Path(target).expanduser().resolve(strict=False)

        if not self.allow:
            raise PathDenied(f"No allow paths configured; refusing access to {resolved}")

        in_allow = any(_is_within(resolved, base) for base in self.allow)
        if not in_allow:
            raise PathDenied(f"Path {resolved} is outside allow list")

        in_deny = any(_is_within(resolved, base) for base in self.deny)
        if in_deny:
            raise PathDenied(f"Path {resolved} is inside deny list")

        return resolved


def _is_within(target: Path, base: Path) -> bool:
    """True if `target` is `base` or a descendant of `base`. Compares resolved paths."""
    try:
        target.relative_to(base)
    except ValueError:
        return False
    return True


def resolve_within(target: str | Path, policy: PathPolicy) -> Path:
    """Shorthand: check a user-supplied path against a policy."""
    return policy.check(Path(target))

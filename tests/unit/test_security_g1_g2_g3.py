"""Security regression tests for the G1/G2/G3 audit fixes.

Each test pins a specific finding from the audit so a future refactor can't
silently reintroduce it. Mark each test with the audit ID in the docstring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# C1 — Sandbox refuses paths INSIDE the chroma directory
# ---------------------------------------------------------------------------


def test_c1_chroma_collision_exact() -> None:
    """C1: `candidate == chroma` is refused."""
    from spark.sandbox.policy import _chroma_collision

    chroma = Path("/tmp/spark-data/chroma")
    assert _chroma_collision(chroma, chroma) is True


def test_c1_chroma_collision_ancestor() -> None:
    """C1: an ancestor of chroma is refused (prevents 'allow the parent dir')."""
    from spark.sandbox.policy import _chroma_collision

    chroma = Path("/tmp/spark-data/chroma")
    assert _chroma_collision(chroma.parent, chroma) is True


def test_c1_chroma_collision_descendant() -> None:
    """C1: a descendant of chroma is refused (the original audit bug)."""
    from spark.sandbox.policy import _chroma_collision

    chroma = Path("/tmp/spark-data/chroma")
    assert _chroma_collision(chroma / "collection", chroma) is True
    assert _chroma_collision(chroma / "a" / "b" / "c", chroma) is True


def test_c1_chroma_collision_unrelated() -> None:
    """C1: unrelated paths are not refused."""
    from spark.sandbox.policy import _chroma_collision

    chroma = Path("/tmp/spark-data/chroma")
    assert _chroma_collision(Path("/tmp/other"), chroma) is False
    assert _chroma_collision(Path("/tmp/spark-data/scratch"), chroma) is False


# ---------------------------------------------------------------------------
# C3 — image_gen subdirectory validation
# ---------------------------------------------------------------------------


def test_c3_image_gen_subdirectory_rejects_traversal() -> None:
    """C3: subdirectory with `..` is rejected at config validation."""
    from pydantic import ValidationError

    from spark.plugins.builtins.image_gen import ImageGenConfig

    for bad in ["..", ".", "../escape", "a/b", "a\\b", ".hidden", "", "a\0b"]:
        with pytest.raises((ValidationError, ValueError)):
            ImageGenConfig(subdirectory=bad)


def test_c3_image_gen_subdirectory_accepts_safe() -> None:
    """C3: safe subdirectory values are accepted."""
    from spark.plugins.builtins.image_gen import ImageGenConfig

    cfg = ImageGenConfig(subdirectory="generated")
    assert cfg.subdirectory == "generated"
    cfg = ImageGenConfig(subdirectory="sub-dir_01")
    assert cfg.subdirectory == "sub-dir_01"


# ---------------------------------------------------------------------------
# C4 — CSV injection guard
# ---------------------------------------------------------------------------


def test_c4_csv_formula_neutralization() -> None:
    """C4: leading `=`, `+`, `-`, `@`, tab, CR are prefixed with a single quote."""
    from spark.plugins.builtins.csv_io import _neutralize_formula

    # Neutralized
    assert _neutralize_formula("=SUM(1,2)") == "'=SUM(1,2)"
    assert _neutralize_formula("+cmd") == "'+cmd"
    assert _neutralize_formula("-formula") == "'-formula"
    assert _neutralize_formula("@sum") == "'@sum"
    assert _neutralize_formula("\tfoo") == "'\tfoo"
    assert _neutralize_formula("\rfoo") == "'\rfoo"

    # Left alone
    assert _neutralize_formula("") == ""
    assert _neutralize_formula("normal") == "normal"
    assert _neutralize_formula("Hello, =world") == "Hello, =world"


# ---------------------------------------------------------------------------
# H1 — NotificationService action_url sanitization
# ---------------------------------------------------------------------------


def test_h1_action_url_accepts_relative() -> None:
    """H1: relative paths starting with `/` are accepted."""
    from spark.notifications.service import _sanitize_action_url

    assert _sanitize_action_url("/skills") == "/skills"
    assert _sanitize_action_url("/downloads/a.png") == "/downloads/a.png"
    assert _sanitize_action_url("/scheduler?focus=abc") == "/scheduler?focus=abc"


def test_h1_action_url_rejects_dangerous() -> None:
    """H1: `javascript:`, absolute URLs, protocol-relative, CRLF are refused."""
    from spark.notifications.service import _sanitize_action_url

    for bad in (
        "javascript:alert(1)",
        "http://evil.com",
        "https://evil.com",
        "//evil.com",
        "data:text/html,<script>...",
        "file:///etc/passwd",
        "/\r\nInjected",
        "/\nSecond-line",
        "/with\x00nul",
        "/with\\back",
        "",
        "   ",
    ):
        assert _sanitize_action_url(bad) is None, f"accepted {bad!r}"


def test_h1_action_url_none_passthrough() -> None:
    """H1: None stays None."""
    from spark.notifications.service import _sanitize_action_url

    assert _sanitize_action_url(None) is None


# ---------------------------------------------------------------------------
# H2 — email_sender SMTP host blocklist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_host",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "0.0.0.0",
        "::1",
    ],
)
def test_h2_reject_internal_smtp_hosts(bad_host: str) -> None:
    """H2: operator-configured internal SMTP hosts are refused."""
    from spark.plugins.builtins.email_sender import _reject_internal_smtp_host

    with pytest.raises(PermissionError):
        _reject_internal_smtp_host(bad_host)


# ---------------------------------------------------------------------------
# H4 — CRLF rejection helpers
# ---------------------------------------------------------------------------


def test_h4_has_crlf_detects_both() -> None:
    from spark.plugins.builtins.email_sender import _has_crlf

    assert _has_crlf("hi\nthere") is True
    assert _has_crlf("hi\r") is True
    assert _has_crlf("safe") is False


# ---------------------------------------------------------------------------
# H6 — pdf_reader page range parses cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (None, (0, 100)),
        ("1-10", (0, 10)),
        ("3", (2, 3)),
        ("5-", (4, 100)),
        ("-20", (0, 20)),
    ],
)
def test_h6_pdf_page_range_accepts_valid(spec: str | None, expected: tuple[int, int]) -> None:
    from spark.plugins.builtins.pdf_reader import _parse_page_range

    assert _parse_page_range(spec, 100) == expected


@pytest.mark.parametrize("bad", ["abc", "abc-def", "0", "1-2-3", "--", "xyz-5"])
def test_h6_pdf_page_range_raises_permission_error(bad: str) -> None:
    """H6: garbage input raises PermissionError (not ValueError)."""
    from spark.plugins.builtins.pdf_reader import _parse_page_range

    with pytest.raises(PermissionError):
        _parse_page_range(bad, 100)


# ---------------------------------------------------------------------------
# M1 — git ref regex tightened
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ok_ref",
    ["main", "master", "feature/x", "v1.2.3", "abc1234", "release/2026-04"],
)
def test_m1_git_safe_ref_accepts_valid(ok_ref: str) -> None:
    from spark.plugins.builtins.git import _REJECTED_REF_SUBSTRINGS, _SAFE_REF

    assert _SAFE_REF.match(ok_ref) is not None
    assert not any(s in ok_ref for s in _REJECTED_REF_SUBSTRINGS)


@pytest.mark.parametrize(
    "bad_ref",
    [
        "-flag",
        "..",
        "abc..def",
        "HEAD@{1}",
        "a//b",
        "a\\b",
    ],
)
def test_m1_git_safe_ref_rejects_dangerous(bad_ref: str) -> None:
    from spark.plugins.builtins.git import _REJECTED_REF_SUBSTRINGS, _SAFE_REF

    has_regex_match = bool(_SAFE_REF.match(bad_ref))
    has_bad_substr = any(s in bad_ref for s in _REJECTED_REF_SUBSTRINGS)
    assert not has_regex_match or has_bad_substr


# ---------------------------------------------------------------------------
# DataVolumeConfig validators (from G1)
# ---------------------------------------------------------------------------


def test_data_volume_rejects_slashes_in_subdir() -> None:
    from pydantic import ValidationError

    from spark.config.runtime_config import DataVolumeConfig

    for bad in ["a/b", "a\\b", "../escape", ".hidden", ".", ".."]:
        with pytest.raises((ValidationError, ValueError)):
            DataVolumeConfig(chroma_subdir=bad)


def test_data_volume_rejects_colliding_subdirs() -> None:
    from pydantic import ValidationError

    from spark.config.runtime_config import DataVolumeConfig

    with pytest.raises((ValidationError, ValueError)):
        DataVolumeConfig(
            chroma_subdir="same",
            scratch_subdir="same",
            deliverables_subdir="diff",
        )


def test_data_volume_paths_are_distinct() -> None:
    from spark.config.runtime_config import DataVolumeConfig

    dv = DataVolumeConfig()
    paths = {dv.chroma_path, dv.scratch_path, dv.deliverables_path}
    assert len(paths) == 3

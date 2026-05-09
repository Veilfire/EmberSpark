"""Tests for the tier-1 / tier-2 consensus fusion layer.

``fuse_hits`` is the step between raw detector output and policy
enforcement. It collapses overlapping hits of the same class into one
canonical hit, preserves span provenance, and boosts confidence when
multiple detection tiers agree.
"""

from __future__ import annotations

from spark.config.enums import DataClass
from spark.privacy.classifiers import DetectorHit
from spark.privacy.guardrails import fuse_hits


def _tier1(cls: DataClass, start: int, end: int, conf: float = 0.9, rule: str = "r1") -> DetectorHit:
    return DetectorHit(
        data_class=cls,
        start=start,
        end=end,
        confidence=conf,
        rule_id=rule,
        tier="tier1",
    )


def _tier2(cls: DataClass, start: int, end: int, conf: float = 0.7, rule: str = "presidio:X") -> DetectorHit:
    return DetectorHit(
        data_class=cls,
        start=start,
        end=end,
        confidence=conf,
        rule_id=rule,
        tier="tier2",
    )


def test_empty_input() -> None:
    assert fuse_hits([]) == []


def test_single_tier1_passes_through() -> None:
    h = _tier1(DataClass.FINANCIAL_CARD, 5, 20)
    fused = fuse_hits([h])
    assert len(fused) == 1
    assert fused[0].tier == "tier1"
    assert fused[0].confidence == 0.9


def test_single_tier2_passes_through() -> None:
    h = _tier2(DataClass.PII_NAME, 5, 20)
    fused = fuse_hits([h])
    assert len(fused) == 1
    assert fused[0].tier == "tier2"


def test_overlap_same_class_tiers_agree_boosts_confidence() -> None:
    t1 = _tier1(DataClass.FINANCIAL_CARD, 5, 20, conf=0.85, rule="luhn")
    t2 = _tier2(DataClass.FINANCIAL_CARD, 5, 20, conf=0.75, rule="presidio:CREDIT_CARD")
    fused = fuse_hits([t1, t2])
    assert len(fused) == 1
    assert fused[0].tier == "consensus"
    assert fused[0].confidence == 0.95  # 0.85 + 0.10
    assert "luhn" in fused[0].rule_id
    assert "presidio" in fused[0].rule_id


def test_overlap_prefers_tier1_span_as_canonical() -> None:
    """Tier-1 detectors have tighter, checksummed spans; keep that span."""
    t1 = _tier1(DataClass.FINANCIAL_CARD, 10, 26, conf=0.9, rule="luhn")
    # Presidio returned a slightly wider span including a leading word.
    t2 = _tier2(DataClass.FINANCIAL_CARD, 5, 27, conf=0.8, rule="presidio:CREDIT_CARD")
    fused = fuse_hits([t1, t2])
    assert len(fused) == 1
    assert fused[0].start == 10
    assert fused[0].end == 26


def test_different_classes_not_fused() -> None:
    cc = _tier1(DataClass.FINANCIAL_CARD, 5, 20)
    # Same span but different class → these are independent facts.
    ssn = _tier1(DataClass.PII_GOV_ID, 5, 20)
    fused = fuse_hits([cc, ssn])
    assert len(fused) == 2
    classes = {h.data_class for h in fused}
    assert classes == {DataClass.FINANCIAL_CARD, DataClass.PII_GOV_ID}


def test_non_overlapping_same_class_stay_separate() -> None:
    first = _tier1(DataClass.FINANCIAL_CARD, 0, 16)
    second = _tier1(DataClass.FINANCIAL_CARD, 30, 46)
    fused = fuse_hits([first, second])
    assert len(fused) == 2


def test_transitive_overlap_merges_into_one_cluster() -> None:
    """A chain of overlapping hits collapses to a single fused hit."""
    a = _tier1(DataClass.FINANCIAL_CARD, 0, 12, conf=0.8)
    b = _tier2(DataClass.FINANCIAL_CARD, 8, 22, conf=0.7)
    c = _tier2(DataClass.FINANCIAL_CARD, 18, 30, conf=0.6)
    fused = fuse_hits([a, b, c])
    assert len(fused) == 1
    # Canonical = tier1 (a); consensus because at least one tier2 also hit.
    assert fused[0].tier == "consensus"
    assert fused[0].start == 0
    assert fused[0].end == 12


def test_confidence_capped_at_one() -> None:
    t1 = _tier1(DataClass.FINANCIAL_CARD, 0, 16, conf=0.95)
    t2 = _tier2(DataClass.FINANCIAL_CARD, 0, 16, conf=0.90)
    fused = fuse_hits([t1, t2])
    assert fused[0].confidence == 1.0


def test_tier2_only_cluster_keeps_tier2() -> None:
    t2a = _tier2(DataClass.PII_BASIC, 5, 20, conf=0.7)
    t2b = _tier2(DataClass.PII_BASIC, 5, 20, conf=0.65)
    fused = fuse_hits([t2a, t2b])
    assert len(fused) == 1
    # No tier1 in the cluster → no consensus label.
    assert fused[0].tier == "tier2"
    # Higher-confidence tier2 hit wins as canonical.
    assert fused[0].confidence == 0.7

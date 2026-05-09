"""Doctor helpers — prewarm costly subsystems so first run is fast."""

from __future__ import annotations


def prewarm_presidio() -> bool:
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine

        AnalyzerEngine()
        AnonymizerEngine()
        return True
    except Exception:  # pragma: no cover
        return False


def prewarm_embeddings(model: str = "BAAI/bge-small-en-v1.5") -> bool:
    try:
        from sentence_transformers import SentenceTransformer

        SentenceTransformer(model)
        return True
    except Exception:  # pragma: no cover
        return False

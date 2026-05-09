"""Pydantic schemas for reflection output and memory candidates."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from spark.config.enums import MemoryType, RetentionClass, Sensitivity


class MemoryCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    canonical_text: str = Field(min_length=1, max_length=8000)
    memory_type: MemoryType = MemoryType.LESSON
    sensitivity: Sensitivity = Sensitivity.LOW
    retention_class: RetentionClass = RetentionClass.REVIEW
    # Confidence is in [0, 1]. We enforce that with a Python validator
    # rather than ``Field(ge=0, le=1)`` because that JSON-Schema form
    # (``{"type": "number", "minimum": 0, "maximum": 1}``) is rejected
    # by Amazon Bedrock's tool-calling subset — it accepts only
    # ``type``, ``description``, ``enum``, ``items``, ``properties``,
    # ``required`` on ``number`` types. Same reasoning applies to any
    # schema we send through ``with_structured_output``.
    confidence: float = 0.5
    tags: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v


class ReflectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    summary: str = Field(max_length=4000)
    failures: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidatePayload] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)

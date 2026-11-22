"""Pydantic request/response models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import config


class LoadRequest(BaseModel):
    url: str = Field(min_length=8)


class ExtractRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    start_s: float = Field(ge=0)
    end_s: float
    overwrite: bool = False

    @field_validator("end_s")
    @classmethod
    def check_range(cls, end_s, info):
        start_s = info.data.get("start_s")
        if start_s is None:
            return end_s
        dur = end_s - start_s
        if dur < config.MIN_CLIP_S:
            raise ValueError(f"Clip must be at least {config.MIN_CLIP_S}s (got {dur:.2f}s)")
        if dur > config.MAX_CLIP_S:
            raise ValueError(f"Clip must be at most {config.MAX_CLIP_S}s (got {dur:.2f}s)")
        return end_s


class AnnotationIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    emotion: str
    valence: int | None = Field(default=None, ge=config.VALENCE_SCALE["min"], le=config.VALENCE_SCALE["max"])
    arousal: int | None = Field(default=None, ge=config.AROUSAL_SCALE["min"], le=config.AROUSAL_SCALE["max"])
    gender: str
    transcript: str = ""
    actor: str = ""
    notes: str = ""
    annotator_id: str = Field(min_length=1)
    confidence: int | None = Field(default=None, strict=True, ge=1, le=3)
    quality: Literal["A", "B", "C"] | None = None
    ambiguous: int | None = Field(default=None, strict=True, ge=0, le=1)
    verified: bool | None = None

    @field_validator("emotion")
    @classmethod
    def check_emotion(cls, v):
        if v not in config.EMOTIONS:
            raise ValueError(f"emotion must be one of {config.EMOTIONS}")
        return v

    @field_validator("gender")
    @classmethod
    def check_gender(cls, v):
        if v not in config.GENDER_OPTIONS:
            raise ValueError(f"gender must be one of {config.GENDER_OPTIONS}")
        return v


class ManualCandidateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False, extra="forbid")
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    start_s: float = Field(ge=0, strict=True)
    end_s: float = Field(gt=0, strict=True)
    source_version: str = Field(min_length=1)
    annotator_id: str = Field(min_length=1)
    active_seconds: float = Field(default=0, ge=0, strict=True)
    blind: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_s <= self.start_s:
            raise ValueError("끝 시각은 시작 시각보다 커야 합니다.")
        return self


class ReviewDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False, extra="forbid")

    status: Literal["accepted", "accepted_edited", "hold", "rejected"]
    revision: int = Field(ge=0, strict=True)
    source_version: str | None = Field(default=None, min_length=1)
    start_s: float = Field(ge=0, strict=True)
    end_s: float = Field(gt=0, strict=True)
    annotator_id: str = Field(min_length=1)
    active_seconds: float = Field(ge=0, strict=True)
    reason: str = ""
    transcript: str = ""
    emotion: Literal["neutral", "angry", "sad", "happy"] | None = None
    gender: Literal["male", "female", "unknown"] = "unknown"
    actor: str = ""
    notes: str = ""
    confidence: int | None = Field(default=None, strict=True, ge=1, le=3)
    quality: Literal["A", "B", "C"] | None = None
    ambiguous: int | None = Field(default=None, strict=True, ge=0, le=1)
    verified: bool = Field(default=False, strict=True)
    blind: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.end_s <= self.start_s:
            raise ValueError("끝 시각은 시작 시각보다 커야 합니다.")
        if self.status in {"hold", "rejected"}:
            if not self.reason:
                raise ValueError("보류·제외 사유를 입력하세요.")
        elif (not self.verified or not self.transcript or self.emotion is None or
              self.confidence is None or self.quality is None or self.ambiguous is None):
            raise ValueError("채택하려면 발화와 화자를 확인하고 대사, 감정, CONF, QUALITY, AMBIG를 입력하세요.")
        return self

"""Dependency-free probability mapping and versioned fusion contracts."""
import math

LABELS = ("neutral", "angry", "sad", "happy", "other")
MAP_VERSION = "common4-other-v1"
SCORE_KIND = "softmax"
MEAN_MODEL = "mean-score"
TEXT_MODEL = "LimYeri/HowRU-KoELECTRA-Emotion-Classifier"
AUDIO_MODEL = "emotion2vec/emotion2vec_plus_base"
TEXT_NAMES = {
    "평범함": "neutral", "neutral": "neutral", "분노": "angry", "anger": "angry",
    "슬픔": "sad", "sadness": "sad", "기쁨": "happy", "joy": "happy",
    "sad": "sad", "happy": "happy", "angry": "angry",
    "설렘": "excitement", "excitement": "excitement", "놀라움": "surprise", "surprise": "surprise",
    "불쾌함": "disgust", "disgust": "disgust", "두려움": "fear", "fear": "fear",
}
TEXT_CLASSES = {"neutral", "angry", "sad", "happy", "excitement", "surprise", "disgust", "fear"}
AUDIO_NAMES = {"<unk>": "unknown"}
AUDIO_CLASSES = {"angry", "disgusted", "fearful", "happy", "neutral", "other", "sad", "surprised", "unknown"}


def distribution(value, keys=None) -> dict[str, float]:
    if (not isinstance(value, dict) or not value or (keys is not None and set(value) != set(keys))
            or any(not isinstance(k, str) or not k.strip() for k in value)
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                   for v in value.values())
            or not math.isclose(math.fsum(value.values()), 1.0, rel_tol=0, abs_tol=1e-5)):
        raise ValueError("Expected a complete probability distribution")
    return dict(value)


def decision(probabilities: dict) -> tuple[str | None, str | None]:
    probabilities = distribution(probabilities, LABELS)
    top = max(probabilities.values())
    winners = [k for k, v in probabilities.items() if math.isclose(v, top, rel_tol=0, abs_tol=1e-8)]
    if len(winners) != 1:
        return None, "tied_scores"
    if winners[0] == "other":
        return None, "unsupported_emotion"
    return winners[0], None


def mapped_scores(kind: str, native: dict) -> dict[str, float]:
    if kind not in ("text", "audio"):
        raise ValueError("Unknown modality")
    native = distribution(native)
    mapped = dict.fromkeys(LABELS, 0.0)
    seen = set()
    for name, value in native.items():
        folded = name.strip().lower()
        if kind == "text":
            canonical = TEXT_NAMES.get(folded)
        else:
            tail = folded.rsplit("/", 1)[-1]
            canonical = AUDIO_NAMES.get(tail, tail)
        expected = TEXT_CLASSES if kind == "text" else AUDIO_CLASSES
        if canonical not in expected or canonical in seen:
            raise ValueError("Unknown or duplicate native class")
        seen.add(canonical)
        mapped[canonical if canonical in LABELS else "other"] += value
    if seen != (TEXT_CLASSES if kind == "text" else AUDIO_CLASSES):
        raise ValueError("Missing native classes")
    return distribution(mapped, LABELS)


def scored_reply(kind: str, native: dict, revision: str) -> dict:
    probabilities = mapped_scores(kind, native)
    label, reason = decision(probabilities)
    return {"label": label or "abstain", "reason": reason, "scores": dict(native),
            "probabilities": probabilities, "score_kind": SCORE_KIND,
            "label_map_version": MAP_VERSION, "model_revision": revision}


def validate_scored(record: dict, kind: str) -> dict[str, float]:
    if (record.get("score_kind") != SCORE_KIND or record.get("label_map_version") != MAP_VERSION
            or not isinstance(record.get("model_revision"), str) or not record["model_revision"].strip()):
        raise ValueError("Missing probability provenance")
    if record.get("version") is not None and record["version"] != record["model_revision"]:
        raise ValueError("Model revision mismatch")
    probabilities = distribution(record.get("probabilities"), LABELS)
    mapped = mapped_scores(kind, record.get("scores"))
    if any(not math.isclose(probabilities[k], mapped[k], rel_tol=0, abs_tol=1e-8) for k in LABELS):
        raise ValueError("Probability mapping mismatch")
    label, reason = decision(probabilities)
    if record.get("label") not in ({None, "abstain"} if label is None else {label}):
        raise ValueError("Probability decision mismatch")
    if record.get("status") is not None and record["status"] != ("ok" if label else "abstained"):
        raise ValueError("Probability status mismatch")
    if record.get("reason") != reason:
        raise ValueError("Probability reason mismatch")
    return probabilities


def mean_fusion(text: dict, audio: dict) -> dict:
    result = {"model_id": MEAN_MODEL, "version": "1", "weights": {"text": 0.5, "audio": 0.5},
              "label_map_version": MAP_VERSION, "score_kind": "mean_softmax",
              "status": "abstained", "label": None, "reason": "missing_valid_scores",
              "scores": None, "probabilities": None}
    if any(r.get("status") == "failed" for r in (text, audio)):
        return result
    try:
        left, right = validate_scored(text, "text"), validate_scored(audio, "audio")
    except (ValueError, TypeError, KeyError):
        return result
    probabilities = {k: (left[k] + right[k]) / 2 for k in LABELS}
    label, reason = decision(probabilities)
    return dict(result, status="ok" if label else "abstained", label=label, reason=reason,
                scores=probabilities, probabilities=probabilities)


def validate_combined(records: dict) -> str:
    """Validate saved fusion without consulting the current environment settings."""
    text, audio, combined = (records[k] for k in ("text", "audio", "combined"))
    if combined.get("model_id") == MEAN_MODEL:
        expected = mean_fusion(text, audio)
        if any(combined.get(k) != v for k, v in expected.items()):
            raise ValueError("Stored mean-score result violates mean-v1")
        return "mean_score"
    if combined.get("model_id") not in (None, "agreement") or combined.get("version") not in (None, "1"):
        raise ValueError("Unknown fusion version")
    agreed = text.get("status") == audio.get("status") == "ok" and text.get("label") == audio.get("label")
    if (combined.get("label") != (text["label"] if agreed else None)
            or combined.get("status") != ("ok" if agreed else "abstained")
            or combined.get("probabilities") is not None):
        raise ValueError("Stored agreement result violates agreement-v1")
    return "agreement"

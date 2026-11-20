"""Select review candidates from whole-video ASR and Silero timestamps."""

from . import config, store


def select(video_id: str, duration: float, segments: list[dict],
           speech: list[tuple[float, float]]) -> list[dict]:
    """Keep up to three distinct speech-backed ASR segments on the source timeline."""
    by_id = {}
    for segment in segments:
        text = segment["text"].strip()
        if not text or segment["no_speech_prob"] > config.ASR_NO_SPEECH_THRESHOLD:
            continue
        start = max(0.0, segment["start"])
        end = min(duration, segment["end"])
        overlaps = [(max(start, left), min(end, right)) for left, right in speech
                    if max(start, left) < min(end, right)]
        if not overlaps:
            continue

        # The transcript covers the full ASR segment. VAD only confirms speech.
        start_ms = round(start * 1000)
        end_ms = round(end * 1000)
        length = end_ms - start_ms
        if not config.MIN_CLIP_S * 1000 <= length <= config.MAX_CLIP_S * 1000:
            continue

        flags = []
        if length < 3000:
            flags.append("short_utterance")
        elif length > 12000:
            flags.append("long_utterance")
        if len(overlaps) > 1:
            flags.append("multiple_speech_intervals")
        clip_id = store.make_clip_id(video_id, start_ms / 1000, end_ms / 1000)
        if clip_id not in by_id:
            by_id[clip_id] = {
                "video_id": video_id, "clip_id": clip_id, "start_s": start_ms / 1000,
                "end_s": end_ms / 1000, "duration_ms": length, "transcript": text,
                "boundary_reason": "asr_segment_with_vad_speech",
                "quality_flags": flags, "speaker_status": "unverified", "status": "pending",
            }

    ranked = sorted(by_id.values(), key=lambda clip: (-clip["duration_ms"], clip["start_s"]))
    return ranked[:3]

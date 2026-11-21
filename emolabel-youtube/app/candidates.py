"""Select review candidates from whole-video ASR and Silero timestamps."""

from . import config, store

SELECTION_VERSION = 2
MIN_SPEECH_RATIO = 0.5
MAX_EDGE_GAP_S = 1.5


def _rank(clip):
    return (
        clip["speech_ratio"] < MIN_SPEECH_RATIO or clip["edge_gap_ms"] > MAX_EDGE_GAP_S * 1000,
        not 3000 <= clip["duration_ms"] <= 12000,
        -clip["speech_ratio"], clip["edge_gap_ms"], -clip["duration_ms"], clip["start_s"],
    )


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

        # Preserve transcript boundaries. Score the portion actually covered by speech.
        start_ms = round(start * 1000)
        end_ms = round(end * 1000)
        length = end_ms - start_ms
        if not config.MIN_CLIP_S * 1000 <= length <= config.MAX_CLIP_S * 1000:
            continue

        flags = []
        intervals = []
        for left, right in sorted(overlaps):
            if intervals and left <= intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], max(right, intervals[-1][1]))
            else:
                intervals.append((left, right))
        ratio = round(sum(right - left for left, right in intervals) / (end - start), 4)
        gap_ms = round(1000 * max(intervals[0][0] - start, end - intervals[-1][1]))
        if ratio < MIN_SPEECH_RATIO:
            flags.append("low_speech_ratio")
        if gap_ms > MAX_EDGE_GAP_S * 1000:
            flags.append("long_edge_silence")
        if length < 3000:
            flags.append("short_utterance")
        elif length > 12000:
            flags.append("long_utterance")
        if len(intervals) > 1:
            flags.append("multiple_speech_intervals")
        clip_id = store.make_clip_id(video_id, start_ms / 1000, end_ms / 1000)
        if clip_id not in by_id:
            by_id[clip_id] = {
                "video_id": video_id, "clip_id": clip_id, "start_s": start_ms / 1000,
                "end_s": end_ms / 1000, "duration_ms": length, "transcript": text,
                "boundary_reason": "asr_segment_with_vad_speech",
                "speech_ratio": ratio, "edge_gap_ms": gap_ms,
                "quality_flags": flags, "speaker_status": "unverified", "status": "pending",
            }

    ranked = sorted(by_id.values(), key=_rank)
    return ranked[:3]

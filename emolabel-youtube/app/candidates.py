"""Select review candidates from whole-video ASR and Silero timestamps."""

import math

from . import config, store

SELECTION_VERSION = 3
MIN_SPEECH_RATIO = 0.5
MAX_EDGE_GAP_S = 1.5
MAX_BOUNDARY_EXTENSION_S = 0.5
BOUNDARY_PADDING_S = 0.08
BOUNDARY_EPSILON_S = 0.001
BOUNDARY_REVIEW_FLAGS = ("speech_continues_before", "speech_continues_after")


def _speech_intervals(speech: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Use merged source-bounded VAD intervals for both detection and scoring."""
    merged = []
    for left, right in sorted(speech):
        left, right = max(0.0, left), min(duration, right)
        if left >= right:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return merged


def _other_asr_segments(segment: dict, segments: list[dict]) -> list[dict]:
    original = (segment["start"], segment["end"], segment["text"])
    return [
        other for other in segments
        if other.get("text", "").strip()
        and (other["start"], other["end"], other["text"]) != original
    ]


def _overlaps_other_asr(others: list[dict], left: float, right: float) -> bool:
    return any(
        min(right, other["end"]) - max(left, other["start"]) > BOUNDARY_EPSILON_S
        for other in others
    )


def _extend_boundaries(segment: dict, segments: list[dict], speech: list[tuple[float, float]],
                       duration: float) -> tuple[float, float, list[str]]:
    """Extend short tails only; never incorporate a neighboring ASR utterance."""
    start, end = max(0.0, segment["start"]), min(duration, segment["end"])
    original_start, original_end = start, end
    others = _other_asr_segments(segment, segments)
    flags = []
    for side, point in (("start", original_start), ("end", original_end)):
        interval = next(((left, right) for left, right in speech
                         if left + BOUNDARY_EPSILON_S < point < right - BOUNDARY_EPSILON_S), None)
        if interval is None:
            continue
        left, right = interval
        delta = point - left if side == "start" else right - point
        if side == "start":
            target = max(0.0, left - BOUNDARY_PADDING_S)
            previous_ends = [b for a, b in speech if b <= left]
            if previous_ends:
                target = max(target, max(previous_ends))
            previous_asr_ends = [other["end"] for other in others if other["end"] <= left]
            if previous_asr_ends:
                target = max(target, max(previous_asr_ends))
            if end - left <= config.MAX_CLIP_S:
                target = max(target, end - config.MAX_CLIP_S)
            extra_left, extra_right, length = target, start, end - target
        else:
            target = min(duration, right + BOUNDARY_PADDING_S)
            following_starts = [a for a, b in speech if a >= right]
            if following_starts:
                target = min(target, min(following_starts))
            following_asr_starts = [other["start"] for other in others if other["start"] >= right]
            if following_asr_starts:
                target = min(target, min(following_asr_starts))
            if right - start <= config.MAX_CLIP_S:
                target = min(target, start + config.MAX_CLIP_S)
            extra_left, extra_right, length = end, target, target - start
        overlaps_asr = _overlaps_other_asr(others, extra_left, extra_right)
        if delta > MAX_BOUNDARY_EXTENSION_S or overlaps_asr or length > config.MAX_CLIP_S:
            flags.append("speech_continues_before" if side == "start" else "speech_continues_after")
        elif side == "start":
            start = target
        else:
            end = target
    if (start, end) != (original_start, original_end):
        flags.append("boundary_adjusted_check_transcript")
    return start, end, flags


def _rank(clip):
    return (
        clip["speech_ratio"] < MIN_SPEECH_RATIO or clip["edge_gap_ms"] > MAX_EDGE_GAP_S * 1000,
        any(flag in clip["quality_flags"] for flag in BOUNDARY_REVIEW_FLAGS),
        not 3000 <= clip["duration_ms"] <= 12000,
        -clip["speech_ratio"], clip["edge_gap_ms"], -clip["duration_ms"], clip["start_s"],
    )


def select(video_id: str, duration: float, segments: list[dict],
           speech: list[tuple[float, float]]) -> list[dict]:
    """Keep up to three speech-backed candidates with conservative boundary checks."""
    by_id = {}
    speech = _speech_intervals(speech, duration)
    for segment in segments:
        text = segment["text"].strip()
        if not text or segment["no_speech_prob"] > config.ASR_NO_SPEECH_THRESHOLD:
            continue
        start = max(0.0, segment["start"])
        end = min(duration, segment["end"])
        if not config.MIN_CLIP_S <= end - start <= config.MAX_CLIP_S:
            continue
        asr_start, asr_end = start, end
        start, end, flags = _extend_boundaries(segment, segments, speech, duration)
        overlaps = [(max(start, left), min(end, right)) for left, right in speech
                    if max(start, left) < min(end, right)]
        if not overlaps:
            continue

        # Preserve the original ASR provenance when a small edge is extended.
        start_ms = round(start * 1000)
        end_ms = min(round(end * 1000), math.floor(duration * 1000 + 1e-9))
        length = end_ms - start_ms
        if not config.MIN_CLIP_S * 1000 <= length <= config.MAX_CLIP_S * 1000:
            continue

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
        boundary_reason = "asr_segment_with_vad_speech"
        if (asr_start, asr_end) != (start, end):
            boundary_reason = "asr_segment_with_vad_boundary_extension"
        elif any(flag in flags for flag in BOUNDARY_REVIEW_FLAGS):
            boundary_reason = "asr_boundary_requires_review"
        if clip_id not in by_id:
            by_id[clip_id] = {
                "video_id": video_id, "clip_id": clip_id, "start_s": start_ms / 1000,
                "end_s": end_ms / 1000, "duration_ms": length, "transcript": text,
                "asr_start_s": asr_start, "asr_end_s": asr_end,
                "source_asr_clip_id": store.make_clip_id(video_id, asr_start, asr_end),
                "boundary_adjustment_ms": {"start": start_ms - round(asr_start * 1000),
                                           "end": end_ms - round(asr_end * 1000)},
                "boundary_reason": boundary_reason,
                "speech_ratio": ratio, "edge_gap_ms": gap_ms,
                "quality_flags": flags, "speaker_status": "unverified", "status": "pending",
            }

    ranked = sorted(by_id.values(), key=_rank)
    return ranked[:3]

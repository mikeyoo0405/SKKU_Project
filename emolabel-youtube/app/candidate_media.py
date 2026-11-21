"""Extract candidate media under the caller's per-video workspace lock."""

import json
from pathlib import Path

from . import media, store


def clip_directory(folder: Path, clip_id: str) -> Path:
    segment = store.parse_clip_id(clip_id)
    if (segment["video_id"] != folder.name or segment["end_s"] <= segment["start_s"]
            or store.make_clip_id(**segment) != clip_id):
        raise ValueError(f"Invalid candidate ID for this video: {clip_id!r}")
    return folder / "clips" / clip_id


def _source_stamp(source: Path) -> list[int]:
    stat = source.stat()
    return [stat.st_size, stat.st_mtime_ns]


def _media_sizes(directory: Path) -> list[int]:
    return [(directory / name).stat().st_size for name in ("clip.mp4", "audio_16k.wav")]


def media_ready(folder: Path, clip_id: str, source: Path) -> bool:
    directory = clip_directory(folder, clip_id)
    try:
        saved = json.loads((directory / "media.json").read_text(encoding="utf-8"))
        sizes = _media_sizes(directory)
        return all(sizes) and saved == {"source_stamp": _source_stamp(source), "sizes": sizes}
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def ensure_media(folder: Path, clip_id: str, source: Path) -> Path:
    """Commit the matching MP4 and WAV together, with media.json as the completion marker."""
    directory = clip_directory(folder, clip_id)
    if media_ready(folder, clip_id, source):
        return directory
    segment = store.parse_clip_id(clip_id)
    start = segment["start_s"]
    duration = segment["end_s"] - start
    directory.mkdir(parents=True, exist_ok=True)
    video = directory / "clip.part.mp4"
    audio = directory / "audio.part.wav"
    marker = directory / "media.json"
    staged_marker = directory / "media.part.json"
    stamp = _source_stamp(source)
    try:
        media.cut_clip(source, video, start, duration)
        media.extract_wav16k(source, audio, start, duration)
        sizes = [video.stat().st_size, audio.stat().st_size]
        if not all(sizes) or stamp != _source_stamp(source):
            raise media.MediaError("Candidate extraction produced empty media or the source changed")
        staged_marker.write_text(json.dumps({"source_stamp": stamp, "sizes": sizes}), encoding="utf-8")
        marker.unlink(missing_ok=True)
        video.replace(directory / "clip.mp4")
        audio.replace(directory / "audio_16k.wav")
        staged_marker.replace(marker)
    finally:
        for partial in (video, audio, staged_marker):
            partial.unlink(missing_ok=True)
    return directory

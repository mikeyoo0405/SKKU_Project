"""yt-dlp integration: URL validation, duration probe, download with cache."""

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

from . import config, media

ALLOWED_HOSTS = {
    "www.youtube.com", "youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be", "music.youtube.com",
}


class YouTubeError(RuntimeError):
    pass


def validate_url(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise YouTubeError(f"Not a YouTube URL (host: {host or 'none'})")


class VideoTooLong(YouTubeError):
    def __init__(self, duration_s: float, limit: float):
        self.duration_s = duration_s
        super().__init__(f"Video is {duration_s:.1f}s long; max allowed is {limit:g}s.")


def video_id_from_url(url: str) -> str:
    """Extract a single video ID before allocating workspace or downloading."""
    try:
        parsed = urlparse(url)
        valid_origin = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in ALLOWED_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 443 if parsed.scheme == "https" else 80}
        )
    except ValueError as exc:
        raise YouTubeError("Invalid YouTube URL") from exc
    if not valid_origin:
        raise YouTubeError("Expected an HTTP(S) YouTube video URL")

    parts = parsed.path.strip("/").split("/")
    ids = []
    if parsed.hostname in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
        ids = parts
    elif parsed.hostname not in {"youtu.be", "www.youtu.be"}:
        if parts == ["watch"]:
            ids = parse_qs(parsed.query).get("v", [])
        elif len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}:
            ids = parts[1:]
    if len(ids) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{11}", ids[0]):
        raise YouTubeError("Expected a single 11-character YouTube video ID")
    return ids[0]


def _ydl_opts(directory: Path | None = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ffmpeg_location": str(Path(config.FFMPEG).parent),
    }
    if directory is not None:
        opts.update(
            {
                "format": "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/b",
                "merge_output_format": "mp4",
                "outtmpl": str(directory / "source.%(ext)s"),
            }
        )
    return opts


def info_path(video_id: str) -> Path:
    return config.DOWNLOADS_DIR / f"{video_id}.info.json"


def video_path(video_id: str) -> Path:
    return config.DOWNLOADS_DIR / f"{video_id}.mp4"


def _load_valid_cache(video_id: str, limit: float) -> dict | None:
    """Return cached metadata if both files are intact and within the duration limit."""
    mp4 = video_path(video_id)
    meta_file = info_path(video_id)
    if not (mp4.exists() and meta_file.exists()):
        return None
    try:
        cached = json.loads(meta_file.read_text(encoding="utf-8"))
        cached_duration = float(cached["duration_s"])
    except (ValueError, KeyError, TypeError, OSError):
        return None
    if cached_duration > limit:
        raise VideoTooLong(cached_duration, limit)
    if cached.get("video_id") == video_id and 0 < cached_duration and mp4.stat().st_size:
        return cached
    return None


def load_video(url: str, *, max_duration_s: float | None = None) -> dict:
    """Use the UI limit by default, or the stricter of UI and batch limits."""
    import yt_dlp

    validate_url(url)
    expected_id = video_id_from_url(url) if max_duration_s is not None else None
    limit = config.MAX_VIDEO_DURATION_S
    if max_duration_s is not None:
        limit = min(limit, max_duration_s)

    if expected_id is not None:
        cached = _load_valid_cache(expected_id, limit)
        if cached is not None:
            return cached

    try:
        with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
            probe_info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise YouTubeError(str(e)) from e

    if not isinstance(probe_info, dict):
        raise YouTubeError("Unexpected download metadata response")
    video_id = probe_info.get("id")
    if (
        not isinstance(video_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
        or (expected_id and video_id != expected_id)
    ):
        raise YouTubeError("Unexpected video ID in download metadata")
    duration = probe_info.get("duration")
    valid_duration = (
        isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and math.isfinite(duration)
        and duration > 0
    )
    if max_duration_s is not None and (probe_info.get("is_live") or not valid_duration):
        raise YouTubeError("Batch requires a finite, known source duration")
    if valid_duration and duration > limit:
        raise VideoTooLong(duration, limit)

    cached = _load_valid_cache(video_id, limit)
    if cached is not None:
        return cached

    mp4 = video_path(video_id)
    meta_file = info_path(video_id)

    config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f"{video_id}-", dir=config.DOWNLOADS_DIR) as stage:
        directory = Path(stage)
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(directory)) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded = directory / "source.mp4"
                if not downloaded.exists():
                    downloaded = directory / Path(ydl.prepare_filename(info)).name
        except yt_dlp.utils.DownloadError as e:
            raise YouTubeError(str(e)) from e
        if not downloaded.is_file():
            raise YouTubeError("Download finished but no video was produced")
        probed = media.probe(downloaded)
        if probed["duration_s"] > limit:
            raise VideoTooLong(probed["duration_s"], limit)
        if probed["video_codec"] != "h264" or downloaded.suffix != ".mp4":
            converted = directory / "converted.mp4"
            media.transcode_to_h264(downloaded, converted)
            downloaded = converted
            probed = media.probe(downloaded)
            if probed["duration_s"] > limit:
                raise VideoTooLong(probed["duration_s"], limit)

        meta = {
            "video_id": video_id,
            "url": probe_info.get("webpage_url") or url,
            "title": probe_info.get("title"),
            "channel": probe_info.get("channel") or probe_info.get("uploader"),
            "duration_s": probed["duration_s"],
            "width": probed["width"],
            "height": probed["height"],
            "fps": probed["fps"],
            "video_codec": probed["video_codec"],
            "audio_codec": probed["audio_codec"],
            "downloaded_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        record = directory / "info.json"
        record.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        downloaded.replace(mp4)
        record.replace(meta_file)
    return meta


def get_meta(video_id: str) -> dict:
    meta_file = info_path(video_id)
    if not meta_file.exists():
        raise YouTubeError(f"Unknown video id: {video_id}")
    return json.loads(meta_file.read_text(encoding="utf-8"))


def get_peaks(video_id: str) -> dict:
    """Waveform peaks, computed once and cached beside the download."""
    cache = config.DOWNLOADS_DIR / f"{video_id}.peaks.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    meta = get_meta(video_id)
    peaks = media.compute_peaks(video_path(video_id), meta["duration_s"])
    cache.write_text(json.dumps(peaks), encoding="utf-8")
    return peaks

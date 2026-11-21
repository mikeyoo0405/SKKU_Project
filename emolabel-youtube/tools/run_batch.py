"""Prepare playable review candidates. Run with python -m tools.run_batch --help."""
import argparse
import json
import math
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from time import perf_counter

from app import asr, candidate_media, candidates, config, media, qc, youtube

PILOT_IDS = ("DGwuCn-EDfQ", "mKwJDo0YTkM", "6yrnO_6Hl0I")
MAX_SECONDS = 180


def write_json(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json_or_none(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _cleanup_partials(folder):
    for pattern in ("*.tmp", "*.part*"):
        for temp in folder.glob(pattern):
            temp.unlink(missing_ok=True)


def _current_settings():
    return [
        MAX_SECONDS, config.MAX_VIDEO_DURATION_S, config.ASR_BACKEND, config.ASR_MODEL,
        config.ASR_DEVICE, config.ASR_COMPUTE_TYPE,
        config.ASR_LANGUAGE, config.ASR_NO_SPEECH_THRESHOLD, config.MIN_CLIP_S, config.MAX_CLIP_S,
    ]


def _valid_source_meta(meta, video_id):
    if not isinstance(meta, dict) or meta.get("video_id") != video_id:
        return False
    duration = meta.get("duration_s")
    return type(duration) in (int, float) and math.isfinite(duration) and duration > 0


def _ensure_source(url, folder, source, timings):
    meta_path = folder / "source.json"
    meta = read_json_or_none(meta_path) if source.is_file() else None
    if _valid_source_meta(meta, folder.name):
        return meta
    started = perf_counter()
    meta = youtube.load_video(url, max_duration_s=MAX_SECONDS)
    write_json(meta_path, meta)
    timings["download"] = perf_counter() - started
    return meta


def _ensure_audio(source, duration, folder, timings):
    wav = folder / "source.wav"
    if wav.exists():
        return wav
    started = perf_counter()
    temporary = folder / "source.part.wav"
    temporary.unlink(missing_ok=True)
    media.extract_wav16k(source, temporary, 0, duration)
    temporary.replace(wav)
    timings["audio"] = perf_counter() - started
    return wav


def _ensure_vad(wav, folder, timings):
    vad_path = folder / "vad.json"
    speech = read_json_or_none(vad_path)
    if isinstance(speech, list):
        return speech
    started = perf_counter()
    speech = qc.speech_intervals(wav)
    write_json(vad_path, speech)
    timings["vad"] = perf_counter() - started
    return speech


def _ensure_asr(wav, folder, timings):
    asr_path = folder / "asr.json"
    transcript = read_json_or_none(asr_path)
    if isinstance(transcript, dict) and isinstance(transcript.get("segments"), list):
        return transcript
    started = perf_counter()
    transcript = asr.transcribe(wav)
    write_json(asr_path, transcript)
    timings["asr"] = perf_counter() - started
    return transcript


def _finish_candidate_media(state, folder, source):
    """Also upgrades completed Phase 1 checkpoints without repeating inference."""
    started = perf_counter()
    try:
        ids = [row["clip_id"] for row in state["candidates"]]
        missing = [key for key in ids if not candidate_media.media_ready(folder, key, source)]
        if not missing and state["status"] == "completed":
            return {**state, "cached": True}
        for key in missing:
            candidate_media.ensure_media(folder, key, source)
        state.update(status="completed")
        state.pop("stage", None)
        state.pop("reason", None)
    except Exception as error:
        state.update(status="failed", stage="candidate_media", reason=str(error))
    seconds = state.setdefault("seconds", {})
    if not isinstance(seconds, dict):
        seconds = state["seconds"] = {}
    seconds["candidate_media"] = perf_counter() - started
    write_json(folder / "state.json", state)
    return state


def run_video(video_id, workspace):
    started = perf_counter()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Invalid YouTube video ID")
    folder = Path(workspace) / video_id
    folder.mkdir(parents=True, exist_ok=True)
    # One OS-backed database lock per video, released even after a process crash.
    with closing(sqlite3.connect(folder / "lock.sqlite", timeout=3600)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        result = process_video(video_id, folder)
        return {**result, "elapsed_s": perf_counter() - started}


def process_video(video_id, folder):
    url = "https://www.youtube.com/watch?v=" + video_id
    state_path = folder / "state.json"
    loaded_state = read_json_or_none(state_path)
    state = loaded_state if isinstance(loaded_state, dict) else {}
    source = youtube.video_path(video_id)
    settings = _current_settings()
    stamp = source.stat() if source.is_file() else None
    source_stamp = [stamp.st_size, stamp.st_mtime_ns] if stamp else None
    reuse = state.get("settings") == settings and state.get("source_stamp") == source_stamp
    status = state.get("status")
    if reuse and (
        status == "excluded" or (
            status == "no_candidates" and source_stamp
            and state.get("selection_version") == candidates.SELECTION_VERSION
        )
    ):
        return {**state, "cached": True}
    if reuse and state.get("selection_version") == candidates.SELECTION_VERSION and source_stamp and state.get("candidates") and (
        status in {"completed", "candidates_ready"} or
        (status == "failed" and state.get("stage") == "candidate_media")
    ):
        if _valid_source_meta(read_json_or_none(folder / "source.json"), video_id):
            return _finish_candidate_media(state, folder, source)

    if not reuse:
        for name in ("source.json", "source.wav", "asr.json", "vad.json"):
            (folder / name).unlink(missing_ok=True)
        state = {}
    timings = dict(state.get("seconds", {})) if isinstance(state.get("seconds"), dict) else {}
    state.update(video_id=video_id, url=url, status="running", seconds=timings, settings=settings,
                 source_stamp=source_stamp)
    state.pop("reason", None)
    state.pop("candidates", None)
    stage = "download"
    started = perf_counter()
    write_json(state_path, state)
    try:
        # Stage markers persist only after the corresponding artifact is complete.
        _cleanup_partials(folder)
        meta = _ensure_source(url, folder, source, timings)
        duration = meta["duration_s"]
        stamp = source.stat()
        if reuse and source_stamp != [stamp.st_size, stamp.st_mtime_ns]:
            for name in ("source.wav", "vad.json", "asr.json"):
                (folder / name).unlink(missing_ok=True)
        state.update(duration_s=duration, source_path=str(source), source_stamp=[stamp.st_size, stamp.st_mtime_ns])
        write_json(state_path, state)

        stage, started = "audio", perf_counter()
        wav = _ensure_audio(source, duration, folder, timings)

        stage, started = "vad", perf_counter()
        speech = _ensure_vad(wav, folder, timings)

        selected = []
        if speech:
            stage, started = "asr", perf_counter()
            transcript = _ensure_asr(wav, folder, timings)
            stage, started = "candidates", perf_counter()
            selected = candidates.select(video_id, duration, transcript["segments"], speech)
            timings[stage] = perf_counter() - started
        state.update(status="candidates_ready" if selected else "no_candidates", candidates=selected)
        state["selection_version"] = candidates.SELECTION_VERSION
        if not selected:
            state["reason"] = "no_eligible_segments" if speech else "no_speech"
    except youtube.VideoTooLong as error:
        state.update(status="excluded", reason="duration_limit", duration_s=error.duration_s)
    except Exception as error:
        state.update(status="failed", stage=stage, reason=str(error))
        timings[stage] = perf_counter() - started
    if state["status"] != "failed":
        state.pop("stage", None)
    write_json(state_path, state)
    if state["status"] == "candidates_ready":
        return _finish_candidate_media(state, folder, source)
    return state


def main():
    parser = argparse.ArgumentParser(description="Extract review candidates from Korean YouTube videos.")
    parser.add_argument("--urls", type=Path, help="UTF-8 file with one URL per line (default: pilot_v1)")
    parser.add_argument("--video-workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    urls = (args.urls.read_text(encoding="utf-8-sig").splitlines() if args.urls else
            ["https://youtu.be/" + video_id for video_id in PILOT_IDS])
    videos = {}
    failed = False
    for index, line in enumerate(urls, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            videos[youtube.video_id_from_url(stripped)] = None
        except (ValueError, youtube.YouTubeError) as error:
            print(f"line {index}: {error}", file=sys.stderr)
            failed = True
    config.ensure_dirs()
    workspace = config.DATA_DIR / "workspace"
    batch_started = perf_counter()
    with ThreadPoolExecutor(args.video_workers) as pool:
        futures = {key: pool.submit(run_video, key, workspace) for key in videos}
        for key, future in futures.items():
            try:
                result = future.result()
            except Exception as error:
                print(f"{key}: {error}", file=sys.stderr)
                failed = True
                continue
            print(json.dumps({"video_id": key, "status": result["status"], "cached": result.get("cached", False),
                              "reason": result.get("reason"), "elapsed_s": result["elapsed_s"],
                              "stage_artifact_seconds": result.get("seconds", {})}, ensure_ascii=False))
            failed |= result["status"] == "failed"
    print(json.dumps({"video_workers": args.video_workers, "unique_videos": len(videos),
                      "batch_elapsed_s": perf_counter() - batch_started}))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())

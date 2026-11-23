"""Prepare playable review candidates. Run with python -m tools.run_batch --help."""
import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from app import asr, candidate_media, candidates, config, media, qc, run_summary, scene, youtube

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


def _archive_selection(folder, state):
    """Retain the previous candidate list before invalidating a selector cache."""
    if not state.get("candidates") or state.get("selection_version") == candidates.SELECTION_VERSION:
        return
    payload = (folder / "state.json").read_bytes()
    fingerprint = hashlib.sha256(payload).hexdigest()
    history = folder / "selection-history"
    history.mkdir(exist_ok=True)
    destination = history / f"{fingerprint}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise ValueError("Archived candidate selection fingerprint mismatch")
        return
    temporary = history / f"{fingerprint}.tmp"
    temporary.write_bytes(payload)
    temporary.replace(destination)


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


def _record_stage(run_stages, stage, status, seconds=None, **extra):
    if run_stages is None:
        return
    entry = {"status": status, "seconds": round(seconds, 3) if seconds is not None else None}
    entry.update(extra)
    run_stages[stage] = entry


def _ensure_source(url, folder, source, timings, run_stages=None):
    meta_path = folder / "source.json"
    meta = read_json_or_none(meta_path) if source.is_file() else None
    if _valid_source_meta(meta, folder.name):
        _record_stage(run_stages, "download", "cached")
        return meta
    started = perf_counter()
    meta = youtube.load_video(url, max_duration_s=MAX_SECONDS)
    write_json(meta_path, meta)
    elapsed = perf_counter() - started
    timings["download"] = elapsed
    _record_stage(run_stages, "download", "executed", elapsed)
    return meta


def _ensure_scene(video_id, source, folder, run_stages=None):
    started = perf_counter()
    try:
        if config.scene_mode() != "warn" or not source.is_file():
            _record_stage(run_stages, "scene", "skipped", scene_status="not_analyzed")
            return True
        record, cached = scene.ensure_scene(video_id, source, folder)
    except (OSError, ValueError):
        _record_stage(run_stages, "scene", "failed", perf_counter() - started, scene_status="failed")
        return False
    if cached:
        _record_stage(run_stages, "scene", "cached", scene_status=record["status"])
        return True
    elapsed = perf_counter() - started
    scene_status = record["status"]
    stage_status = "executed" if scene_status == "ok" else scene_status
    _record_stage(
        run_stages,
        "scene",
        stage_status,
        elapsed if stage_status in {"executed", "failed"} else None,
        scene_status=scene_status,
    )
    return False


def _ensure_audio(source, duration, folder, timings, run_stages=None):
    wav = folder / "source.wav"
    if wav.exists():
        _record_stage(run_stages, "audio", "cached")
        return wav
    started = perf_counter()
    temporary = folder / "source.part.wav"
    temporary.unlink(missing_ok=True)
    media.extract_wav16k(source, temporary, 0, duration)
    temporary.replace(wav)
    elapsed = perf_counter() - started
    timings["audio"] = elapsed
    _record_stage(run_stages, "audio", "executed", elapsed)
    return wav


def _ensure_vad(wav, folder, timings, run_stages=None):
    vad_path = folder / "vad.json"
    speech = read_json_or_none(vad_path)
    if isinstance(speech, list):
        _record_stage(run_stages, "vad", "cached")
        return speech
    started = perf_counter()
    speech = qc.speech_intervals(wav)
    write_json(vad_path, speech)
    elapsed = perf_counter() - started
    timings["vad"] = elapsed
    _record_stage(run_stages, "vad", "executed", elapsed)
    return speech


def _ensure_asr(wav, folder, timings, run_stages=None):
    asr_path = folder / "asr.json"
    transcript = read_json_or_none(asr_path)
    if isinstance(transcript, dict) and isinstance(transcript.get("segments"), list):
        _record_stage(run_stages, "asr", "cached")
        return transcript
    started = perf_counter()
    transcript = asr.transcribe(wav)
    write_json(asr_path, transcript)
    elapsed = perf_counter() - started
    timings["asr"] = elapsed
    _record_stage(run_stages, "asr", "executed", elapsed)
    return transcript


def _finish_candidate_media(state, folder, source, run_stages=None):
    """Also upgrades completed Phase 1 checkpoints without repeating inference."""
    started = perf_counter()
    try:
        ids = [row["clip_id"] for row in state["candidates"]]
        missing = [key for key in ids if not candidate_media.media_ready(folder, key, source)]
        if not missing and state["status"] == "completed":
            _record_stage(run_stages, "candidate_media", "cached")
            return {**state, "cached": True, "run_stages": run_stages}
        for key in missing:
            candidate_media.ensure_media(folder, key, source)
        state.update(status="completed")
        state.pop("stage", None)
        state.pop("reason", None)
        elapsed = perf_counter() - started
        _record_stage(run_stages, "candidate_media", "executed", elapsed)
    except Exception as error:
        elapsed = perf_counter() - started
        state.update(status="failed", stage="candidate_media", reason=str(error))
        _record_stage(run_stages, "candidate_media", "failed", elapsed)
    seconds = state.setdefault("seconds", {})
    if not isinstance(seconds, dict):
        seconds = state["seconds"] = {}
    seconds["candidate_media"] = elapsed
    write_json(folder / "state.json", state)
    return {**state, "run_stages": run_stages}


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
    run_stages = {stage: {"status": "skipped", "seconds": None} for stage in run_summary.BATCH_STAGES}

    if reuse and (
        status == "excluded" or (
            status == "no_candidates" and source_stamp
            and state.get("selection_version") == candidates.SELECTION_VERSION
        )
    ):
        if status == "excluded":
            _record_stage(run_stages, "download", "cached")
            _record_stage(run_stages, "scene", "skipped", scene_status="not_analyzed")
            return {**state, "cached": True, "run_stages": run_stages}
        for stage in ("download", "audio", "vad"):
            _record_stage(run_stages, stage, "cached")
        if state.get("reason") != "no_speech":
            for stage in ("asr", "candidates"):
                _record_stage(run_stages, stage, "cached")
        scene_cached = _ensure_scene(video_id, source, folder, run_stages)
        return {**state, "cached": scene_cached, "run_stages": run_stages}

    if reuse and state.get("selection_version") == candidates.SELECTION_VERSION and source_stamp and state.get("candidates") and (
        status in {"completed", "candidates_ready"} or
        (status == "failed" and state.get("stage") == "candidate_media")
    ):
        if _valid_source_meta(read_json_or_none(folder / "source.json"), video_id):
            for stage in ("download", "audio", "vad", "asr", "candidates"):
                _record_stage(run_stages, stage, "cached")
            scene_cached = _ensure_scene(video_id, source, folder, run_stages)
            finished = _finish_candidate_media(state, folder, source, run_stages)
            if not scene_cached and finished.get("cached"):
                finished = {**finished, "cached": False}
            return finished

    _archive_selection(folder, state)
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
        meta = _ensure_source(url, folder, source, timings, run_stages)
        duration = meta["duration_s"]
        stamp = source.stat()
        if reuse and source_stamp != [stamp.st_size, stamp.st_mtime_ns]:
            for name in ("source.wav", "vad.json", "asr.json"):
                (folder / name).unlink(missing_ok=True)
        state.update(duration_s=duration, source_path=str(source), source_stamp=[stamp.st_size, stamp.st_mtime_ns])
        write_json(state_path, state)

        _ensure_scene(video_id, source, folder, run_stages)

        stage, started = "audio", perf_counter()
        wav = _ensure_audio(source, duration, folder, timings, run_stages)

        stage, started = "vad", perf_counter()
        speech = _ensure_vad(wav, folder, timings, run_stages)

        selected = []
        if speech:
            stage, started = "asr", perf_counter()
            transcript = _ensure_asr(wav, folder, timings, run_stages)
            stage, started = "candidates", perf_counter()
            selected = candidates.select(video_id, duration, transcript["segments"], speech)
            elapsed = perf_counter() - started
            timings[stage] = elapsed
            _record_stage(run_stages, "candidates", "executed", elapsed)
        state.update(status="candidates_ready" if selected else "no_candidates", candidates=selected)
        state["selection_version"] = candidates.SELECTION_VERSION
        if not selected:
            state["reason"] = "no_eligible_segments" if speech else "no_speech"
    except youtube.VideoTooLong as error:
        elapsed = perf_counter() - started
        _record_stage(run_stages, stage, "executed", elapsed)
        state.update(status="excluded", reason="duration_limit", duration_s=error.duration_s)
    except Exception as error:
        elapsed = perf_counter() - started
        state.update(status="failed", stage=stage, reason=str(error))
        timings[stage] = elapsed
        _record_stage(run_stages, stage, "failed", elapsed)
    if state["status"] != "failed":
        state.pop("stage", None)
    write_json(state_path, state)
    if state["status"] == "candidates_ready":
        return _finish_candidate_media(state, folder, source, run_stages)
    return {**state, "run_stages": run_stages}


def run_batch_videos(video_ids, workspace, *, video_workers=1):
    """Run batch processing for unique video IDs and persist the S2 timing summary."""
    config.ensure_dirs()
    run_id = uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    batch_started = perf_counter()
    results = []
    video_records = []
    failed = False
    with ThreadPoolExecutor(video_workers) as pool:
        futures = {key: pool.submit(run_video, key, workspace) for key in video_ids}
        for key, future in futures.items():
            try:
                result = future.result()
            except Exception as error:
                print(f"{key}: {error}", file=sys.stderr)
                failed = True
                video_records.append({
                    "video_id": key,
                    "status": "failed",
                    "cached": False,
                    "reason": str(error),
                    "wall_seconds": None,
                    "duration_s": None,
                    "stages": {},
                })
                continue
            results.append((key, result))
            video_records.append({
                "video_id": key,
                "status": result["status"],
                "cached": result.get("cached", False),
                "reason": result.get("reason"),
                "wall_seconds": result.get("elapsed_s"),
                "duration_s": result.get("duration_s"),
                "stages": result.get("run_stages"),
            })
            failed |= result["status"] == "failed"
    batch_elapsed = perf_counter() - batch_started
    finished_at = datetime.now(timezone.utc).isoformat()
    try:
        scene_mode = config.scene_mode()
    except ValueError:
        scene_mode = "invalid"
    summary = run_summary.build_batch_run_record(
        run_id,
        started_at,
        finished_at,
        video_workers=video_workers,
        scene_mode=scene_mode,
        batch_wall_seconds=batch_elapsed,
        videos=video_records,
    )
    run_summary.save_batch_run(Path(workspace), summary)
    return results, summary, failed


def main():
    parser = argparse.ArgumentParser(description="Extract review candidates from Korean YouTube videos.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--urls", type=Path, help="UTF-8 file with one URL per line (default: pilot_v1)")
    modes.add_argument("--export-candidates", type=Path, metavar="MANIFEST",
                       help="Freeze completed workspace candidates for model comparison")
    modes.add_argument("--candidates", type=Path, metavar="MANIFEST",
                       help="Run local emotion models on a frozen manifest")
    parser.add_argument("--edited-transcripts", type=Path, help="Separate corrected-transcript experiment (JSON)")
    parser.add_argument("--run-tag", default="", help="Experiment name stored with inference results")
    parser.add_argument("--video-workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if (args.edited_transcripts or args.run_tag) and not args.candidates:
        parser.error("--edited-transcripts and --run-tag require --candidates")
    workspace = config.DATA_DIR / "workspace"
    if args.export_candidates or args.candidates:
        from tools import emotion_batch
        try:
            if args.export_candidates:
                result = emotion_batch.export_manifest(workspace, args.export_candidates)
            else:
                result = emotion_batch.run_manifest(workspace, args.candidates, run_tag=args.run_tag,
                                                    edited_transcripts=args.edited_transcripts)
        except (ValueError, OSError, sqlite3.Error) as error:
            parser.exit(1, f"{error}\n")
        print(json.dumps(result, ensure_ascii=False))
        return int(bool(result.get("input_errors")))
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
    results, summary, batch_failed = run_batch_videos(videos.keys(), workspace, video_workers=args.video_workers)
    failed |= batch_failed
    for key, result in results:
        print(json.dumps({"video_id": key, "status": result["status"], "cached": result.get("cached", False),
                          "reason": result.get("reason"), "elapsed_s": result["elapsed_s"],
                          "run_stages": result.get("run_stages", {}),
                          "stage_artifact_seconds": result.get("seconds", {})}, ensure_ascii=False))
    print(json.dumps({"run_id": summary["run_id"], "video_workers": args.video_workers,
                      "unique_videos": len(videos), "batch_elapsed_s": summary["batch_wall_seconds"]}))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())

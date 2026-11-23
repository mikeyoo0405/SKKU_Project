"""Read-only batch timing (S2) and current review status (S3) summaries."""

from __future__ import annotations

import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from . import config

BATCH_STAGES = (
    "download",
    "audio",
    "vad",
    "asr",
    "scene",
    "candidates",
    "candidate_media",
)
LAST_BATCH_RUN_FILE = "last-batch-run.json"
RTF_BASIS = "speech_analysis"
VALID_STAGE_STATUSES = {"executed", "cached", "skipped", "failed", "unavailable", "unmeasured"}


def _valid_seconds(value: Any) -> float | None:
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return round(float(value), 3)
    return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent, suffix=".tmp", delete=False,
    )
    temporary = Path(stream.name)
    try:
        with stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _normalize_video_stages(raw_stages: dict | None) -> dict[str, dict]:
    stages = {}
    source = raw_stages if isinstance(raw_stages, dict) else {}
    for stage in BATCH_STAGES:
        entry = source.get(stage)
        if not isinstance(entry, dict):
            stages[stage] = {"status": "unmeasured", "seconds": None}
            continue
        status = entry.get("status")
        if status not in VALID_STAGE_STATUSES:
            status = "unmeasured"
        seconds = _valid_seconds(entry.get("seconds")) if status in {"executed", "failed"} else None
        normalized: dict[str, Any] = {"status": status, "seconds": seconds}
        if stage == "scene" and isinstance(entry.get("scene_status"), str):
            normalized["scene_status"] = entry["scene_status"]
        stages[stage] = normalized
    return stages


def build_batch_run_record(
    run_id: str,
    started_at: str,
    finished_at: str,
    *,
    video_workers: int,
    scene_mode: str,
    batch_wall_seconds: float,
    videos: list[dict],
) -> dict:
    """Build a summary record for a single batch execution without mixing cached artifact times."""
    normalized_videos = []
    status_counts = {"completed": 0, "no_candidates": 0, "excluded": 0, "failed": 0}
    stage_buckets = {
        stage: {
            "executed_videos": 0,
            "cached_videos": 0,
            "skipped_videos": 0,
            "failed_videos": 0,
            "unavailable_videos": 0,
            "unmeasured_videos": 0,
            "_sum": 0.0,
            "_measured": 0,
        }
        for stage in BATCH_STAGES
    }

    executed_duration_sum = 0.0
    executed_wall_sum = 0.0
    executed_measured_videos = 0

    for video in videos:
        status = video.get("status") or "failed"
        if status in status_counts:
            status_counts[status] += 1
        else:
            status_counts["failed"] += 1
        stages = _normalize_video_stages(video.get("stages"))
        for stage, info in stages.items():
            bucket = stage_buckets[stage]
            st = info["status"]
            bucket[f"{st}_videos"] += 1
            if info["seconds"] is not None:
                bucket["_sum"] += info["seconds"]
                bucket["_measured"] += 1
        cached_video = bool(video.get("cached", False))
        wall_s = _valid_seconds(video.get("wall_seconds"))
        dur_s = _valid_seconds(video.get("duration_s"))
        video_rtf = None
        analysis_executed = stages["vad"]["status"] == "executed" and (
            stages["asr"]["status"] == "executed"
            or (status == "no_candidates" and video.get("reason") == "no_speech")
        )
        if (
            not cached_video
            and status in {"completed", "no_candidates"}
            and analysis_executed
            and wall_s is not None
            and dur_s is not None
            and dur_s > 0
        ):
            video_rtf = round(wall_s / dur_s, 3)
            executed_duration_sum += dur_s
            executed_wall_sum += wall_s
            executed_measured_videos += 1
        normalized_videos.append(
            {
                "video_id": video.get("video_id"),
                "status": status,
                "cached": cached_video,
                "reason": video.get("reason"),
                "wall_seconds": wall_s,
                "duration_s": dur_s,
                "rtf": video_rtf,
                "stages": stages,
            }
        )

    stage_summary = {}
    for stage, bucket in stage_buckets.items():
        measured = bucket.pop("_measured")
        total = bucket.pop("_sum")
        stage_summary[stage] = {
            **bucket,
            "executed_seconds_sum": round(total, 3) if measured > 0 else None,
        }

    batch_wall = _valid_seconds(batch_wall_seconds)
    processed_duration = round(executed_duration_sum, 3) if executed_measured_videos > 0 else None
    batch_rtf = (
        round(batch_wall / executed_duration_sum, 3)
        if (
            executed_measured_videos > 0
            and executed_measured_videos == len(normalized_videos)
            and batch_wall is not None
            and executed_duration_sum > 0
        )
        else None
    )
    executed_videos_rtf = (
        round(executed_wall_sum / executed_duration_sum, 3)
        if executed_measured_videos > 0 and executed_duration_sum > 0
        else None
    )

    return {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "video_workers": int(video_workers),
        "scene_mode": scene_mode,
        "video_count": len(normalized_videos),
        "batch_wall_seconds": batch_wall,
        "rtf_basis": RTF_BASIS,
        "processed_duration_seconds": processed_duration,
        "batch_rtf": batch_rtf,
        "executed_videos_rtf": executed_videos_rtf,
        "status_counts": status_counts,
        "stage_summary": stage_summary,
        "videos": normalized_videos,
    }


def save_batch_run(workspace: Path, record: dict) -> Path:
    run_id = str(record.get("run_id") or "")
    if run_id:
        _write_json(workspace / "batch-runs" / f"{run_id}.json", record)
    latest = workspace / LAST_BATCH_RUN_FILE
    _write_json(latest, record)
    return latest


def read_latest_batch_run(workspace: Path) -> dict | None:
    record = _read_json(workspace / LAST_BATCH_RUN_FILE)
    if not record or record.get("schema_version") != 1:
        return None
    if not isinstance(record.get("stage_summary"), dict) or not isinstance(record.get("videos"), list):
        return None
    if record.get("rtf_basis") != RTF_BASIS:
        for key in ("processed_duration_seconds", "batch_rtf", "executed_videos_rtf"):
            record[key] = None
        for video in record["videos"]:
            if isinstance(video, dict):
                video.pop("rtf", None)
    return record


def read_latest_emotion_run(workspace: Path) -> dict | None:
    """Read timing metadata from the latest separate emotion inference run."""
    runs_dir = workspace / "emotion-runs"
    if not runs_dir.is_dir():
        return None

    latest_summary = None
    latest_dir = None
    for folder in sorted(runs_dir.iterdir()):
        if not folder.is_dir():
            continue
        summary = _read_json(folder / "summary.json")
        if not summary or not isinstance(summary.get("started_at"), str):
            continue
        if latest_summary is None or summary["started_at"] > latest_summary["started_at"]:
            latest_summary = summary
            latest_dir = folder

    if latest_summary is None or latest_dir is None:
        return None

    totals = {"text": 0.0, "audio": 0.0, "combined": 0.0}
    measured = {"text": 0, "audio": 0, "combined": 0}
    for path in sorted(latest_dir.glob("*.json")):
        if path.name in {"summary.json", "manifest.json"}:
            continue
        entry = _read_json(path)
        results = (entry or {}).get("results")
        if not isinstance(results, dict):
            continue
        for kind in ("text", "audio", "combined"):
            prediction = results.get(kind)
            sec = _valid_seconds(prediction.get("elapsed_s")) if isinstance(prediction, dict) else None
            if sec is not None:
                totals[kind] += sec
                measured[kind] += 1

    return {
        "separate_from_batch": True,
        "run_id": latest_summary.get("run_id"),
        "run_tag": latest_summary.get("run_tag") or "",
        "input_tag": latest_summary.get("input_tag") or "",
        "started_at": latest_summary.get("started_at"),
        "candidates": latest_summary.get("candidates"),
        "input_errors": latest_summary.get("input_errors", 0),
        "measured_candidates": {**measured},
        "text_seconds_sum": round(totals["text"], 3) if measured["text"] > 0 else None,
        "audio_seconds_sum": round(totals["audio"], 3) if measured["audio"] > 0 else None,
        "combined_seconds_sum": round(totals["combined"], 3) if measured["combined"] > 0 else None,
    }


def _collect_review_time(active_rows: list[dict]) -> dict:
    """Sum recorded active review seconds over non-superseded rows without double counting."""
    total_seconds = 0.0
    measured_events = 0
    unmeasured_events = 0

    for row in active_rows:
        history = row.get("history")
        if isinstance(history, list) and history:
            for event in history:
                if not isinstance(event, dict):
                    unmeasured_events += 1
                    continue
                sec = _valid_seconds(event.get("active_seconds"))
                if sec is not None:
                    total_seconds += sec
                    measured_events += 1
                else:
                    unmeasured_events += 1
        elif row.get("status") != "pending":
            review_data = row.get("review")
            sec = _valid_seconds(review_data.get("active_seconds")) if isinstance(review_data, dict) else None
            if sec is not None:
                total_seconds += sec
                measured_events += 1
            else:
                unmeasured_events += 1

    if measured_events == 0:
        coverage = "unmeasured"
    elif unmeasured_events > 0:
        coverage = "partial"
    else:
        coverage = "complete"

    return {
        "total_active_seconds": round(total_seconds, 1) if measured_events > 0 else None,
        "measured_events": measured_events,
        "unmeasured_events": unmeasured_events,
        "coverage": coverage,
    }


def summarize_review_rows(rows: list[dict]) -> dict:
    """Summarize the current review queue without double-counting superseded or withdrawn history."""
    active = [row for row in rows if row.get("status") != "superseded"]
    superseded_count = sum(1 for row in rows if row.get("status") == "superseded")

    by_origin = {"automatic": 0, "manual": 0}
    by_status = {
        "pending": 0,
        "accepted_total": 0,
        "accepted": 0,
        "accepted_edited": 0,
        "hold": 0,
        "rejected": 0,
    }
    scene_counts = {
        "has_cuts": 0,
        "no_cuts": 0,
        "unavailable": 0,
        "failed": 0,
        "not_analyzed": 0,
    }

    for row in active:
        origin = "manual" if row.get("origin") == "manual" else "automatic"
        by_origin[origin] += 1

        status = row.get("status")
        if status == "accepted":
            by_status["accepted"] += 1
            by_status["accepted_total"] += 1
        elif status == "accepted_edited":
            by_status["accepted_edited"] += 1
            by_status["accepted_total"] += 1
        elif status in {"pending", "hold", "rejected"}:
            by_status[status] += 1

        warning = row.get("scene_warning")
        scene_status = warning.get("status") if isinstance(warning, dict) else "not_analyzed"
        if scene_status in scene_counts:
            scene_counts[scene_status] += 1
        else:
            scene_counts["not_analyzed"] += 1

    timing = _collect_review_time(active)
    accepted_total = by_status["accepted_total"]
    seconds_per_accepted = (
        round(timing["total_active_seconds"] / accepted_total, 1)
        if timing["coverage"] == "complete" and accepted_total > 0
        else None
    )

    return {
        "total_candidates": len(active),
        "superseded_excluded": superseded_count,
        "by_origin": by_origin,
        "by_status": by_status,
        "scene_warnings": scene_counts,
        "timing": {
            **timing,
            "seconds_per_accepted": seconds_per_accepted,
        },
    }


def build_workspace_summary(workspace: Path, rows: list[dict], *, blind: bool = False) -> dict:
    """Assemble S2 (latest batch & separate emotion timing) + S3 (current review status)."""
    try:
        mode = config.scene_mode()
    except ValueError:
        mode = "off"
    return {
        "scope": "current_review_workspace",
        "blind": bool(blind),
        "scene_mode": mode,
        "review": summarize_review_rows(rows),
        "latest_batch_run": read_latest_batch_run(workspace),
        "latest_emotion_run": None if blind else read_latest_emotion_run(workspace),
    }

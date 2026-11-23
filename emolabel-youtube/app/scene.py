"""Optional PySceneDetect AdaptiveDetector scene-cut analysis and per-video cache."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path

from . import config

ANALYZER_NAME = "pyscenedetect"
DETECTOR_NAME = "AdaptiveDetector"
ANALYZER_VERSION = "1"
CUT_EPSILON_S = 0.001


class SceneUnavailable(RuntimeError):
    """Raised when the optional PySceneDetect dependency is not installed."""


def current_settings() -> dict:
    return {
        "adaptive_threshold": config.scene_adaptive_threshold(),
        "min_scene_len": config.scene_min_scene_len(),
    }


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_scene(folder: Path) -> dict | None:
    path = folder / "scene.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _normalize_cuts(cuts) -> list[float]:
    cleaned: set[float] = set()
    for value in cuts:
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            continue
        cleaned.add(round(float(value), 3))
    return sorted(cleaned)


def detect_cuts(
    video_path: Path,
    *,
    adaptive_threshold: float | None = None,
    min_scene_len: int | None = None,
) -> tuple[list[float], str]:
    """Run PySceneDetect AdaptiveDetector lazily so the app works without it."""
    try:
        import scenedetect
        from scenedetect import AdaptiveDetector, SceneManager, open_video
    except ImportError as exc:
        raise SceneUnavailable("PySceneDetect가 설치되지 않았습니다.") from exc

    threshold = config.scene_adaptive_threshold() if adaptive_threshold is None else adaptive_threshold
    min_len = config.scene_min_scene_len() if min_scene_len is None else min_scene_len

    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(AdaptiveDetector(adaptive_threshold=threshold, min_scene_len=min_len))
    manager.detect_scenes(video=video)
    scenes = manager.get_scene_list()
    cuts = [scenes[index][0].get_seconds() for index in range(1, len(scenes))]
    version = str(getattr(scenedetect, "__version__", "unknown"))
    return _normalize_cuts(cuts), version


def _matches_ok_cache(record: dict | None, video_id: str, source_stamp: list[int], settings: dict) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        record.get("schema_version") == 1
        and record.get("status") == "ok"
        and record.get("video_id") == video_id
        and record.get("source_stamp") == source_stamp
        and record.get("analyzer") == ANALYZER_NAME
        and record.get("detector") == DETECTOR_NAME
        and record.get("analyzer_version") == ANALYZER_VERSION
        and record.get("settings") == settings
        and isinstance(record.get("cuts_s"), list)
    )


def ensure_scene(
    video_id: str,
    source: Path,
    folder: Path,
    *,
    detector=None,
) -> tuple[dict, bool]:
    """Ensure cached scene-cut analysis for a source video without raising on optional failure."""
    stat = source.stat()
    source_stamp = [stat.st_size, stat.st_mtime_ns]
    settings = current_settings()
    cached = read_scene(folder)
    if _matches_ok_cache(cached, video_id, source_stamp, settings):
        return cached, True

    runner = detect_cuts if detector is None else detector
    base = {
        "schema_version": 1,
        "video_id": video_id,
        "source_stamp": source_stamp,
        "analyzer": ANALYZER_NAME,
        "detector": DETECTOR_NAME,
        "analyzer_version": ANALYZER_VERSION,
        "settings": settings,
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        cuts, pkg_version = runner(
            source,
            adaptive_threshold=settings["adaptive_threshold"],
            min_scene_len=settings["min_scene_len"],
        )
        record = {
            **base,
            "status": "ok",
            "package_version": str(pkg_version) if pkg_version else None,
            "cuts_s": _normalize_cuts(cuts),
            "reason": None,
        }
    except SceneUnavailable:
        record = {
            **base,
            "status": "unavailable",
            "package_version": None,
            "cuts_s": [],
            "reason": "dependency_missing",
        }
    except Exception:
        record = {
            **base,
            "status": "failed",
            "package_version": None,
            "cuts_s": [],
            "reason": "detection_failed",
        }

    _write_json(folder / "scene.json", record)
    return record, False


def cuts_in_segment(cuts_s: list[float], start_s: float, end_s: float) -> list[float]:
    """Return strictly interior cut timestamps for the given [start_s, end_s] segment."""
    if (
        type(start_s) not in (int, float)
        or type(end_s) not in (int, float)
        or not (math.isfinite(start_s) and math.isfinite(end_s))
        or end_s <= start_s
    ):
        return []
    low = start_s + CUT_EPSILON_S
    high = end_s - CUT_EPSILON_S
    if high <= low:
        return []
    return _normalize_cuts(cut for cut in cuts_s if type(cut) in (int, float) and low < cut < high)


def segment_warning(
    folder: Path,
    source_stamp: list[int] | None,
    start_s: float,
    end_s: float,
    *,
    mode: str | None = None,
    source: Path | None = None,
) -> dict:
    """Compute a read-time scene-cut warning for any candidate segment."""
    try:
        active_mode = config.scene_mode() if mode is None else mode
        settings = current_settings() if active_mode == "warn" else None
    except ValueError:
        return {"status": "failed", "cuts_s": [], "cut_count": 0, "reason": "invalid_settings"}
    if active_mode == "off":
        return {
            "status": "not_analyzed",
            "cuts_s": [],
            "cut_count": 0,
            "source_cuts_s": None,
            "analyzer": None,
            "detector": None,
            "reason": "scene_mode_off",
        }

    record = read_scene(folder)
    if record is None or record.get("schema_version") != 1:
        return {
            "status": "not_analyzed",
            "cuts_s": [],
            "cut_count": 0,
            "source_cuts_s": None,
            "analyzer": None,
            "detector": None,
            "reason": "not_run",
        }

    if source is not None:
        try:
            stat = source.stat()
            if source_stamp != [stat.st_size, stat.st_mtime_ns]:
                source_stamp = None
        except OSError:
            source_stamp = None
    if not source_stamp or record.get("source_stamp") != source_stamp:
        return {
            "status": "not_analyzed",
            "cuts_s": [],
            "cut_count": 0,
            "source_cuts_s": None,
            "analyzer": record.get("analyzer"),
            "detector": record.get("detector"),
            "reason": "source_changed",
        }

    if (
        (source is not None and record.get("video_id") != folder.name)
        or record.get("analyzer_version") != ANALYZER_VERSION
        or record.get("settings") != settings
    ):
        return {"status": "not_analyzed", "cuts_s": [], "cut_count": 0, "reason": "settings_changed"}

    status = record.get("status")
    if status == "unavailable":
        return {
            "status": "unavailable",
            "cuts_s": [],
            "cut_count": 0,
            "source_cuts_s": None,
            "analyzer": record.get("analyzer"),
            "detector": record.get("detector"),
            "reason": record.get("reason") or "dependency_missing",
        }
    if status == "failed":
        return {
            "status": "failed",
            "cuts_s": [],
            "cut_count": 0,
            "source_cuts_s": None,
            "analyzer": record.get("analyzer"),
            "detector": record.get("detector"),
            "reason": record.get("reason") or "detection_failed",
        }
    if status != "ok" or not isinstance(record.get("cuts_s"), list):
        return {
            "status": "failed",
            "cuts_s": [],
            "cut_count": 0,
            "source_cuts_s": None,
            "analyzer": record.get("analyzer"),
            "detector": record.get("detector"),
            "reason": "invalid_cache",
        }

    all_cuts = _normalize_cuts(record["cuts_s"])
    interior = cuts_in_segment(all_cuts, start_s, end_s)
    return {
        "status": "has_cuts" if interior else "no_cuts",
        "cuts_s": interior,
        "cut_count": len(interior),
        "source_cuts_s": all_cuts,
        "analyzer": record.get("analyzer"),
        "detector": record.get("detector"),
        "reason": None,
    }

"""Storage for the manual workflow and the shared final annotation format."""

import json
import os
import re
import shutil
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from . import config, media

CLIP_ID_RE = re.compile(r"^(?P<video_id>[A-Za-z0-9_-]{6,})_(?P<start_ms>\d{7,})_(?P<end_ms>\d{7,})$")


class StoreError(RuntimeError):
    status_code = 400


class ClipConflict(StoreError):
    status_code = 409


class ClipNotFound(StoreError):
    status_code = 404


def is_lock_conflict(error: sqlite3.OperationalError) -> bool:
    # Extended result codes retain the primary SQLite code in the low byte.
    return (getattr(error, "sqlite_errorcode", 0) & 0xff) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}


def make_clip_id(video_id: str, start_s: float, end_s: float) -> str:
    return f"{video_id}_{round(start_s * 1000):07d}_{round(end_s * 1000):07d}"


def parse_clip_id(clip_id: str) -> dict:
    m = CLIP_ID_RE.fullmatch(clip_id)
    if not m or len(clip_id) > 150 or len(m["start_ms"]) > 15 or len(m["end_ms"]) > 15:
        raise StoreError(f"Invalid clip id: {clip_id}")
    return {
        "video_id": m["video_id"],
        "start_s": int(m["start_ms"]) / 1000,
        "end_s": int(m["end_ms"]) / 1000,
    }


def clip_dir(clip_id: str) -> Path:
    parse_clip_id(clip_id)  # rejects path-traversal garbage before touching the filesystem
    return config.OUTPUT_DIR / clip_id


def _draft_dir(clip_id: str) -> Path:
    return config.DATA_DIR / "workspace" / parse_clip_id(clip_id)["video_id"] / "manual" / clip_id


def _read_dir(clip_id: str) -> Path:
    final = clip_dir(clip_id)
    if final.exists():
        return final
    draft = _draft_dir(clip_id)
    # If a worker stopped between the two renames during an overwrite, keep
    # the previous complete draft readable until the next edit restores it.
    previous = draft.with_suffix(".previous")
    return previous if not draft.exists() and previous.exists() else draft


@contextmanager
def manual_edit(clip_id: str, *, allow_confirmed: bool = False, discard_draft: bool = False):
    """Serialize manual edits with batch processing and human decisions."""
    from . import review

    draft = _draft_dir(clip_id)
    folder = draft.parent.parent
    draft.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder / "lock.sqlite", timeout=30)) as lock:
        try:
            lock.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if not is_lock_conflict(exc):
                raise
            raise ClipConflict("배치 또는 다른 검수가 진행 중입니다. 잠시 후 다시 시도하세요.") from exc
        if review.owns_candidate_locked(folder, clip_id) and not discard_draft:
            raise ClipConflict("배치 후보는 검수 큐에서 수정하세요.")
        final = clip_dir(clip_id)
        if (final / "annotation.json").exists():
            if not allow_confirmed or (read_annotation(clip_id).get("review") or {}).get("origin") == "batch_review":
                raise ClipConflict("이미 확정된 클립입니다.")
            yield final
            return
        previous = draft.with_suffix(".previous")
        if previous.exists():
            if draft.exists():
                shutil.rmtree(previous)
            else:
                previous.rename(draft)
        if final.exists():
            if draft.exists():
                raise ClipConflict("기존 추출본과 작업 중인 추출본이 모두 존재합니다.")
            final.rename(draft)
        yield draft


def require_media(directory: Path) -> None:
    for name in ("clip.mp4", "audio_16k.wav"):
        path = directory / name
        if not path.is_file() or not path.stat().st_size:
            raise ClipNotFound(f"Missing or empty {name}; extract the clip first")


def extract_draft(clip_id: str, source: Path, overwrite: bool) -> None:
    segment = parse_clip_id(clip_id)
    with manual_edit(clip_id) as draft:
        if draft.exists() and not overwrite:
            raise ClipConflict(f"Clip {clip_id} already exists")
        with TemporaryDirectory(dir=draft.parent) as temporary:
            ready = Path(temporary) / "ready"
            ready.mkdir()
            start = segment["start_s"]
            duration = segment["end_s"] - start
            media.cut_clip(source, ready / "clip.mp4", start, duration)
            media.extract_wav16k(source, ready / "audio_16k.wav", start, duration)
            require_media(ready)
            previous = draft.with_suffix(".previous")
            if draft.exists():
                draft.rename(previous)
            ready.rename(draft)
            if previous.exists():
                shutil.rmtree(previous)


def clip_video_path(clip_id: str) -> Path:
    return _read_dir(clip_id) / "clip.mp4"


def clip_wav_path(clip_id: str) -> Path:
    return _read_dir(clip_id) / "audio_16k.wav"


def _atomic_write_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_prelabels(clip_id: str, prelabels: dict) -> None:
    _atomic_write_json(_draft_dir(clip_id) / "prelabels.json", prelabels)


def read_prelabels(clip_id: str) -> dict | None:
    p = _read_dir(clip_id) / "prelabels.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def read_annotation(clip_id: str) -> dict | None:
    p = clip_dir(clip_id) / "annotation.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_annotation(clip_id: str, labels: dict, annotator_id: str,
                    source_meta: dict, clip_media: dict) -> dict:
    """Caller holds manual_edit(..., allow_confirmed=True) for the whole save."""
    d = _read_dir(clip_id)
    seg = parse_clip_id(clip_id)
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    existing = read_annotation(clip_id)
    prelabels = read_prelabels(clip_id) or {}
    qc = prelabels.pop("qc", None)
    record = {
        "schema_version": 1,
        "tool": {"name": config.TOOL_NAME, "version": config.TOOL_VERSION},
        "clip_id": clip_id,
        "source": {
            "url": source_meta.get("url"),
            "video_id": seg["video_id"],
            "title": source_meta.get("title"),
            "channel": source_meta.get("channel"),
            "source_duration_s": source_meta.get("duration_s"),
            "downloaded_at": source_meta.get("downloaded_at"),
        },
        "segment": {
            "start_s": seg["start_s"],
            "end_s": seg["end_s"],
            "duration_s": round(seg["end_s"] - seg["start_s"], 3),
        },
        "media": {"clip_file": "clip.mp4", "audio_file": "audio_16k.wav", **clip_media},
        # QC verification (face/voice/speaker checks) gets its own section;
        # prelabels keeps only the model suggestions the annotator corrects.
        "prelabels": prelabels or None,
        "qc": qc,
        "labels": labels,
        "annotator": {"id": annotator_id},
        "timestamps": {
            "clip_created_at": (existing or {}).get("timestamps", {}).get("clip_created_at")
            or datetime.fromtimestamp((d / "clip.mp4").stat().st_mtime)
            .astimezone().isoformat(timespec="seconds"),
            "annotation_saved_at": now,
        },
    }
    if existing and existing.get("labels") != labels:
        record["history"] = [*existing.get("history", []), {
            "labels": existing.get("labels"), "annotator": existing.get("annotator"),
            "timestamps": existing.get("timestamps"),
        }]
    elif existing and "history" in existing:
        record["history"] = existing["history"]
    if not (d / "prelabels.json").is_file():
        _atomic_write_json(d / "prelabels.json", {})
    _atomic_write_json(d / "annotation.json", record)
    final = clip_dir(clip_id)
    if d != final:
        final.parent.mkdir(parents=True, exist_ok=True)
        d.rename(final)
    return record


def clip_summary(clip_id: str) -> dict:
    seg = parse_clip_id(clip_id)
    ann = read_annotation(clip_id)
    return {
        "clip_id": clip_id,
        "video_id": seg["video_id"],
        "start_s": seg["start_s"],
        "end_s": seg["end_s"],
        "annotated": ann is not None,
        "emotion": (ann or {}).get("labels", {}).get("emotion"),
    }


def list_clips(video_id: str | None = None) -> list[dict]:
    ids = set()
    workspace = config.DATA_DIR / "workspace"
    for folder in (config.OUTPUT_DIR, *workspace.glob("*/manual")):
        for d in folder.iterdir() if folder.is_dir() else ():
            name = d.name.removesuffix(".previous") if folder != config.OUTPUT_DIR else d.name
            match = CLIP_ID_RE.fullmatch(name)
            if match and (not video_id or match["video_id"] == video_id) and (d / "clip.mp4").is_file():
                ids.add(name)
    return [clip_summary(clip_id) for clip_id in sorted(ids)]


def delete_clip(clip_id: str) -> None:
    with manual_edit(clip_id, discard_draft=True) as draft:
        if not draft.is_dir():
            raise ClipNotFound(f"No such clip: {clip_id}")
        shutil.rmtree(draft)

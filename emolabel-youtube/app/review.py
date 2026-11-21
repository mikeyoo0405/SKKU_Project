"""Human review ledger and recoverable publication of confirmed batch candidates."""

import json
import math
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import config, store, youtube
from .candidate_media import ensure_media


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


ACCEPTED = {"accepted", "accepted_edited"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def _folder(clip_id):
    match = store.CLIP_ID_RE.fullmatch(clip_id)
    if not match or not VIDEO_ID.fullmatch(match["video_id"]):
        raise ValueError("Invalid candidate ID")
    folder = config.DATA_DIR / "workspace" / match["video_id"]
    if not folder.is_dir():
        raise NotFound("Unknown candidate")
    return folder


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _read_optional(path):
    try:
        data = _read(path)
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _rows(folder):
    path = folder / "reviews.json"
    ledger = _read(path) if path.is_file() else {}
    rows = {}
    # A machine checkpoint can be rebuilt; the human ledger is authoritative.
    state = _read_optional(folder / "state.json")
    source_fps = _read_optional(folder / "source.json").get("fps")
    if state.get("status") == "completed":
        for candidate in state.get("candidates", []):
            clip_id = candidate["clip_id"]
            match = store.CLIP_ID_RE.fullmatch(clip_id)
            if not match or match["video_id"] != folder.name:
                continue
            if clip_id not in ledger and (store.clip_dir(clip_id) / "annotation.json").is_file():
                continue  # Existing human output remains available in the manual clip list.
            flags = candidate.get("quality_flags", [])
            stamp = state.get("source_stamp") or []
            rows[clip_id] = {
                **candidate, "video_id": folder.name, "status": "pending", "revision": 0,
                "duration_s": round(candidate["end_s"] - candidate["start_s"], 3),
                "source_duration_s": state["duration_s"], "source_stamp": state.get("source_stamp"),
                "source_version": ":".join(str(value) for value in stamp),
                "fps": source_fps,
                "asr_transcript": candidate.get("transcript", ""), "history": [],
                "review": None, "parent_clip_id": None, "quality_flags": flags,
                "low_quality": bool(flags),
                "model_disagreement": bool(candidate.get("model_disagreement", False)),
            }
    for key, saved in ledger.items():
        rows[key] = saved if saved.get("fps") is not None or source_fps is None else {**saved, "fps": source_fps}
    return rows, ledger


def _recover_if_needed(folder):
    if (folder / "review-transaction").exists():
        _finish_transaction(folder)


def owns_candidate_locked(folder: Path, clip_id: str) -> bool:
    """Called by the manual API with the per-video write lock already held."""
    _recover_if_needed(folder)
    rows, _ = _rows(folder)
    return clip_id in rows


def _snapshot(folder):
    if (folder / "review-transaction").exists():
        with closing(sqlite3.connect(folder / "lock.sqlite", timeout=30)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            _recover_if_needed(folder)
    rows, _ = _rows(folder)
    return rows


def get_candidate(clip_id: str) -> dict:
    row = _snapshot(_folder(clip_id)).get(clip_id)
    if row is None:
        raise NotFound("Unknown candidate")
    return row


def list_candidates() -> list[dict]:
    workspace = config.DATA_DIR / "workspace"
    rows = []
    if workspace.is_dir():
        for folder in sorted(workspace.iterdir()):
            if folder.is_dir() and VIDEO_ID.fullmatch(folder.name):
                rows.extend(_snapshot(folder).values())
    return sorted(rows, key=lambda row: (not row["model_disagreement"], row["video_id"], row["start_s"]))


def _require_owner(directory, revision):
    path = directory / "annotation.json"
    if path.is_file():
        annotation = _read(path)
        owner = annotation.get("review") or {}
        if owner.get("origin") == "batch_review" and owner.get("revision") == revision:
            return
    raise Conflict("Output belongs to another annotation or revision")


def _finish_transaction(folder):
    """Roll a durable save forward; every rename can be retried after interruption."""
    txn = folder / "review-transaction"
    intent = _read(txn / "intent.json")
    old_id, new_id = intent["withdraw"], intent["publish"]
    if old_id:
        archive = folder / "quarantine" / intent["id"] / old_id
        if not archive.exists():
            source = store.clip_dir(old_id)
            _require_owner(source, intent["previous_revision"])
            archive.parent.mkdir(parents=True, exist_ok=True)
            source.rename(archive)
    if new_id:
        destination = store.clip_dir(new_id)
        staged = txn / "output"
        if staged.exists():
            if destination.exists():
                raise Conflict("An output already exists at the edited boundaries")
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged.rename(destination)
        else:
            record = _read(destination / "annotation.json")
            if record.get("review", {}).get("transaction_id") != intent["id"]:
                raise Conflict("Could not identify the published annotation")
    ledger = txn / "reviews.json"
    if ledger.exists():
        ledger.replace(folder / "reviews.json")
    # Remove the recovery marker atomically before cleaning up its files.
    completed = folder / "review-preparing"
    txn.rename(completed)
    shutil.rmtree(completed)


def _check_source(row):
    source = youtube.video_path(row["video_id"])
    try:
        stat = source.stat()
    except FileNotFoundError as exc:
        raise Conflict("Source media is missing; run the batch again") from exc
    if row["source_stamp"] != [stat.st_size, stat.st_mtime_ns]:
        raise Conflict("The source changed since this candidate was generated")
    return source


def _new_bounds(row, decision):
    start, end = decision["start_s"], decision["end_s"]
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError("Candidate boundaries must be finite")
    left, right = round(start * 1000), round(end * 1000)
    if (start < 0 or right > round(row["source_duration_s"] * 1000)
            or not config.MIN_CLIP_S * 1000 <= right - left <= config.MAX_CLIP_S * 1000):
        raise ValueError("Candidate boundaries are outside the permitted range")
    return left / 1000, right / 1000


def _stage_output(folder, stage, row, txid):
    source = _check_source(row)
    media = ensure_media(folder, row["clip_id"], source)
    stage.mkdir()
    for name in ("clip.mp4", "audio_16k.wav"):
        file = media / name
        if not file.is_file() or not file.stat().st_size:
            raise Conflict("Candidate media is incomplete")
        shutil.copy2(file, stage / name)
    decision = row["review"]
    try:
        meta = _read(folder / "source.json")
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Conflict("Source metadata is missing; run the batch again") from exc
    if not isinstance(meta, dict):
        raise Conflict("Source metadata is invalid; run the batch again")
    prelabels = {"asr": {"text": row["asr_transcript"]}}
    _write(stage / "prelabels.json", prelabels)
    labels = {key: decision.get(key) for key in
              ("emotion", "gender", "actor", "transcript", "notes", "confidence", "quality", "ambiguous")}
    labels.update(valence=None, arousal=None)
    annotation = {
        "schema_version": 1, "tool": {"name": config.TOOL_NAME, "version": config.TOOL_VERSION},
        "clip_id": row["clip_id"],
        "source": {key: meta.get(key) for key in ("url", "video_id", "title", "channel", "downloaded_at")},
        "segment": {key: row[key] for key in ("start_s", "end_s", "duration_s")},
        "media": {"clip_file": "clip.mp4", "audio_file": "audio_16k.wav"},
        "prelabels": prelabels, "qc": None, "labels": labels, "annotator": {"id": decision["annotator_id"]},
        "timestamps": {"annotation_saved_at": row["history"][-1]["at"],
                       "clip_created_at": datetime.fromtimestamp((media / "clip.mp4").stat().st_mtime,
                                                                 timezone.utc).isoformat()},
        "review": {**decision, "origin": "batch_review", "revision": row["revision"],
                   "parent_clip_id": row["parent_clip_id"], "transaction_id": txid},
    }
    annotation["source"].update(video_id=row["video_id"], source_duration_s=row["source_duration_s"])
    _write(stage / "annotation.json", annotation)


def save_decision(clip_id: str, payload: dict) -> dict:
    folder = _folder(clip_id)
    with closing(sqlite3.connect(folder / "lock.sqlite", timeout=30)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        _recover_if_needed(folder)
        rows, ledger = _rows(folder)
        if clip_id not in rows:
            raise NotFound("Unknown candidate")
        before = rows[clip_id]
        if before["status"] == "superseded" or before["revision"] != payload["revision"]:
            raise Conflict("Candidate has changed; reload the review")
        if (payload.get("source_version") is not None
                and payload["source_version"] != before["source_version"]):
            raise Conflict("Candidate source has changed; reload the review")
        status = payload["status"]
        if status not in ACCEPTED | {"rejected", "hold"}:
            raise ValueError("Unknown review status")
        if status == "rejected":
            start, end = before["start_s"], before["end_s"]
        else:
            start, end = _new_bounds(before, payload)
        new_id = store.make_clip_id(folder.name, start, end)
        moved = new_id != clip_id
        if moved and new_id in rows:
            raise Conflict("A candidate already uses these boundaries")
        if status == "accepted" and (moved or before["parent_clip_id"]
                                     or payload["transcript"].strip() != before["asr_transcript"].strip()):
            status = "accepted_edited"
        if moved or status in ACCEPTED:
            _check_source(before)
        withdraw = before["status"] in ACCEPTED
        if withdraw:
            _require_owner(store.clip_dir(clip_id), before["revision"])
        if status in ACCEPTED and (moved or not withdraw) and store.clip_dir(new_id).exists():
            raise Conflict("An output already exists at these boundaries")
        reason = payload["reason"].strip()
        if not reason and status in ACCEPTED:
            reason = "reviewer_confirmed" if status == "accepted" else "reviewer_confirmed_edits"
        decision = dict(payload, start_s=start, end_s=end, status=status, reason=reason)
        event = {**decision, "at": datetime.now(timezone.utc).isoformat(), "from_clip_id": clip_id,
                 "clip_id": new_id, "from_status": before["status"]}
        if moved:
            ledger[clip_id] = {
                **before, "status": "superseded", "superseded_by": new_id,
                "history": [*before["history"], dict(event, status="superseded")],
                "revision": before["revision"] + 1,
            }
        row = {
            **before, "clip_id": new_id, "revision": before["revision"] + 1, "start_s": start,
            "end_s": end, "duration_s": round(end - start, 3), "duration_ms": round((end - start) * 1000),
            "status": status, "transcript": decision["transcript"], "review": decision,
            "low_quality": bool(before["quality_flags"]) or decision.get("quality") == "C",
            "history": [*before["history"], event],
            "parent_clip_id": clip_id if moved else before["parent_clip_id"],
        }
        ledger[new_id] = row
        stage = folder / "review-preparing"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        txid = uuid4().hex
        if status in ACCEPTED:
            _stage_output(folder, stage / "output", row, txid)
        elif moved:
            ensure_media(folder, new_id, _check_source(row))
        _write(stage / "reviews.json", ledger)
        _write(stage / "intent.json", {
            "id": txid, "previous_revision": before["revision"],
            "withdraw": clip_id if withdraw else None, "publish": new_id if status in ACCEPTED else None,
        })
        stage.rename(folder / "review-transaction")
        _finish_transaction(folder)
        return row

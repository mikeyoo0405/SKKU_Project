"""Human review ledger and recoverable publication of confirmed batch candidates."""

import hashlib
import json
import math
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from . import config, store, youtube
from .candidate_media import clip_directory, ensure_media, media_ready


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


ACCEPTED = {"accepted", "accepted_edited"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def _folder(clip_id):
    try:
        segment = store.parse_clip_id(clip_id)
    except store.StoreError as exc:
        raise ValueError("Invalid candidate ID") from exc
    if not VIDEO_ID.fullmatch(segment["video_id"]):
        raise ValueError("Invalid candidate ID")
    folder = config.DATA_DIR / "workspace" / segment["video_id"]
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
    rows, _ = _rows(folder)
    return rows


def get_candidate(clip_id: str) -> dict:
    row = _snapshot(_folder(clip_id)).get(clip_id)
    if row is None:
        raise NotFound("Unknown candidate")
    return row


def _manual_source(video_id: str) -> tuple[Path, dict, dict]:
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Invalid video ID")
    source = youtube.video_path(video_id)
    if not source.is_file() or source.stat().st_size == 0:
        raise Conflict("원본 영상이 없습니다. URL을 먼저 불러오세요.")
    stat = source.stat()
    stamp = [stat.st_size, stat.st_mtime_ns]
    folder = config.DATA_DIR / "workspace" / video_id
    state = _read_optional(folder / "state.json")
    if state.get("source_stamp") and state["source_stamp"] != stamp:
        raise Conflict("원본이 변경됐습니다. 배치를 갱신한 뒤 다시 시도하세요.")
    try:
        meta = youtube.get_meta(video_id)
    except (youtube.YouTubeError, json.JSONDecodeError, UnicodeDecodeError):
        meta = _read_optional(folder / "source.json")
    duration = state.get("duration_s")
    if duration is None:
        duration = meta.get("duration_s")
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise Conflict("원본 길이를 확인할 수 없습니다. URL을 다시 불러오세요.")
    context = {"video_id": video_id, "source_duration_s": duration, "source_stamp": stamp,
               "source_version": ":".join(map(str, stamp)), "fps": meta.get("fps")}
    return source, meta, context


def _normalize_bounds(start: float, end: float, duration: float, message: str) -> tuple[float, float, int]:
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError(message)
    left, right = round(start * 1000), round(end * 1000)
    min_ms, max_ms = round(config.MIN_CLIP_S * 1000), round(config.MAX_CLIP_S * 1000)
    if (start < 0 or end > duration or right > math.floor(duration * 1000 + 1e-9)
            or not min_ms <= right - left <= max_ms):
        raise ValueError(message)
    return left / 1000, right / 1000, right - left


def _asr_draft_for_range(folder: Path, start_s: float, end_s: float, source_stamp: list[int]) -> str:
    state = _read_optional(folder / "state.json")
    if state.get("source_stamp") != source_stamp:
        return ""
    data = _read_optional(folder / "asr.json")
    segments = data.get("segments")
    if not isinstance(segments, list):
        return ""
    clip_span = end_s - start_s
    matched = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        s0, s1, text = seg.get("start"), seg.get("end"), seg.get("text")
        nsp = seg.get("no_speech_prob", 0.0)
        if (type(s0) not in (int, float) or type(s1) not in (int, float)
                or type(nsp) not in (int, float) or not isinstance(text, str)):
            continue
        if not (math.isfinite(s0) and math.isfinite(s1) and math.isfinite(nsp)):
            continue
        if s1 <= s0 or nsp > config.ASR_NO_SPEECH_THRESHOLD:
            continue
        cleaned = text.strip()
        if not cleaned:
            continue
        overlap = min(s1, end_s) - max(s0, start_s)
        if overlap > 0 and (overlap >= (s1 - s0) * 0.5 or overlap >= clip_span * 0.5):
            matched.append((s0, cleaned))
    matched.sort(key=lambda item: item[0])
    return " ".join(text for _, text in matched)


def candidate_audio_path(clip_id: str) -> Path:
    row = get_candidate(clip_id)
    if row["status"] in ACCEPTED:
        path = store.clip_dir(clip_id) / "audio_16k.wav"
        if path.is_file() and path.stat().st_size:
            return path
        raise NotFound("확정 음성 파일을 찾을 수 없습니다.")
    folder = config.DATA_DIR / "workspace" / row["video_id"]
    source = _check_source(row)
    if not media_ready(folder, clip_id, source):
        raise NotFound("후보 음성이 준비되지 않았습니다.")
    return clip_directory(folder, clip_id) / "audio_16k.wav"


def run_candidate_asr(wav_path: Path, clip_id: str) -> dict:
    from . import asr
    result = asr.transcribe(wav_path)
    return {
        "clip_id": clip_id,
        "text": str(result.get("text") or "").strip(),
        "model": str(result.get("model") or ""),
        "engine": str(result.get("engine") or ""),
    }


def transcribe_candidate(clip_id: str) -> dict:
    return run_candidate_asr(candidate_audio_path(clip_id), clip_id)


def manual_source_context(video_id: str) -> dict:
    return _manual_source(video_id)[2]


def add_manual_candidate(video_id: str, payload: dict) -> dict:
    """Publish extracted manual candidates only in the durable human ledger."""
    from .schemas import ManualCandidateRequest
    request = ManualCandidateRequest.model_validate({**payload, "video_id": video_id}).model_dump()
    _manual_source(video_id)
    folder = config.DATA_DIR / "workspace" / video_id
    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder / "lock.sqlite", timeout=30)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        _recover_if_needed(folder)
        source, meta, context = _manual_source(video_id)
        if request["source_version"] != context["source_version"]:
            raise Conflict("원본이 변경됐습니다. 영상을 다시 불러오세요.")
        duration = context["source_duration_s"]
        start, end, duration_ms = _normalize_bounds(
            request["start_s"], request["end_s"], duration, "구간의 범위 또는 길이를 확인하세요."
        )
        key = store.make_clip_id(video_id, start, end)
        rows, ledger = _rows(folder)
        machine_ids = {c.get("clip_id") for c in _read_optional(folder / "state.json").get("candidates", [])}
        draft = folder / "manual" / key
        if (key in rows or key in machine_ids or store.clip_dir(key).exists()
                or draft.exists() or draft.with_suffix(".previous").exists()):
            raise Conflict("같은 경계의 구간이 이미 있습니다. 기존 구간을 확인하세요.")
        directory = ensure_media(folder, key, source)
        if any(not (directory / name).is_file() or not (directory / name).stat().st_size
               for name in ("clip.mp4", "audio_16k.wav")):
            raise Conflict("새 구간의 영상과 음성이 준비되지 않았습니다.")
        _check_source(context)
        draft_text = _asr_draft_for_range(folder, start, end, context["source_stamp"])
        created = {"annotator_id": request["annotator_id"], "at": datetime.now(timezone.utc).isoformat(),
                   "active_seconds": request["active_seconds"]}
        row = {**context, "clip_id": key, "start_s": start, "end_s": end,
               "duration_s": round(end - start, 3), "duration_ms": duration_ms,
               "origin": "manual", "manual_creation": created, "status": "pending", "revision": 0,
               "transcript": draft_text, "asr_transcript": draft_text, "parent_clip_id": None, "review": None,
               "quality_flags": [], "low_quality": False, "model_disagreement": False,
               "history": [{**created, "status": "pending", "action": "manual_added", "clip_id": key}]}
        ledger[key] = row
        _persist_manual_ledger(folder, ledger, meta, video_id, duration, context)
        return row


def _persist_manual_ledger(folder, ledger, meta, video_id, duration, context):
    temporary = folder / f"manual-add-{uuid4().hex}.tmp"
    source_temporary = folder / f"manual-source-{uuid4().hex}.tmp"
    try:
        # A URL-only video has metadata in downloads rather than workspace.
        if not (folder / "source.json").is_file():
            source_meta = {**meta, "video_id": video_id, "duration_s": duration}
            _write(source_temporary, source_meta)
            source_temporary.replace(folder / "source.json")
        _write(temporary, ledger)
        _check_source(context)
        temporary.replace(folder / "reviews.json")
    finally:
        temporary.unlink(missing_ok=True)
        source_temporary.unlink(missing_ok=True)


@lru_cache(maxsize=512)
def _media_digest(path, signature):
    # The file's identity and modification times are part of the cache key.
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _matches_media(path, expected_stamp, expected_digest):
    stat = path.stat()
    stamp = [stat.st_size, stat.st_mtime_ns]
    return (stamp == expected_stamp and isinstance(expected_digest, str)
            and _media_digest(path, (*stamp, stat.st_ctime_ns, stat.st_ino)) == expected_digest)


def _emotion_results(folder, row):
    if row.get("origin") == "manual" or row["status"] != "pending" or row["revision"] or row["parent_clip_id"]:
        return None
    if row.get("transcript", "") != row["asr_transcript"]:
        return None
    directory = folder / "clips" / row["clip_id"]
    try:
        entry = _read_optional(directory / "emotion-latest.json")
        if (entry.get("schema_version") != 1 or entry.get("input_tag") != "original_asr"
                or entry.get("clip_id") != row["clip_id"] or entry.get("input_error")
                or entry.get("transcript") != row["asr_transcript"]
                or entry.get("input_transcript") != row["asr_transcript"]
                or entry.get("source_stamp") != row["source_stamp"]):
            return None
        if not _matches_media(youtube.video_path(folder.name), row["source_stamp"], entry["source_sha256"]):
            return None
        for kind, name in (("wav", "audio_16k.wav"), ("video", "clip.mp4")):
            if not _matches_media(directory / name, entry["media_stamp"][kind], entry["media_sha256"][kind]):
                return None
        records = {}
        for kind in ("text", "audio", "combined"):
            result = entry["results"][kind]
            if not isinstance(result, dict):
                return None
            status, label = result["status"], result.get("label")
            if status not in ("ok", "abstained", "failed"):
                return None
            if (status == "ok" and label not in config.EMOTIONS) or (status != "ok" and label is not None):
                return None
            if any(result.get(key) is not None and not isinstance(result[key], str)
                   for key in ("reason", "model_id", "version")):
                return None
            records[kind] = {key: result.get(key) for key in ("status", "label", "reason", "model_id", "version")}
        from .emotion_scores import validate_combined, validate_scored
        validate_combined(entry["results"])
        for kind in records:
            result = entry["results"][kind]
            if result.get("probabilities") is not None:
                if kind != "combined":
                    validate_scored(result, kind)
                records[kind]["probabilities"] = result["probabilities"]
        return records
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None


def blind_candidate(row):
    masked = dict(row, asr_transcript="", transcript="", review=None, history=[], model_disagreement=False)
    masked.pop("model_suggestions", None)
    return masked


def list_candidates(blind=False) -> list[dict]:
    workspace = config.DATA_DIR / "workspace"
    rows = []
    if workspace.is_dir():
        for folder in sorted(workspace.iterdir()):
            if folder.is_dir() and VIDEO_ID.fullmatch(folder.name):
                for row in _snapshot(folder).values():
                    row = {**row, "model_disagreement": False}
                    row.pop("model_suggestions", None)
                    if blind:
                        row = blind_candidate(row)
                    else:
                        results = _emotion_results(folder, row)
                        if results:
                            row["model_suggestions"] = results
                            labels = [results[kind]["label"] for kind in ("text", "audio")]
                            row["model_disagreement"] = all(labels) and labels[0] != labels[1]
                    rows.append(row)
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
    start, end, _ = _normalize_bounds(
        decision["start_s"], decision["end_s"], row["source_duration_s"],
        "Candidate boundaries are outside the permitted range",
    )
    return start, end


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
        "candidate_origin": row.get("origin", "automatic"),
        "manual_creation": row.get("manual_creation"),
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
        transcript_edited = (before.get("origin") != "manual"
                             and payload["transcript"].strip() != before["asr_transcript"].strip())
        if status == "accepted" and (moved or before["parent_clip_id"] or transcript_edited):
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

"""Freeze original batch inputs and archive independent local-model comparisons."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import uuid4

from app import candidate_media, config, store, youtube


FILES = {"wav": "audio_16k.wav", "video": "clip.mp4"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
SHA256 = re.compile(r"[a-f0-9]{64}")


def _read(path):
    return json.loads(path.read_text("utf-8-sig"))


def _save(path, data, *, exclusive=False):
    """Expose only complete JSON; an exported list cannot overwrite a different one."""
    payload = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        name = Path(tmp.name)
        try:
            tmp.write(payload)
        except BaseException:
            tmp.close()
            name.unlink(missing_ok=True)
            raise
    try:
        if exclusive:
            try:
                if os.name == "nt":
                    # On Windows rename is atomic and refuses an existing destination.
                    name.rename(path)
                else:
                    os.link(name, path)
            except FileExistsError:
                if path.read_bytes() != payload:
                    raise
        else:
            name.replace(path)
    finally:
        name.unlink(missing_ok=True)


def _stamp(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def _hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _checked_path(root, path):
    # A copied workspace can contain directory junctions or file symlinks.
    if path.resolve() != root.resolve() / path.relative_to(root):
        raise ValueError("linked workspace paths are not valid frozen inputs")
    return path


@contextmanager
def _locked(folder):
    _checked_path(folder.parent, folder)
    lock_path = _checked_path(folder, folder / "lock.sqlite")
    if not folder.is_dir():
        raise ValueError("candidate workspace is missing")
    with closing(sqlite3.connect(lock_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        yield


def _current_source(folder, state):
    source = youtube.video_path(folder.name)
    _checked_path(source.parent, source)
    if not isinstance(state, dict):
        raise ValueError("invalid batch checkpoint")
    if state.get("status") != "completed" or state.get("source_stamp") != _stamp(source):
        raise ValueError("source or batch checkpoint changed")
    return source


def _candidate_directory(folder, key):
    directory = candidate_media.clip_directory(folder, key)
    for name in (*FILES.values(), "media.json", "emotion-latest.json"):
        _checked_path(folder, directory / name)
    return directory


def _location(workspace, key):
    if not isinstance(key, str):
        raise ValueError("invalid clip_id")
    try:
        segment = store.parse_clip_id(key)
    except store.StoreError as exc:
        raise ValueError("invalid clip_id") from exc
    vid = segment["video_id"]
    if not VIDEO_ID.fullmatch(vid):
        raise ValueError("invalid video ID")
    folder = workspace / vid
    directory = candidate_media.clip_directory(folder, key)
    return folder, directory


def export_manifest(workspace, path):
    destination = path.resolve()
    if destination.is_relative_to(config.OUTPUT_DIR.resolve()):
        raise ValueError("use a manifest path outside confirmed output")
    if destination.is_relative_to(workspace.resolve()):
        relative = destination.relative_to(workspace.resolve())
        if len(relative.parts) > 1 and (VIDEO_ID.fullmatch(relative.parts[0]) or relative.parts[0] == "emotion-runs"):
            raise ValueError("use a manifest path outside candidate and run directories")
    rows = []
    for folder in sorted(workspace.glob("*")):
        if not folder.is_dir() or not VIDEO_ID.fullmatch(folder.name):
            continue
        with _locked(folder):
            checkpoint = _checked_path(folder, folder / "state.json")
            if not checkpoint.is_file():
                continue
            state = _read(checkpoint)
            if not isinstance(state, dict):
                raise ValueError(f"{folder.name}: invalid batch checkpoint")
            if state.get("status") != "completed":
                continue
            source = _current_source(folder, state)
            source_hash = _hash(source)
            for candidate in state["candidates"]:
                key = candidate["clip_id"]
                directory = _candidate_directory(folder, key)
                if not candidate_media.media_ready(folder, key, source):
                    raise ValueError(f"{key}: candidate media is incomplete; run extraction again")
                rows.append({"clip_id": key, "transcript": candidate["transcript"],
                             "source_sha256": source_hash,
                             "media_sha256": {kind: _hash(directory / name) for kind, name in FILES.items()}})
    manifest = {"schema_version": 1, "input_tag": "original_asr", "candidates": rows}
    _validate_manifest(workspace, manifest)
    _save(path, manifest, exclusive=True)
    return {"manifest": str(path), "candidates": len(rows)}


def _validate_manifest(workspace, manifest):
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "input_tag", "candidates"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest.get("input_tag") != "original_asr"
            or not isinstance(manifest.get("candidates"), list) or not manifest["candidates"]):
        raise ValueError("expected a nonempty original_asr candidate manifest (schema 1)")
    keys = set()
    for row in manifest["candidates"]:
        if not isinstance(row, dict) or set(row) != {"clip_id", "transcript", "media_sha256", "source_sha256"}:
            raise ValueError("expected clip_id, transcript, media_sha256 and source_sha256")
        key = row.get("clip_id")
        _location(workspace, key)
        hashes = row.get("media_sha256")
        if (key in keys or not isinstance(row.get("transcript"), str)
                or not isinstance(hashes, dict) or set(hashes) != set(FILES)
                or not all(isinstance(value, str) and SHA256.fullmatch(value)
                           for value in [row.get("source_sha256"), *hashes.values()])):
            raise ValueError(f"{key}: duplicate ID or invalid frozen input")
        keys.add(key)
    return keys


def _check_current(folder, directory, frozen):
    """Return local stamps for the verified frozen input, independent of other machines' mtimes."""
    state = _read(_checked_path(folder, folder / "state.json"))
    source = _current_source(folder, state)
    key = frozen["clip_id"]
    _candidate_directory(folder, key)
    if not any(isinstance(item, dict) and item.get("clip_id") == key and item.get("transcript") == frozen["transcript"]
               for item in state.get("candidates") or []):
        raise ValueError("original candidate or ASR transcript changed")
    if _hash(source) != frozen["source_sha256"] or not candidate_media.media_ready(folder, key, source):
        raise ValueError("source or extracted media changed")
    stamps = {}
    for kind, name in FILES.items():
        file = directory / name
        if _hash(file) != frozen["media_sha256"][kind]:
            raise ValueError(f"{kind} fingerprint mismatch")
        stamps[kind] = _stamp(file)
    return {"source_stamp": state["source_stamp"], "media_stamp": stamps}


def run_manifest(workspace, path, *, run_tag="", edited_transcripts=None, classify=None):
    if classify is None:
        from app.emotion import classify

    manifest = _read(path)
    keys = _validate_manifest(workspace, manifest)
    edits = {}
    tag = "original_asr"
    if edited_transcripts is not None:
        edits = _read(edited_transcripts)
        if (not isinstance(edits, dict) or set(edits) != keys
                or not all(isinstance(value, str) and value.strip() for value in edits.values())):
            raise ValueError("edited transcripts must map every manifest clip_id to a nonempty string")
        tag = "corrected_transcript"

    run_id = uuid4().hex
    archive = _checked_path(workspace, workspace / "emotion-runs" / run_id)
    archive.mkdir(parents=True)
    _save(archive / "manifest.json", manifest)
    summary = {"run_id": run_id, "run_tag": run_tag, "input_tag": tag, "candidates": len(keys),
               "started_at": datetime.now(timezone.utc).isoformat(), "input_errors": 0}
    for frozen in manifest["candidates"]:
        key = frozen["clip_id"]
        folder, directory = _location(workspace, key)
        transcript = edits.get(key, frozen["transcript"])
        entry = {"schema_version": 1, **frozen, "run_id": run_id, "run_tag": run_tag,
                 "input_tag": tag, "input_transcript": transcript, "auto_accept": False}
        try:
            # Snapshot the matched bytes; a reviewer or another batch can work during inference.
            with TemporaryDirectory(dir=archive) as temporary:
                snapshot = Path(temporary)
                with _locked(folder):
                    _check_current(folder, directory, frozen)
                    for kind, name in FILES.items():
                        shutil.copyfile(directory / name, snapshot / name)
                        if _hash(snapshot / name) != frozen["media_sha256"][kind]:
                            raise ValueError(f"{kind} changed during input preparation")
                inputs = {"clip_id": key, "transcript": transcript, "input_tag": tag,
                          "wav_path": str(snapshot / FILES["wav"]), "video_path": str(snapshot / FILES["video"])}
                entry["results"] = classify(inputs)
            with _locked(folder):
                entry.update(_check_current(folder, directory, frozen))
                _save(archive / f"{key}.json", entry)
                if tag == "original_asr":
                    _save(directory / "emotion-latest.json", entry)
        except (ValueError, OSError, sqlite3.Error) as exc:
            entry["input_error"] = str(exc)
            summary["input_errors"] += 1
            _save(archive / f"{key}.json", entry)
    _save(archive / "summary.json", summary)
    return summary

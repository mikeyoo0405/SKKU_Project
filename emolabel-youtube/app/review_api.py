"""HTTP interface for batch candidate playback and human decisions."""
import sqlite3
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import candidate_media, config, media, review, schemas, store, youtube

router = APIRouter(prefix="/api/review/candidates")


@contextmanager
def _http_errors():
    try:
        yield
    except review.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except sqlite3.OperationalError as exc:
        if not store.is_lock_conflict(exc):
            raise
        raise HTTPException(409, "배치 또는 다른 검수가 진행 중입니다. 잠시 후 다시 시도하세요.") from exc
    except review.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (store.StoreError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except media.MediaError as exc:
        raise HTTPException(500, str(exc)) from exc


def _with_urls(row):
    prefix = f"/api/review/candidates/{row['clip_id']}"
    source = youtube.video_path(row["video_id"])
    try:
        stat = source.stat()
        available = row.get("source_stamp") == [stat.st_size, stat.st_mtime_ns]
    except FileNotFoundError:
        available = False
    return {**row, "media_url": prefix + "/media", "audio_url": prefix + "/audio",
            "source_url": f"/media/{row['video_id']}.mp4" if available else None}


@router.get("")
def candidates():
    with _http_errors():
        return [_with_urls(row) for row in review.list_candidates()]


@router.post("/{clip_id}")
def decide(clip_id: str, decision: schemas.ReviewDecision):
    with _http_errors():
        return _with_urls(review.save_decision(clip_id, decision.model_dump()))


@router.get("/{clip_id}/{kind}")
def playback(clip_id: str, kind: Literal["media", "audio"]):
    with _http_errors():
        row = review.get_candidate(clip_id)
        filename = "audio_16k.wav" if kind == "audio" else "clip.mp4"
        mime = "audio/wav" if kind == "audio" else "video/mp4"
        if row["status"] in review.ACCEPTED:
            path = store.clip_dir(clip_id) / filename
            if path.is_file():
                return FileResponse(path, media_type=mime)
            raise HTTPException(404, "확정 미디어 파일을 찾을 수 없습니다.")
        video_id = row["video_id"]
        folder = config.DATA_DIR / "workspace" / video_id
        if not _with_urls(row)["source_url"] or not candidate_media.media_ready(folder, clip_id, youtube.video_path(video_id)):
            raise HTTPException(404, "후보 미디어가 준비되지 않았습니다. 배치를 다시 실행하세요.")
        directory = candidate_media.clip_directory(folder, clip_id)
        if kind == "audio":
            return FileResponse(directory / "audio_16k.wav", media_type="audio/wav")
        return FileResponse(directory / "clip.mp4", media_type="video/mp4")

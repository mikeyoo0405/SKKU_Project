"""FastAPI app: serves the annotation UI, media (with Range support), and the API."""

import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import asr, config, gender, media, qc, schemas, store, youtube
from .review_api import router as review_router, summary_router as review_summary_router

config.ensure_dirs()

app = FastAPI(title=config.TOOL_NAME, version=config.TOOL_VERSION)
app.include_router(review_router)
app.include_router(review_summary_router)


@app.middleware("http")
async def disable_ui_cache(request, call_next):
    """Keep local UI edits visible while the annotation tool is under development."""
    response = await call_next(request)
    if request.url.path in {"/", "/static/style.css", "/static/app.js", "/static/review.mjs"}:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config():
    return {
        "tool_version": config.TOOL_VERSION,
        "emotions": [{"value": e, "label": config.EMOTION_LABELS[e]} for e in config.EMOTIONS],
        "gender_options": [{"value": g, "label": config.GENDER_LABELS[g]} for g in config.GENDER_OPTIONS],
        "valence_scale": config.VALENCE_SCALE,
        "arousal_scale": config.AROUSAL_SCALE,
        "min_clip_s": config.MIN_CLIP_S,
        "max_clip_s": config.MAX_CLIP_S,
        "gender_confidence_threshold": config.GENDER_CONFIDENCE_THRESHOLD,
    }


@app.post("/api/videos")
def load_video(req: schemas.LoadRequest):
    try:
        meta = youtube.load_video(req.url.strip())
    except youtube.YouTubeError as e:
        msg = str(e)
        code = 422 if "max allowed" in msg else 400
        raise HTTPException(code, msg)
    except media.MediaError as e:
        raise HTTPException(500, str(e))
    return {
        **meta,
        "media_url": f"/media/{meta['video_id']}.mp4",
        "clips": store.list_clips(meta["video_id"]),
    }


@app.get("/api/videos/{video_id}/peaks")
def get_peaks(video_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(422, "Invalid video ID")
    try:
        return youtube.get_peaks(video_id)
    except youtube.YouTubeError as e:
        raise HTTPException(404, str(e))
    except media.MediaError as e:
        raise HTTPException(500, str(e))


@app.post("/api/videos/{video_id}/extract")
def extract_clip(video_id: str, req: schemas.ExtractRequest):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(400, "Invalid YouTube video ID")
    try:
        meta = youtube.get_meta(video_id)
    except youtube.YouTubeError as e:
        raise HTTPException(404, str(e))
    if req.end_s > meta["duration_s"] + 0.05:
        raise HTTPException(422, f"end_s {req.end_s:.2f} exceeds video duration {meta['duration_s']:.2f}")

    clip_id = store.make_clip_id(video_id, req.start_s, req.end_s)
    dur = req.end_s - req.start_s
    try:
        store.extract_draft(clip_id, youtube.video_path(video_id), req.overwrite)
    except store.StoreError as e:
        raise HTTPException(e.status_code, str(e))
    except media.MediaError as e:
        raise HTTPException(500, str(e))
    return {
        "clip_id": clip_id,
        "clip_url": f"/api/clips/{clip_id}/media",
        "duration_s": round(dur, 3),
    }


@app.post("/api/clips/{clip_id}/prelabel")
def prelabel_clip(clip_id: str):
    try:
        with store.manual_edit(clip_id) as draft:
            wav = draft / "audio_16k.wav"
            if not wav.is_file() or not wav.stat().st_size:
                raise HTTPException(404, f"No extracted audio for clip {clip_id}")
            prelabels = {
                "asr": asr.transcribe(wav),
                "gender": gender.predict(wav),
                "qc": qc.run(draft / "clip.mp4", wav),
            }
            store.write_prelabels(clip_id, prelabels)
            return prelabels
    except store.StoreError as e:
        raise HTTPException(e.status_code, str(e))


@app.get("/api/clips/{clip_id}/media")
def clip_media(clip_id: str):
    try:
        p = store.clip_video_path(clip_id)
    except store.StoreError as e:
        raise HTTPException(400, str(e))
    if not p.exists():
        raise HTTPException(404, f"No such clip: {clip_id}")
    return FileResponse(p, media_type="video/mp4")


@app.post("/api/clips/{clip_id}/annotation")
def save_annotation(clip_id: str, req: schemas.AnnotationIn):
    try:
        with store.manual_edit(clip_id, allow_confirmed=True) as directory:
            store.require_media(directory)
            seg = store.parse_clip_id(clip_id)
            source_meta = youtube.get_meta(seg["video_id"])
            probe = media.probe(directory / "clip.mp4")
            clip_media = {key: probe[key] for key in ("video_codec", "audio_codec", "width", "height", "fps")}
            labels = req.model_dump(exclude={"annotator_id"})
            for key in ("transcript", "actor", "notes"):
                labels[key] = labels[key].strip()
            return store.save_annotation(clip_id, labels, req.annotator_id.strip(), source_meta, clip_media)
    except store.StoreError as e:
        raise HTTPException(e.status_code, str(e))
    except youtube.YouTubeError as e:
        raise HTTPException(404, str(e))
    except media.MediaError as e:
        raise HTTPException(500, str(e))


@app.get("/api/clips")
def list_clips(video_id: str | None = None):
    return store.list_clips(video_id)


@app.delete("/api/clips/{clip_id}", status_code=204)
def delete_clip(clip_id: str):
    try:
        store.delete_clip(clip_id)
    except store.StoreError as e:
        raise HTTPException(e.status_code, str(e))


# Mounted last so explicit routes win. /media serves downloads with Range support.
app.mount("/media", StaticFiles(directory=config.DOWNLOADS_DIR), name="media")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

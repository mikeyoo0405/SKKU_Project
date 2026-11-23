"""HTTP contracts for the batch review queue, media and explicit human decisions."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config
from app.main import app


class ReviewApiTests(unittest.TestCase):
    video_id = "DGwuCn-EDfQ"
    clip_id = video_id + "_0001000_0005000"

    def setUp(self):
        self.client = self.enterContext(TestClient(app))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "DATA_DIR", root))
        self.enterContext(patch.object(config, "OUTPUT_DIR", root / "output"))
        self.enterContext(patch.object(config, "DOWNLOADS_DIR", root / "downloads"))
        self.endpoint = "/api/review/candidates/" + self.clip_id
        self.payload = dict(status="accepted", revision=0, start_s=1, end_s=5,
                            annotator_id="pilot", active_seconds=4.5, transcript="안녕하세요",
                            emotion="happy", confidence=3, quality="A", ambiguous=0,
                            verified=True, blind=False)

    def test_empty_installation_has_an_empty_queue(self):
        result = self.client.get("/api/review/candidates")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), [])

    def test_sqlite_conflicts_and_storage_failures_have_distinct_responses(self):
        with TestClient(app, raise_server_exceptions=False) as client:
            for code, status in ((sqlite3.SQLITE_BUSY, 409), (sqlite3.SQLITE_LOCKED_SHAREDCACHE, 409),
                                 (sqlite3.SQLITE_READONLY, 500), (sqlite3.SQLITE_IOERR, 500)):
                error = sqlite3.OperationalError("database error")
                error.sqlite_errorcode = code
                with self.subTest(code=code), patch("app.review.list_candidates", side_effect=error):
                    self.assertEqual(client.get("/api/review/candidates").status_code, status)

    def test_invalid_decisions_are_rejected_before_storage(self):
        for change in ({"confidence": None}, {"verified": False}, {"annotator_id": "  "},
                       {"transcript": " "}, {"end_s": 1}, {"active_seconds": -1},
                       {"start_s": False}, {"end_s": True}, {"active_seconds": True},
                       {"source_version": ""},
                       {"emotion": "other"}, {"ambiguous": 2}, {"revision": -1},
                       {"status": "hold", "reason": " "}):
            with self.subTest(change=change):
                result = self.client.post(self.endpoint, json=self.payload | change)
                self.assertEqual(result.status_code, 422)

    def test_unknown_candidate(self):
        self.assertEqual(self.client.post(self.endpoint, json=self.payload).status_code, 404)

    def test_media_urls_only_serve_candidate_audio_and_video(self):
        path = config.DATA_DIR / "workspace" / self.video_id / "clips" / self.clip_id
        path.mkdir(parents=True)
        (path / "clip.mp4").write_bytes(b"clip")
        (path / "audio_16k.wav").write_bytes(b"audio")
        config.DOWNLOADS_DIR.mkdir()
        source = config.DOWNLOADS_DIR / f"{self.video_id}.mp4"
        source.write_bytes(b"source")
        stamp = [source.stat().st_size, source.stat().st_mtime_ns]
        row = dict(clip_id=self.clip_id, video_id=self.video_id, start_s=1, end_s=5)
        (path.parent.parent / "state.json").write_text(json.dumps({
            "status": "completed", "source_stamp": stamp, "duration_s": 10, "candidates": [row],
        }), encoding="utf-8")
        result = self.client.get("/api/review/candidates").json()[0]
        self.assertEqual(result["source_url"], f"/media/{self.video_id}.mp4")
        self.assertEqual(self.client.get(result["media_url"]).status_code, 404)
        (path / "media.json").write_text(json.dumps({
            "source_stamp": [source.stat().st_size, source.stat().st_mtime_ns],
            "sizes": [4, 5],
        }), encoding="utf-8")
        for field, contents in (("media_url", b"clip"), ("audio_url", b"audio")):
            self.assertEqual(self.client.get(result[field]).content, contents)
        self.assertEqual(self.client.get(self.endpoint + "/annotation.json").status_code, 422)
        (path.parent.parent / "source.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.client.post(self.endpoint, json=self.payload).status_code, 200)
        source.write_bytes(b"replacement source")
        saved = self.client.get("/api/review/candidates").json()[0]
        self.assertIsNone(saved["source_url"])
        self.assertEqual(self.client.get(saved["media_url"]).content, b"clip")
        withdrawal = self.payload | {"status": "hold", "revision": 1, "reason": "다시 확인"}
        self.assertEqual(self.client.post(self.endpoint, json=withdrawal).status_code, 200)
        self.assertEqual(self.client.get(saved["media_url"]).status_code, 404)

    def test_manual_candidate_source_creation_conflicts_and_playback(self):
        self.assertEqual(self.client.get("/api/review/candidates/source/bad-id").status_code, 422)
        self.assertEqual(self.client.get(f"/api/review/candidates/source/{self.video_id}").status_code, 409)
        config.DOWNLOADS_DIR.mkdir()
        source = config.DOWNLOADS_DIR / f"{self.video_id}.mp4"
        source.write_bytes(b"source")
        (config.DOWNLOADS_DIR / f"{self.video_id}.info.json").write_text(
            json.dumps({"video_id": self.video_id, "duration_s": 12.0, "fps": 30}), encoding="utf-8"
        )
        ctx = self.client.get(f"/api/review/candidates/source/{self.video_id}")
        self.assertEqual(ctx.status_code, 200)
        source_version = ctx.json()["source_version"]

        def fake_media(folder, clip, src):
            directory = folder / "clips" / clip
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "clip.mp4").write_bytes(b"mclip")
            (directory / "audio_16k.wav").write_bytes(b"maudio")
            stat = src.stat()
            (directory / "media.json").write_text(
                json.dumps({"source_stamp": [stat.st_size, stat.st_mtime_ns], "sizes": [5, 6]}),
                encoding="utf-8",
            )
            return directory

        payload = {"video_id": self.video_id, "start_s": 6, "end_s": 9,
                   "source_version": source_version, "annotator_id": "pilot",
                   "active_seconds": 2.0, "blind": False}
        with patch("app.review.ensure_media", side_effect=fake_media):
            created = self.client.post("/api/review/candidates", json=payload)
            self.assertEqual(created.status_code, 200)
            row = created.json()
            self.assertEqual(row["origin"], "manual")
            self.assertEqual(row["status"], "pending")
            self.assertEqual(self.client.get(row["media_url"]).content, b"mclip")
            self.assertEqual(self.client.get(row["audio_url"]).content, b"maudio")
            self.assertEqual(self.client.post("/api/review/candidates", json=payload).status_code, 409)
            self.assertEqual(
                self.client.post("/api/review/candidates", json=payload | {"start_s": 9, "end_s": 6}).status_code,
                422,
            )
            fake_asr = {"engine": "faster-whisper", "model": "medium", "language": "ko", "text": "자동 대사", "segments": []}
            with patch("app.asr.transcribe", return_value=fake_asr):
                transcribed = self.client.post(f"/api/review/candidates/{row['clip_id']}/transcribe")
                self.assertEqual(transcribed.status_code, 200)
                self.assertEqual(transcribed.json()["text"], "자동 대사")
            with patch("app.asr.transcribe", side_effect=RuntimeError("asr unavailable")):
                failed = self.client.post(f"/api/review/candidates/{row['clip_id']}/transcribe")
                self.assertEqual(failed.status_code, 503)

    def test_workspace_summary_endpoint_and_blind_mode(self):
        normal = self.client.get("/api/review/summary")
        self.assertEqual(normal.status_code, 200)
        body = normal.json()
        self.assertEqual(body["scope"], "current_review_workspace")
        self.assertFalse(body["blind"])
        self.assertEqual(body["review"]["total_candidates"], 0)
        self.assertIsNone(body["latest_batch_run"])

        blind = self.client.get("/api/review/summary?blind=true")
        self.assertEqual(blind.status_code, 200)
        self.assertTrue(blind.json()["blind"])
        self.assertIsNone(blind.json()["latest_emotion_run"])

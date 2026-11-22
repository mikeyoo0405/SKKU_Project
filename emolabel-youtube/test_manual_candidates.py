"""Manual additions are independent durable review candidates, never ASR outputs."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
from unittest.mock import patch

from app import config, review, schemas, store
import test_review


class ManualCandidateTests(unittest.TestCase):
    setUp = test_review.ReviewTests.setUp
    write_state = test_review.ReviewTests.write_state
    save = test_review.ReviewTests.save

    def addition(self, **updates):
        payload = dict(start_s=6, end_s=9, annotator_id="creator", active_seconds=2.5,
                       source_version=":".join(map(str, self.state["source_stamp"])), blind=False)
        return review.add_manual_candidate(self.video, payload | updates)

    def test_independent_pending_row_and_unchanged_machine_inputs(self):
        before = (self.folder / "state.json").read_bytes()
        row = self.addition()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["origin"], "manual")
        self.assertEqual(row["asr_transcript"], "")
        self.assertEqual(row["transcript"], "")
        self.assertIsNone(row["review"])
        self.assertIsNone(row["parent_clip_id"])
        self.assertEqual(row["manual_creation"]["annotator_id"], "creator")
        self.assertEqual(row["manual_creation"]["active_seconds"], 2.5)
        self.assertTrue(row["manual_creation"]["at"])
        self.assertEqual((self.folder / "state.json").read_bytes(), before)
        self.assertFalse(store.clip_dir(row["clip_id"]).exists())
        self.assertEqual(len(review.list_candidates()), 2)
        self.assertNotIn("model_suggestions", review.get_candidate(row["clip_id"]))

    def test_duplicate_sources_outputs_and_drafts_are_preserved(self):
        with self.assertRaises(review.Conflict):
            self.addition(start_s=1, end_s=5)
        row = self.addition()
        with self.assertRaises(review.Conflict):
            self.addition(start_s=6.0001)
        for directory in (store.clip_dir(store.make_clip_id(self.video, 7, 10)),
                          self.folder / "manual" / store.make_clip_id(self.video, 8, 11)):
            directory.mkdir(parents=True)
        for start, end in ((7, 10), (8, 11)):
            with self.assertRaises(review.Conflict):
                self.addition(start_s=start, end_s=end)
        self.assertEqual(review.get_candidate(row["clip_id"])["status"], "pending")

    def test_invalid_and_stale_inputs_publish_nothing(self):
        for change in ({"start_s": -1}, {"start_s": True}, {"end_s": 15}, {"end_s": 6},
                       {"end_s": 6.01}, {"end_s": float("inf")}, {"start_s": float("nan")},
                       {"source_version": "old"}, {"annotator_id": " "}, {"active_seconds": -1}):
            with self.subTest(change=change), self.assertRaises((ValueError, review.Conflict)):
                self.addition(**change)
        self.assertFalse((self.folder / "reviews.json").exists())
        self.assertEqual(len(review.list_candidates()), 1)

    def test_no_batch_state_uses_download_metadata_and_survives_batch_rebuild(self):
        (self.folder / "state.json").unlink()
        (self.folder / "source.json").unlink()
        from app import youtube
        youtube.info_path(self.video).write_text(json.dumps({"video_id": self.video, "duration_s": 12, "title": "video"}), encoding="utf-8")
        row = self.addition()
        self.state.update(status="no_candidates", candidates=[])
        self.write_state()
        self.assertEqual(review.list_candidates()[0]["clip_id"], row["clip_id"])
        self.save(clip=row["clip_id"], start_s=6, end_s=9, transcript="manual words")
        annotation = store.read_annotation(row["clip_id"])
        self.assertEqual(annotation["candidate_origin"], "manual")
        self.assertEqual(annotation["manual_creation"]["annotator_id"], "creator")
        self.assertEqual(annotation["review"]["origin"], "batch_review")
        self.save(clip=row["clip_id"], revision=1, start_s=6, end_s=9, status="hold", reason="listen")
        self.assertFalse(store.clip_dir(row["clip_id"]).exists())

    def test_extraction_or_write_failure_and_source_race_never_publish_candidate(self):
        for target in ("app.review.ensure_media", "app.review._write"):
            with patch(target, side_effect=OSError("disk failure")), self.assertRaises(OSError):
                self.addition()
            self.assertFalse((self.folder / "reviews.json").exists())
        original = self.encode.side_effect
        def changed(folder, key, source):
            result = original(folder, key, source)
            source.write_bytes(b"changed original source")
            return result
        with patch("app.review.ensure_media", side_effect=changed), self.assertRaises(review.Conflict):
            self.addition()
        self.assertFalse((self.folder / "reviews.json").exists())

    def test_concurrent_identical_creation_has_one_winner(self):
        def create(_):
            try:
                return self.addition()["clip_id"]
            except review.Conflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(create, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_failure_preserves_existing_ledger_and_retry_succeeds(self):
        row = self.addition()
        before = (self.folder / "reviews.json").read_bytes()
        with patch("app.review._write", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.addition(start_s=9, end_s=11)
        self.assertEqual((self.folder / "reviews.json").read_bytes(), before)
        self.assertEqual(review.get_candidate(row["clip_id"])["revision"], 0)
        self.assertEqual(self.addition(start_s=9, end_s=11)["status"], "pending")

    def test_manual_candidate_does_not_enter_original_asr_manifest(self):
        from tools.emotion_batch import export_manifest
        directory = self.encode.side_effect(self.folder, self.clip, self.source)
        stamp = self.state["source_stamp"]
        (directory / "media.json").write_text(json.dumps({"source_stamp": stamp,
            "sizes": [(directory / name).stat().st_size for name in ("clip.mp4", "audio_16k.wav")]}), encoding="utf-8")
        workspace = self.folder.parent
        before, after = workspace / "before.json", workspace / "after.json"
        export_manifest(workspace, before)
        self.addition()
        export_manifest(workspace, after)
        self.assertEqual(before.read_bytes(), after.read_bytes())

    def test_normalized_end_never_exceeds_fractional_source_duration(self):
        self.state["duration_s"] = 10.0006
        self.write_state()
        with self.assertRaises(ValueError):
            self.addition(start_s=6, end_s=10.0006)

    def test_manual_candidate_history_survives_edit_reject_hold_and_accept(self):
        row = self.addition()
        key = row["clip_id"]
        for revision, status in enumerate(("hold", "rejected", "accepted")):
            self.save(clip=key, start_s=6, end_s=9, revision=revision, status=status, reason="reviewed")
        edited = self.save(clip=key, start_s=5.5, end_s=9, revision=3)
        self.assertEqual(edited["origin"], "manual")
        self.assertEqual(edited["manual_creation"], row["manual_creation"])
        self.assertEqual(review.get_candidate(key)["status"], "superseded")
        self.state.update(status="no_candidates", candidates=[])
        self.write_state()
        self.assertEqual(review.get_candidate(edited["clip_id"])["review"]["emotion"], "happy")

    def test_api_schema_and_route_without_http_runtime(self):
        from app.review_api import add_candidate
        request = schemas.ManualCandidateRequest(video_id=self.video, start_s=6, end_s=9,
            source_version=":".join(map(str, self.state["source_stamp"])), annotator_id="creator", blind=True)
        result = add_candidate(request)
        self.assertEqual(result["origin"], "manual")
        self.assertTrue(result["audio_url"].endswith("/audio"))
        self.assertNotIn("model_suggestions", result)
        with self.assertRaises(ValueError):
            schemas.ManualCandidateRequest.model_validate(request.model_dump() | {"end_s": float("inf")})

    def test_manual_accept_stays_accepted_until_boundaries_or_explicit_edit(self):
        row = self.addition()
        saved = self.save(clip=row["clip_id"], start_s=6, end_s=9, transcript="직접 적은 대사", status="accepted")
        self.assertEqual(saved["status"], "accepted")
        self.assertEqual(store.read_annotation(row["clip_id"])["review"]["status"], "accepted")
        edited = self.save(clip=row["clip_id"], revision=1, start_s=6, end_s=9.5,
                           transcript="직접 적은 대사", status="accepted")
        self.assertEqual(edited["status"], "accepted_edited")

    def test_partial_overlap_is_allowed_and_never_receives_model_suggestions(self):
        overlap = self.addition(start_s=3, end_s=7)
        self.assertEqual(overlap["origin"], "manual")
        self.assertEqual(overlap["status"], "pending")
        self.assertEqual(len(review.list_candidates()), 2)
        self.assertNotIn("model_suggestions", review.get_candidate(overlap["clip_id"]))

    def test_corrupt_info_json_falls_back_and_writes_valid_source_meta(self):
        from app import youtube
        (self.folder / "source.json").unlink()
        youtube.info_path(self.video).write_text("{broken", encoding="utf-8")
        row = self.addition()
        self.assertEqual(row["origin"], "manual")
        meta = json.loads((self.folder / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["video_id"], self.video)
        self.assertEqual(meta["duration_s"], 12)

    def test_http_errors_keep_corrupt_json_as_server_error_and_reject_bool_decision(self):
        from app import review_api
        (self.folder / "reviews.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            with review_api._http_errors():
                review.list_candidates()
        for bad in ({"start_s": False}, {"end_s": True}, {"active_seconds": True}, {"source_version": ""}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                schemas.ReviewDecision.model_validate(self.payload | bad)

    def test_manual_addition_prefills_from_asr_json_and_masks_in_blind(self):
        (self.folder / "asr.json").write_text(json.dumps({
            "segments": [
                {"start": 0.5, "end": 2.0, "text": "앞 문장", "no_speech_prob": 0.1},
                {"start": 5.8, "end": 6.1, "text": "경계 스침", "no_speech_prob": 0.1},
                {"start": 6.2, "end": 8.8, "text": "새로 고른 대사", "no_speech_prob": 0.1},
                {"start": 6.5, "end": 8.0, "text": "무음 잡음", "no_speech_prob": 0.95},
                {"start": 9.5, "end": 11.0, "text": "뒤 문장", "no_speech_prob": 0.1},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        row = self.addition()
        self.assertEqual(row["asr_transcript"], "새로 고른 대사")
        self.assertEqual(row["transcript"], "새로 고른 대사")
        blind = review.blind_candidate(row)
        self.assertEqual(blind["asr_transcript"], "")
        self.assertEqual(blind["transcript"], "")
        (self.folder / "asr.json").write_text("{corrupt", encoding="utf-8")
        second = self.addition(start_s=9, end_s=11)
        self.assertEqual(second["asr_transcript"], "")
        self.assertEqual(second["transcript"], "")

    def test_transcribe_candidate_returns_draft_without_mutating_ledger(self):
        from fastapi import HTTPException
        from app import review_api

        row = self.addition()
        key = row["clip_id"]
        directory = self.folder / "clips" / key
        stat = self.source.stat()
        (directory / "media.json").write_text(json.dumps({
            "source_stamp": [stat.st_size, stat.st_mtime_ns],
            "sizes": [(directory / name).stat().st_size for name in ("clip.mp4", "audio_16k.wav")],
        }), encoding="utf-8")
        before = (self.folder / "reviews.json").read_bytes()
        fake = {"engine": "faster-whisper", "model": "medium", "language": "ko", "text": "전사된 대사", "segments": []}
        with patch("app.asr.transcribe", return_value=fake) as mock_asr:
            result = review_api.transcribe(key)
        self.assertEqual(result, {"clip_id": key, "text": "전사된 대사", "model": "medium", "engine": "faster-whisper"})
        mock_asr.assert_called_once_with(directory / "audio_16k.wav")
        self.assertEqual((self.folder / "reviews.json").read_bytes(), before)
        self.assertFalse(store.clip_dir(key).exists())
        with patch("app.asr.transcribe", side_effect=RuntimeError("/secret/path boom")), self.assertRaises(HTTPException) as ctx:
            review_api.transcribe(key)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertNotIn("/secret/path", ctx.exception.detail)
        self.save(clip=key, start_s=6, end_s=9, transcript="확정 대사", status="accepted")
        with patch("app.asr.transcribe", return_value=fake) as mock_asr:
            review.transcribe_candidate(key)
        mock_asr.assert_called_once_with(store.clip_dir(key) / "audio_16k.wav")


if __name__ == "__main__":
    unittest.main()

"""Phase 1 orchestration tests with local files and no network or model downloads."""
import io
import json
import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


class BatchTests(unittest.TestCase):
    video_id = "DGwuCn-EDfQ"

    def setUp(self):
        from tools import run_batch

        self.batch = run_batch
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.workspace = root / "workspace"
        self.enterContext(patch.object(run_batch.config, "DATA_DIR", root))
        self.workspace.mkdir()
        self.output = root / "output"
        self.output.mkdir()
        self.enterContext(patch.object(run_batch.config, "OUTPUT_DIR", self.output))
        self.enterContext(patch.object(run_batch.config, "DOWNLOADS_DIR", root))
        source = root / f"{self.video_id}.mp4"
        source.write_bytes(b"cached source")
        metadata = {"video_id": self.video_id, "duration_s": 12, "url": "pilot URL"}
        self.download = self.enterContext(patch.object(
            run_batch.youtube, "load_video", return_value=metadata
        ))
        self.enterContext(patch.object(run_batch.youtube, "video_path", return_value=source))
        self.cut = self.enterContext(patch.object(
            run_batch.media, "cut_clip",
            side_effect=lambda src, dst, start, duration: dst.write_bytes(b"video")
        ))
        self.extract = self.enterContext(patch.object(
            run_batch.media, "extract_wav16k",
            side_effect=lambda src, dst, start, duration: dst.write_bytes(b"audio")
        ))
        self.vad = self.enterContext(patch.object(
            run_batch.qc, "speech_intervals", return_value=[(1.0, 6.0)]
        ))
        self.transcript = {"segments": [{"start": 1.0, "end": 6.0, "text": "안녕하세요",
                                         "no_speech_prob": 0.01}]}
        self.asr = self.enterContext(patch.object(
            run_batch.asr, "transcribe", return_value=self.transcript
        ))

    def process(self):
        result = self.batch.run_video(self.video_id, self.workspace)
        state_path = self.workspace / self.video_id / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], state["status"])
        return result

    def test_metadata_recovery_reuses_media_and_allows_acceptance(self):
        from app import review

        row = self.process()["candidates"][0]
        path = self.workspace / self.video_id / "source.json"
        for damaged in (None, b"{", b"{}"):
            if damaged is None:
                path.unlink()
            else:
                path.write_bytes(damaged)
            result = self.process()
            self.assertEqual(result["candidates"], [row])
            self.assertEqual(json.loads(path.read_text("utf-8")), self.download.return_value)
        self.vad.assert_called_once()
        self.asr.assert_called_once()
        self.cut.assert_called_once()
        self.assertEqual(self.extract.call_count, 2)
        saved = review.save_decision(row["clip_id"], dict(
            revision=0, start_s=row["start_s"], end_s=row["end_s"], status="accepted",
            reason="", annotator_id="tester", transcript=row["transcript"], emotion="happy",
            confidence=3, quality="A", ambiguous=0, verified=True, active_seconds=5, blind=False,
        ))
        self.assertEqual(saved["status"], "accepted")
        self.assertTrue((self.output / row["clip_id"] / "annotation.json").is_file())

    def test_rerun_reuses_candidate_and_preserves_annotation(self):
        first = self.process()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(len(first["candidates"]), 1)
        clip_id = first["candidates"][0]["clip_id"]
        accepted = self.output / clip_id / "annotation.json"
        accepted.parent.mkdir()
        accepted.write_bytes(b'{"human":true}')

        again = self.process()
        self.assertEqual(again["candidates"], first["candidates"])
        self.assertEqual(accepted.read_bytes(), b'{"human":true}')
        for stage in (self.download, self.cut, self.vad, self.asr):
            stage.assert_called_once()
        self.assertEqual(self.extract.call_count, 2)  # Full source and candidate audio.
        self.assertTrue(again["cached"])

    def test_old_candidate_ranking_is_refreshed_without_inference(self):
        first = self.process()
        path = self.workspace / self.video_id / "state.json"
        old = json.loads(path.read_text("utf-8"))
        old.pop("selection_version")
        old["candidates"][0].pop("speech_ratio")
        path.write_text(json.dumps(old), encoding="utf-8")
        again = self.process()
        self.assertEqual(again["candidates"], first["candidates"])
        self.assertTrue(self.process()["cached"])
        self.asr.assert_called_once()
        self.vad.assert_called_once()

    def test_phase_one_checkpoint_backfills_only_review_media(self):
        first = self.process()
        folder = self.workspace / self.video_id
        if (folder / "clips").exists():
            shutil.rmtree(folder / "clips")
        state_path = folder / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["seconds"].pop("candidate_media", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        again = self.process()
        directory = folder / "clips" / first["candidates"][0]["clip_id"]
        self.assertEqual((directory / "clip.mp4").read_bytes(), b"video")
        self.assertEqual((directory / "audio_16k.wav").read_bytes(), b"audio")
        self.assertFalse(again.get("cached", False))
        self.assertIn("candidate_media", again["seconds"])
        self.asr.assert_called_once()
        self.vad.assert_called_once()
        self.assertTrue(self.process()["cached"])

    def test_candidate_media_error_retries_without_asr(self):
        self.cut.side_effect = RuntimeError("video encoder interrupted")
        failed = self.process()
        self.assertEqual((failed["status"], failed["stage"]), ("failed", "candidate_media"))
        clip_id = failed["candidates"][0]["clip_id"]
        self.assertFalse((self.workspace / self.video_id / "clips" / clip_id / "clip.mp4").exists())
        self.cut.side_effect = lambda src, dst, start, duration: dst.write_bytes(b"video")
        self.assertEqual(self.process()["status"], "completed")
        self.asr.assert_called_once()

    def test_truncated_candidate_audio_is_repaired_using_source_bounds(self):
        first = self.process()
        candidate = first["candidates"][0]
        directory = self.workspace / self.video_id / "clips" / candidate["clip_id"]
        audio = directory / "audio_16k.wav"
        audio.write_bytes(b"")
        again = self.process()
        self.assertEqual(again["status"], "completed")
        self.assertFalse(again.get("cached", False))
        self.assertEqual(audio.read_bytes(), b"audio")
        for encoder in (self.cut, self.extract):
            src, _, start, length = encoder.call_args.args
            self.assertEqual(src, self.batch.youtube.video_path(self.video_id))
            self.assertEqual((start, length), (candidate["start_s"], candidate["duration_ms"] / 1000))
        self.asr.assert_called_once()

    def test_failed_asr_retries_without_repeating_extraction(self):
        self.asr.side_effect = [RuntimeError("ASR interrupted"), self.transcript]
        self.assertEqual(self.process()["status"], "failed")
        self.assertEqual(self.process()["status"], "completed")
        self.download.assert_called_once()
        self.assertEqual(self.extract.call_count, 2)
        self.vad.assert_called_once()
        self.assertEqual(self.asr.call_count, 2)

    def test_no_speech_is_a_finished_result(self):
        self.vad.return_value = []
        self.assertEqual(self.process()["status"], "no_candidates")
        self.assertEqual(self.process()["status"], "no_candidates")
        self.download.assert_called_once()
        self.vad.assert_called_once()

    def test_changed_source_rebuilds_audio_and_transcript(self):
        self.process()
        source = self.batch.youtube.video_path(self.video_id)
        source.write_bytes(b"replacement source")
        self.process()
        self.assertEqual(self.extract.call_count, 4)
        self.assertEqual(self.cut.call_count, 2)
        self.assertEqual(self.asr.call_count, 2)

    def test_concurrent_requests_share_one_video_result(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(self.batch.run_video, self.video_id, self.workspace)
                       for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual(results[0]["candidates"], results[1]["candidates"])
        self.assertEqual(results[0]["status"], "completed")
        self.download.assert_called_once()
        self.asr.assert_called_once()

    def test_invalid_utf8_state_is_rebuilt(self):
        self.process()
        (self.workspace / self.video_id / "state.json").write_bytes(bytes([255]))
        self.assertEqual(self.process()["status"], "completed")

    def test_read_error_preserves_checkpoint(self):
        checkpoint = self.workspace / "state.json"
        checkpoint.write_bytes(b"{}")
        with patch.object(Path, "read_text", side_effect=PermissionError), self.assertRaises(PermissionError):
            self.batch.read_json_or_none(checkpoint)
        self.assertEqual(checkpoint.read_bytes(), b"{}")

    def test_inference_setting_changes_invalidate_cache(self):
        with patch.object(self.batch.config, "ASR_DEVICE", "cpu"), patch.object(
            self.batch.config, "ASR_COMPUTE_TYPE", "int8"
        ):
            self.process()
            with patch.object(self.batch.config, "ASR_COMPUTE_TYPE", "float32"):
                self.process()
                with patch.object(self.batch.config, "ASR_DEVICE", "cuda"):
                    self.process()
        self.assertEqual(self.asr.call_count, 3)

    def test_video_too_long_is_excluded_and_cached(self):
        self.download.side_effect = self.batch.youtube.VideoTooLong(240.0, 180)
        first = self.process()
        self.assertEqual(first["status"], "excluded")
        self.assertEqual(first["reason"], "duration_limit")
        second = self.process()
        self.assertTrue(second["cached"])
        self.assertEqual(second["status"], "excluded")
        self.download.assert_called_once()

    def test_corrupted_workspace_json_recovers(self):
        self.process()
        folder = self.workspace / self.video_id
        (folder / "state.json").write_text("{broken", encoding="utf-8")
        (folder / "source.part.wav").write_bytes(b"partial")
        recovered = self.process()
        self.assertEqual(recovered["status"], "completed")
        self.assertFalse((folder / "source.part.wav").exists())

        # Corrupt a mid-pipeline artifact while state is failed; retry should rebuild it.
        state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        state["status"] = "failed"
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "vad.json").write_text("{broken", encoding="utf-8")
        retried = self.process()
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(self.vad.call_count, 3)

    def test_cli_keeps_processing_after_invalid_input_and_deduplicates(self):
        urls = self.workspace.parent / "urls.txt"
        urls.write_text(
            f"# pilot_v1 comment\nhttps://example.com/invalid\nhttps://youtu.be/{self.video_id}\n"
            f"https://www.youtube.com/shorts/{self.video_id}\n",
            encoding="utf-8",
        )
        with patch.object(sys, "argv", ["run_batch", "--urls", str(urls)]), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(self.batch.main(), 1)
        self.download.assert_called_once()
        self.assertTrue((self.workspace / self.video_id / "state.json").is_file())

    def test_cli_ignores_comments_and_returns_zero(self):
        urls = self.workspace.parent / "urls_ok.txt"
        urls.write_text(f"# pilot_v1\n\nhttps://youtu.be/{self.video_id}\n", encoding="utf-8")
        with patch.object(sys, "argv", ["run_batch", "--urls", str(urls)]), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(self.batch.main(), 0)
        self.download.assert_called_once()

    def test_candidate_media_backfill_handles_missing_seconds_field(self):
        self.process()
        folder = self.workspace / self.video_id
        state_path = folder / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("seconds", None)
        state["status"] = "candidates_ready"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        rebuilt = self.process()
        self.assertEqual(rebuilt["status"], "completed")
        self.assertIn("candidate_media", rebuilt["seconds"])


if __name__ == "__main__":
    unittest.main()

"""Phase 1 orchestration tests with local files and no network or model downloads."""
import io
import json
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
        for stage in (self.download, self.extract, self.vad, self.asr):
            stage.assert_called_once()

    def test_failed_asr_retries_without_repeating_extraction(self):
        self.asr.side_effect = [RuntimeError("ASR interrupted"), self.transcript]
        self.assertEqual(self.process()["status"], "failed")
        self.assertEqual(self.process()["status"], "completed")
        self.download.assert_called_once()
        self.extract.assert_called_once()
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
        self.assertEqual(self.extract.call_count, 2)
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


if __name__ == "__main__":
    unittest.main()

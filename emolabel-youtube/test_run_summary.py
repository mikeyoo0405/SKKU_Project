"""Unit and integration tests for S1 scene backfill/recalc, S2 timing summary, and S3 review summary."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import review, run_summary, scene
from tools import run_batch


class RunSummaryAndSceneIntegrationTests(unittest.TestCase):
    video_id = "DGwuCn-EDfQ"

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = root
        self.workspace = root / "workspace"
        self.output = root / "output"
        self.workspace.mkdir()
        self.output.mkdir()
        self.enterContext(patch.object(run_batch.config, "DATA_DIR", root))
        self.enterContext(patch.object(run_batch.config, "OUTPUT_DIR", self.output))
        self.enterContext(patch.object(run_batch.config, "DOWNLOADS_DIR", root))
        self.source = root / f"{self.video_id}.mp4"
        self.source.write_bytes(b"pilot-video-bytes")
        self.meta = {"video_id": self.video_id, "duration_s": 12.0, "url": "https://youtu.be/DGwuCn-EDfQ"}
        self.download = self.enterContext(patch.object(run_batch.youtube, "load_video", return_value=self.meta))
        self.enterContext(patch.object(run_batch.youtube, "video_path", return_value=self.source))
        self.enterContext(patch.object(run_batch.youtube, "get_meta", return_value=self.meta))
        self.cut = self.enterContext(
            patch.object(run_batch.media, "cut_clip", side_effect=lambda src, dst, start, dur: dst.write_bytes(b"mp4"))
        )
        self.extract = self.enterContext(
            patch.object(run_batch.media, "extract_wav16k", side_effect=lambda src, dst, start, dur: dst.write_bytes(b"wav"))
        )
        self.vad = self.enterContext(patch.object(run_batch.qc, "speech_intervals", return_value=[(1.0, 6.0)]))
        self.asr = self.enterContext(
            patch.object(
                run_batch.asr,
                "transcribe",
                return_value={"segments": [{"start": 1.0, "end": 6.0, "text": "안녕하세요", "no_speech_prob": 0.01}]},
            )
        )

    def test_s1_backfills_existing_completed_candidates_without_rerunning_asr(self):
        # First run with scene mode off -> completes normally.
        with patch.dict(os.environ, {"EMOLABEL_SCENE_MODE": "off"}):
            _, summary1, failed1 = run_batch.run_batch_videos([self.video_id], self.workspace, video_workers=1)
        self.assertFalse(failed1)
        self.assertEqual(summary1["stage_summary"]["scene"]["skipped_videos"], 1)
        self.assertIsNotNone(summary1["batch_rtf"])
        self.assertIsNone(summary1["stage_summary"]["scene"]["executed_seconds_sum"])
        self.assertEqual(self.asr.call_count, 1)
        self.assertEqual(self.vad.call_count, 1)

        # Enable warn mode and re-run -> scene runs on existing completed video without re-running ASR/VAD/download.
        fake_cuts = ([3.2, 8.5], "0.6.5")
        with patch.dict(os.environ, {"EMOLABEL_SCENE_MODE": "warn"}), patch.object(
            scene, "detect_cuts", return_value=fake_cuts
        ) as detect_mock:
            _, summary2, failed2 = run_batch.run_batch_videos([self.video_id], self.workspace, video_workers=1)
            self.assertFalse(failed2)
            detect_mock.assert_called_once()
            self.assertEqual(self.asr.call_count, 1)
            self.assertEqual(self.vad.call_count, 1)
            self.assertEqual(self.download.call_count, 1)

            # Cached stages must NOT leak old timings into this run's summary.
            self.assertEqual(summary2["stage_summary"]["asr"]["cached_videos"], 1)
            self.assertIsNone(summary2["stage_summary"]["asr"]["executed_seconds_sum"])
            self.assertEqual(summary2["stage_summary"]["scene"]["executed_videos"], 1)
            self.assertIsNotNone(summary2["stage_summary"]["scene"]["executed_seconds_sum"])
            self.assertIsNone(summary2["batch_rtf"])
            self.assertIsNone(summary2["executed_videos_rtf"])
            self.assertIsNone(summary2["videos"][0]["rtf"])

            rows = review.list_candidates()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["scene_warning"]["status"], "has_cuts")
            self.assertEqual(rows[0]["scene_warning"]["cuts_s"], [3.2])

            # Third run with warn mode -> scene is now cached too, so all stages report cached & None seconds.
            _, summary3, failed3 = run_batch.run_batch_videos([self.video_id], self.workspace, video_workers=1)
            self.assertFalse(failed3)
            detect_mock.assert_called_once()
            self.assertEqual(summary3["stage_summary"]["scene"]["cached_videos"], 1)
            self.assertIsNone(summary3["stage_summary"]["scene"]["executed_seconds_sum"])
            self.assertTrue(summary3["videos"][0]["cached"])

    def test_s1_recalculates_on_manual_add_and_boundary_edit(self):
        with patch.dict(os.environ, {"EMOLABEL_SCENE_MODE": "warn"}), patch.object(
            scene, "detect_cuts", return_value=([3.2, 8.5], "0.6.5")
        ):
            run_batch.run_batch_videos([self.video_id], self.workspace, video_workers=1)
            auto_row = review.list_candidates()[0]
            self.assertEqual(auto_row["scene_warning"]["status"], "has_cuts")
            self.assertEqual(auto_row["scene_warning"]["cuts_s"], [3.2])

            # Manual addition spanning [7.0, 10.0] picks up cut at 8.5.
            ctx = review.manual_source_context(self.video_id)
            self.assertEqual(ctx["scene_warning"]["source_cuts_s"], [3.2, 8.5])
            manual_row = review.add_manual_candidate(
                self.video_id,
                {
                    "annotator_id": "tester",
                    "source_version": ctx["source_version"],
                    "start_s": 7.0,
                    "end_s": 10.0,
                    "active_seconds": 4.0,
                    "blind": False,
                },
            )
            self.assertEqual(manual_row["scene_warning"]["status"], "has_cuts")
            self.assertEqual(manual_row["scene_warning"]["cuts_s"], [8.5])

            # Boundary edit moving automatic candidate to [3.5, 6.0] removes interior cut 3.2 -> no_cuts.
            edited = review.save_decision(
                auto_row["clip_id"],
                {
                    "revision": auto_row["revision"],
                    "source_version": auto_row["source_version"],
                    "start_s": 3.5,
                    "end_s": 6.0,
                    "status": "accepted",
                    "reason": "cut trimmed",
                    "annotator_id": "tester",
                    "transcript": "수정된 대사",
                    "emotion": "neutral",
                    "confidence": 3,
                    "quality": "A",
                    "ambiguous": 0,
                    "verified": True,
                    "active_seconds": 12.0,
                    "blind": False,
                },
            )
            self.assertEqual(edited["scene_warning"]["status"], "no_cuts")
            self.assertEqual(edited["scene_warning"]["cuts_s"], [])

    def test_scene_failures_do_not_block_batch_or_review(self):
        with patch.dict(os.environ, {"EMOLABEL_SCENE_MODE": "wrong"}):
            _, summary, failed = run_batch.run_batch_videos([self.video_id], self.workspace)
            self.assertFalse(failed)
            self.assertEqual(summary["stage_summary"]["scene"]["failed_videos"], 1)
            self.assertEqual(review.list_candidates()[0]["scene_warning"]["reason"], "invalid_settings")

        with patch.dict(os.environ, {"EMOLABEL_SCENE_MODE": "warn"}), patch.object(
            scene, "ensure_scene", side_effect=OSError("could not write scene cache")
        ):
            _, summary, failed = run_batch.run_batch_videos([self.video_id], self.workspace)
            self.assertFalse(failed)
            self.assertEqual(summary["stage_summary"]["scene"]["failed_videos"], 1)
            self.assertEqual(summary["stage_summary"]["asr"]["cached_videos"], 1)

    def test_review_does_not_display_scene_cache_after_source_replacement(self):
        with patch.dict(os.environ, {"EMOLABEL_SCENE_MODE": "warn"}), patch.object(
            scene, "detect_cuts", return_value=([3.2], "0.6.5")
        ):
            run_batch.run_batch_videos([self.video_id], self.workspace)
            self.source.write_bytes(b"replacement video")
            warning = review.list_candidates()[0]["scene_warning"]
            self.assertEqual(warning["reason"], "source_changed")
            self.assertEqual(warning["cuts_s"], [])

    def test_s2_and_s3_summary_no_double_counting_and_blind_protection(self):
        with patch.dict(os.environ, {"EMOLABEL_SCENE_MODE": "warn"}), patch.object(
            scene, "detect_cuts", return_value=([3.2, 8.5], "0.6.5")
        ):
            run_batch.run_batch_videos([self.video_id], self.workspace, video_workers=1)
            auto_row = review.list_candidates()[0]

            # Boundary edit creates a superseded row + an accepted_edited row (12s active time).
            edited = review.save_decision(
                auto_row["clip_id"],
                {
                    "revision": auto_row["revision"],
                    "source_version": auto_row["source_version"],
                    "start_s": 3.5,
                    "end_s": 6.0,
                    "status": "accepted",
                    "reason": "boundary trimmed",
                    "annotator_id": "tester",
                    "transcript": "수정된 대사",
                    "emotion": "happy",
                    "confidence": 3,
                    "quality": "A",
                    "ambiguous": 0,
                    "verified": True,
                    "active_seconds": 12.0,
                    "blind": False,
                },
            )
            # Manual add (4s active time) -> then hold (6s active time).
            ctx = review.manual_source_context(self.video_id)
            manual_row = review.add_manual_candidate(
                self.video_id,
                {
                    "annotator_id": "tester",
                    "source_version": ctx["source_version"],
                    "start_s": 7.0,
                    "end_s": 10.0,
                    "active_seconds": 4.0,
                    "blind": False,
                },
            )
            review.save_decision(
                manual_row["clip_id"],
                {
                    "revision": manual_row["revision"],
                    "source_version": manual_row["source_version"],
                    "start_s": 7.0,
                    "end_s": 10.0,
                    "status": "hold",
                    "reason": "scene cut",
                    "annotator_id": "tester",
                    "transcript": "",
                    "emotion": None,
                    "confidence": 2,
                    "quality": "B",
                    "ambiguous": 1,
                    "verified": True,
                    "active_seconds": 6.0,
                    "blind": False,
                },
            )

            # Write a separate emotion run record to confirm separation from batch timing.
            emo_dir = self.workspace / "emotion-runs" / "run123"
            emo_dir.mkdir(parents=True)
            (emo_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "run123",
                        "run_tag": "howru-emotion2vec-mean-v1",
                        "input_tag": "original_asr",
                        "candidates": 1,
                        "started_at": "2026-11-23T12:00:00+00:00",
                        "input_errors": 0,
                    }
                ),
                encoding="utf-8",
            )
            (emo_dir / f"{auto_row['clip_id']}.json").write_text(
                json.dumps(
                    {
                        "results": {
                            "text": {"elapsed_s": 1.25},
                            "audio": {"elapsed_s": 2.75},
                            "combined": {"elapsed_s": 4.0},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (emo_dir / "invalid.json").write_text('{"results":{"text":"invalid"}}', encoding="utf-8")

            summary = review.workspace_summary(blind=False)
            self.assertEqual(summary["scope"], "current_review_workspace")
            rev = summary["review"]
            # Active candidates: 1 edited automatic + 1 manual = 2 (superseded excluded!).
            self.assertEqual(rev["total_candidates"], 2)
            self.assertEqual(rev["superseded_excluded"], 1)
            self.assertEqual(rev["by_origin"], {"automatic": 1, "manual": 1})
            self.assertEqual(rev["by_status"]["accepted_total"], 1)
            self.assertEqual(rev["by_status"]["accepted_edited"], 1)
            self.assertEqual(rev["by_status"]["accepted"], 0)
            self.assertEqual(rev["by_status"]["hold"], 1)
            self.assertEqual(rev["by_status"]["pending"], 0)
            self.assertEqual(rev["by_status"]["rejected"], 0)
            # Active time = 12.0 (edited) + 4.0 (manual add) + 6.0 (manual hold) = 22.0 (NO double count from superseded!).
            self.assertEqual(rev["timing"]["total_active_seconds"], 22.0)
            self.assertEqual(rev["timing"]["seconds_per_accepted"], 22.0)
            self.assertEqual(rev["timing"]["coverage"], "complete")
            self.assertEqual(rev["scene_warnings"]["has_cuts"], 1)
            self.assertEqual(rev["scene_warnings"]["no_cuts"], 1)

            # Separate emotion run timing reported separately from batch timing.
            emo = summary["latest_emotion_run"]
            self.assertIsNotNone(emo)
            self.assertTrue(emo["separate_from_batch"])
            self.assertEqual(emo["text_seconds_sum"], 1.25)
            self.assertEqual(emo["audio_seconds_sum"], 2.75)
            self.assertEqual(emo["combined_seconds_sum"], 4.0)

            # Blind mode keeps structural review summary & active time, hides separate emotion run.
            blind_summary = review.workspace_summary(blind=True)
            self.assertTrue(blind_summary["blind"])
            self.assertIsNone(blind_summary["latest_emotion_run"])
            self.assertEqual(blind_summary["review"]["total_candidates"], 2)
            self.assertEqual(blind_summary["review"]["timing"]["total_active_seconds"], 22.0)

            # Withdraw accepted clip to rejected -> accepted_total drops to 0, seconds_per_accepted becomes None.
            review.save_decision(
                edited["clip_id"],
                {
                    "revision": edited["revision"],
                    "source_version": edited["source_version"],
                    "start_s": 3.5,
                    "end_s": 6.0,
                    "status": "rejected",
                    "reason": "withdrawn",
                    "annotator_id": "tester",
                    "transcript": "수정된 대사",
                    "emotion": None,
                    "confidence": None,
                    "quality": None,
                    "ambiguous": None,
                    "verified": True,
                    "active_seconds": 3.0,
                    "blind": False,
                },
            )
            after_withdraw = review.workspace_summary(blind=False)["review"]
            self.assertEqual(after_withdraw["by_status"]["accepted_total"], 0)
            self.assertEqual(after_withdraw["by_status"]["rejected"], 1)
            self.assertIsNone(after_withdraw["timing"]["seconds_per_accepted"])
            self.assertEqual(after_withdraw["timing"]["total_active_seconds"], 25.0)

    def test_s3_unmeasured_and_partial_time_never_reported_as_zero(self):
        rows = [
            {"status": "pending", "origin": "automatic", "history": []},
        ]
        empty = run_summary.summarize_review_rows(rows)
        self.assertIsNone(empty["timing"]["total_active_seconds"])
        self.assertIsNone(empty["timing"]["seconds_per_accepted"])
        self.assertEqual(empty["timing"]["coverage"], "unmeasured")

        partial_rows = [
            {"status": "accepted", "origin": "automatic", "history": [{"active_seconds": 10.0}, {"status": "accepted"}]},
        ]
        partial = run_summary.summarize_review_rows(partial_rows)
        self.assertEqual(partial["timing"]["total_active_seconds"], 10.0)
        self.assertEqual(partial["timing"]["unmeasured_events"], 1)
        self.assertEqual(partial["timing"]["coverage"], "partial")
        self.assertIsNone(partial["timing"]["seconds_per_accepted"])

    def test_s2_rtf_calculated_only_for_executed_runs_and_null_when_cached(self):
        analysis_stages = {
            "vad": {"status": "executed"},
            "asr": {"status": "executed"},
        }
        # Full non-cached run -> both batch_rtf and executed_videos_rtf populated.
        full = run_summary.build_batch_run_record(
            "r1", "2026-11-23T12:00:00+00:00", "2026-11-23T12:00:06+00:00",
            video_workers=1, scene_mode="warn", batch_wall_seconds=6.0,
            videos=[
                {"video_id": "DGwuCn-EDfQ", "status": "completed", "cached": False, "wall_seconds": 6.0, "duration_s": 12.0, "stages": analysis_stages},
            ],
        )
        self.assertEqual(full["processed_duration_seconds"], 12.0)
        self.assertEqual(full["batch_rtf"], 0.5)
        self.assertEqual(full["executed_videos_rtf"], 0.5)
        self.assertEqual(full["videos"][0]["rtf"], 0.5)

        # Fully cached run -> RTF must stay None (never 0.0).
        cached = run_summary.build_batch_run_record(
            "r2", "2026-11-23T12:01:00+00:00", "2026-11-23T12:01:00+00:00",
            video_workers=1, scene_mode="warn", batch_wall_seconds=0.05,
            videos=[
                {"video_id": "DGwuCn-EDfQ", "status": "completed", "cached": True, "wall_seconds": 0.05, "duration_s": 12.0, "stages": {}},
            ],
        )
        self.assertIsNone(cached["processed_duration_seconds"])
        self.assertIsNone(cached["batch_rtf"])
        self.assertIsNone(cached["executed_videos_rtf"])
        self.assertIsNone(cached["videos"][0]["rtf"])

        # Mixed run (1 executed + 1 cached) -> batch_rtf is None, executed_videos_rtf covers executed video only.
        mixed = run_summary.build_batch_run_record(
            "r3", "2026-11-23T12:02:00+00:00", "2026-11-23T12:02:04+00:00",
            video_workers=1, scene_mode="warn", batch_wall_seconds=4.0,
            videos=[
                {"video_id": "DGwuCn-EDfQ", "status": "completed", "cached": True, "wall_seconds": 0.02, "duration_s": 12.0, "stages": {}},
                {"video_id": "mKwJDo0YTkM", "status": "completed", "cached": False, "wall_seconds": 3.0, "duration_s": 10.0, "stages": analysis_stages},
            ],
        )
        self.assertIsNone(mixed["batch_rtf"])
        self.assertEqual(mixed["executed_videos_rtf"], 0.3)
        self.assertIsNone(mixed["videos"][0]["rtf"])
        self.assertEqual(mixed["videos"][1]["rtf"], 0.3)
        legacy = {key: value for key, value in mixed.items() if key != "rtf_basis"}
        run_summary.save_batch_run(self.workspace, legacy)
        restored = run_summary.read_latest_batch_run(self.workspace)
        self.assertEqual(restored["batch_wall_seconds"], 4.0)
        self.assertIsNone(restored["executed_videos_rtf"])
        self.assertNotIn("rtf", restored["videos"][1])


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app import config, store


class ReviewTests(unittest.TestCase):
    def setUp(self):
        from app import review

        self.review = review
        data = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.output = data / "output"
        self.enterContext(patch.object(config, "DATA_DIR", data))
        self.enterContext(patch.object(config, "OUTPUT_DIR", self.output))
        self.enterContext(patch.object(config, "DOWNLOADS_DIR", data / "downloads"))
        self.video = "DGwuCn-EDfQ"
        self.clip = store.make_clip_id(self.video, 1, 5)
        self.folder = data / "workspace" / self.video
        self.folder.mkdir(parents=True)
        config.ensure_dirs()
        self.source = config.DOWNLOADS_DIR / (self.video + ".mp4")
        self.source.write_bytes(b"source")
        stat = self.source.stat()
        self.state = {
            "status": "completed", "duration_s": 12, "video_id": self.video,
            "source_stamp": [stat.st_size, stat.st_mtime_ns],
            "candidates": [{"clip_id": self.clip, "video_id": self.video,
                            "start_s": 1, "end_s": 5, "transcript": "draft",
                            "quality_flags": [], "status": "pending"}],
        }
        self.write_state()
        (self.folder / "source.json").write_text('{"title":"test","duration_s":12}', encoding="utf-8")

        def media(folder, clip, source):
            directory = folder / "clips" / clip
            directory.mkdir(exist_ok=True, parents=True)
            (directory / "clip.mp4").write_bytes(clip.encode())
            (directory / "audio_16k.wav").write_bytes(b"wav")
            return directory

        self.encode = self.enterContext(patch("app.review.ensure_media", side_effect=media))
        self.payload = dict(status="accepted", revision=0, start_s=1, end_s=5,
                            reason="", annotator_id="reviewer", active_seconds=3.8,
                            transcript="draft", emotion="happy", gender="unknown", actor="",
                            notes="", confidence=3, quality="A", ambiguous=0, verified=True,
                            blind=False)

    def write_state(self):
        (self.folder / "state.json").write_text(json.dumps(self.state), encoding="utf-8")

    def save(self, clip=None, **overrides):
        return self.review.save_decision(clip or self.clip, {**self.payload, **overrides})

    def test_accepted_output_and_withdrawal(self):
        self.assertEqual(self.review.get_candidate(self.clip)["status"], "pending")
        self.save()
        directory = self.output / self.clip
        self.assertEqual({p.name for p in directory.iterdir()},
                         {"clip.mp4", "audio_16k.wav", "prelabels.json", "annotation.json"})
        annotation = store.read_annotation(self.clip)
        self.assertEqual(annotation["labels"]["transcript"], "draft")
        self.assertIsNone(annotation["labels"]["valence"])
        self.assertEqual(annotation["annotator"]["id"], "reviewer")
        self.assertEqual(annotation["review"]["reason"], "reviewer_confirmed")
        self.assertEqual(self.review.list_candidates()[0]["revision"], 1)
        withdrawn = self.save(status="hold", revision=1, reason="unclear")
        self.assertFalse(directory.exists())
        self.assertTrue(list((self.folder / "quarantine").rglob("annotation.json")))
        self.assertEqual(withdrawn["history"][-1]["reason"], "unclear")
        self.assertEqual(self.save(status="rejected", revision=2, reason="overlap")["revision"], 3)
        self.assertEqual(self.save(revision=3)["status"], "accepted")

    def test_changed_boundaries_replace_only_after_media_ready(self):
        self.save()
        with patch("app.review.ensure_media", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.save(revision=1, start_s=0.5)
        self.assertTrue((self.output / self.clip / "annotation.json").exists())
        edited = self.save(revision=1, start_s=0.5)
        self.assertEqual(edited["status"], "accepted_edited")
        self.assertEqual(edited["parent_clip_id"], self.clip)
        self.assertEqual(self.review.get_candidate(self.clip)["status"], "superseded")
        self.assertEqual([p.name for p in self.output.iterdir()], [edited["clip_id"]])
        with self.assertRaises(self.review.Conflict):
            self.save(revision=2)

    def test_history_survives_batch_refresh_and_rejected_transcript_edits(self):
        row = self.save(status="rejected", reason="overlap", transcript="human")
        self.state.update(status="no_candidates", candidates=[])
        self.write_state()
        self.assertEqual(self.review.list_candidates()[0]["review"]["transcript"], "human")
        self.assertEqual(row["history"][0]["transcript"], "human")

    def test_collision_with_another_candidate_or_legacy_output(self):
        other = store.make_clip_id(self.video, 0, 5)
        self.state["candidates"].append(dict(self.state["candidates"][0], clip_id=other, start_s=0))
        self.write_state()
        with self.assertRaises(self.review.Conflict):
            self.save(start_s=0)
        legacy = self.output / self.clip
        legacy.mkdir()
        (legacy / "annotation.json").write_bytes(b'{"legacy":true}')
        self.assertNotIn(self.clip, [row["clip_id"] for row in self.review.list_candidates()])
        with self.assertRaises(self.review.NotFound):
            self.save()
        self.assertEqual((legacy / "annotation.json").read_bytes(), b'{"legacy":true}')

    def test_stale_review_or_source_cannot_publish(self):
        self.assertEqual(self.save(transcript="human")["status"], "accepted_edited")
        with self.assertRaises(self.review.Conflict):
            self.save()
        self.source.write_bytes(b"changed source")
        with self.assertRaises(self.review.Conflict):
            self.save(revision=1, start_s=0)
        self.assertEqual(store.read_annotation(self.clip)["labels"]["transcript"], "human")
        self.assertEqual(self.save(revision=1, status="hold", reason="verify source")["status"], "hold")

    def test_concurrent_reviewers_publish_one_version(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(self.save) for _ in range(2)]
            errors = [f.exception() for f in pending]
        self.assertEqual(sum(isinstance(e, self.review.Conflict) for e in errors), 1)
        self.assertEqual(len(self.review.get_candidate(self.clip)["history"]), 1)

    def test_prior_browser_snapshot_cannot_label_regenerated_source(self):
        token = self.review.get_candidate(self.clip)["source_version"]
        self.source.write_bytes(b"new media")
        stat = self.source.stat()
        self.state["source_stamp"] = [stat.st_size, stat.st_mtime_ns]
        self.write_state()
        with self.assertRaises(self.review.Conflict):
            self.save(source_version=token)
        self.assertFalse((self.output / self.clip).exists())

    def test_committed_intent_recovers_after_interrupted_publication(self):
        self.save()
        finish = self.review._finish_transaction
        with patch("app.review._finish_transaction", side_effect=OSError("interrupted")), self.assertRaises(OSError):
            self.save(revision=1, start_s=0)
        with patch("app.review._finish_transaction", wraps=finish) as recovered:
            self.assertEqual(self.review.get_candidate(self.clip)["status"], "superseded")
            recovered.assert_called_once()
        self.assertEqual(len(list(self.output.glob("*/annotation.json"))), 1)
        self.assertFalse((self.output / self.clip).exists())

    def test_invalid_id_or_boundary_precedes_mutation(self):
        for value in ("../other", self.clip + "/x"):
            with self.assertRaises(ValueError):
                self.review.get_candidate(value)
        for end in (15, 1.0001, float("nan")):
            with self.assertRaises(ValueError):
                self.save(end_s=end)
        self.assertEqual(self.review.get_candidate(self.clip)["revision"], 0)

    def test_interrupted_after_quarantine_recovers_without_losing_previous_annotation(self):
        self.save()
        rename = Path.rename

        def interrupted(path, target):
            if path == self.folder / "review-transaction" / "output":
                raise PermissionError("media in use")
            return rename(path, target)

        with patch.object(Path, "rename", interrupted), self.assertRaises(PermissionError):
            self.save(revision=1, start_s=0)
        self.assertFalse((self.output / self.clip).exists())
        rows = self.review.list_candidates()
        current = next(row for row in rows if row["status"] == "accepted_edited")
        backup, = (self.folder / "quarantine").rglob("annotation.json")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["clip_id"], self.clip)
        self.assertTrue((self.output / current["clip_id"] / "annotation.json").is_file())

    def test_rejected_keeps_original_bounds_and_skips_media_extraction(self):
        rejected = self.save(status="rejected", reason="overlap", start_s=1.0, end_s=1.05)
        self.assertEqual(rejected["clip_id"], self.clip)
        self.assertEqual((rejected["start_s"], rejected["end_s"]), (1, 5))
        self.encode.assert_not_called()

    def test_missing_source_metadata_raises_conflict_and_fps_is_exposed(self):
        (self.folder / "source.json").write_text('{"title":"test","duration_s":12,"fps":24}', encoding="utf-8")
        self.assertEqual(self.review.get_candidate(self.clip)["fps"], 24)
        (self.folder / "source.json").unlink()
        with self.assertRaises(self.review.Conflict):
            self.save()

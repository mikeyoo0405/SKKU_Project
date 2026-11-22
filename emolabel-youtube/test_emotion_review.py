"""Only matching, original machine results may guide a non-blind review."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, review, store


class EmotionReviewTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "DATA_DIR", root))
        self.enterContext(patch.object(config, "OUTPUT_DIR", root / "output"))
        self.enterContext(patch.object(config, "DOWNLOADS_DIR", root / "downloads"))
        config.ensure_dirs()
        video = "DGwuCn-EDfQ"
        source = config.DOWNLOADS_DIR / f"{video}.mp4"
        source.write_bytes(b"original")
        stamp = self.stamp(source)
        self.folder = root / "workspace" / video
        self.rows = []
        for start in (1, 5):
            clip = store.make_clip_id(video, start, start + 2)
            self.rows.append(dict(clip_id=clip, start_s=start, end_s=start + 2, transcript="draft"))
        clip = self.rows[-1]["clip_id"]
        self.media = self.folder / "clips" / clip
        self.media.mkdir(parents=True)
        self.state = dict(status="completed", candidates=self.rows, duration_s=15, source_stamp=stamp)
        self.write(self.folder / "state.json", self.state)
        self.result = dict(schema_version=1, run_id="test-run", clip_id=clip, source_stamp=stamp,
                           transcript="draft", input_transcript="draft", input_tag="original_asr",
                           source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), media_stamp={}, media_sha256={},
                           results=dict(text=dict(status="ok", label="happy", reason=None),
                                        audio=dict(status="ok", label="sad", reason=None),
                                        combined=dict(status="abstained", label=None, reason="disagreement"),
                                        model_disagreement=True))
        for name, filename in (("wav", "audio_16k.wav"), ("video", "clip.mp4")):
            file = self.media / filename
            file.write_bytes(filename.encode())
            self.result["media_stamp"][name] = self.stamp(file)
            self.result["media_sha256"][name] = hashlib.sha256(file.read_bytes()).hexdigest()
        self.latest = self.media / "emotion-latest.json"
        self.write(self.latest, self.result)

    @staticmethod
    def stamp(path):
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns]

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_unblinded_queue_prioritizes_disagreement_without_changing_human_state(self):
        first = review.list_candidates()[0]
        self.assertEqual(first["clip_id"], self.result["clip_id"])
        self.assertEqual(first["model_suggestions"]["audio"]["label"], "sad")
        self.assertTrue(first["model_disagreement"])
        self.assertEqual(first["status"], "pending")
        self.assertIsNone(first["review"])
        blind = review.list_candidates(blind=True)
        self.assertEqual([r["start_s"] for r in blind], [1, 5])
        self.assertTrue(all("model_suggestions" not in r and not r["model_disagreement"] for r in blind))

    def test_legacy_candidate_without_transcript_remains_reviewable(self):
        self.rows[-1].pop("transcript")
        self.write(self.folder / "state.json", self.state)
        self.assertEqual(len(review.list_candidates()), 2)
        self.assertTrue(all(not r.get("model_suggestions") for r in review.list_candidates()))

    def test_mean_suggestion_survives_disagreement_and_tampering_is_hidden(self):
        from app.emotion_scores import AUDIO_CLASSES, TEXT_CLASSES, mean_fusion, scored_reply
        records = self.result["results"]
        for kind, classes, values in (("text", TEXT_CLASSES, {"happy": 0.6, "sad": 0.4}),
                                      ("audio", AUDIO_CLASSES, {"happy": 0.45, "sad": 0.5, "neutral": 0.05})):
            reply = scored_reply(kind, dict.fromkeys(classes, 0) | values, "a" * 40)
            records[kind] = dict(reply, status="ok")
        records["combined"] = mean_fusion(records["text"], records["audio"])
        self.write(self.latest, self.result)
        suggestions = review.list_candidates()[0]["model_suggestions"]
        self.assertEqual(suggestions["combined"]["label"], "happy")
        self.assertAlmostEqual(suggestions["combined"]["probabilities"]["happy"], 0.525)
        self.assertNotIn("model_suggestions", review.list_candidates(blind=True)[0])
        records["combined"]["probabilities"]["happy"] = 0.99
        self.write(self.latest, self.result)
        self.assertTrue(all(not r.get("model_suggestions") for r in review.list_candidates()))

    def test_superseded_or_edited_inputs_and_corrupt_sidecars_are_not_suggested(self):
        for change in (dict(clip_id="other"), dict(input_tag="corrected_asr"),
                       dict(source_stamp=[0, 0]), dict(transcript="updated"),
                       dict(media_stamp={}), dict(media_sha256={"wav": "bad", "video": "bad"}),
                       dict(results={"text": None}),
                       dict(results=self.result["results"] | {"audio": {"status": "ok", "label": "sad",
                                                                         "reason": float("nan")}})):
            with self.subTest(change=change):
                self.write(self.latest, self.result | change)
                self.assertTrue(all(not r.get("model_suggestions") and not r["model_disagreement"]
                                    for r in review.list_candidates()))
        self.latest.write_text("{", encoding="utf-8")
        self.assertEqual(len(review.list_candidates()), 2)
        self.write(self.latest, self.result)
        human = dict(review.get_candidate(self.result["clip_id"]), status="hold", transcript="human")
        self.write(self.folder / "reviews.json", {human["clip_id"]: human})
        self.assertTrue(all(not r.get("model_suggestions") for r in review.list_candidates()))

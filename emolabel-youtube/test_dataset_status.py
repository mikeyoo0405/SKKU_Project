import json
import tempfile
import unittest
from pathlib import Path

from tools.dataset_status import inspect


class DatasetStatusTests(unittest.TestCase):
    def test_only_complete_accepted_media_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = root / "DGwuCn-EDfQ_0001000_0003000"
            clip.mkdir()
            (clip / "annotation.json").write_text(json.dumps({
                "clip_id": clip.name, "labels": {"emotion": "happy", "transcript": "test"},
                "annotator": {"id": "tester"},
            }), encoding="utf-8")
            (clip / "clip.mp4").write_bytes(b"video")
            (clip / "audio_16k.wav").touch()
            counts, _, issues = inspect(root)
            self.assertTrue(issues)
            self.assertEqual(sum(counts.values()), 0)
            (clip / "audio_16k.wav").write_bytes(b"audio")
            self.assertEqual(inspect(root)[0]["happy"], 1)

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import config, store
from tools import emotion_batch


class FixedBatchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "DOWNLOADS_DIR", self.root))
        self.ws = self.root / "workspace"
        vid = "DGwuCn-EDfQ"
        source = self.root / f"{vid}.mp4"
        source.write_bytes(b"source video")
        self.key = store.make_clip_id(vid, 1, 4)
        self.folder = self.ws / vid
        self.clip = self.folder / "clips" / self.key
        self.clip.mkdir(parents=True)
        (self.clip / "clip.mp4").write_bytes(b"clip")
        (self.clip / "audio_16k.wav").write_bytes(b"audio")
        stamp = [source.stat().st_size, source.stat().st_mtime_ns]
        self.state = dict(video_id=vid, source_stamp=stamp, status="completed",
                          candidates=[dict(clip_id=self.key, start_s=1, end_s=4, transcript="원본")])
        (self.folder / "state.json").write_text(json.dumps(self.state))
        (self.clip / "media.json").write_text(json.dumps(dict(source_stamp=stamp, sizes=[4, 5])))
        self.manifest = self.root / "manifest.json"
        self.latest = self.clip / "emotion-latest.json"
        self.result = {"text": {"label": "sad"}, "audio": {"label": "sad"},
                       "combined": {"label": "sad"}, "model_disagreement": False}

    def test_original_reruns_archive_separately_and_do_not_touch_ledger(self):
        ledger = self.folder / "reviews.json"
        ledger.write_bytes(b'{"human":"saved"}')
        emotion_batch.export_manifest(self.ws, self.manifest)
        model = Mock(return_value=self.result)
        first = emotion_batch.run_manifest(self.ws, self.manifest, classify=model)
        second = emotion_batch.run_manifest(self.ws, self.manifest, classify=model)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(model.call_args.args[0]["transcript"], "원본")
        sidecar = json.loads(self.latest.read_text("utf-8"))
        self.assertEqual(sidecar["results"], self.result)
        self.assertEqual(sidecar["input_tag"], "original_asr")
        self.assertTrue((self.ws / "emotion-runs" / first["run_id"] / f"{self.key}.json").is_file())
        self.assertEqual(ledger.read_bytes(), b'{"human":"saved"}')

    def test_cli_exports_then_runs_without_models(self):
        downloads = self.root / "downloads"
        downloads.mkdir()
        (self.root / "DGwuCn-EDfQ.mp4").rename(downloads / "DGwuCn-EDfQ.mp4")
        env = {**os.environ, "EMOLABEL_DATA_DIR": str(self.root),
               "EMOLABEL_EMOTION_TEXT": "", "EMOLABEL_EMOTION_AUDIO": ""}
        for flag in ("--export-candidates", "--candidates"):
            command = [sys.executable, "-B", "-m", "tools.run_batch", flag, str(self.manifest)]
            result = subprocess.run(command, cwd=Path(__file__).parent, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["candidates"], 1)
        records = json.loads(self.latest.read_text("utf-8"))["results"]
        self.assertEqual(records["text"]["reason"], "model_unavailable")
        self.assertIsNone(records["combined"]["label"])
        self.assertEqual(records["text"]["diagnostic"], {
            "stage": "configuration", "code": "model_not_configured"})

    def test_edited_transcript_is_separate_and_keeps_original_suggestion(self):
        emotion_batch.export_manifest(self.ws, self.manifest)
        self.latest.write_bytes(b"original")
        edits = self.root / "edits.json"
        edits.write_text(json.dumps({self.key: "수정 대사"}))
        model = Mock(return_value=self.result)
        summary = emotion_batch.run_manifest(self.ws, self.manifest, edited_transcripts=edits, classify=model)
        self.assertEqual(model.call_args.args[0]["transcript"], "수정 대사")
        entry = json.loads((self.ws / "emotion-runs" / summary["run_id"] / f"{self.key}.json").read_text("utf-8"))
        self.assertEqual(entry["input_tag"], "corrected_transcript")
        self.assertEqual(self.latest.read_bytes(), b"original")

    def test_changed_audio_is_never_sent_to_model(self):
        emotion_batch.export_manifest(self.ws, self.manifest)
        (self.clip / "audio_16k.wav").write_bytes(b"other")
        model = Mock(return_value=self.result)
        summary = emotion_batch.run_manifest(self.ws, self.manifest, classify=model)
        self.assertEqual(summary["input_errors"], 1)
        model.assert_not_called()
        self.assertFalse(self.latest.exists())

    def test_candidate_replaced_during_inference_cannot_receive_stale_suggestion(self):
        emotion_batch.export_manifest(self.ws, self.manifest)
        def model(inputs):
            self.assertEqual(Path(inputs["wav_path"]).read_bytes(), b"audio")
            self.state["candidates"][0]["transcript"] = "새 대사"
            (self.folder / "state.json").write_text(json.dumps(self.state))
            return self.result
        summary = emotion_batch.run_manifest(self.ws, self.manifest, classify=model)
        self.assertEqual(summary["input_errors"], 1)
        self.assertFalse(self.latest.exists())

    def test_manifest_is_immutable_and_rejects_invalid_ids(self):
        emotion_batch.export_manifest(self.ws, self.manifest)
        original = self.manifest.read_bytes()
        emotion_batch.export_manifest(self.ws, self.manifest)
        self.state["candidates"][0]["transcript"] = "changed"
        (self.folder / "state.json").write_text(json.dumps(self.state))
        with self.assertRaises(FileExistsError):
            emotion_batch.export_manifest(self.ws, self.manifest)
        self.assertEqual(self.manifest.read_bytes(), original)
        data = json.loads(original)
        data["candidates"][0]["clip_id"] = "../foreign"
        self.manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            emotion_batch.run_manifest(self.ws, self.manifest, classify=Mock())

    def test_manifest_cannot_create_human_ledger_or_inject_result_metadata(self):
        with self.assertRaises(ValueError):
            emotion_batch.export_manifest(self.ws, self.folder / "reviews.json")
        self.assertFalse((self.folder / "reviews.json").exists())
        emotion_batch.export_manifest(self.ws, self.manifest)
        modified = json.loads(self.manifest.read_bytes())
        modified["candidates"][0]["schema_version"] = 5
        self.manifest.write_text(json.dumps(modified))
        with self.assertRaises(ValueError):
            emotion_batch.run_manifest(self.ws, self.manifest, classify=Mock())

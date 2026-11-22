"""Exercise the local adapter protocol with temporary Python model stand-ins."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import emotion


class EmotionTests(unittest.TestCase):
    def setUp(self):
        self.variables = self.enterContext(patch.dict(os.environ, {
            "EMOLABEL_EMOTION_TEXT": "", "EMOLABEL_EMOTION_AUDIO": "",
        }))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        wav = self.root / "audio.wav"
        wav.write_bytes(b"test audio")
        self.inputs = {"clip_id": "fixed", "transcript": "안녕하세요",
                       "wav_path": str(wav), "video_path": "unused.mp4", "input_tag": "original_asr"}

    def configure(self, kind, script, **extra):
        runner = self.root / f"{kind}.py"
        runner.write_text(script, encoding="utf-8")
        spec = {"command": [sys.executable, str(runner)], "model_id": kind, "version": "1",
                "quantization": "none", "prompt_version": "pilot-v1", "seed": 17, **extra}
        self.variables[f"EMOLABEL_EMOTION_{kind.upper()}"] = json.dumps(spec)

    def returns(self, kind, value):
        self.configure(kind, f"print({json.dumps(value)!r})")

    def test_agree_with_identical_candidate_and_isolated_modalities(self):
        read = "import sys,json\nr=json.loads(sys.stdin.buffer.read())\n"
        fixed = "assert r['clip_id']=='fixed' and r['input_tag']=='original_asr'\n"
        self.configure("text", read + fixed + "assert r['transcript']=='안녕하세요' and 'wav_path' not in r\n"
                       + "assert r['settings']['temperature']==0 and r['settings']['seed']==17\n"
                       + """print('{"label":" SAD ","scores":{"sad":0.72}}')""")
        self.configure("audio", read + fixed + "assert 'transcript' not in r and 'video_path' not in r\n"
                       + "assert open(r['wav_path'],'rb').read()==b'test audio'\n"
                       + """print('{"label":"sad"}')""")
        result = emotion.classify(self.inputs)
        self.assertEqual(result["combined"]["label"], "sad")
        self.assertEqual(result["text"]["scores"], {"sad": 0.72})
        self.assertEqual(result["audio"]["version"], "1")
        self.assertGreater(result["text"]["elapsed_s"], 0)
        self.assertFalse(result["model_disagreement"])
        self.assertFalse(result["auto_accept"])

    def test_conflict_and_abstention(self):
        self.returns("text", {"label": "angry"})
        self.returns("audio", {"label": "neutral"})
        result = emotion.classify(self.inputs)
        self.assertTrue(result["model_disagreement"])
        self.assertIsNone(result["combined"]["label"])
        self.returns("audio", {"label": "abstain", "reason": "uncertain"})
        result = emotion.classify(self.inputs)
        self.assertFalse(result["model_disagreement"])
        self.assertEqual(result["audio"]["status"], "abstained")
        self.assertEqual(result["audio"]["reason"], "uncertain")
        self.assertIsNone(result["combined"]["label"])

    def test_unavailable_configuration_and_missing_inputs(self):
        self.assertEqual(emotion.classify(self.inputs)["audio"]["reason"], "model_unavailable")
        self.configure("text", "pass", command=["missing-local-executable-emolabel"])
        self.configure("audio", "pass")
        result = emotion.classify(dict(self.inputs, wav_path=None))
        self.assertEqual(result["text"]["reason"], "model_unavailable")
        self.assertEqual(result["audio"]["reason"], "missing_input")
        self.configure("text", "pass", version="")
        self.assertEqual(emotion.classify(self.inputs)["text"]["reason"], "invalid_configuration")

    def test_bad_output_and_timeout_remain_reviewable(self):
        self.configure("audio", "import time; time.sleep(60)", timeout_s=0.15)
        self.returns("text", {"label": "fear"})
        result = emotion.classify(self.inputs)
        self.assertEqual(result["audio"]["reason"], "timeout")
        self.assertEqual(result["audio"]["diagnostic"], {"stage": "inference", "code": "timeout"})
        self.assertEqual(result["text"]["reason"], "invalid_output")
        self.assertEqual(result["combined"]["status"], "abstained")
        self.assertIsNone(result["combined"]["label"])

    def test_scores_and_runner_errors_are_validated(self):
        for value, reason in [({"label": "sad", "scores": {"sad": True}}, "invalid_output"),
                              ({"label": "sad", "scores": {"sad": float("nan")}}, "invalid_output"),
                              ({"error": "model_unavailable"}, "model_unavailable"),
                              ({"error": "inference_failed", "label": "sad"}, "invalid_output")]:
            with self.subTest(value=value):
                self.returns("text", value)
                result = emotion.classify(self.inputs)
                self.assertEqual(result["text"]["reason"], reason)
                self.assertIsNone(result["text"]["label"])

    def test_conflicting_labels_in_one_reply_cannot_form_agreement(self):
        self.configure("text", """print('{"label":"happy","label":"sad"}')""")
        self.returns("audio", {"label": "sad"})
        result = emotion.classify(self.inputs)
        self.assertEqual(result["text"]["reason"], "invalid_output")
        self.assertIsNone(result["combined"]["label"])

    def test_oversized_output_is_rejected_even_if_runner_keeps_running(self):
        self.configure("text", "import sys,time\nsys.stdout.buffer.write(b'x'*70000)\n"
                            "sys.stdout.flush()\ntime.sleep(60)", timeout_s=2)
        result = emotion.classify(self.inputs)
        self.assertEqual(result["text"]["reason"], "invalid_output")
        self.assertEqual(result["combined"]["status"], "abstained")

    def test_safe_diagnostic_survives_and_untrusted_details_are_rejected(self):
        diagnostic = {"stage": "model_load", "code": "checkpoint_load_failed"}
        self.returns("text", {"error": "inference_failed", "diagnostic": diagnostic})
        result = emotion.classify(self.inputs)
        self.assertEqual(result["text"]["diagnostic"], diagnostic)
        self.returns("text", {"error": "inference_failed", "diagnostic": diagnostic | {"message": "SECRET_TOKEN"}})
        result = emotion.classify(self.inputs)
        self.assertEqual(result["text"]["reason"], "invalid_output")
        self.assertNotIn("SECRET_TOKEN", json.dumps(result))

    def test_nonzero_exit_is_recorded_without_stderr_and_audio_still_runs(self):
        self.configure("text", "import sys\nprint('SECRET_TOKEN /private/path',file=sys.stderr)\nsys.exit(7)")
        self.returns("audio", {"label": "happy"})
        result = emotion.classify(self.inputs)
        self.assertEqual(result["text"]["diagnostic"], {"stage": "process_exit", "code": "nonzero_exit", "returncode": 7})
        self.assertEqual(result["audio"]["label"], "happy")
        self.assertNotIn("SECRET_TOKEN", json.dumps(result))

    def test_nonstring_wav_path_reports_missing_input_not_invalid_output(self):
        self.returns("audio", {"label": "happy"})
        result = emotion.classify(dict(self.inputs, wav_path=12345))
        self.assertEqual(result["audio"]["reason"], "missing_input")
        self.assertEqual(result["audio"]["diagnostic"], {"stage": "input_validation", "code": "missing_input"})

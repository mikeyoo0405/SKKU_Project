"""Score contracts and adapter integration; mocks do not establish model acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app import emotion
from app.emotion_scores import (AUDIO_CLASSES, AUDIO_MODEL, LABELS, TEXT_CLASSES, TEXT_MODEL,
                               decision, distribution, mean_fusion, scored_reply, validate_combined)
from tools import configure_emotion, scored_emotion
from tools.check_emotion_connection import check


def native(kind, **values):
    classes = TEXT_CLASSES if kind == "text" else AUDIO_CLASSES
    result = {k: 0.0 for k in classes}
    result.update(values)
    return result


def record(kind, **values):
    reply = scored_reply(kind, native(kind, **values), "a" * 40)
    label = reply["label"]
    return dict(reply, status="abstained" if label == "abstain" else "ok",
                label=None if label == "abstain" else label, elapsed_s=0.5)


class ScoreContractTests(unittest.TestCase):
    def test_mapping_preserves_other_mass_and_native_names(self):
        scores = {"평범함": 0.1, "분노": 0.1, "슬픔": 0.1, "기쁨": 0.2,
                  "설렘": 0.2, "놀라움": 0.1, "불쾌함": 0.1, "두려움": 0.1}
        reply = scored_reply("text", scores, "a" * 40)
        self.assertEqual(reply["scores"], scores)
        self.assertAlmostEqual(reply["probabilities"]["other"], 0.5)
        self.assertEqual(reply["label"], "abstain")
        bilingual = {f"中文/{k}": v for k, v in native("audio", neutral=1).items()}
        self.assertEqual(scored_reply("audio", bilingual, "a" * 40)["label"], "neutral")
        real_tokens = {"生气/angry": 0.0, "厌恶/disgusted": 0.0, "恐惧/fearful": 0.0, "开心/happy": 0.7,
                       "中立/neutral": 0.2, "其他/other": 0.0, "难过/sad": 0.0, "吃惊/surprised": 0.0, "<unk>": 0.1}
        audio_reply = scored_reply("audio", real_tokens, "a" * 40)
        self.assertEqual(audio_reply["label"], "happy")
        self.assertAlmostEqual(audio_reply["probabilities"]["other"], 0.1)
        with self.assertRaises(ValueError):
            scored_reply("audio", real_tokens | {"unknown": 0.0}, "a" * 40)

    def test_mean_can_choose_label_despite_disagreement(self):
        text = record("text", happy=0.6, sad=0.4)
        audio = record("audio", sad=0.5, happy=0.45, neutral=0.05)
        result = mean_fusion(text, audio)
        self.assertEqual(result["label"], "happy")
        self.assertAlmostEqual(result["probabilities"]["happy"], 0.525)
        self.assertAlmostEqual(result["probabilities"]["sad"], 0.45)
        validate_combined(dict(text=text, audio=audio, combined=result))
        result["scores"]["happy"] = 0.9
        with self.assertRaises(ValueError):
            validate_combined(dict(text=text, audio=audio, combined=result))
        agreement = {"model_id": "agreement", "version": "1", "status": "ok", "label": "happy",
                     "probabilities": {"happy": 1.0}}
        with self.assertRaises(ValueError):
            validate_combined(dict(text={"status": "ok", "label": "happy"},
                                   audio={"status": "ok", "label": "happy"}, combined=agreement))

    def test_individual_other_abstention_still_has_usable_distribution(self):
        result = mean_fusion(record("text", fear=0.6, happy=0.4), record("audio", happy=1))
        self.assertEqual(result["label"], "happy")
        self.assertAlmostEqual(result["probabilities"]["other"], 0.3)

    def test_other_tie_failure_and_legacy_logits_never_force_a_label(self):
        for left, right, reason in (
            (record("text", fear=1), record("audio", fearful=1), "unsupported_emotion"),
            (record("text", sad=1), record("audio", happy=1), "tied_scores"),
            ({"status": "failed"}, record("audio", happy=1), "missing_valid_scores"),
            ({"status": "ok", "label": "happy", "scores": {"happy": 12}},
             record("audio", happy=1), "missing_valid_scores"),
        ):
            with self.subTest(reason=reason):
                result = mean_fusion(left, right)
                self.assertEqual(result["status"], "abstained")
                self.assertEqual(result["reason"], reason)

    def test_invalid_distributions_and_missing_or_duplicate_native_labels(self):
        for values in ({"a": True}, {"a": float("nan")}, {"a": float("inf")},
                       {"a": -0.1, "b": 1.1}, {"a": 0.8}, {}, {"": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                distribution(values)
        for scores in ({"neutral": 1}, native("text", neutral=0.5, mystery=0.5),
                       native("text", neutral=0.5, 平범함=0.5)):
            with self.assertRaises(ValueError):
                scored_reply("text", scores, "a" * 40)
        self.assertEqual(decision(dict.fromkeys(LABELS, 0.2)), (None, "tied_scores"))
        self.assertAlmostEqual(sum(scored_emotion.softmax([10001, 10000])), 1)

    def test_normalizer_rejects_forged_scores_and_revision(self):
        reply = scored_reply("text", native("text", happy=1), "a" * 40)
        self.assertEqual(emotion._normalize(reply, "text")["label"], "happy")
        for change in ({"label": "sad"}, {"score_kind": "logits"},
                       {"probabilities": {"happy": 1}}, {"reason": "invented"}):
            with self.assertRaises(ValueError):
                emotion._normalize(reply | change, "text")


class ScoredAdapterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.request = {"input_kind": "text", "model_id": TEXT_MODEL, "version": "a" * 40,
                        "transcript": "행복해요", "settings": {"seed": 0, "temperature": 0,
                        "quantization": "none", "prompt_version": "native-softmax-v1"}}

    def test_text_uses_cpu_local_safetensors_and_softmax(self):
        torch, transformers = MagicMock(), MagicMock()
        model = MagicMock()
        model.config.id2label = dict(enumerate(sorted(TEXT_CLASSES)))
        model.return_value.logits.__getitem__.return_value.tolist.return_value = [0.0] * 8
        loader = transformers.AutoModelForSequenceClassification.from_pretrained
        loader.return_value = (model, {"missing_keys": []})
        with patch.dict(sys.modules, torch=torch, transformers=transformers):
            result = scored_emotion.run("text", self.root, self.request)
        self.assertEqual(result["reason"], "unsupported_emotion")
        self.assertEqual(len(result["scores"]), 8)
        self.assertEqual(loader.call_args.kwargs["trust_remote_code"], False)
        self.assertTrue(loader.call_args.kwargs["local_files_only"])
        self.assertTrue(loader.call_args.kwargs["use_safetensors"])
        model.to.assert_called_once_with("cpu")
        transformers.AutoTokenizer.from_pretrained.return_value.assert_called_once_with(
            self.request["transcript"], return_tensors="pt", truncation=True, max_length=512)

    def test_audio_consumes_native_softmax_without_second_softmax(self):
        wav = self.root / "speech.wav"
        wav.touch()
        request = self.request | {"input_kind": "audio", "model_id": AUDIO_MODEL, "wav_path": str(wav)}
        sf, torch, np, funasr = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        samples = MagicMock(ndim=1)
        samples.__len__.return_value = 16000
        sf.read.return_value = (samples, 16000)
        np.isfinite.return_value.all.return_value = True
        scores = native("audio", happy=0.8, neutral=0.2)
        scores["<unk>"] = scores.pop("unknown")
        array_scores = MagicMock()
        array_scores.tolist.return_value = list(scores.values())
        model = funasr.AutoModel.return_value
        model.generate.return_value = [{"labels": list(scores), "scores": array_scores}]
        with patch.dict(sys.modules, soundfile=sf, torch=torch, numpy=np, funasr=funasr):
            result = scored_emotion.run("audio", self.root, request)
        self.assertEqual(result["probabilities"]["happy"], 0.8)
        self.assertEqual(result["model_revision"], "a" * 40)
        self.assertFalse(funasr.AutoModel.call_args.kwargs["trust_remote_code"])
        self.assertEqual(funasr.AutoModel.call_args.kwargs["device"], "cpu")
        self.assertFalse(model.generate.call_args.kwargs["extract_embedding"])

    def test_validation_missing_model_and_safe_cli(self):
        with self.assertRaises(scored_emotion.AdapterFailure):
            scored_emotion.run("text", self.root, self.request | {"version": "main"})
        missing = self.root / "absent"
        result = subprocess.run([sys.executable, "-B", "-m", "tools.scored_emotion", "text", "--model", str(missing)],
                                input=json.dumps(self.request), encoding="utf-8", capture_output=True, timeout=10)
        reply = json.loads(result.stdout)
        self.assertEqual(reply["diagnostic"], {"stage": "model_load", "code": "model_missing"})
        self.assertNotIn("행복해요", result.stdout + result.stderr)

    def test_mean_mode_round_trip_uses_protocol_and_legacy_mode_is_explicit(self):
        wav = self.root / "speech.wav"
        wav.write_bytes(b"audio")
        settings = {}
        for kind, values in (("text", {"happy": 0.6, "sad": 0.4}),
                             ("audio", {"sad": 0.5, "happy": 0.45, "neutral": 0.05})):
            reply = scored_reply(kind, native(kind, **values), "a" * 40)
            script = self.root / f"{kind}.py"
            script.write_text("import json; print(" + repr(json.dumps(reply)) + ")", encoding="utf-8")
            settings[f"EMOLABEL_EMOTION_{kind.upper()}"] = json.dumps({
                "command": [sys.executable, str(script)], "model_id": TEXT_MODEL if kind == "text" else AUDIO_MODEL,
                "version": "a" * 40, "quantization": "none", "prompt_version": "native-softmax-v1"})
        inputs = {"clip_id": "fixed", "input_tag": "original_asr", "transcript": "행복해요", "wav_path": str(wav)}
        with patch.dict(os.environ, settings | {"EMOLABEL_EMOTION_FUSION": "mean-v1"}):
            result = emotion.classify(inputs)
            self.assertEqual(result["combined"]["label"], "happy")
            self.assertTrue(result["model_disagreement"])
            self.assertFalse(result["auto_accept"])
            validate_combined(result)
        with patch.dict(os.environ, settings | {"EMOLABEL_EMOTION_FUSION": "agreement-v1"}):
            self.assertIsNone(emotion.classify(inputs)["combined"]["label"])
        changed = json.loads(settings["EMOLABEL_EMOTION_TEXT"])
        changed["version"] = "b" * 40
        with patch.dict(os.environ, settings | {"EMOLABEL_EMOTION_TEXT": json.dumps(changed),
                                              "EMOLABEL_EMOTION_FUSION": "mean-v1"}):
            result = emotion.classify(inputs)
            self.assertEqual(result["text"]["reason"], "invalid_output")
            self.assertIsNone(result["combined"]["probabilities"])

    def test_connection_checker_does_not_claim_label_only_models_connected(self):
        results = {"text": record("text", happy=1) | {"model_id": TEXT_MODEL},
                   "audio": record("audio", happy=1) | {"model_id": AUDIO_MODEL}}
        results["combined"] = mean_fusion(results["text"], results["audio"])
        with patch("tools.check_emotion_connection.classify", return_value=results):
            self.assertEqual(check({})["connection"], "VERIFIED")
            results["audio"] = {"model_id": AUDIO_MODEL, "status": "ok", "label": "happy"}
            self.assertEqual(check({})["connection"], "FAILED")

    def test_config_fragment_requires_real_files_fixed_revision_and_new_destination(self):
        for name in ("config.json", "model.safetensors", "config.yaml", "model.pt", "tokens.txt"):
            (self.root / name).touch()
        with self.assertRaises(ValueError):
            configure_emotion.model_spec("text", self.root, sys.executable, "main")
        text = configure_emotion.model_spec("text", self.root, sys.executable, "a" * 40)
        audio = configure_emotion.model_spec("audio", self.root, sys.executable, "b" * 40)
        fragment = configure_emotion.env_fragment(text, audio)
        self.assertIn("EMOLABEL_EMOTION_FUSION=mean-v1", fragment)
        self.assertIn(TEXT_MODEL, fragment)
        self.assertIn(AUDIO_MODEL, fragment)
        with self.assertRaises(ValueError):
            configure_emotion.model_spec("video", self.root, sys.executable, "a" * 40)
        fake_venv_py = self.root / "venv-python"
        fake_venv_py.write_bytes(b"")
        orig_resolve = Path.resolve
        with patch.object(Path, "resolve", autospec=True,
                          side_effect=lambda p: self.root / "base-python" if p == fake_venv_py else orig_resolve(p)):
            spec = configure_emotion.model_spec("text", self.root, fake_venv_py, "a" * 40)
        self.assertEqual(spec["command"][0], os.path.abspath(str(fake_venv_py)))
        existing = self.root / "existing-report.json"
        existing.write_text("keep", encoding="utf-8")
        cli = subprocess.run([sys.executable, "-B", "-m", "tools.check_emotion_connection",
                              "--wav", str(self.root / "missing.wav"), "--transcript", "테스트",
                              "--output", str(existing)], capture_output=True, text=True, timeout=10)
        self.assertEqual(cli.returncode, 1)
        self.assertIn("Report already exists", cli.stderr)
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()

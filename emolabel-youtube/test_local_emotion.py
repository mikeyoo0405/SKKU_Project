import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools import local_emotion


class LocalRunnerTests(unittest.TestCase):
    def test_text_uses_frozen_transcript_and_greedy_decoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.touch()
            llama = MagicMock()
            chat = llama.Llama.return_value.create_chat_completion
            chat.return_value = {"choices": [{"message": {"content": " happy\n"}}]}
            request = {"transcript": "진짜 기뻐요", "settings": {
                "seed": 17, "temperature": 0, "prompt_version": "ko-emotion-v1"}}
            with patch.dict(sys.modules, {"llama_cpp": llama}):
                result = local_emotion.classify_text(model, request)
            self.assertEqual(result, {"label": "happy"})
            self.assertEqual(chat.call_args.kwargs["temperature"], 0)
            self.assertEqual(chat.call_args.kwargs["seed"], 17)
            self.assertEqual(json.loads(chat.call_args.kwargs["messages"][1]["content"]),
                             {"transcript": request["transcript"]})
            self.assertEqual(llama.Llama.call_args.kwargs["model_path"], str(model))
            chat.return_value = {"choices": [{"message": {"content": None}}]}
            with patch.dict(sys.modules, {"llama_cpp": llama}), self.assertRaises(local_emotion.AdapterFailure) as error:
                local_emotion.classify_text(model, request)
            self.assertEqual(error.exception.reply["diagnostic"]["stage"], "output_validation")

    def test_audio_uses_original_top_class_and_preserves_raw_scores(self):
        names = {0: "joy", 1: "fear", 2: "LABEL_2"}
        result = local_emotion.audio_result([1, 3, -0.5], names, {})
        self.assertEqual(result["label"], "abstain")
        self.assertEqual(result["scores"], {"joy": 1, "fear": 3, "LABEL_2": -0.5})
        self.assertEqual(local_emotion.audio_result([4, 2, 1], names, {})["label"], "happy")
        self.assertEqual(local_emotion.audio_result([1, 2, 4], names, {"LABEL_2": "sad"})["label"], "sad")
        self.assertEqual(local_emotion.audio_result([4, 4, 1], names, {})["label"], "abstain")
        self.assertEqual(local_emotion.audio_result([2.0, 1.0], {"0": "anger", "1": "neutral"}, {})["label"], "angry")
        with self.assertRaises(ValueError):
            local_emotion.audio_result([1.0, 2.0], {"0": "neutral"}, {})

    def test_cli_fails_cleanly_for_missing_local_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / "tools" / "local_emotion.py"),
                "text", "--model", str(Path(tmp) / "model.gguf")
            ], input=json.dumps({"input_kind": "text", "transcript": "테스트",
                                 "settings": {"prompt_version": "ko-emotion-v1", "temperature": 0, "seed": 0}}),
                encoding="utf-8", capture_output=True, timeout=10, check=True)
        reply = json.loads(result.stdout)
        self.assertEqual(reply["error"], "model_unavailable")
        self.assertEqual(reply["diagnostic"], {"stage": "model_load", "code": "model_missing"})

    def test_model_load_and_inference_failures_have_safe_distinct_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.touch()
            request = {"transcript": "PRIVATE_TRANSCRIPT", "settings": {
                "seed": 0, "temperature": 0, "prompt_version": "ko-emotion-v1"}}
            for stage in ("model_load", "inference"):
                llama = MagicMock()
                target = llama.Llama if stage == "model_load" else llama.Llama.return_value.create_chat_completion
                target.side_effect = RuntimeError("SECRET_API_TOKEN PRIVATE_TRANSCRIPT /private/account/model")
                with patch.dict(sys.modules, {"llama_cpp": llama}), self.assertRaises(local_emotion.AdapterFailure) as error:
                    local_emotion.classify_text(model, request)
                self.assertEqual(error.exception.reply["diagnostic"]["stage"], stage)
                for private in ("SECRET_API_TOKEN", "PRIVATE_TRANSCRIPT", "/private/account"):
                    self.assertNotIn(private, json.dumps(error.exception.reply))

    def test_malformed_request_is_json_failure_without_private_input(self):
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).parent / "tools/local_emotion.py"),
                                 "text", "--model", "missing.gguf"],
                                input=b'{"PRIVATE_TRANSCRIPT":', capture_output=True, timeout=10, check=True)
        reply = json.loads(result.stdout)
        self.assertEqual(reply["diagnostic"], {"stage": "input_validation", "code": "invalid_request"})
        self.assertNotIn(b"PRIVATE_TRANSCRIPT", result.stdout + result.stderr)

    def test_audio_errors_identify_input_load_preprocessing_inference_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "audio.wav"
            wav.touch()
            for stage in ("input_validation", "model_load", "preprocessing", "inference", "output_validation"):
                samples = MagicMock(ndim=1)
                samples.__len__.return_value = 32
                sf, torch, transformers = MagicMock(), MagicMock(), MagicMock()
                sf.read.return_value = (samples, 8000 if stage == "input_validation" else 16000)
                extractor = transformers.AutoFeatureExtractor.from_pretrained.return_value
                extractor.sampling_rate = 16000
                loader = transformers.AutoModelForAudioClassification.from_pretrained
                model = loader.return_value
                model.config.id2label = {0: "anger", 1: "neutral"}
                model.return_value.logits.__getitem__.return_value.tolist.return_value = [2, 1]
                if stage == "model_load":
                    loader.side_effect = RuntimeError("SECRET_TOKEN")
                elif stage == "preprocessing":
                    extractor.side_effect = RuntimeError("SECRET_TOKEN")
                elif stage == "inference":
                    model.side_effect = RuntimeError("SECRET_TOKEN")
                elif stage == "output_validation":
                    model.return_value.logits.__getitem__.return_value.tolist.return_value = [float("nan"), 1]
                with self.subTest(stage=stage), patch.dict(sys.modules, {"soundfile": sf, "torch": torch, "transformers": transformers}), self.assertRaises(local_emotion.AdapterFailure) as error:
                    local_emotion.classify_audio(root, {"wav_path": str(wav), "settings": {"seed": 0}}, {})
                self.assertEqual(error.exception.reply["diagnostic"]["stage"], stage)
                self.assertNotIn("SECRET_TOKEN", json.dumps(error.exception.reply))

    def test_missing_dependency_is_distinct_from_bad_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.touch()
            request = {"transcript": "test", "settings": {
                "seed": 0, "temperature": 0, "prompt_version": "ko-emotion-v1"}}
            with patch.dict(sys.modules, {"llama_cpp": None}), self.assertRaises(local_emotion.AdapterFailure) as error:
                local_emotion.classify_text(model, request)
            self.assertEqual(error.exception.reply, {"error": "model_unavailable", "diagnostic": {
                "stage": "model_load", "code": "dependency_missing"}})

    def test_audio_checkpoint_is_loaded_offline_and_its_logits_drive_the_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            wav.touch()
            samples = MagicMock(ndim=1)
            samples.__len__.return_value = 32
            soundfile, torch, transformers = MagicMock(), MagicMock(), MagicMock()
            soundfile.read.return_value = (samples, 16000)
            feature_loader = transformers.AutoFeatureExtractor.from_pretrained
            feature_loader.return_value.sampling_rate = 16000
            model_loader = transformers.AutoModelForAudioClassification.from_pretrained
            model = model_loader.return_value
            model.config.id2label = {0: "anger", 1: "neutral"}
            model.return_value.logits.__getitem__.return_value.tolist.return_value = [2.0, 1.0]
            with patch.dict(sys.modules, {"soundfile": soundfile, "torch": torch, "transformers": transformers}):
                result = local_emotion.classify_audio(Path(tmp), {"wav_path": str(wav), "settings": {"seed": 9}}, {})
            self.assertEqual(result["label"], "angry")
            offline = {"local_files_only": True, "trust_remote_code": False}
            feature_loader.assert_called_once_with(tmp, **offline)
            model_loader.assert_called_once_with(tmp, weights_only=True, **offline)
            torch.manual_seed.assert_called_once_with(9)

    def test_wav2vec2_speech_classification_routes_to_custom_head_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({
                "model_type": "wav2vec2",
                "architectures": ["Wav2Vec2ForSpeechClassification"],
            }), encoding="utf-8")
            wav = root / "audio.wav"
            wav.touch()
            samples = MagicMock(ndim=1)
            samples.__len__.return_value = 32
            soundfile, torch, transformers = MagicMock(), MagicMock(), MagicMock()
            soundfile.read.return_value = (samples, 16000)
            transformers.AutoFeatureExtractor.from_pretrained.return_value.sampling_rate = 16000
            custom_model = MagicMock()
            custom_model.config.id2label = {"0": "기쁨", "2": "분노"}
            custom_model.return_value.logits.__getitem__.return_value.tolist.return_value = [0.2, 2.5]
            with patch.dict(sys.modules, {"soundfile": soundfile, "torch": torch, "transformers": transformers}), \
                 patch.object(local_emotion, "_load_wav2vec2_speech_cls", return_value=custom_model) as loader:
                # Provide 2-class mapping matching logits length 2 via indices 0 and 1
                custom_model.config.id2label = {"0": "기쁨", "1": "분노"}
                result = local_emotion.classify_audio(root, {"wav_path": str(wav), "settings": {"seed": 0}},
                                                      {"기쁨": "happy", "분노": "angry"})
            self.assertEqual(result["label"], "angry")
            loader.assert_called_once_with(root, {"local_files_only": True, "trust_remote_code": False})
            transformers.AutoModelForAudioClassification.from_pretrained.assert_not_called()

    def test_boolean_temperature_and_nonstring_wav_path_fail_input_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.gguf"
            model.touch()
            with self.assertRaises(local_emotion.AdapterFailure) as error:
                local_emotion.classify_text(model, {"transcript": "테스트", "settings": {
                    "seed": 0, "temperature": False, "prompt_version": "ko-emotion-v1"}})
            self.assertEqual(error.exception.reply["diagnostic"], {
                "stage": "input_validation", "code": "invalid_settings"})
            reply = local_emotion.classify_audio(root, {"wav_path": 12345, "settings": {"seed": 0}}, {})
            self.assertEqual(reply, {"error": "missing_input", "diagnostic": {
                "stage": "input_validation", "code": "missing_input"}})

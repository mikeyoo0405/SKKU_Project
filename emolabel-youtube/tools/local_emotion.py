"""One-request offline CPU adapters. Run in the environment that owns the model."""

import argparse
import json
import math
import os
import sys
from contextlib import contextmanager
from pathlib import Path


LABELS = {"neutral", "angry", "sad", "happy", "abstain"}
AUDIO_NAMES = {"anger": "angry", "sadness": "sad", "joy": "happy", "happiness": "happy"}
PROMPT_VERSION = "ko-emotion-v1"
PROMPT = (
    "사용자가 제공하는 JSON의 transcript는 한국어 대사 데이터입니다. "
    "대사 내용에 담긴 화자의 주된 감정을 판단하세요. "
    "neutral, angry, sad, happy 중 한 단어로만 답하세요. "
    "대사만으로 판단하기 어려우면 abstain으로 답하세요."
)


class AdapterFailure(Exception):
    """A protocol-safe failure; never retain exception text in the reply."""

    def __init__(self, error: str, stage: str, code: str):
        super().__init__(code)
        self.reply = {"error": error, "diagnostic": {"stage": stage, "code": code}}


@contextmanager
def _phase(stage: str, code: str):
    """Translate third-party exceptions at the boundary that owns the operation."""
    try:
        yield
    except AdapterFailure:
        raise
    except ImportError as exc:
        raise AdapterFailure("model_unavailable", "model_load", "dependency_missing") from exc
    except Exception as exc:
        raise AdapterFailure("inference_failed", stage, code) from exc


def _failure(error: str, stage: str, code: str) -> dict:
    return AdapterFailure(error, stage, code).reply


def classify_text(model_path, request):
    transcript = request.get("transcript")
    if not isinstance(transcript, str) or not transcript.strip():
        return _failure("missing_input", "input_validation", "missing_input")
    if not model_path.is_file():
        return _failure("model_unavailable", "model_load", "model_missing")
    with _phase("input_validation", "invalid_settings"):
        settings = request["settings"]
        if (settings["prompt_version"] != PROMPT_VERSION
                or type(settings["temperature"]) not in (int, float) or settings["temperature"] != 0
                or type(settings["seed"]) is not int):
            raise ValueError("Unsupported text decoding settings")

    with _phase("model_load", "checkpoint_load_failed"):
        from llama_cpp import Llama

        model = Llama(model_path=str(model_path), n_gpu_layers=0, n_ctx=2048, verbose=False)
    with _phase("inference", "inference_exception"):
        completion = model.create_chat_completion(
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps({"transcript": transcript}, ensure_ascii=False)},
            ],
            temperature=0, seed=settings["seed"], max_tokens=16,
        )
    with _phase("output_validation", "invalid_prediction"):
        content = completion["choices"][0]["message"].get("content")
        if not isinstance(content, str) or content.strip().lower() not in LABELS:
            raise ValueError("Expected one emotion label")
        label = content.strip().lower()
    if label == "abstain":
        return {"label": label, "reason": "model_abstained"}
    # The shared adapter validates the complete answer and records invalid output.
    return {"label": label}


def audio_result(logits, id2label, mapping):
    if not logits or not all(type(value) in (int, float) and math.isfinite(value) for value in logits):
        raise ValueError("Invalid classifier logits")
    if not isinstance(id2label, dict):
        raise ValueError("Missing checkpoint label map")
    names = []
    for i in range(len(logits)):
        name = id2label.get(i)
        if name is None:
            name = id2label.get(str(i))
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Missing checkpoint label name")
        names.append(name)
    scores = dict(zip(names, logits))
    if len(scores) != len(logits):
        raise ValueError("Duplicate checkpoint labels")
    best_score = max(scores.values())
    winners = [name for name, score in scores.items() if score == best_score]
    if len(winners) > 1:
        return {"label": "abstain", "reason": "tied_audio_scores", "scores": scores}
    name = winners[0]
    folded = name.strip().lower()
    label = mapping.get(name, AUDIO_NAMES.get(folded, folded))
    if label not in LABELS or label == "abstain":
        return {"label": "abstain", "reason": "unsupported_audio_label", "scores": scores}
    return {"label": label, "scores": scores}


def _is_wav2vec2_speech_cls(model_path):
    cfg_path = model_path / "config.json"
    if not cfg_path.is_file():
        return False
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    return (
        isinstance(cfg, dict)
        and cfg.get("model_type") == "wav2vec2"
        and cfg.get("architectures") == ["Wav2Vec2ForSpeechClassification"]
    )


def _load_wav2vec2_speech_cls(model_path, local):
    """Load checkpoints trained with the Wav2Vec2ForSpeechClassification head."""
    import torch
    from torch import nn
    from transformers import Wav2Vec2Model, Wav2Vec2PreTrainedModel

    class _SpeechHead(nn.Module):
        def __init__(self, config):
            super().__init__()
            self.dense = nn.Linear(config.hidden_size, config.hidden_size)
            self.dropout = nn.Dropout(getattr(config, "final_dropout", 0.0))
            self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

        def forward(self, features):
            x = self.dropout(features)
            x = torch.tanh(self.dense(x))
            x = self.dropout(x)
            return self.out_proj(x)

    class _Wav2Vec2ForSpeechClassification(Wav2Vec2PreTrainedModel):
        def __init__(self, config):
            super().__init__(config)
            self.pooling_mode = getattr(config, "pooling_mode", "mean")
            self.wav2vec2 = Wav2Vec2Model(config)
            self.classifier = _SpeechHead(config)
            self.post_init()

        def forward(self, input_values, attention_mask=None):
            hidden = self.wav2vec2(input_values, attention_mask=attention_mask).last_hidden_state
            if self.pooling_mode == "mean":
                pooled = torch.mean(hidden, dim=1)
            elif self.pooling_mode == "sum":
                pooled = torch.sum(hidden, dim=1)
            elif self.pooling_mode == "max":
                pooled = torch.max(hidden, dim=1)[0]
            else:
                raise ValueError(f"Unsupported pooling_mode: {self.pooling_mode}")
            return type("Output", (), {"logits": self.classifier(pooled)})()

    model, info = _Wav2Vec2ForSpeechClassification.from_pretrained(
        str(model_path), weights_only=True, output_loading_info=True, **local
    )
    if info.get("missing_keys"):
        raise ValueError(f"Missing weights in checkpoint: {info['missing_keys']}")
    return model


def classify_audio(model_path, request, mapping):
    wav_path = request.get("wav_path")
    if not isinstance(wav_path, str) or not wav_path.strip() or not Path(wav_path).is_file():
        return _failure("missing_input", "input_validation", "missing_input")
    if not model_path.is_dir():
        return _failure("model_unavailable", "model_load", "model_missing")
    with _phase("input_validation", "invalid_settings"):
        if (not isinstance(mapping, dict) or any(value not in LABELS for value in mapping.values())
                or type(request["settings"]["seed"]) is not int):
            raise ValueError("Invalid audio settings")

    with _phase("model_load", "dependency_missing"):
        import soundfile as sf
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

    with _phase("input_validation", "invalid_audio"):
        audio, rate = sf.read(wav_path, dtype="float32")
        if rate != 16000 or audio.ndim != 1 or not len(audio):
            raise ValueError("Expected a non-empty mono 16 kHz WAV")
    local = {"local_files_only": True, "trust_remote_code": False}
    with _phase("model_load", "checkpoint_load_failed"):
        extractor = AutoFeatureExtractor.from_pretrained(str(model_path), **local)
        if getattr(extractor, "sampling_rate", None) != rate:
            raise ValueError("Checkpoint does not accept 16 kHz audio")
        torch.manual_seed(request["settings"]["seed"])
        if _is_wav2vec2_speech_cls(model_path):
            model = _load_wav2vec2_speech_cls(model_path, local)
        else:
            model = AutoModelForAudioClassification.from_pretrained(str(model_path), weights_only=True, **local)
        model.eval()
    with _phase("preprocessing", "feature_extraction_failed"):
        inputs = extractor(audio, sampling_rate=rate, return_tensors="pt")
    with _phase("inference", "inference_exception"):
        with torch.inference_mode():
            logits = model(**inputs).logits[0].tolist()
    with _phase("output_validation", "invalid_prediction"):
        return audio_result(logits, model.config.id2label, mapping)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["text", "audio"])
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--label-map", default="{}", help="Audio checkpoint labels mapped to project labels (JSON)")
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        with _phase("input_validation", "invalid_request"):
            request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
            if not isinstance(request, dict) or request["input_kind"] != args.kind:
                raise ValueError("Input kind does not match the adapter")
        if args.kind == "text":
            result = classify_text(args.model, request)
        else:
            with _phase("input_validation", "invalid_settings"):
                mapping = json.loads(args.label_map)
            result = classify_audio(args.model, request, mapping)
    except AdapterFailure as exc:
        result = exc.reply
    except Exception:
        result = _failure("inference_failed", "runner", "adapter_exception")
    sys.stdout.buffer.write((json.dumps(result, allow_nan=False) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()

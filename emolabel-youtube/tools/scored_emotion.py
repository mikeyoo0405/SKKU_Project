"""Offline CPU adapters for HowRU text and emotion2vec+ base probability outputs."""
import argparse
from contextlib import redirect_stdout
import json
import math
import os
from pathlib import Path
import sys

from app.emotion_scores import AUDIO_MODEL, TEXT_MODEL, scored_reply
from tools.local_emotion import AdapterFailure, _failure, _phase


def softmax(logits: list[float]) -> list[float]:
    if not logits or any(type(v) not in (int, float) or not math.isfinite(v) for v in logits):
        raise ValueError("Invalid logits")
    maximum = max(logits)
    values = [math.exp(v - maximum) for v in logits]
    total = math.fsum(values)
    return [v / total for v in values]


def named_scores(names: list[str], values: list[float]) -> dict[str, float]:
    if (len(names) != len(values) or any(not isinstance(n, str) or not n.strip() for n in names)
            or len(set(names)) != len(names)):
        raise ValueError("Invalid native labels")
    return dict(zip(names, values))


def classify_text(model_path: Path, request: dict) -> dict:
    with _phase("model_load", "dependency_missing"):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
    local = {"local_files_only": True, "trust_remote_code": False}
    with _phase("model_load", "checkpoint_load_failed"):
        torch.manual_seed(request["settings"]["seed"])
        tokenizer = AutoTokenizer.from_pretrained(str(model_path), **local)
        model, info = AutoModelForSequenceClassification.from_pretrained(
            str(model_path), use_safetensors=True, output_loading_info=True, **local)
        if info.get("missing_keys") or info.get("mismatched_keys"):
            raise ValueError("Incomplete checkpoint")
        model.to("cpu").eval()
    with _phase("preprocessing", "feature_extraction_failed"):
        inputs = tokenizer(request["transcript"], return_tensors="pt", truncation=True, max_length=512)
    with _phase("inference", "inference_exception"):
        with torch.inference_mode():
            logits = model(**inputs).logits[0].tolist()
    with _phase("output_validation", "invalid_prediction"):
        labels = model.config.id2label
        names = [labels.get(i, labels.get(str(i))) for i in range(len(logits))]
        return scored_reply("text", named_scores(names, softmax(logits)), request["version"])


def classify_audio(model_path: Path, request: dict) -> dict:
    with _phase("model_load", "dependency_missing"):
        import numpy as np
        import soundfile as sf
        import torch
        from funasr import AutoModel
    with _phase("input_validation", "invalid_audio"):
        samples, rate = sf.read(request["wav_path"], dtype="float32")
        if rate != 16000 or samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all():
            raise ValueError("Expected finite mono 16 kHz audio")
    with _phase("model_load", "checkpoint_load_failed"):
        torch.manual_seed(request["settings"]["seed"])
        model = AutoModel(model=str(model_path), device="cpu", disable_update=True,
                          trust_remote_code=False, disable_pbar=True)
    with _phase("inference", "inference_exception"):
        with torch.inference_mode():
            result = model.generate(input=samples, fs=16000, granularity="utterance", extract_embedding=False)
    with _phase("output_validation", "invalid_prediction"):
        if not isinstance(result, list) or len(result) != 1:
            raise ValueError("Expected one utterance")
        raw_scores = result[0]["scores"]
        if hasattr(raw_scores, "tolist"):
            raw_scores = raw_scores.tolist()
        if not isinstance(raw_scores, (list, tuple)) or any(type(v) is bool for v in raw_scores):
            raise ValueError("Invalid score sequence")
        scores = [float(v) for v in raw_scores]
        return scored_reply("audio", named_scores(result[0]["labels"], scores), request["version"])


def run(kind: str, model_path: Path, request: dict) -> dict:
    with _phase("input_validation", "invalid_request"):
        if not isinstance(request, dict) or request.get("input_kind") != kind:
            raise ValueError("Wrong modality")
        expected = TEXT_MODEL if kind == "text" else AUDIO_MODEL
        if request.get("model_id") != expected:
            raise ValueError("Wrong model identity")
    with _phase("input_validation", "invalid_settings"):
        settings = request["settings"]
        if (type(settings["seed"]) is not int or type(settings["temperature"]) not in (int, float)
                or settings["temperature"] != 0 or settings["quantization"] != "none"
                or settings["prompt_version"] != "native-softmax-v1"
                or not isinstance(request.get("version"), str) or not request["version"].strip()
                or request["version"].lower() in {"main", "latest", "unknown"}):
            raise ValueError("Expected native classifier settings and a fixed revision")
    with _phase("input_validation", "missing_input"):
        value = request.get("transcript" if kind == "text" else "wav_path")
        if not isinstance(value, str) or not value.strip() or (kind == "audio" and not Path(value).is_file()):
            raise AdapterFailure("missing_input", "input_validation", "missing_input")
    if not model_path.is_dir():
        return _failure("model_unavailable", "model_load", "model_missing")
    return classify_text(model_path, request) if kind == "text" else classify_audio(model_path, request)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["text", "audio"])
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    try:
        with _phase("input_validation", "invalid_request"):
            request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        # Third-party progress messages must not corrupt the one-JSON stdout protocol.
        with redirect_stdout(sys.stderr):
            result = run(args.kind, args.model.resolve(), request)
    except AdapterFailure as exc:
        result = exc.reply
    except Exception:
        result = _failure("inference_failed", "runner", "adapter_exception")
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()

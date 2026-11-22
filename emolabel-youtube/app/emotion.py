"""Classify fixed clip inputs with separately installed offline model executables."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from threading import Thread
from time import perf_counter

from . import config
from .emotion_scores import mean_fusion, validate_scored


MAX_RESULT_BYTES = 65536
RUNNER_ERRORS = {"model_unavailable", "missing_input", "inference_failed"}
DIAGNOSTIC_CODES = {
    "configuration": {"model_not_configured", "invalid_configuration"},
    "input_validation": {"missing_input", "invalid_request", "invalid_settings", "invalid_audio", "input_unreadable"},
    "model_load": {"model_missing", "dependency_missing", "checkpoint_load_failed"},
    "preprocessing": {"feature_extraction_failed"},
    "inference": {"inference_exception", "timeout"},
    "output_validation": {"invalid_prediction", "invalid_protocol_output"},
    "process_launch": {"executable_missing", "spawn_failed"},
    "process_exit": {"nonzero_exit"},
    "runner": {"legacy_runner_error", "adapter_exception"},
}


def _diagnostic(stage: str, code: str) -> dict:
    return {"stage": stage, "code": code}


def _validated_diagnostic(value) -> dict:
    """Accept only protocol constants, never paths, exception messages or stderr."""
    if not isinstance(value, dict) or set(value) - {"stage", "code", "returncode"}:
        raise ValueError("Invalid diagnostic fields")
    stage, code = value.get("stage"), value.get("code")
    if (not isinstance(stage, str) or not isinstance(code, str)
            or code not in DIAGNOSTIC_CODES.get(stage, set())):
        raise ValueError("Unknown diagnostic code")
    if "returncode" in value and (stage != "process_exit" or type(value["returncode"]) is not int):
        raise ValueError("Invalid process return code")
    return dict(value)


def _unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("Duplicate model output field")
    return fields


def _stop_tree(process):
    """WSL uses one process group per inference, including runner-created workers."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill.exe", "/pid", str(process.pid), "/t", "/f"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                process.kill()
            except ProcessLookupError:
                pass
    process.wait()


def _run(command, request, timeout):
    with tempfile.TemporaryFile() as stdin:
        stdin.write(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        stdin.seek(0)
        process = subprocess.Popen(
            command, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=config.PROJECT_ROOT, start_new_session=os.name != "nt",
        )
        result = bytearray()

        def read_reply():
            with process.stdout as pipe:
                result.extend(pipe.read(MAX_RESULT_BYTES + 1))

        reader = Thread(target=read_reply, daemon=True)
        deadline = perf_counter() + timeout
        try:
            reader.start()
            reader.join(timeout)
            if reader.is_alive():
                raise subprocess.TimeoutExpired(command, timeout)
            if len(result) > MAX_RESULT_BYTES:
                raise ValueError("Oversized model reply")
            code = process.wait(timeout=max(0, deadline - perf_counter()))
        except BaseException:
            _stop_tree(process)
            reader.join(timeout=1)
            raise
        if code:
            return {"error": "inference_failed", "diagnostic": {
                "stage": "process_exit", "code": "nonzero_exit", "returncode": code}}
    return json.loads(result.decode("utf-8"), object_pairs_hook=_unique_fields)


def _normalize(reply, kind=None):
    if not isinstance(reply, dict):
        raise ValueError("Expected one JSON object")
    if "error" in reply:
        if "label" in reply or not isinstance(reply["error"], str) or reply["error"] not in RUNNER_ERRORS:
            raise ValueError("Invalid model error")
        diagnostic = _validated_diagnostic(reply["diagnostic"]) if "diagnostic" in reply else _diagnostic("runner", "legacy_runner_error")
        return {"status": "failed", "reason": reply["error"], "label": None, "diagnostic": diagnostic}
    label = reply.get("label")
    if not isinstance(label, str):
        raise ValueError("Missing label")
    label = label.strip().lower()
    extra = {}
    if "probabilities" in reply or "score_kind" in reply:
        validate_scored(dict(reply, label=label), kind)
        extra = {k: reply[k] for k in ("probabilities", "score_kind", "label_map_version", "model_revision")}
    scores = reply.get("scores")
    if scores is not None and (not isinstance(scores, dict) or not all(
        type(score) in (float, int) and math.isfinite(score) for score in scores.values()
    )):
        raise ValueError("Invalid raw scores")
    if label == "abstain":
        reason = reply.get("reason", "model_abstained")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Invalid abstention reason")
        return {"status": "abstained", "reason": reason, "label": None, "scores": scores, "diagnostic": None, **extra}
    if label not in config.EMOTIONS:
        raise ValueError("Unknown label")
    return {"status": "ok", "label": label, "reason": None, "scores": scores, "diagnostic": None, **extra}


def _classify_one(kind, inputs):
    started = perf_counter()
    record = {"clip_id": inputs["clip_id"], "input_tag": inputs["input_tag"], "input_kind": kind,
              "model_id": None, "version": None, "settings": None, "label": None, "scores": None,
              "status": "failed", "reason": "model_unavailable",
              "diagnostic": _diagnostic("configuration", "model_not_configured")}
    stage = "configuration"
    try:
        try:
            spec = config.emotion_model(kind)
        except (ValueError, OverflowError, RecursionError):
            record["reason"] = "invalid_configuration"
            record["diagnostic"] = _diagnostic("configuration", "invalid_configuration")
            return record
        if spec is None:
            return record
        settings = {k: spec[k] for k in ("seed", "temperature", "quantization", "prompt_version")}
        command = spec["command"]
        record.update(model_id=spec["model_id"], version=spec["version"], settings=settings,
                      executable=shutil.which(command[0]) or command[0], timeout_s=spec["timeout_s"],
                      command_sha256=hashlib.sha256(json.dumps(command).encode()).hexdigest())
        request = {key: record[key] for key in
                   ("clip_id", "input_tag", "input_kind", "model_id", "version", "settings")}
        stage = "input_validation"
        if kind == "text":
            transcript = inputs.get("transcript")
            if not isinstance(transcript, str) or not transcript.strip():
                record["reason"] = "missing_input"
                record["diagnostic"] = _diagnostic("input_validation", "missing_input")
                return record
            request["transcript"] = transcript
        else:
            wav = inputs.get("wav_path")
            if (not isinstance(wav, (str, Path)) or not str(wav).strip()
                    or not Path(wav).is_file() or Path(wav).stat().st_size == 0):
                record["reason"] = "missing_input"
                record["diagnostic"] = _diagnostic("input_validation", "missing_input")
                return record
            request["wav_path"] = str(Path(wav).resolve())
        stage = "runner"
        reply = _normalize(_run(command, request, spec["timeout_s"]), kind)
        if "model_revision" in reply and reply["model_revision"] != spec["version"]:
            raise ValueError("Model revision mismatch")
        record.update(reply)
    except subprocess.TimeoutExpired:
        record["reason"] = "timeout"
        record["diagnostic"] = _diagnostic("inference", "timeout")
    except FileNotFoundError:
        record["reason"] = "missing_input" if stage == "input_validation" else "model_unavailable"
        record["diagnostic"] = (_diagnostic("input_validation", "missing_input") if stage == "input_validation"
                                else _diagnostic("process_launch", "executable_missing"))
    except OSError:
        record["reason"] = "inference_failed"
        record["diagnostic"] = (_diagnostic("input_validation", "input_unreadable") if stage == "input_validation"
                                else _diagnostic("process_launch", "spawn_failed"))
    except (ValueError, OverflowError, RecursionError, TypeError):
        record["reason"] = "invalid_output"
        record["diagnostic"] = _diagnostic("output_validation", "invalid_protocol_output")
    finally:
        record["elapsed_s"] = round(perf_counter() - started, 6)
    return record


def classify(inputs: dict) -> dict:
    """Run each modality sequentially. Neither a failed model nor fusion changes a review."""
    text = _classify_one("text", inputs)
    audio = _classify_one("audio", inputs)
    both_ok = text["status"] == audio["status"] == "ok"
    disagreement = both_ok and text["label"] != audio["label"]
    label = text["label"] if both_ok and not disagreement else None
    combined = {"model_id": "agreement", "version": "1", "input_kind": "text+audio",
                "clip_id": inputs["clip_id"], "input_tag": inputs["input_tag"],
                "status": "ok" if label else "abstained", "label": label, "scores": None,
                "reason": "model_disagreement" if disagreement else None,
                "elapsed_s": round(text["elapsed_s"] + audio["elapsed_s"], 6)}
    if not both_ok:
        combined["reason"] = "missing_valid_prediction"
    mode = os.getenv("EMOLABEL_EMOTION_FUSION", "agreement-v1").strip()
    if mode == "mean-v1":
        combined.update(mean_fusion(text, audio))
    elif mode != "agreement-v1":
        combined.update(model_id="invalid-fusion", status="abstained", label=None, reason="invalid_configuration")
    return {"text": text, "audio": audio, "combined": combined,
            "model_disagreement": disagreement, "auto_accept": False}

"""Run installed models on one real clip and report score-contract acceptance."""
import argparse
import json
from pathlib import Path

from app.emotion import classify
from app.emotion_scores import AUDIO_MODEL, TEXT_MODEL, validate_combined, validate_scored


def check(inputs: dict) -> dict:
    results = classify(inputs)
    verified = False
    try:
        for kind, model_id in (("text", TEXT_MODEL), ("audio", AUDIO_MODEL)):
            record = results[kind]
            if record.get("model_id") != model_id or record.get("status") == "failed":
                raise ValueError("Expected final model")
            validate_scored(record, kind)
        verified = validate_combined(results) == "mean_score" and results["combined"]["probabilities"] is not None
    except (ValueError, TypeError, KeyError):
        pass
    return {"schema_version": 1, "connection": "VERIFIED" if verified else "FAILED",
            "scope": "one real input; model loading, inference and mean-score contract only",
            "quality": "NOT EVALUATED: human labels required", "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", type=Path, required=True, help="Real mono 16 kHz speech WAV")
    parser.add_argument("--transcript", required=True, help="Transcript of that same clip")
    parser.add_argument("--output", type=Path, required=True, help="New report path under ignored data/")
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(1, "Report already exists; choose a new --output filename. Existing report preserved.\n")
    report = check({"clip_id": "connection-check", "input_tag": "original_asr",
                    "wav_path": str(args.wav.resolve()), "transcript": args.transcript})
    try:
        payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    except OSError:
        parser.exit(1, "Report not written. Use a new writable output path.\n")
    print(json.dumps({"connection": report["connection"], "report": str(args.output)}))
    if report["connection"] != "VERIFIED":
        parser.exit(1)


if __name__ == "__main__":
    main()

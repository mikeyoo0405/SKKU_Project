"""Create an exclusive dotenv fragment for the final two local classifiers."""
import argparse
import json
import os
from pathlib import Path
import re

from app.emotion_scores import AUDIO_MODEL, TEXT_MODEL


def model_spec(kind: str, model: Path, python: Path, revision: str, timeout: int = 120) -> dict:
    if kind not in ("text", "audio"):
        raise ValueError("Unknown modality")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Use the downloaded Hugging Face commit SHA (40 lowercase hex characters)")
    # Keep the venv Python symlink path intact so Linux venvs load pyvenv.cfg.
    model = Path(model).expanduser().resolve()
    python = Path(os.path.abspath(os.path.expanduser(str(python))))
    required = ("config.json", "model.safetensors") if kind == "text" else ("config.yaml", "model.pt", "tokens.txt")
    if not python.is_file() or any(not (model / name).is_file() for name in required):
        raise ValueError("Local Python or required checkpoint files are missing")
    return {"command": [str(python), "-m", "tools.scored_emotion", kind, "--model", str(model)],
            "model_id": TEXT_MODEL if kind == "text" else AUDIO_MODEL,
            "version": revision, "quantization": "none", "prompt_version": "native-softmax-v1",
            "seed": 0, "timeout_s": timeout}


def env_fragment(text: dict, audio: dict) -> str:
    lines = ["# Generated local classifier settings; source with set -a in Ubuntu.",
             "EMOLABEL_EMOTION_FUSION=mean-v1"]
    for kind, spec in (("TEXT", text), ("AUDIO", audio)):
        value = json.dumps(spec, ensure_ascii=False)
        if any(char in value for char in ("'", "\n", "\r")):
            raise ValueError("Model paths cannot contain shell quotes or newlines")
        lines.append(f"EMOLABEL_EMOTION_{kind}='{value}'")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True, help="Absolute Python path in the model runtime")
    for kind in ("text", "audio"):
        parser.add_argument(f"--{kind}-model", type=Path, required=True)
        parser.add_argument(f"--{kind}-revision", required=True, help="Downloaded checkpoint commit SHA")
    parser.add_argument("--output", type=Path, required=True, help="New .env fragment; never overwrites a file")
    args = parser.parse_args()
    try:
        text = model_spec("text", args.text_model, args.python, args.text_revision)
        audio = model_spec("audio", args.audio_model, args.python, args.audio_revision)
        payload = env_fragment(text, audio)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Configuration not created: {exc}\n")
    print("Created local model settings. Activate them before running batch inference or the UI.")


if __name__ == "__main__":
    main()

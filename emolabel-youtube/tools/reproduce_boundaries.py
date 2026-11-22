"""Read-only R0 inventory. VAD crossings are suspects, never human-confirmed speech cuts."""
import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import wave
from pathlib import Path

from .evaluate_models import read_json


BOUNDARY_EPSILON_S = 0.001


def crossing(start, end, intervals):
    return {"start_inside_vad": any(left + BOUNDARY_EPSILON_S < start < right - BOUNDARY_EPSILON_S for left, right in intervals),
            "end_inside_vad": any(left + BOUNDARY_EPSILON_S < end < right - BOUNDARY_EPSILON_S for left, right in intervals)}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(project):
    project = Path(project)
    workspace = project / "data" / "workspace"
    report = {"schema_version": 1, "status": "NOT RUN", "candidate_count": 0,
              "human_confirmed_failures": 0, "human_confirmed_normal": 0,
              "ui_browser_check": "NOT RUN: browser playback and listening required",
              "notes": ["VAD crossings only identify suspects; VAD is not a word/speaker boundary oracle",
                        "Duration checks cannot establish semantic completeness or audio alignment"],
              "videos": [], "errors": []}
    if not workspace.is_dir():
        report["errors"].append("Workspace unavailable; provide accessible project data or run in Ubuntu")
        return report
    ffprobe = shutil.which("ffprobe")
    for folder in sorted(workspace.iterdir()):
        if not folder.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]{11}", folder.name):
            continue
        try:
            state = read_json(folder / "state.json")
            if state.get("status") != "completed":
                continue
            asr = read_json(folder / "asr.json")
            vad = read_json(folder / "vad.json")
            if not isinstance(asr.get("segments"), list) or not isinstance(vad, list):
                raise ValueError("Invalid ASR/VAD artifacts")
            for pair in vad:
                if (not isinstance(pair, list) or len(pair) != 2
                        or not all(type(x) in (int, float) and math.isfinite(x) for x in pair)
                        or pair[0] < 0 or pair[0] >= pair[1]):
                    raise ValueError("Invalid VAD interval")
            video = {"video_id": folder.name, "selection_version": state.get("selection_version"),
                     "fingerprints": {name: digest(folder / name) for name in ("state.json", "asr.json", "vad.json")},
                     "candidates": []}
            source = project / "data" / "downloads" / f"{folder.name}.mp4"
            video["source_sha256"] = digest(source) if source.is_file() else None
            duration_s = state.get("duration_s")
            if type(duration_s) not in (int, float) or not math.isfinite(duration_s) or duration_s <= 0:
                raise ValueError("Invalid video duration")
            max_end_ms = round(duration_s * 1000)
            for row in state["candidates"]:
                key, start, end = row["clip_id"], row["start_s"], row["end_s"]
                if (not isinstance(key, str) or not re.fullmatch(re.escape(folder.name) + r"_\d{7,}_\d{7,}", key)
                        or not all(type(x) in (int, float) and math.isfinite(x) for x in (start, end))
                        or not 0 <= start < end or round(end * 1000) > max_end_ms):
                    raise ValueError("Invalid candidate identity or bounds")
                matches = [seg for seg in asr["segments"] if abs(seg["start"] - start) <= .001
                           and abs(seg["end"] - end) <= .001 and seg["text"].strip() == row["transcript"]]
                risks = crossing(start, end, vad)
                item = {"clip_id": key, "start_s": start, "end_s": end,
                        "exact_asr_boundary_match": bool(matches), **risks,
                        "source_asr_clip_id": row.get("source_asr_clip_id"),
                        "asr_start_s": row.get("asr_start_s"), "asr_end_s": row.get("asr_end_s"),
                        "boundary_adjustment_ms": row.get("boundary_adjustment_ms"),
                        "boundary_reason": row.get("boundary_reason"),
                        "quality_flags": row.get("quality_flags", []),
                        "classification": "boundary_suspect" if any(risks.values()) else "control_candidate_unconfirmed",
                        "human_review": "NOT RUN", "media": {}}
                directory = folder / "clips" / key
                wav = directory / "audio_16k.wav"
                if wav.is_file():
                    with wave.open(str(wav), "rb") as stream:
                        framerate = stream.getframerate()
                        if framerate <= 0:
                            raise ValueError("Invalid WAV frame rate")
                        duration = stream.getnframes() / framerate
                        item["media"]["wav"] = {"duration_s": duration, "delta_s": duration - (end - start),
                                                 "duration_check": "PASS" if abs(duration - (end - start)) <= .05 else "FAIL",
                                                 "sha256": digest(wav)}
                else:
                    item["media"]["wav"] = {"duration_check": "NOT RUN: missing WAV"}
                clip = directory / "clip.mp4"
                if ffprobe and clip.is_file():
                    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                           "-of", "json", str(clip)], capture_output=True, check=True, timeout=30)
                    duration = float(json.loads(proc.stdout)["format"]["duration"])
                    if not math.isfinite(duration):
                        raise ValueError("Invalid video duration")
                    item["media"]["mp4"] = {"duration_s": duration, "delta_s": duration - (end - start),
                                             "duration_check": "PASS" if abs(duration - (end - start)) <= .15 else "FAIL",
                                             "sha256": digest(clip)}
                else:
                    item["media"]["mp4"] = {"duration_check": "NOT RUN: missing FFprobe or MP4"}
                video["candidates"].append(item)
            report["videos"].append(video)
        except (OSError, ValueError, TypeError, KeyError, wave.Error, subprocess.SubprocessError) as exc:
            report["errors"].append({"video_id": folder.name, "error_type": type(exc).__name__})
    report["candidate_count"] = sum(len(video["candidates"]) for video in report["videos"])
    if report["candidate_count"]:
        report["status"] = "PARTIAL: automated inventory only; human confirmation required"
    else:
        report["errors"].append("No completed, readable candidate artifacts found")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.project)
    try:
        payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    except FileExistsError:
        parser.exit(1, "Report already exists; choose a new --output filename. Existing report preserved.\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Could not save boundary report: {type(exc).__name__}\n")
    print(json.dumps({"status": report["status"], "candidates": report["candidate_count"], "report": str(args.output)}))
    return 0 if report["candidate_count"] and not report["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

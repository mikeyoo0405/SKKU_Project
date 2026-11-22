"""Compare archived predictions on frozen inputs, with optional independent human gold.

No inference or annotation writes. All missing/failed predictions retain their denominator.
Run: python -m tools.evaluate_models --help
"""
import argparse
import json
import math
import re
from pathlib import Path

from app.emotion_scores import validate_combined

LABELS = ("neutral", "angry", "sad", "happy")
CLIP_ID = re.compile(r"[A-Za-z0-9_-]{11}_\d{7,}_\d{7,}")
SHA = re.compile(r"[a-f0-9]{64}")


def read_json(path):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate JSON field")
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def manifest_rows(manifest):
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or manifest.get("input_tag") != "original_asr"
            or not isinstance(manifest.get("candidates"), list) or not manifest["candidates"]):
        raise ValueError("Expected a nonempty original_asr manifest (schema 1)")
    rows = {}
    for row in manifest["candidates"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid manifest row")
        key, hashes = row.get("clip_id"), row.get("media_sha256")
        if (not isinstance(key, str) or not CLIP_ID.fullmatch(key) or key in rows
                or not isinstance(row.get("transcript"), str)
                or not isinstance(hashes, dict) or set(hashes) != {"wav", "video"}
                or not all(isinstance(h, str) and SHA.fullmatch(h)
                           for h in [row.get("source_sha256"), *hashes.values()])):
            raise ValueError("Invalid or duplicate frozen candidate")
        left, right = key.rsplit("_", 2)[1:]
        if int(left) >= int(right):
            raise ValueError("Invalid candidate boundaries")
        rows[key] = row
    return rows


def gold_labels(gold, rows):
    if (not isinstance(gold, dict) or type(gold.get("schema_version")) is not int
            or gold["schema_version"] != 1
            or gold.get("independent") is not True
            or not isinstance(gold.get("annotator_id"), str) or not gold["annotator_id"].strip()
            or not isinstance(gold.get("candidates"), list)):
        raise ValueError("Gold requires schema 1, independent=true, annotator_id and candidates")
    labels = {}
    for row in gold["candidates"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid gold row")
        key = row.get("clip_id")
        if not isinstance(key, str) or key not in rows or key in labels or row.get("emotion") not in LABELS:
            raise ValueError("Unknown/duplicate gold ID or invalid emotion")
        for field in ("source_sha256", "media_sha256"):
            if row.get(field) != rows[key][field]:
                raise ValueError("Gold input fingerprint mismatch")
        labels[key] = row["emotion"]
    if set(labels) != set(rows):
        raise ValueError("Gold must cover every frozen candidate; do not silently drop samples")
    return labels


def prediction(record):
    if not isinstance(record, dict) or record.get("status") not in ("ok", "abstained", "failed"):
        raise ValueError("Invalid prediction status")
    status, label = record["status"], record.get("label")
    if ((status == "ok" and label not in LABELS) or (status != "ok" and label is not None)):
        raise ValueError("Invalid prediction label")
    elapsed = record.get("elapsed_s")
    if elapsed is not None and (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
        raise ValueError("Invalid inference time")
    return {"status": status, "label": label, "elapsed_s": elapsed}


def _metrics(truth, predictions):
    matrix = {label: {column: 0 for column in (*LABELS, "unpredicted")} for label in LABELS}
    for actual, pred in zip(truth, predictions):
        matrix[actual][pred["label"] if pred["status"] == "ok" else "unpredicted"] += 1
    recall, f1 = {}, []
    for label in LABELS:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        fp = sum(matrix[other][label] for other in LABELS if other != label)
        fn = support - tp
        recall[label] = tp / support if support else None
        f1.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0)
    return {"n": len(truth), "accuracy": sum(matrix[k][k] for k in LABELS) / len(truth) if truth else None,
            "macro_f1": sum(f1) / len(LABELS) if truth else None,
            "recall": recall, "confusion_matrix": matrix,
            "support": {k: sum(matrix[k].values()) for k in LABELS}}


def score(predictions, truth=None):
    n = len(predictions)
    if not n:
        raise ValueError("At least one prediction is required")
    counts = {s: sum(p["status"] == s for p in predictions) for s in ("ok", "abstained", "failed", "missing")}
    times = [p["elapsed_s"] for p in predictions if p.get("elapsed_s") is not None]
    result = {"n": n, "counts": counts, "coverage": counts["ok"] / n,
              "abstention_rate": counts["abstained"] / n, "failure_rate": counts["failed"] / n,
              "missing_rate": counts["missing"] / n, "unpredicted_rate": 1 - counts["ok"] / n,
              "mean_elapsed_s": sum(times) / len(times) if times else None,
              "timed_count": len(times), "quality": "NOT RUN: independent gold not supplied"}
    if truth is not None:
        valid = [(t, p) for t, p in zip(truth, predictions) if p["status"] == "ok"]
        result.update(quality="DEVELOPMENT COMPARISON; final acceptance requires held-out data",
                      all_samples=_metrics(truth, predictions),
                      predicted_only=_metrics([t for t, _ in valid], [p for _, p in valid]))
    return result


def compare(manifest, runs, gold=None):
    rows = manifest_rows(manifest)
    labels = gold_labels(gold, rows) if gold is not None else None
    reports = []
    for run in runs:
        folder = Path(run)
        if not folder.is_dir():
            raise ValueError("Run directory is missing")
        if read_json(folder / "manifest.json") != manifest:
            raise ValueError("Run manifest differs from the frozen evaluation input")
        summary = read_json(folder / "summary.json")
        if not isinstance(summary, dict) or summary.get("input_tag") != "original_asr":
            raise ValueError("Only original_asr completed runs are comparable")
        run_id = summary.get("run_id")
        if (not isinstance(run_id, str) or not run_id
                or type(summary.get("candidates")) is not int or summary["candidates"] != len(rows)):
            raise ValueError("Invalid run summary")
        extras = {p.stem for p in folder.glob("*.json")} - {"manifest", "summary"} - rows.keys()
        if extras:
            raise ValueError("Unknown candidate IDs in run directory")
        variants = {k: [] for k in ("text", "audio", "agreement", "text_priority_warning")}
        fusion_kind = None
        metadata = {}
        warnings = 0
        for key, frozen in rows.items():
            path = folder / f"{key}.json"
            if not path.is_file():
                for values in variants.values():
                    values.append({"status": "missing", "label": None, "elapsed_s": None})
                continue
            entry = read_json(path)
            if not isinstance(entry, dict) or type(entry.get("schema_version")) is not int or entry["schema_version"] != 1:
                raise ValueError("Invalid run entry")
            if any(entry.get(f) != frozen[f] for f in ("clip_id", "transcript", "source_sha256", "media_sha256")):
                raise ValueError("Prediction input fingerprint/transcript mismatch")
            if (entry.get("run_id") != run_id or entry.get("input_tag") != "original_asr"
                    or entry.get("input_transcript") != frozen["transcript"]):
                raise ValueError("Prediction run/input mismatch")
            if entry.get("input_error"):
                for values in variants.values():
                    values.append({"status": "failed", "label": None, "elapsed_s": None})
                continue
            records = entry.get("results")
            if not isinstance(records, dict):
                raise ValueError("Missing model results")
            current = {}
            for kind in ("text", "audio", "combined"):
                record = records.get(kind)
                current[kind] = prediction(record)
                if record.get("clip_id") != key or record.get("input_tag") != "original_asr":
                    raise ValueError("Model result input mismatch")
                model = {f: record.get(f) for f in ("model_id", "version", "settings", "command_sha256")}
                if kind in metadata and metadata[kind] != model:
                    raise ValueError("Mixed model settings within one run")
                metadata[kind] = model
            text, audio, combined = (current[k] for k in ("text", "audio", "combined"))
            current_fusion = validate_combined(records)
            if fusion_kind is not None and fusion_kind != current_fusion:
                raise ValueError("Mixed fusion versions within one run")
            fusion_kind = current_fusion
            warnings += int(text["status"] == audio["status"] == "ok" and text["label"] != audio["label"])
            priority = dict(text, elapsed_s=(text["elapsed_s"] + audio["elapsed_s"]
                                            if text["elapsed_s"] is not None and audio["elapsed_s"] is not None else None))
            for kind, value in (("text", text), ("audio", audio), ("agreement", combined), ("text_priority_warning", priority)):
                variants[kind].append(value)
        if fusion_kind == "mean_score":
            variants["mean_score"] = variants.pop("agreement")
        reports.append({"run_id": run_id, "run_tag": summary.get("run_tag", ""), "models": metadata,
                        "disagreement_warnings": warnings,
                        "variants": {k: score(v, [labels[key] for key in rows] if labels else None)
                                     for k, v in variants.items()}})
    if not reports:
        raise ValueError("At least one run is required")
    return {"schema_version": 1, "candidate_count": len(rows), "independent_gold": labels is not None,
            "final_acceptance": "NOT EVALUATED: held-out split and human time not verified",
            "notes": ["text_priority_warning uses text only; warnings do not change labels or app behavior",
                      "text_priority_warning latency includes sequential text and audio inference",
                      "Absent classes have null recall and zero F1 in the four-class macro average"],
            "runs": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New JSON report; existing files are never replaced")
    args = parser.parse_args()
    try:
        report = compare(read_json(args.manifest), args.runs, read_json(args.gold) if args.gold else None)
        payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    except FileExistsError:
        parser.exit(1, "Evaluation report already exists; choose a new --output filename. Existing report preserved.\n")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f"Evaluation failed: {exc}\n")
    print(json.dumps({"report": str(args.output), "candidates": report["candidate_count"],
                      "runs": len(report["runs"]), "independent_gold": report["independent_gold"]}))


if __name__ == "__main__":
    main()

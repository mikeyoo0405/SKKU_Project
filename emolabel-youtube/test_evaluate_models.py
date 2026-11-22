import copy
import json
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

from tools.evaluate_models import LABELS, compare, gold_labels, manifest_rows, read_json, score


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.run = self.root / "run"
        self.run.mkdir()
        rows = [{"clip_id": f"pilot_00001_{i * 3000:07d}_{i * 3000 + 2000:07d}",
                 "transcript": "synthetic", "source_sha256": "a" * 64,
                 "media_sha256": {"wav": str(i) * 64, "video": "b" * 64}} for i in range(4)]
        self.manifest = {"schema_version": 1, "input_tag": "original_asr", "candidates": rows}
        self.gold = {"schema_version": 1, "independent": True, "annotator_id": "synthetic-test",
                     "candidates": [{k: row[k] for k in ("clip_id", "source_sha256", "media_sha256")}
                                    | {"emotion": label} for row, label in zip(rows, LABELS)]}
        self.write("manifest.json", self.manifest)
        self.write("summary.json", {"run_id": "r1", "input_tag": "original_asr", "candidates": 4})
        self.entries = []
        text_values = [("ok", "neutral"), ("ok", "happy"), ("abstained", None), ("failed", None)]
        for row, actual, (status, label) in zip(rows, LABELS, text_values):
            key = row["clip_id"]
            def result(kind, status, label):
                return {"clip_id": key, "input_tag": "original_asr", "status": status,
                        "label": label, "model_id": kind, "version": "1", "settings": {}, "elapsed_s": 1.0}
            entry = {"schema_version": 1, **row, "input_transcript": row["transcript"],
                     "run_id": "r1", "input_tag": "original_asr", "results": {
                         "text": result("text", status, label), "audio": result("audio", "ok", actual),
                         "combined": result("agreement", "ok" if label == actual else "abstained",
                                            actual if label == actual else None)}}
            self.entries.append(entry)
            self.write(key + ".json", entry)

    def write(self, name, value):
        (self.run / name).write_text(json.dumps(value), encoding="utf-8")

    def evaluate(self, gold=True):
        return compare(self.manifest, [self.run], self.gold if gold else None)

    def test_known_four_class_metrics_and_strict_agreement(self):
        report = self.evaluate()
        variants = report["runs"][0]["variants"]
        text = variants["text"]
        self.assertEqual(text["counts"], {"ok": 2, "abstained": 1, "failed": 1, "missing": 0})
        self.assertEqual(text["all_samples"]["accuracy"], .25)
        self.assertEqual(text["all_samples"]["macro_f1"], .25)
        self.assertEqual(text["predicted_only"]["accuracy"], .5)
        self.assertEqual(text["all_samples"]["recall"], {"neutral": 1, "angry": 0, "sad": 0, "happy": 0})
        self.assertEqual(variants["audio"]["all_samples"]["macro_f1"], 1)
        self.assertEqual(variants["agreement"]["abstention_rate"], .75)
        self.assertEqual(variants["text_priority_warning"]["all_samples"], text["all_samples"])
        self.assertEqual(variants["text_priority_warning"]["mean_elapsed_s"], 2)
        self.assertEqual(report["runs"][0]["disagreement_warnings"], 1)

    def test_no_gold_only_reports_coverage_never_accuracy(self):
        result = self.evaluate(gold=False)
        self.assertFalse(result["independent_gold"])
        self.assertNotIn("all_samples", result["runs"][0]["variants"]["audio"])

    def test_mean_run_is_validated_and_not_mislabeled_as_agreement(self):
        from app.emotion_scores import AUDIO_CLASSES, TEXT_CLASSES, mean_fusion, scored_reply
        for entry in self.entries:
            records = entry["results"]
            for kind, classes, values in (("text", TEXT_CLASSES, {"happy": 0.6, "sad": 0.4}),
                                          ("audio", AUDIO_CLASSES, {"happy": 0.45, "sad": 0.5, "neutral": 0.05})):
                records[kind].update(scored_reply(kind, dict.fromkeys(classes, 0) | values, "a" * 40), status="ok", version="a" * 40)
            records["combined"].update(mean_fusion(records["text"], records["audio"]))
            self.write(entry["clip_id"] + ".json", entry)
        variants = self.evaluate()["runs"][0]["variants"]
        self.assertNotIn("agreement", variants)
        self.assertEqual(variants["mean_score"]["counts"]["ok"], 4)
        self.entries[0]["results"]["combined"]["label"] = "sad"
        self.write(self.entries[0]["clip_id"] + ".json", self.entries[0])
        with self.assertRaises(ValueError):
            self.evaluate()

    def test_missing_prediction_keeps_denominator(self):
        (self.run / (self.entries[0]["clip_id"] + ".json")).unlink()
        text = self.evaluate()["runs"][0]["variants"]["text"]
        self.assertEqual(text["n"], 4)
        self.assertEqual(text["counts"]["missing"], 1)
        self.assertEqual(text["all_samples"]["accuracy"], 0)

    def test_all_failed_or_abstained_have_no_predicted_only_accuracy(self):
        for status in ("abstained", "failed", "missing"):
            result = score([{"status": status, "label": None, "elapsed_s": None}] * 4, list(LABELS))
            self.assertEqual(result["coverage"], 0)
            self.assertEqual(result["all_samples"]["macro_f1"], 0)
            self.assertIsNone(result["predicted_only"]["accuracy"])

    def test_gold_must_be_independent_complete_unique_and_fingerprinted(self):
        variants = []
        for change in ({"independent": False}, {"candidates": self.gold["candidates"][:-1]},
                       {"candidates": self.gold["candidates"] + [self.gold["candidates"][0]]}):
            variants.append(self.gold | change)
        for field, value in (("emotion", "fear"), ("clip_id", "unknown"), ("source_sha256", "c" * 64)):
            altered = copy.deepcopy(self.gold)
            altered["candidates"][0][field] = value
            variants.append(altered)
        for gold in variants:
            with self.subTest(gold=gold), self.assertRaises(ValueError):
                gold_labels(gold, manifest_rows(self.manifest))

    def test_manifest_duplicate_and_changed_run_inputs_rejected(self):
        bad = copy.deepcopy(self.manifest)
        bad["candidates"].append(bad["candidates"][0])
        with self.assertRaises(ValueError):
            manifest_rows(bad)
        bad = copy.deepcopy(self.manifest)
        bad["candidates"][0]["transcript"] = "changed"
        self.write("manifest.json", bad)
        with self.assertRaises(ValueError):
            self.evaluate()

    def test_bad_label_settings_time_and_fingerprint_rejected(self):
        original = self.entries[1]
        for mutation in ("label", "settings", "elapsed_s", "source_sha256", "agreement"):
            bad = copy.deepcopy(original)
            if mutation == "source_sha256":
                bad[mutation] = "c" * 64
            elif mutation == "agreement":
                bad["results"]["combined"].update(status="ok", label="happy")
            else:
                bad["results"]["text"][mutation] = {"label": "fear", "settings": {"seed": 4}, "elapsed_s": float("nan")}[mutation]
            self.write(bad["clip_id"] + ".json", bad)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.evaluate()
        self.write(original["clip_id"] + ".json", original)

    def test_unknown_result_id_and_duplicate_json_keys_rejected(self):
        self.write("unexpected.json", {})
        with self.assertRaises(ValueError):
            self.evaluate()
        file = self.root / "duplicate.json"
        file.write_text('{"label":"happy","label":"sad"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            read_json(file)

    def test_cli_writes_report_without_modifying_inputs_and_refuses_overwrite(self):
        before = {p.name: p.read_bytes() for p in self.run.iterdir()}
        output = self.root / "report.json"
        command = [sys.executable, "-B", "-m", "tools.evaluate_models", "--manifest",
                   str(self.run / "manifest.json"), "--runs", str(self.run), "--output", str(output)]
        run = subprocess.run(command, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr.decode())
        self.assertFalse(read_json(output)["independent_gold"])
        original = output.read_bytes()
        again = subprocess.run(command, capture_output=True, timeout=10)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual({p.name: p.read_bytes() for p in self.run.iterdir()}, before)

    def test_boolean_schema_version_and_empty_predictions_rejected(self):
        with self.assertRaises(ValueError):
            manifest_rows(self.manifest | {"schema_version": True})
        with self.assertRaises(ValueError):
            gold_labels(self.gold | {"schema_version": True}, manifest_rows(self.manifest))
        with self.assertRaises(ValueError):
            score([])


if __name__ == "__main__":
    unittest.main()

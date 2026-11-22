"""Synthetic R0 evidence, explicitly separate from real speech/listening acceptance."""
import tempfile
import unittest
import json
import wave
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from app import candidates, media
from tools.reproduce_boundaries import audit, crossing


class BoundaryReproductionTests(unittest.TestCase):
    def test_long_continuous_vad_keeps_bounds_and_requires_boundary_review(self):
        segment = {"start": 2, "end": 5, "text": "합성 대사", "no_speech_prob": .1}
        row, = candidates.select("pilot_00001", 10, [segment], [(1, 6)])
        self.assertEqual((row["start_s"], row["end_s"]), (2, 5))
        self.assertEqual(crossing(2, 5, [(1, 6)]), {"start_inside_vad": True, "end_inside_vad": True})
        self.assertIn("speech_continues_before", row["quality_flags"])
        self.assertIn("speech_continues_after", row["quality_flags"])

    def test_complete_control_has_no_vad_crossing(self):
        segment = {"start": 1, "end": 6, "text": "합성 대사", "no_speech_prob": .1}
        row, = candidates.select("pilot_00001", 10, [segment], [(1.2, 5.8)])
        self.assertFalse(any(crossing(row["start_s"], row["end_s"], [(1.2, 5.8)]).values()))

    def test_extraction_forwards_the_same_start_and_duration(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(media, "_run") as run:
            for extract, suffix in ((media.cut_clip, ".mp4"), (media.extract_wav16k, ".wav")):
                extract(Path(tmp) / "source.mp4", Path(tmp) / ("out" + suffix), 2, 3)
                command = run.call_args.args[0]
                self.assertEqual(command[command.index("-ss") + 1], "2.000")
                self.assertEqual(command[command.index("-t") + 1], "3.000")

    def test_missing_real_artifacts_are_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = audit(Path(tmp))
        self.assertEqual(report["status"], "NOT RUN")
        self.assertEqual(report["candidate_count"], 0)
        self.assertEqual(report["human_confirmed_normal"], 0)

    def test_existing_report_is_preserved_with_actionable_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "report.json"
            output.write_text("existing report", encoding="utf-8")
            run = subprocess.run([sys.executable, "-B", "-m", "tools.reproduce_boundaries",
                                  "--project", str(root), "--output", str(output)],
                                 capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 1)
            self.assertIn(b"choose a new --output", run.stderr)
            self.assertNotIn(b"Traceback", run.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing report")

    def test_inventory_separates_suspects_from_unconfirmed_controls_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "data" / "workspace" / "pilot_00001"
            folder.mkdir(parents=True)
            segments = [{"start": a, "end": b, "text": "synthetic", "no_speech_prob": .1}
                        for a, b in ((2, 5), (6, 9))]
            vad = [[1, 5.5], [6.2, 8.8]]
            rows = candidates.select(folder.name, 10, segments, vad)
            for name, contents in (("state.json", {"status": "completed", "duration_s": 10,
                                                   "selection_version": 2, "candidates": rows}),
                                   ("asr.json", {"segments": segments}), ("vad.json", vad)):
                (folder / name).write_text(json.dumps(contents), encoding="utf-8")
            for row in rows:
                directory = folder / "clips" / row["clip_id"]
                directory.mkdir(parents=True)
                with wave.open(str(directory / "audio_16k.wav"), "wb") as wav:
                    wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    wav.writeframes(b"\0\0" * round((row["end_s"] - row["start_s"]) * 16000))
            before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with patch("tools.reproduce_boundaries.shutil.which", return_value=None):
                report = audit(root)
            self.assertEqual(report["candidate_count"], 2)
            kinds = {r["classification"] for r in report["videos"][0]["candidates"]}
            self.assertEqual(kinds, {"boundary_suspect", "control_candidate_unconfirmed"})
            extended = next(r for r in report["videos"][0]["candidates"] if r["asr_start_s"] == 2)
            self.assertEqual(extended["asr_end_s"], 5)
            self.assertEqual(extended["boundary_adjustment_ms"]["end"], 580)
            self.assertTrue(all(r["media"]["wav"]["duration_check"] == "PASS"
                                for r in report["videos"][0]["candidates"]))
            self.assertEqual({str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}, before)

    def test_fractional_ms_duration_and_subms_vad_rounding_are_accepted(self):
        self.assertEqual(crossing(1.0, 5.2, [(1.0, 5.2004)]),
                         {"start_inside_vad": False, "end_inside_vad": False})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "data" / "workspace" / "pilot_00001"
            folder.mkdir(parents=True)
            row = {"clip_id": "pilot_00001_0005000_0010124", "start_s": 5.0, "end_s": 10.124,
                   "transcript": "synthetic"}
            for name, contents in (("state.json", {"status": "completed", "duration_s": 10.1236,
                                                   "selection_version": 2, "candidates": [row]}),
                                   ("asr.json", {"segments": [{"start": 5.0, "end": 10.1236, "text": "synthetic"}]}),
                                   ("vad.json", [[5.1, 10.0]])):
                (folder / name).write_text(json.dumps(contents), encoding="utf-8")
            with patch("tools.reproduce_boundaries.shutil.which", return_value=None):
                report = audit(root)
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()

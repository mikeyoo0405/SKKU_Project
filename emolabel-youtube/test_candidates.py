import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def segment(start, end, text="안녕하세요", probability=0.1):
    return {"start": start, "end": end, "text": text, "no_speech_prob": probability}


class CandidateTests(unittest.TestCase):
    def setUp(self):
        from app.candidates import select
        self.select = select

    def test_ranks_length_then_start_and_deduplicates(self):
        segments = [segment(18, 22), segment(11, 15), segment(4, 10),
                    segment(4, 10), segment(0, 4)]
        rows = self.select("pilot_00001", 22, segments, [(0, 22)])
        self.assertEqual([r["start_s"] for r in rows], [4, 0, 11])
        self.assertEqual(rows[0]["clip_id"], "pilot_00001_0004000_0010000")

    def test_empty_or_unreliable_speech_excluded(self):
        segments = [segment(0, 1, " "), segment(0, 1, probability=0.99), segment(2, 3)]
        self.assertEqual(self.select("pilot_00001", 3, segments, [(0, 1)]), [])
        self.assertEqual(self.select("pilot_00001", 3, [segment(0, 2)], []), [])

    def test_bounds_and_advisory_flags(self):
        rows = self.select("pilot_00001", 3, [segment(0, 4)],
                                 [(1, 1.5), (2.3, 3.5)])
        self.assertEqual((rows[0]["start_s"], rows[0]["end_s"]), (0, 3))
        self.assertEqual(rows[0]["quality_flags"], ["multiple_speech_intervals"])
        self.assertEqual(rows[0]["speaker_status"], "unverified")

    def test_vad_converts_sample_offsets_to_seconds(self):
        from app import qc

        reader = MagicMock(return_value=(SimpleNamespace(ndim=1), 16000))
        with patch.dict("sys.modules", {"soundfile": SimpleNamespace(read=reader)}), \
             patch.object(qc, "_speech_segments", return_value=[{"start": 8000, "end": 32000}]):
            self.assertEqual(qc.speech_intervals(Path("test.wav")), [(0.5, 2.0)])

    def test_partial_vad_keeps_full_transcript_interval(self):
        row, = self.select("pilot_00001", 10, [segment(0, 10)], [(8, 9)])
        self.assertEqual((row["start_s"], row["end_s"]), (0, 10))

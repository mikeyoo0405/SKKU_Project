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
        self.assertEqual(rows[0]["quality_flags"], ["low_speech_ratio", "multiple_speech_intervals"])
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
        self.assertIn("long_edge_silence", row["quality_flags"])
        self.assertIn("low_speech_ratio", row["quality_flags"])

    def test_three_clear_utterances_outrank_silent_pilot_segment(self):
        bounds = [(1, 4), (5, 9), (10, 15)]
        segments = [segment(*pair) for pair in [*bounds, (105.30, 113.96)]]
        speech = [*bounds, (112.30, 113.96)]
        rows = self.select("pilot_00001", 179, segments, speech)
        self.assertEqual([row["start_s"] for row in rows], [10, 5, 1])

    def test_short_vad_tail_is_extended_with_provenance_and_padding(self):
        row, = self.select("pilot_00001", 10, [segment(2, 5)], [(1.8, 5.2)])
        self.assertEqual((row["start_s"], row["end_s"]), (1.72, 5.28))
        self.assertEqual((row["asr_start_s"], row["asr_end_s"]), (2, 5))
        self.assertEqual(row["boundary_adjustment_ms"], {"start": -280, "end": 280})
        self.assertEqual(row["source_asr_clip_id"], "pilot_00001_0002000_0005000")
        self.assertIn("boundary_adjusted_check_transcript", row["quality_flags"])
        self.assertNotIn("speech_continues_after", row["quality_flags"])

    def test_neighboring_asr_is_not_merged_and_cut_gets_a_warning(self):
        rows = self.select("6yrnO_6Hl0I", 60,
                           [segment(47.78, 50.78), segment(50.78, 53.78, "다음 대사")],
                           [(47.6, 50.98), (51.1, 53.6)])
        row = next(r for r in rows if r["asr_start_s"] == 47.78)
        self.assertEqual(row["end_s"], 50.78)
        self.assertIn("speech_continues_after", row["quality_flags"])
        self.assertEqual(row["transcript"], "안녕하세요")

    def test_known_normal_control_is_unchanged(self):
        row, = self.select("DGwuCn-EDfQ", 60, [segment(11, 16)], [(11.1, 15.9)])
        self.assertEqual((row["start_s"], row["end_s"]), (11, 16))
        self.assertEqual(row["quality_flags"], [])
        self.assertEqual(row["boundary_adjustment_ms"], {"start": 0, "end": 0})

    def test_extensions_stay_inside_source_and_maximum_clip_length(self):
        from app import candidates, config
        row, = self.select("pilot_00001", 5, [segment(.1, 4.9)], [(0, 5)])
        self.assertEqual((row["start_s"], row["end_s"]), (0, 5))
        with patch.object(config, "MAX_CLIP_S", 3):
            row, = self.select("pilot_00001", 10, [segment(2, 5)], [(1.8, 5.2)])
        self.assertEqual((row["start_s"], row["end_s"]), (2, 5))
        self.assertIn("speech_continues_after", row["quality_flags"])
        self.assertEqual(candidates.SELECTION_VERSION, 3)

    def test_complete_candidates_outrank_unresolved_continuous_speech(self):
        rows = self.select("pilot_00001", 40,
                           [segment(2, 10), segment(15, 19), segment(22, 26), segment(30, 34)],
                           [(0, 12), (15.1, 18.9), (22.1, 25.9), (30.1, 33.9)])
        self.assertEqual({r["start_s"] for r in rows}, {15, 22, 30})

    def test_boundary_padding_never_enters_a_separate_vad_interval(self):
        row, = self.select("pilot_00001", 10, [segment(2, 5)], [(1.8, 5.2), (5.24, 6)])
        self.assertEqual(row["end_s"], 5.24)
        self.assertNotIn("multiple_speech_intervals", row["quality_flags"])

    def test_padding_clamps_to_nonoverlapping_adjacent_asr_and_max_clip_length(self):
        from app import config
        rows = self.select("pilot_00001", 20,
                           [segment(2, 5), segment(5.2, 8, "다음 대사")],
                           [(1.8, 5.15), (5.3, 7.9)])
        first = next(r for r in rows if r["asr_start_s"] == 2)
        self.assertEqual(first["end_s"], 5.2)
        self.assertNotIn("speech_continues_after", first["quality_flags"])
        self.assertIn("boundary_adjusted_check_transcript", first["quality_flags"])
        with patch.object(config, "MAX_CLIP_S", 15):
            row, = self.select("pilot_00001", 20, [segment(0, 14.7)], [(0.1, 14.95)])
        self.assertEqual((row["start_s"], row["end_s"]), (0, 15))
        self.assertNotIn("speech_continues_after", row["quality_flags"])

    def test_fractional_ms_duration_never_rounds_end_past_source_duration(self):
        row, = self.select("pilot_00001", 10.1236, [segment(5.0, 10.1236)], [(5.1, 10.1)])
        self.assertLessEqual(row["end_s"], 10.1236)
        self.assertEqual(row["end_s"], 10.123)

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, scene


class SceneAnalysisTests(unittest.TestCase):
    def _make_source(self, root: Path, content: bytes = b"video-bytes") -> tuple[Path, list[int]]:
        source = root / "sample.mp4"
        source.write_bytes(content)
        stat = source.stat()
        return source, [stat.st_size, stat.st_mtime_ns]

    def test_off_mode_always_returns_not_analyzed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, stamp = self._make_source(folder)
            scene.ensure_scene("DGwuCn-EDfQ", source, folder, detector=lambda *a, **kw: ([2.5], "0.6.5"))
            warning = scene.segment_warning(folder, stamp, 1.0, 4.0, mode="off")
            self.assertEqual(warning["status"], "not_analyzed")
            self.assertEqual(warning["reason"], "scene_mode_off")
            self.assertEqual(warning["cuts_s"], [])

    def test_has_cuts_vs_no_cuts_and_boundary_exclusivity(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, stamp = self._make_source(folder)
            record, cached = scene.ensure_scene(
                "DGwuCn-EDfQ", source, folder, detector=lambda *a, **kw: ([1.0, 2.5, 4.0, 8.2], "0.6.5")
            )
            self.assertFalse(cached)
            self.assertEqual(record["status"], "ok")

            inside = scene.segment_warning(folder, stamp, 1.0, 4.0, mode="warn")
            self.assertEqual(inside["status"], "has_cuts")
            self.assertEqual(inside["cuts_s"], [2.5])
            self.assertEqual(inside["cut_count"], 1)
            self.assertEqual(inside["source_cuts_s"], [1.0, 2.5, 4.0, 8.2])

            clean = scene.segment_warning(folder, stamp, 4.0, 8.0, mode="warn")
            self.assertEqual(clean["status"], "no_cuts")
            self.assertEqual(clean["cuts_s"], [])
            self.assertEqual(clean["cut_count"], 0)

    def test_cache_reuse_and_invalidation_on_source_or_settings_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, stamp = self._make_source(folder, b"v1")
            calls = []

            def fake_detector(path, **kwargs):
                calls.append(kwargs)
                return ([3.2], "0.6.5")

            _, cached1 = scene.ensure_scene("DGwuCn-EDfQ", source, folder, detector=fake_detector)
            _, cached2 = scene.ensure_scene("DGwuCn-EDfQ", source, folder, detector=fake_detector)
            self.assertFalse(cached1)
            self.assertTrue(cached2)
            self.assertEqual(len(calls), 1)

            # Changing source invalidates stale scene.json immediately.
            source.write_bytes(b"v2-longer-content")
            new_stat = source.stat()
            new_stamp = [new_stat.st_size, new_stat.st_mtime_ns]
            stale = scene.segment_warning(folder, new_stamp, 1.0, 5.0, mode="warn")
            self.assertEqual(stale["status"], "not_analyzed")
            self.assertEqual(stale["reason"], "source_changed")

            _, cached3 = scene.ensure_scene("DGwuCn-EDfQ", source, folder, detector=fake_detector)
            self.assertFalse(cached3)
            self.assertEqual(len(calls), 2)

            # Settings change triggers re-analysis.
            with patch.dict(os.environ, {"EMOLABEL_SCENE_ADAPTIVE_THRESHOLD": "4.5"}):
                _, cached4 = scene.ensure_scene("DGwuCn-EDfQ", source, folder, detector=fake_detector)
                self.assertFalse(cached4)
                self.assertEqual(len(calls), 3)

    def test_unavailable_and_failed_are_distinguished_and_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, stamp = self._make_source(folder)

            def missing(*a, **kw):
                raise scene.SceneUnavailable("missing")

            rec1, cached1 = scene.ensure_scene("DGwuCn-EDfQ", source, folder, detector=missing)
            self.assertFalse(cached1)
            self.assertEqual(rec1["status"], "unavailable")
            warn1 = scene.segment_warning(folder, stamp, 1.0, 4.0, mode="warn")
            self.assertEqual(warn1["status"], "unavailable")
            self.assertEqual(warn1["reason"], "dependency_missing")

            def broken(*a, **kw):
                raise RuntimeError("corrupt frame")

            rec2, cached2 = scene.ensure_scene("DGwuCn-EDfQ", source, folder, detector=broken)
            self.assertFalse(cached2)
            self.assertEqual(rec2["status"], "failed")
            warn2 = scene.segment_warning(folder, stamp, 1.0, 4.0, mode="warn")
            self.assertEqual(warn2["status"], "failed")
            self.assertEqual(warn2["reason"], "detection_failed")

            # Once dependency works, retry succeeds cleanly.
            rec3, cached3 = scene.ensure_scene("DGwuCn-EDfQ", source, folder, detector=lambda *a, **kw: ([2.0], "0.6.5"))
            self.assertFalse(cached3)
            self.assertEqual(rec3["status"], "ok")
            warn3 = scene.segment_warning(folder, stamp, 1.0, 4.0, mode="warn")
            self.assertEqual(warn3["status"], "has_cuts")

    def test_not_run_when_scene_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _, stamp = self._make_source(folder)
            warn = scene.segment_warning(folder, stamp, 1.0, 4.0, mode="warn")
            self.assertEqual(warn["status"], "not_analyzed")
            self.assertEqual(warn["reason"], "not_run")

    def test_invalid_scene_mode_rejected(self):
        with patch.dict(os.environ, {"EMOLABEL_SCENE_MODE": "strict"}):
            with self.assertRaises(ValueError):
                config.scene_mode()


if __name__ == "__main__":
    unittest.main()

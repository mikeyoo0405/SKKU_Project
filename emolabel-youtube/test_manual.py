"""The original manual UI keeps drafts editable and human-confirmed media intact."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import config, main, media, schemas, store


class ManualTests(unittest.TestCase):
    video = "DGwuCn-EDfQ"
    clip = video + "_0001000_0004000"

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.output = root / "output"
        self.output.mkdir()
        for name, value in (("DATA_DIR", root), ("OUTPUT_DIR", self.output), ("DOWNLOADS_DIR", root)):
            self.enterContext(patch.object(config, name, value))
        (root / (self.video + ".mp4")).write_bytes(b"source")
        self.enterContext(patch.object(main.youtube, "get_meta", return_value={"duration_s": 7}))
        self.cut = self.enterContext(patch.object(media, "cut_clip",
            side_effect=lambda src, dest, start, duration: dest.write_bytes(b"cut")))
        self.wav = self.enterContext(patch.object(media, "extract_wav16k",
            side_effect=lambda src, dest, start, duration: dest.write_bytes(b"wav")))
        self.enterContext(patch.object(media, "probe", return_value=dict(
            width=100, height=200, video_codec="h264", audio_codec="aac", fps=30)))
        self.labels = schemas.AnnotationIn(emotion="happy", gender="unknown", valence=4, arousal=4,
                                          transcript="sample", annotator_id="tester")

    def extract(self, overwrite=False):
        return main.extract_clip(self.video, schemas.ExtractRequest(start_s=1, end_s=4, overwrite=overwrite))

    def test_manual_extraction_is_published_only_on_save(self):
        self.extract()
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(main.clip_media(self.clip).path.read_bytes(), b"cut")
        self.assertFalse(store.list_clips(self.video)[0]["annotated"])
        result = main.save_annotation(self.clip, self.labels)
        final = self.output / self.clip
        self.assertEqual(result["labels"]["transcript"], "sample")
        self.assertEqual(json.loads((final / "annotation.json").read_text(encoding="utf-8")), result)
        self.assertEqual(json.loads((final / "prelabels.json").read_text(encoding="utf-8")), {})
        self.assertEqual((final / "audio_16k.wav").read_bytes(), b"wav")
        self.assertTrue(store.list_clips(self.video)[0]["annotated"])

    def test_failed_overwrite_preserves_previous_draft_and_failed_save_can_retry(self):
        self.extract()
        self.wav.side_effect = media.MediaError("test failure")
        with self.assertRaises(HTTPException):
            self.extract(overwrite=True)
        self.assertEqual(store.clip_wav_path(self.clip).read_bytes(), b"wav")
        store.clip_wav_path(self.clip).write_bytes(b"")
        with self.assertRaises(HTTPException):
            main.save_annotation(self.clip, self.labels)
        self.assertFalse((self.output / self.clip).exists())
        store.clip_wav_path(self.clip).write_bytes(b"fixed")
        main.save_annotation(self.clip, self.labels)
        self.assertEqual((self.output / self.clip / "audio_16k.wav").read_bytes(), b"fixed")

    def test_protects_confirmed_batch_output_from_all_manual_mutations(self):
        final = self.output / self.clip
        final.mkdir()
        for name in ("clip.mp4", "audio_16k.wav"):
            (final / name).write_bytes(b"accepted")
        content = b'{"labels":{"emotion":"sad"},"review":{"origin":"batch_review"}}'
        (final / "annotation.json").write_bytes(content)
        for action in (lambda: self.extract(True), lambda: main.prelabel_clip(self.clip),
                       lambda: main.delete_clip(self.clip), lambda: main.save_annotation(self.clip, self.labels)):
            with self.assertRaises(HTTPException) as caught:
                action()
            self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual((final / "annotation.json").read_bytes(), content)
        self.cut.assert_not_called()

    def test_existing_unconfirmed_output_can_still_be_opened_and_saved(self):
        legacy = self.output / self.clip
        legacy.mkdir()
        (legacy / "clip.mp4").write_bytes(b"legacy video")
        (legacy / "audio_16k.wav").write_bytes(b"legacy audio")
        (legacy / "prelabels.json").write_text('{"qc":{"speech":true}}')
        self.assertEqual(main.clip_media(self.clip).path.read_bytes(), b"legacy video")
        record = main.save_annotation(self.clip, self.labels)
        self.assertEqual(record["qc"], {"speech": True})
        self.assertTrue((legacy / "annotation.json").is_file())

    def test_legacy_unconfirmed_draft_can_be_discarded_after_batch_selects_same_id(self):
        legacy = self.output / self.clip
        legacy.mkdir()
        (legacy / "clip.mp4").write_bytes(b"legacy")
        with patch("app.review.owns_candidate_locked", return_value=True):
            main.delete_clip(self.clip)
        self.assertFalse(legacy.exists())

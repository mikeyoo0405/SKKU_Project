"""Offline tests for Phase 1 URL identity and batch duration checks."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app import youtube


class BatchYoutubeTests(unittest.TestCase):
    video_id = "DGwuCn-EDfQ"
    url = f"https://www.youtube.com/shorts/{video_id}"

    def test_url_aliases(self):
        for url in [self.url, f"https://youtu.be/{self.video_id}?t=2",
                    f"https://m.youtube.com/watch?v={self.video_id}&t=1"]:
            self.assertEqual(youtube.video_id_from_url(url), self.video_id)
        for url in ["https://youtu.be/../other", "https://example.com/watch?v=DGwuCn-EDfQ",
                    "https://youtube.com/watch?v=DGwuCn-EDfQ&v=DGwuCn-EDfQ",
                    "ftp://youtu.be/DGwuCn-EDfQ", "https://youtube.com@evil.com/shorts/DGwuCn-EDfQ"]:
            with self.subTest(url=url), self.assertRaises(youtube.YouTubeError):
                youtube.video_id_from_url(url)

    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(youtube.config, "DOWNLOADS_DIR", self.directory))
        self.ydl = MagicMock()
        fake_module = SimpleNamespace(YoutubeDL=self.ydl, utils=SimpleNamespace(DownloadError=RuntimeError))
        self.enterContext(patch.dict("sys.modules", {"yt_dlp": fake_module}))
        self.info = {"id": self.video_id, "duration": 10}
        self.probed = {"duration_s": 10, "video_codec": "h264", "audio_codec": "aac",
                       "width": 320, "height": 640, "fps": 30}
        self.mock_probe = self.enterContext(patch.object(youtube.media, "probe", return_value=self.probed))
        self.ydl.side_effect = self.fake_ydl

    def fake_ydl(self, options):
        session = MagicMock()
        def extract(url, download):
            if download:
                out = Path(options["outtmpl"].replace("%(ext)s", "mp4"))
                out.write_bytes(b"mock media")
            return self.info
        session.__enter__.return_value.extract_info.side_effect = extract
        return session

    def test_limits_before_and_after_download(self):
        self.info["duration"] = 181
        with self.assertRaises(youtube.VideoTooLong):
            youtube.load_video(self.url, max_duration_s=180)
        self.assertEqual(self.ydl.call_count, 1)
        self.info["duration"] = 180
        self.probed["duration_s"] = 181
        with self.assertRaises(youtube.VideoTooLong):
            youtube.load_video(self.url, max_duration_s=180)
        self.assertFalse(youtube.info_path(self.video_id).exists())
        self.assertFalse(youtube.video_path(self.video_id).exists())

    def test_unknown_duration_and_live(self):
        for duration, live in [(None, False), (10, True)]:
            self.info.update(duration=duration, is_live=live)
            with self.assertRaises(youtube.YouTubeError):
                youtube.load_video(self.url, max_duration_s=180)
        self.assertEqual(self.ydl.call_count, 2)

    def test_cache_and_recovery(self):
        youtube.video_path(self.video_id).write_bytes(b"old")
        youtube.info_path(self.video_id).write_text("{", encoding="utf-8")
        record = youtube.load_video(self.url, max_duration_s=180)
        self.assertEqual(record["video_id"], self.video_id)
        self.assertEqual(json.loads(youtube.info_path(self.video_id).read_text(encoding="utf-8")), record)
        calls_after_build = self.ydl.call_count
        self.probed["duration_s"] = 999
        self.assertEqual(youtube.load_video(self.url, max_duration_s=180), record)
        self.assertEqual(self.ydl.call_count, calls_after_build)
        self.assertEqual(self.mock_probe.call_count, 1)

    def test_malformed_probe_metadata_raises_youtube_error(self):
        for bad_info in [None, {}, {"id": 123}, {"id": self.video_id, "duration": "10"}]:
            self.info = bad_info
            with self.subTest(bad_info=bad_info), self.assertRaises(youtube.YouTubeError):
                youtube.load_video(self.url, max_duration_s=180)

    def test_ui_keeps_configured_limit(self):
        self.info["duration"] = 250
        self.probed["duration_s"] = 250
        with patch.object(youtube.config, "MAX_VIDEO_DURATION_S", 900):
            youtube.load_video(self.url)
        self.info["duration"] = 10
        with self.assertRaises(youtube.VideoTooLong):
            youtube.load_video(self.url, max_duration_s=180)

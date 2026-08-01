"""Tests for bot.services.media_downloader.

Instagram carousel (slideshow) posts are reported by yt-dlp as playlists whose
entries are a mix of photos and videos.  download_media() must return every
slide (tagged as "video" or "photo") so the handler can forward the whole post.
"""

import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

from bot.services import media_downloader as md

VIDEO_ENTRY = {
    "id": "reel_abc",
    "formats": [
        {"url": "https://cdn.example.com/v1.mp4", "width": 640, "height": 360, "vcodec": "h264"},
        {"url": "https://cdn.example.com/v2.mp4", "width": 1280, "height": 720, "vcodec": "h264"},
    ],
    "thumbnails": [{"url": "https://cdn.example.com/thumb.jpg", "width": 1080, "height": 1350}],
    "http_headers": {"Referer": "https://www.instagram.com/"},
}

PHOTO_ENTRY = {
    "id": "post_photo",
    "formats": [],
    "thumbnails": [
        {"url": "https://cdn.example.com/p1.jpg", "width": 1080, "height": 1350},
        {"url": "https://cdn.example.com/p0.jpg", "width": 320, "height": 400},
    ],
    "http_headers": {"Referer": "https://www.instagram.com/"},
}


def install_fake_yt_dlp(info):
    """Replace the yt_dlp import with a fake that returns ``info``."""
    fake_yt_dlp = types.ModuleType("yt_dlp")

    class FakeYoutubeDL:
        def __init__(self, opts=None):
            self.opts = opts or {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return info

    fake_yt_dlp.YoutubeDL = FakeYoutubeDL
    sys.modules["yt_dlp"] = fake_yt_dlp


async def fetch_ok(url, headers, path):
    with open(path, "wb") as fh:
        fh.write(b"fake-media-bytes")
    return True


class MediaDownloaderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.out_dir = tempfile.mkdtemp(prefix="media_dl_test_")
        self._yt_dlp_before = sys.modules.get("yt_dlp")

    def tearDown(self):
        shutil.rmtree(self.out_dir, ignore_errors=True)
        if self._yt_dlp_before is None:
            sys.modules.pop("yt_dlp", None)
        else:
            sys.modules["yt_dlp"] = self._yt_dlp_before

    async def _download(self, info):
        install_fake_yt_dlp(info)
        with mock.patch.object(md, "_fetch", new=mock.AsyncMock(side_effect=fetch_ok)):
            return await md.download_media("https://www.instagram.com/p/abc123/", self.out_dir)

    async def test_photo_only_slideshow_downloads_every_photo(self):
        info = {"_type": "playlist", "entries": [PHOTO_ENTRY, PHOTO_ENTRY, PHOTO_ENTRY]}

        items = await self._download(info)

        self.assertEqual([item.kind for item in items], ["photo", "photo", "photo"])
        self.assertTrue(all(os.path.isfile(item.path) for item in items))
        self.assertTrue(all(item.path.startswith(self.out_dir) for item in items))

    async def test_carousel_mixed_keeps_order_and_kind(self):
        info = {"_type": "playlist", "entries": [VIDEO_ENTRY, PHOTO_ENTRY, VIDEO_ENTRY]}

        items = await self._download(info)

        self.assertEqual([item.kind for item in items], ["video", "photo", "video"])
        self.assertTrue(all(os.path.isfile(item.path) for item in items))

    async def test_single_video_uses_single_download(self):
        info = {"id": "reel_solo", "title": "A reel", "formats": VIDEO_ENTRY["formats"]}
        install_fake_yt_dlp(info)
        fake_path = os.path.join(self.out_dir, "solo.mp4")
        with mock.patch.object(
            md, "_download_single", new=mock.AsyncMock(return_value=fake_path)
        ) as single_mock:
            items = await md.download_media("https://www.instagram.com/reel/abc123/", self.out_dir)

        self.assertEqual([item.kind for item in items], ["video"])
        self.assertEqual(items[0].path, fake_path)
        single_mock.assert_awaited_once()

    async def test_extract_failure_returns_empty(self):
        install_fake_yt_dlp(None)

        items = await md.download_media("https://www.instagram.com/p/abc123/", self.out_dir)

        self.assertEqual(items, [])

    async def test_skips_slides_that_fail_to_fetch(self):
        info = {"_type": "playlist", "entries": [PHOTO_ENTRY, VIDEO_ENTRY]}
        install_fake_yt_dlp(info)

        async def fetch_fail(url, headers, path):
            return False

        with mock.patch.object(md, "_fetch", new=mock.AsyncMock(side_effect=fetch_fail)):
            items = await md.download_media("https://www.instagram.com/p/abc123/", self.out_dir)

        self.assertEqual(items, [])

    def test_best_video_format_prefers_highest_resolution(self):
        best = md._best_video_format(VIDEO_ENTRY)

        self.assertEqual(best["height"], 720)
        self.assertEqual(best["url"], "https://cdn.example.com/v2.mp4")

    def test_best_video_format_skips_hls_and_dash_manifests(self):
        entry = {
            "formats": [
                {"url": "https://cdn.example.com/v.m3u8", "width": 720, "height": 1280, "vcodec": "h264"},
                {"url": "https://cdn.example.com/v.mpd", "width": 1080, "height": 1920, "vcodec": "h264"},
                {"url": "https://cdn.example.com/v.mp4", "width": 480, "height": 854, "vcodec": "h264"},
            ]
        }

        best = md._best_video_format(entry)

        self.assertEqual(best["url"], "https://cdn.example.com/v.mp4")

    def test_photo_slide_is_picked_from_thumbnails(self):
        best = md._best_photo(PHOTO_ENTRY)

        self.assertEqual(best["url"], "https://cdn.example.com/p1.jpg")
        self.assertEqual((best["width"], best["height"]), (1080, 1350))


if __name__ == "__main__":
    unittest.main()

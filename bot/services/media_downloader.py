"""Media download helpers for the video-download feature.

Instagram carousel/slideshow posts are reported by yt-dlp as a playlist whose
entries are a mix of photos and videos.  A single yt-dlp run can only produce
one output file, so each slide is downloaded individually here and tagged as a
"video" or a "photo" for the Telegram handler to forward.

yt-dlp is imported lazily (matching the rest of the codebase) so this module
stays importable in unit tests without it being installed.
"""

import asyncio
import glob
import logging
import os
import re
import uuid
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

EXTRACT_TIMEOUT = 45
DOWNLOAD_TIMEOUT = 300
INSTA_REFERER = "https://www.instagram.com/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_SINGLE_FORMAT_SELECTOR = (
    "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
    "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
)


@dataclass
class MediaItem:
    path: str
    kind: str  # "video" | "photo"


def _detect_media_info(url: str) -> dict:
    """Extract a post's info without downloading it.

    ``ignore_no_formats_error`` keeps photo-only slides alive so that their
    thumbnails (i.e. the actual slide image) survive into the entries, and
    ``simulate`` stops yt-dlp from writing playlist metadata files.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        logger.error("yt-dlp is not installed")
        return {}

    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "noplaylist": True,
        "extract_flat": False,
        "ignore_no_formats_error": True,
        "simulate": True,
    }
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


async def download_media(url: str, out_dir: str) -> list[MediaItem]:
    """Download every media item of a post (supports carousel/slideshow)."""
    loop = asyncio.get_running_loop()
    try:
        info = await asyncio.wait_for(
            loop.run_in_executor(None, _detect_media_info, url),
            timeout=EXTRACT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("media extract timed out")
        return []
    except Exception as e:
        logger.error(f"media extract failed: {type(e).__name__}: {e}")
        return []

    if not info:
        return []

    if info.get("_type") == "playlist":
        return await _download_carousel(info, out_dir)

    path = await _download_single(url, out_dir)
    if path:
        return [MediaItem(path=path, kind="video")]
    return []


async def _download_carousel(info: dict, out_dir: str) -> list[MediaItem]:
    items: list[MediaItem] = []
    base = str(uuid.uuid4())[:8]
    for idx, entry in enumerate(info.get("entries") or [], start=1):
        if not isinstance(entry, dict):
            continue
        item = await _download_entry(entry, os.path.join(out_dir, f"{base}_{idx:02d}"))
        if item:
            items.append(item)
    return items


async def _download_entry(entry: dict, out_base: str) -> MediaItem | None:
    headers = dict(entry.get("http_headers") or {})
    headers.setdefault("Referer", INSTA_REFERER)
    headers.setdefault("User-Agent", DEFAULT_USER_AGENT)

    video = _best_video_format(entry)
    if video:
        path = out_base + ".mp4"
        if await _fetch(video["url"], headers, path):
            return MediaItem(path=path, kind="video")
        return None

    photo = _best_photo(entry)
    if photo:
        path = out_base + ".jpg"
        if await _fetch(photo["url"], headers, path):
            return MediaItem(path=path, kind="photo")
    return None


def _best_video_format(entry: dict) -> dict | None:
    """Pick the highest-resolution direct video URL of a slide.

    Manifests (HLS/DASH) are ignored because downloading them would produce a
    playlist file instead of a playable video.
    """
    candidates = []
    for fmt in entry.get("formats") or []:
        url = fmt.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        if fmt.get("vcodec") == "none":
            continue
        if re.search(r"\.(m3u8|mpd)(\?|$)", url, re.IGNORECASE):
            continue
        candidates.append(fmt)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda f: (int(f.get("height") or 0), int(f.get("width") or 0)),
    )


def _best_photo(entry: dict) -> dict | None:
    candidates = [
        t for t in entry.get("thumbnails") or []
        if isinstance(t.get("url"), str) and t["url"].startswith("http")
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda t: (int(t.get("width") or 0), int(t.get("height") or 0)),
    )


async def _fetch(url: str, headers: dict, path: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                with open(path, "wb") as fh:
                    async for chunk in response.aiter_bytes():
                        fh.write(chunk)
        return os.path.getsize(path) > 0
    except Exception as e:
        logger.error(f"direct download failed: {type(e).__name__}: {e}")
        try:
            os.remove(path)
        except OSError:
            pass
        return False


async def _download_single(url: str, out_dir: str) -> str:
    """Download a single (non-carousel) media item via yt-dlp."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return ""

    name = str(uuid.uuid4())[:8]
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": _SINGLE_FORMAT_SELECTOR,
        "outtmpl": os.path.join(out_dir, f"{name}.%(ext)s"),
        "merge_output_format": "mp4",
        "socket_timeout": 30,
        "noplaylist": True,
    }
    loop = asyncio.get_running_loop()
    try:
        info = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: YoutubeDL(opts).extract_info(url, download=True)),
            timeout=DOWNLOAD_TIMEOUT,
        )
        if info:
            files = glob.glob(os.path.join(out_dir, f"{name}.*"))
            if files:
                return max(files, key=os.path.getsize)
            return YoutubeDL(opts).prepare_filename(info)
    except asyncio.TimeoutError:
        logger.error("simple download timed out")
    except Exception as e:
        logger.error(f"simple download failed: {type(e).__name__}: {e}")
    return ""

import asyncio
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.keyboards.inline import back_to_menu_keyboard, cancel_keyboard, download_platforms_keyboard
from bot.middlewares.membership import check_membership

logger = logging.getLogger(__name__)
router = Router()

YT_RE = re.compile(
    r"(https?://)?(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/|live/)|youtu\.be/)([\w-]{11})",
    re.IGNORECASE,
)
INSTA_RE = re.compile(r"(https?://)?(www\.)?instagram\.com/(reel|p|tv)/[\w-]+", re.IGNORECASE)
TIKTOK_RE = re.compile(r"(https?://)?(www\.)?(vm\.)?tiktok\.com/[\w./?=-]+", re.IGNORECASE)

DOWNLOAD_TIMEOUT = 300
EXTRACT_TIMEOUT = 45
TELEGRAM_BOT_FILE_LIMIT_MB = 50
STANDARD_VIDEO_HEIGHTS = (4320, 2160, 1440, 1080, 720, 480, 360)


class DownloadStates(StatesGroup):
    waiting_link = State()
    choosing_quality = State()


def _download_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "migmig_dl")
    os.makedirs(path, exist_ok=True)
    return path


def _standard_quality(width: int, height: int) -> int:
    # Cropped YouTube streams can be 1920x1040 or 1152x649 even though their
    # conventional quality labels are 1080p and 720p. Account for both axes.
    equivalent_height = max(height, round(width * 9 / 16))
    return min(STANDARD_VIDEO_HEIGHTS, key=lambda value: abs(value - equivalent_height))


async def _get_yt_formats(url: str) -> list[dict]:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _yt_extract_formats, url),
            timeout=EXTRACT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("yt-dlp extract timed out")
        return []
    except Exception as e:
        logger.error(f"yt-dlp extract failed: {type(e).__name__}: {e}")
        return []


def _yt_extract_formats(url: str) -> list[dict]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return []

    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "noplaylist": True,
        "extract_flat": False,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = (info or {}).get("title", "video")[:60]
    quality_heights: dict[int, int] = {}
    for fmt in (info or {}).get("formats", []):
        width = int(fmt.get("width") or 0)
        height = int(fmt.get("height") or 0)
        has_video = fmt.get("vcodec") != "none"
        if has_video and height >= 360:
            quality = _standard_quality(width, height)
            # Keep the largest real stream in each user-facing quality tier.
            quality_heights[quality] = max(quality_heights.get(quality, 0), height)

    ffmpeg_available = shutil.which("ffmpeg") is not None
    formats = []
    for quality in sorted(quality_heights, reverse=True):
        height = quality_heights[quality]
        if ffmpeg_available:
            selector = (
                f"bestvideo[height={height}][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
                f"best[height={height}][ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
                f"bestvideo[height={height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height={height}]+bestaudio/"
                f"best[height={height}][ext=mp4]/"
                f"best[height={height}]/"
                f"bestvideo[height<={height}][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
                f"best[height<={height}][ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
                f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]/best"
            )
        else:
            selector = (
                f"best[height={height}][ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
                f"best[height={height}][ext=mp4]/"
                f"best[height={height}]/"
                f"best[height<={height}][ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
                f"best[height<={height}][ext=mp4]/"
                f"best[height<={height}]/best"
            )
        formats.append(
            {
                "id": selector,
                "label": f"📺 {quality}p",
                "height": height,
                "title": title,
            }
        )

    if not formats:
        has_video = any(fmt.get("vcodec") != "none" for fmt in (info or {}).get("formats", []))
        if has_video:
            formats.append(
                {
                    "id": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
                    "label": "📺 بهترین کیفیت",
                    "height": 0,
                    "title": title,
                }
            )

    return formats


def _yt_download(url: str, format_id: str, out_dir: str) -> str:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return ""

    name = str(uuid.uuid4())[:8]
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": format_id,
        "outtmpl": os.path.join(out_dir, f"{name}.%(ext)s"),
        "merge_output_format": "mp4",
        "socket_timeout": 30,
        "noplaylist": True,
        "extract_flat": False,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        if not info:
            return ""
        files = glob.glob(os.path.join(out_dir, f"{name}.*"))
        if files:
            return max(files, key=os.path.getsize)
        return YoutubeDL(opts).prepare_filename(info)
    except Exception as e:
        logger.error(f"yt-dlp download error: {type(e).__name__}: {e}")
        return ""


async def _simple_download(url: str, out_dir: str) -> str:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return ""

    name = str(uuid.uuid4())[:8]
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": (
            "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
            "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
        ),
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


def _video_dimensions(path: str) -> tuple[int, int] | None:
    """Read display dimensions so Telegram keeps the video's aspect ratio."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        stream = json.loads(result.stdout)["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])

        rotation = int(stream.get("tags", {}).get("rotate", 0) or 0)
        for side_data in stream.get("side_data_list", []):
            if "rotation" in side_data:
                rotation = int(side_data["rotation"] or 0)
                break
        if abs(rotation) % 180 == 90:
            width, height = height, width

        return (width, height) if width > 0 and height > 0 else None
    except (KeyError, IndexError, TypeError, ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as e:
        logger.warning("Could not detect video dimensions for %s: %s", path, e)
        return None


async def _send_video(bot: Bot, chat_id: int, path: str, caption: str | None = None) -> None:
    # Telegram only offers "Save to Gallery" for media sent as a video.  A file
    # sent with send_document is treated as a generic attachment, even if it is
    # an MP4 file.
    dimensions = await asyncio.to_thread(_video_dimensions, path)
    send_options = {}
    if dimensions:
        send_options["width"], send_options["height"] = dimensions

    await bot.send_video(
        chat_id=chat_id,
        video=FSInputFile(path, filename=f"video{os.path.splitext(path)[1] or '.mp4'}"),
        caption=caption,
        supports_streaming=True,
        **send_options,
    )


async def _send_upload_action(bot: Bot, chat_id: int) -> None:
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)


@router.callback_query(F.data == "main:download")
async def download_menu(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)
    if not is_member:
        await callback.answer("⚠️ ابتدا در کانال ما عضو شوید. /start", show_alert=True)
        return
    await callback.message.edit_text(
        "🎬 دانلود ویدیو\n\nپلتفرم مورد نظر را انتخاب کنید:",
        reply_markup=download_platforms_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dl:"))
async def download_platform(callback: CallbackQuery, state: FSMContext):
    platform = callback.data.split(":")[1]
    await state.update_data(dl_platform=platform)

    if platform == "youtube":
        await callback.message.edit_text(
            "▶️ لینک ویدیوی یوتیوب را ارسال کنید:\n(مثال: https://youtu.be/xxxxx)",
            reply_markup=cancel_keyboard(),
        )
        await state.set_state(DownloadStates.waiting_link)
    elif platform == "instagram":
        await callback.message.edit_text(
            "📸 لینک پست/ریلز اینستاگرام را ارسال کنید:\n(مثال: https://instagram.com/reel/xxxxx)",
            reply_markup=cancel_keyboard(),
        )
        await state.set_state(DownloadStates.waiting_link)
    elif platform == "tiktok":
        await callback.message.edit_text(
            "🎵 لینک ویدیوی تیک‌تاک را ارسال کنید:\n(مثال: https://vm.tiktok.com/xxxxx)",
            reply_markup=cancel_keyboard(),
        )
        await state.set_state(DownloadStates.waiting_link)
    await callback.answer()


@router.message(DownloadStates.waiting_link)
async def download_receive_link(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        await message.answer("⚠️ لطفا لینک را به صورت متن ارسال کنید.", reply_markup=cancel_keyboard())
        return

    url = message.text.strip()
    data_so_far = await state.get_data()
    platform = data_so_far.get("dl_platform", "")

    if platform == "youtube":
        if not YT_RE.search(url):
            await message.answer("⚠️ لینک یوتیوب معتبر نیست. دوباره بفرستید:", reply_markup=cancel_keyboard())
            return
        status = await message.answer("⏳ در حال دریافت کیفیت‌های موجود...")
        formats = await _get_yt_formats(url)
        if not formats:
            await status.edit_text(
                "❌ نتوانستم کیفیت‌ها را دریافت کنم.\nمطمئن شوید لینک درست است و yt-dlp روی سرور نصب و به‌روز است.",
                reply_markup=back_to_menu_keyboard(),
            )
            await state.clear()
            return

        await state.update_data(dl_url=url, dl_formats=formats)
        buttons = [
            [InlineKeyboardButton(text=fmt["label"], callback_data=f"yt_q:{idx}")]
            for idx, fmt in enumerate(formats)
        ]
        buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="cancel_action")])

        await status.edit_text(
            f"🎬 {formats[0].get('title', '')}\n\nکیفیت مورد نظر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await state.set_state(DownloadStates.choosing_quality)
        return

    if platform == "instagram":
        if not INSTA_RE.search(url):
            await message.answer("⚠️ لینک اینستاگرام معتبر نیست.", reply_markup=cancel_keyboard())
            return
    elif platform == "tiktok":
        if not TIKTOK_RE.search(url):
            await message.answer("⚠️ لینک تیک‌تاک معتبر نیست.", reply_markup=cancel_keyboard())
            return
    else:
        await message.answer("⚠️ پلتفرم ناشناخته.", reply_markup=back_to_menu_keyboard())
        await state.clear()
        return

    status = await message.answer("⏳ در حال دانلود ویدیو...")
    await state.clear()

    path = await _simple_download(url, _download_dir())
    if not path or not os.path.isfile(path):
        await status.edit_text(
            "❌ دانلود ناموفق بود.\nممکن است ویدیو خصوصی باشد یا نیاز به ورود داشته باشد.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    try:
        await _send_upload_action(bot, message.from_user.id)
        await _send_video(bot, message.from_user.id, path)
        await status.delete()
    except Exception as e:
        await status.edit_text(
            f"❌ ارسال ناموفق (ممکن است حجم فایل > 50MB باشد):\n{type(e).__name__}",
            reply_markup=back_to_menu_keyboard(),
        )
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


@router.callback_query(DownloadStates.choosing_quality, F.data.startswith("yt_q:"))
async def download_quality(callback: CallbackQuery, state: FSMContext, bot: Bot):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    formats = data.get("dl_formats", [])
    url = data.get("dl_url", "")
    if idx >= len(formats):
        await callback.answer("❌ انتخاب نامعتبر", show_alert=True)
        return

    fmt = formats[idx]
    await callback.message.edit_text(f"⏳ در حال دانلود {fmt['label']}...")

    try:
        path = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _yt_download, url, fmt["id"], _download_dir()),
            timeout=DOWNLOAD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("yt download runner timed out")
        await callback.message.edit_text(
            "❌ زمان دانلود بیش از حد طول کشید. لطفا کیفیت پایین‌تری را انتخاب کنید یا دوباره تلاش کنید.",
            reply_markup=back_to_menu_keyboard(),
        )
        await state.clear()
        await callback.answer()
        return
    except Exception as e:
        logger.error(f"yt download runner failed: {type(e).__name__}: {e}")
        await callback.message.edit_text(
            f"❌ خطا در دانلود: {type(e).__name__}",
            reply_markup=back_to_menu_keyboard(),
        )
        await state.clear()
        await callback.answer()
        return

    if not path or not os.path.isfile(path):
        await callback.message.edit_text(
            "❌ دانلود ناموفق بود. لطفا دوباره تلاش کنید.",
            reply_markup=back_to_menu_keyboard(),
        )
        await state.clear()
        await callback.answer()
        return

    file_size_mb = os.path.getsize(path) / (1024 * 1024)
    if file_size_mb > TELEGRAM_BOT_FILE_LIMIT_MB:
        await callback.message.edit_text(
            f"❌ حجم فایل ({file_size_mb:.1f} MB) بیش از حد مجاز {TELEGRAM_BOT_FILE_LIMIT_MB} مگابایت تلگرام است.",
            reply_markup=back_to_menu_keyboard(),
        )
        os.remove(path)
        await state.clear()
        await callback.answer()
        return

    try:
        await _send_upload_action(bot, callback.from_user.id)
        await _send_video(
            bot,
            callback.from_user.id,
            path,
            caption=f"🎬 {fmt.get('title', '')} - {fmt['label']}",
        )
        await callback.message.delete()
    except Exception as e:
        await callback.message.edit_text(
            f"❌ ارسال ناموفق بود (ممکن است حجم فایل بیش از حد مجاز تلگرام باشد):\n{type(e).__name__}",
            reply_markup=back_to_menu_keyboard(),
        )
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

    await state.clear()
    await callback.answer()

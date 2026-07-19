import logging
import os
import asyncio
import uuid
import re
import glob

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatAction

from bot.keyboards.inline import download_platforms_keyboard, back_to_menu_keyboard, cancel_keyboard
from bot.middlewares.membership import check_membership

logger = logging.getLogger(__name__)
router = Router()

YT_RE = re.compile(
    r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|m\.youtube\.com/watch\?v=)([\w-]{11})',
    re.IGNORECASE
)
INSTA_RE = re.compile(r'(https?://)?(www\.)?instagram\.com/(reel|p|tv)/[\w-]+', re.IGNORECASE)
TIKTOK_RE = re.compile(r'(https?://)?(www\.)?(vm\.)?tiktok\.com/[\w./?=-]+', re.IGNORECASE)


class DownloadStates(StatesGroup):
    waiting_link = State()
    choosing_quality = State()


async def _get_yt_formats(url: str) -> list:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _yt_extract_formats, url)
    except Exception as e:
        logger.error(f"yt-dlp extract failed: {type(e).__name__}: {e}")
        return []


def _yt_extract_formats(url: str) -> list:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return []

    opts = {"quiet": True, "no_warnings": True}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = []
        seen_res = set()
        best_audio_id = ""
        for f in info.get("formats", []):
            h = f.get("height") or 0
            has_video = f.get("vcodec") != "none"
            has_audio = f.get("acodec") != "none"
            fid = f.get("format_id", "")
            if not has_video and has_audio and not best_audio_id:
                best_audio_id = fid
            if not has_video:
                continue
            if h < 360:
                continue
            if h in seen_res:
                continue
            seen_res.add(h)
            if has_audio and fid != best_audio_id:
                merge_fid = fid
            else:
                merge_fid = f"{fid}+{best_audio_id}" if best_audio_id else fid
            formats.append({
                "id": merge_fid,
                "label": f"📺 {h}p",
                "height": h,
                "title": info.get("title", "video")[:60],
            })
        if best_audio_id:
            formats.append({
                "id": best_audio_id,
                "label": "🎵 صوت (MP3)",
                "height": 0,
                "title": info.get("title", "video")[:60],
            })
        return formats


def _yt_download(url: str, format_id: str, out_path: str) -> str:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return ""
    name = str(uuid.uuid4())[:8]
    opts = {
        "quiet": True, "no_warnings": True,
        "format": format_id,
        "outtmpl": f"{out_path}/{name}.%(ext)s",
        "merge_output_format": "mp4",
        "socket_timeout": 30,
        "extract_flat": False,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return ""
            pattern = f"{out_path}/{name}.*"
            files = glob.glob(pattern)
            if files:
                return files[0]
            return ydl.prepare_filename(info)
    except Exception as e:
        logger.error(f"yt-dlp download error: {type(e).__name__}: {e}")
        return ""


@router.callback_query(F.data == "main:download")
async def download_menu(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)
    if not is_member:
        await callback.answer("⚠️ ابتدا در کانال ما عضو شوید. /start", show_alert=True)
        return
    await callback.message.edit_text(
        "🎬 دانلود ویدیو\n\nپلتفرم مورد نظر را انتخاب کنید:",
        reply_markup=download_platforms_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dl:"))
async def download_platform(callback: CallbackQuery, state: FSMContext):
    platform = callback.data.split(":")[1]
    await state.update_data(dl_platform=platform)

    if platform == "youtube":
        await callback.message.edit_text(
            "▶️ لینک ویدیوی یوتیوب را ارسال کنید:\n(مثال: https://youtu.be/xxxxx)",
            reply_markup=cancel_keyboard()
        )
        await state.set_state(DownloadStates.waiting_link)
    elif platform == "instagram":
        await callback.message.edit_text(
            "📸 لینک پست/ریلز اینستاگرام را ارسال کنید:\n(مثال: https://instagram.com/reel/xxxxx)",
            reply_markup=cancel_keyboard()
        )
        await state.set_state(DownloadStates.waiting_link)
    elif platform == "tiktok":
        await callback.message.edit_text(
            "🎵 لینک ویدیوی تیک‌تاک را ارسال کنید:\n(مثال: https://vm.tiktok.com/xxxxx)",
            reply_markup=cancel_keyboard()
        )
        await state.set_state(DownloadStates.waiting_link)
    await callback.answer()


async def _simple_download(url: str, out_dir: str) -> str:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return ""
    name = str(uuid.uuid4())[:8]
    opts = {
        "quiet": True, "no_warnings": True,
        "outtmpl": f"{out_dir}/{name}.%(ext)s",
        "merge_output_format": "mp4",
    }
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, lambda: YoutubeDL(opts).extract_info(url, download=True))
        if info:
            return YoutubeDL(opts).prepare_filename(info)
    except Exception as e:
        logger.error(f"simple download failed: {type(e).__name__}: {e}")
    return ""


@router.message(DownloadStates.waiting_link)
async def download_receive_link(message: Message, state: FSMContext, bot: Bot):
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
                "❌ نتوانستم کیفیت‌ها را دریافت کنم.\nمطمئن شوید لینک درست است و yt-dlp روی سرور نصب است.",
                reply_markup=back_to_menu_keyboard()
            )
            await state.clear()
            return

        await state.update_data(dl_url=url, dl_formats=formats)
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        buttons = []
        for idx, f in enumerate(formats):
            buttons.append([InlineKeyboardButton(text=f["label"], callback_data=f"yt_q:{idx}")])
        buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="cancel_action")])

        await status.edit_text(
            f"🎬 {formats[0].get('title', '')}\n\nکیفیت مورد نظر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
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

    out_dir = "/tmp/migmig_dl"
    os.makedirs(out_dir, exist_ok=True)

    path = await _simple_download(url, out_dir)

    if not path or not os.path.isfile(path):
        await status.edit_text(
            "❌ دانلود ناموفق بود.\nممکن است ویدیو خصوصی باشد یا نیاز به ورود داشته باشد.",
            reply_markup=back_to_menu_keyboard()
        )
        return

    try:
        await message.answer_chat_action(ChatAction.UPLOAD_VIDEO)
        await bot.send_video(
            chat_id=message.from_user.id,
            video=open(path, "rb"),
        )
        await status.delete()
    except Exception as e:
        await status.edit_text(
            f"❌ ارسال ناموفق (ممکن است حجم فایل > 50MB باشد):\n{type(e).__name__}",
            reply_markup=back_to_menu_keyboard()
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

    out_dir = "/tmp/migmig_dl"
    os.makedirs(out_dir, exist_ok=True)

    try:
        path = await asyncio.get_running_loop().run_in_executor(
            None, _yt_download, url, fmt["id"], out_dir
        )
    except Exception as e:
        logger.error(f"yt download runner failed: {type(e).__name__}: {e}")
        await callback.message.edit_text(
            f"❌ خطا در دانلود: {type(e).__name__}",
            reply_markup=back_to_menu_keyboard()
        )
        await state.clear()
        await callback.answer()
        return

    if not path or not os.path.isfile(path):
        await callback.message.edit_text(
            "❌ دانلود ناموفق بود. لطفا دوباره تلاش کنید.",
            reply_markup=back_to_menu_keyboard()
        )
        await state.clear()
        await callback.answer()
        return

    file_size_mb = os.path.getsize(path) / (1024 * 1024)
    if file_size_mb > 50:
        await callback.message.edit_text(
            f"❌ حجم فایل ({file_size_mb:.1f} MB) بیش از حد مجاز ۵۰ مگابایت تلگرام است.",
            reply_markup=back_to_menu_keyboard()
        )
        os.remove(path)
        await state.clear()
        await callback.answer()
        return

    try:
        await callback.message.answer_chat_action(ChatAction.UPLOAD_VIDEO)
        await bot.send_video(
            chat_id=callback.from_user.id,
            video=open(path, "rb"),
            caption=f"🎬 {fmt.get('title', '')} — {fmt['label']}",
        )
        await callback.message.delete()
    except Exception as e:
        await callback.message.edit_text(
            f"❌ ارسال ناموفق بود (ممکن است حجم فایل بیش از حد مجاز تلگرام باشد):\n{type(e).__name__}",
            reply_markup=back_to_menu_keyboard()
        )
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

    await state.clear()
    await callback.answer()

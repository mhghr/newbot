import asyncio
import logging
import os
import re
import tempfile
import threading

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import ADMIN_IDS, DATABASE_URL

logger = logging.getLogger(__name__)

router = Router()

REMOTE_DIR = "/opt/migmig-bot"
SERVICE_NAME = "migmig-bot"
EXCLUDE_UPLOAD = {".git", "venv", "__pycache__", "backups", ".env.example", ".gitignore"}


class TransferStates(StatesGroup):
    waiting_host = State()
    waiting_username = State()
    waiting_password = State()
    waiting_confirm = State()


def _parse_db_url(url: str):
    m = re.match(
        r"postgresql://(?P<user>[^:]+):(?P<pass>[^@]+)@(?P<host>[^:]+):(?P<port>\d+)/(?P<db>\S+)",
        url,
    )
    if not m:
        raise ValueError("Invalid DATABASE_URL format")
    return m.groupdict()


@router.callback_query(F.data == "admin:transfer")
async def transfer_entry(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    await state.clear()
    await state.set_state(TransferStates.waiting_host)
    await callback.message.edit_text(
        "📡 انتقال سرور\n\n"
        "آدرس IP یا hostname سرور جدید را وارد کنید.\n"
        "(برای لغو /cancel)",
    )
    await callback.answer()


@router.message(TransferStates.waiting_host)
async def got_host(message: Message, state: FSMContext):
    if not message.text or message.text.startswith("/"):
        if message.text == "/cancel":
            await state.clear()
            await message.answer("❌ عملیات لغو شد.")
            return
        await message.answer("لطفا آدرس سرور را وارد کنید:")
        return

    host = message.text.strip()
    await state.update_data(host=host)
    await state.set_state(TransferStates.waiting_username)
    await message.answer(
        f"✅ آدرس: {host}\n\n"
        f"حالا نام کاربری SSH را وارد کنید:\n"
        f"(معمولا root)"
    )


@router.message(TransferStates.waiting_username)
async def got_username(message: Message, state: FSMContext):
    if not message.text or message.text.startswith("/"):
        if message.text == "/cancel":
            await state.clear()
            await message.answer("❌ عملیات لغو شد.")
            return
        await message.answer("لطفا نام کاربری را وارد کنید:")
        return

    username = message.text.strip()
    await state.update_data(username=username)
    await state.set_state(TransferStates.waiting_password)
    await message.answer("🔑 حالا رمز عبور SSH سرور جدید را وارد کنید:")


@router.message(TransferStates.waiting_password)
async def got_password(message: Message, state: FSMContext, bot: Bot):
    if not message.text or message.text.startswith("/"):
        if message.text == "/cancel":
            await state.clear()
            await message.answer("❌ عملیات لغو شد.")
            return
        await message.answer("لطفا رمز عبور را وارد کنید:")
        return

    password = message.text
    await state.update_data(password=password)

    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        pass

    data = await state.get_data()
    await state.set_state(TransferStates.waiting_confirm)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="شروع انتقال", callback_data="transfer:start")],
        [InlineKeyboardButton(text="❌ لغو", callback_data="admin:back")],
    ])

    text = (
        "📡 اطلاعات انتقال:\n\n"
        f"🖥 سرور: {data['host']}\n"
        f"👤 کاربر: {data['username']}\n"
        f"🔑 رمز: {'*' * len(password)}\n\n"
        "برای شروع انتقال، دکمه زیر را بزنید:"
    )
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "transfer:start", TransferStates.waiting_confirm)
async def start_transfer(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    data = await state.get_data()
    host = data["host"]
    username = data["username"]
    password = data["password"]

    await state.clear()

    status_msg = await callback.message.edit_text("⏳ در حال اتصال به سرور جدید...")
    await callback.answer()

    chat_id = callback.message.chat.id
    msg_id = status_msg.message_id

    progress_lines = []
    progress_lock = threading.Lock()

    def report(text: str):
        with progress_lock:
            progress_lines.append(text)

    transfer_error = []
    transfer_done = threading.Event()

    def _run_transfer():
        try:
            _do_transfer(host, username, password, report)
        except Exception as e:
            report(f"❌ خطا: {e}")
            transfer_error.append(str(e))
            logger.error(f"Transfer failed: {e}")
        finally:
            transfer_done.set()

    thread = threading.Thread(target=_run_transfer, daemon=True)
    thread.start()

    last_count = 0
    while not transfer_done.is_set() or last_count < len(progress_lines):
        with progress_lock:
            current = list(progress_lines)
        if len(current) > last_count:
            try:
                await bot.edit_message_text(
                    "\n".join(current),
                    chat_id=chat_id,
                    message_id=msg_id,
                )
            except Exception:
                pass
            last_count = len(current)
        await asyncio.sleep(1.0)

    with progress_lock:
        current = list(progress_lines)
    try:
        await bot.edit_message_text(
            "\n".join(current),
            chat_id=chat_id,
            message_id=msg_id,
        )
    except Exception:
        pass


def _do_transfer(host: str, username: str, password: str, report: callable):
    import paramiko

    report("⏳ اتصال SSH به سرور جدید...")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, username=username, password=password, timeout=15)
    except paramiko.AuthenticationException:
        report("❌ خطای احراز هویت. نام کاربری یا رمز عبور اشتباه است.")
        return
    except Exception as e:
        report(f"❌ اتصال ناموفق: {e}")
        return

    try:
        report("✅ اتصال برقرار شد.")

        # --- Step 1: System dependencies ---
        report("📦 نصب پیش‌نیازهای سیستمی...")
        _run(ssh, (
            "export DEBIAN_FRONTEND=noninteractive && "
            "apt-get update -y && "
            "apt-get install -y python3 python3-venv python3-pip "
            "postgresql postgresql-contrib openssl curl ffmpeg"
        ))

        # --- Step 2: DB backup locally ---
        report("💾 تهیه نسخه پشتیبان از دیتابیس...")
        db_info = _parse_db_url(DATABASE_URL)
        dump_path = os.path.join(tempfile.gettempdir(), "migmig_db_dump.sql")
        code = os.system(f'sudo -u postgres pg_dump {db_info["db"]} > "{dump_path}"')
        if code != 0 or not os.path.exists(dump_path) or os.path.getsize(dump_path) == 0:
            report("⚠️ خطا در تهیه نسخه پشتیبان دیتابیس (ممکن است خالی باشد).")
            return

        # --- Step 3: Upload project files ---
        report("📤 ارسال فایل‌های پروژه...")
        project_dir = os.getcwd()
        sftp = ssh.open_sftp()
        _ensure_remote_dir(ssh, REMOTE_DIR)
        _upload_dir(sftp, project_dir, REMOTE_DIR)
        sftp.put(dump_path, f"{REMOTE_DIR}/db_dump.sql")
        sftp.close()
        try:
            os.remove(dump_path)
        except Exception:
            pass

        # --- Step 4: Read .env, update DB URL for remote ---
        report("⚙️ تنظیم فایل .env روی سرور جدید...")
        env_content = _read_env_file(project_dir)
        old_db_url = DATABASE_URL

        # Generate new DB password for remote and replace in .env content
        db_pass_remote = _run(ssh, "openssl rand -hex 16").strip()
        new_db_url = (
            f"postgresql://{db_info['user']}:{db_pass_remote}"
            f"@localhost:{db_info['port']}/{db_info['db']}"
        )
        # Replace any DB URL line containing the db name
        env_content = re.sub(
            r"^DATABASE_URL\s*=\s*.*$",
            f"DATABASE_URL={new_db_url}",
            env_content,
            flags=re.MULTILINE,
        )

        # --- Step 5: Setup PostgreSQL on remote ---
        report("🗄 راه‌اندازی دیتابیس روی سرور جدید...")
        _run(ssh, "systemctl enable postgresql && systemctl start postgresql")

        # Create user
        _run(ssh, (
            f"sudo -u postgres psql -c "
            f"\"DO \\$\\$ BEGIN "
            f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{db_info['user']}') THEN "
            f"CREATE ROLE {db_info['user']} WITH LOGIN PASSWORD '{db_pass_remote}'; "
            f"ELSE ALTER ROLE {db_info['user']} WITH LOGIN PASSWORD '{db_pass_remote}'; "
            f"END IF; END \\$\\$\""
        ))

        # Create database
        _run(ssh, (
            f"sudo -u postgres psql -tc "
            f"\"SELECT 1 FROM pg_database WHERE datname='{db_info['db']}'\" | grep -q 1 || "
            f"sudo -u postgres createdb -O {db_info['user']} {db_info['db']}"
        ))

        # Grant schema permissions
        _run(ssh, (
            f"sudo -u postgres psql -d {db_info['db']} -c "
            f"\"ALTER SCHEMA public OWNER TO {db_info['user']}; "
            f"GRANT ALL ON SCHEMA public TO {db_info['user']}\""
        ))

        # Upload updated .env
        sftp = ssh.open_sftp()
        sftp.putfo(_bytes_io(env_content), f"{REMOTE_DIR}/.env")
        sftp.chmod(f"{REMOTE_DIR}/.env", 0o600)
        sftp.close()

        # --- Step 6: Restore database on remote ---
        report("📥 بازیابی دیتابیس روی سرور جدید...")
        _run(ssh, f"sudo -u postgres psql -d {db_info['db']} -f {REMOTE_DIR}/db_dump.sql")
        _run(ssh, f"rm -f {REMOTE_DIR}/db_dump.sql")

        # --- Step 7: Python venv ---
        report("🐍 ایجاد محیط مجازی و نصب پکیج‌ها...")
        _run(ssh, f"python3 -m venv {REMOTE_DIR}/venv")
        _run(ssh, f"{REMOTE_DIR}/venv/bin/pip install --upgrade pip")
        _run(ssh, f"{REMOTE_DIR}/venv/bin/pip install -U -r {REMOTE_DIR}/requirements.txt")
        _run(ssh, f"sed -i 's/\\r$//' {REMOTE_DIR}/*.sh")

        # --- Step 8: Create systemd service ---
        report("⚡ ایجاد سرویس systemd...")
        service = (
            f"[Unit]\n"
            f"Description=MigMig VPN Telegram Bot\n"
            f"After=network-online.target postgresql.service\n"
            f"Wants=network-online.target\n\n"
            f"[Service]\n"
            f"Type=simple\n"
            f"WorkingDirectory={REMOTE_DIR}\n"
            f"ExecStart={REMOTE_DIR}/venv/bin/python run.py\n"
            f"Restart=always\n"
            f"RestartSec=5\n\n"
            f"[Install]\n"
            f"WantedBy=multi-user.target\n"
        )
        sftp = ssh.open_sftp()
        sftp.putfo(_bytes_io(service), f"/etc/systemd/system/{SERVICE_NAME}.service")
        sftp.close()
        _run(ssh, "systemctl daemon-reload")
        _run(ssh, f"systemctl enable {SERVICE_NAME}")
        _run(ssh, f"systemctl restart {SERVICE_NAME}")

        import time
        time.sleep(4)

        # --- Step 9: Verify ---
        report("✅ بررسی وضعیت ربات روی سرور جدید...")
        result = _run(ssh, f"systemctl is-active {SERVICE_NAME}")
        if "active" in result:
            report("🎉 انتقال با موفقیت انجام شد! ربات روی سرور جدید فعال است.")
            report("⚠️ فراموش نکنید توکن ربات را از @BotFather تغییر ندهید.")
        else:
            report(f"⚠️ ربات روی سرور جدید اجرا نشد. وضعیت: {result.strip()}")
            report(f"برای بررسی دستی:")
            report(f"  ssh {username}@{host}")
            report(f"  journalctl -u {SERVICE_NAME} -n 50 --no-pager")

    finally:
        ssh.close()


def _run(ssh, cmd: str) -> str:
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=300)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0 and err.strip():
        logger.warning(f"Remote cmd exit={exit_code}: {cmd[:120]}\n{err[:300]}")
    return out


def _ensure_remote_dir(ssh, remote_dir: str):
    _run(ssh, f"mkdir -p {remote_dir}")


def _upload_dir(sftp, local_root: str, remote_root: str):
    import stat as statmod

    try:
        sftp.mkdir(remote_root)
    except IOError:
        pass

    for name in os.listdir(local_root):
        local_path = os.path.join(local_root, name)

        skip = False
        if name in EXCLUDE_UPLOAD:
            skip = True
        elif name.endswith(".pyc"):
            skip = True
        if skip:
            continue

        remote_path = f"{remote_root}/{name}".replace("\\", "/")

        try:
            if os.path.isdir(local_path):
                _upload_dir(sftp, local_path, remote_path)
            else:
                sftp.put(local_path, remote_path)
                file_stat = os.stat(local_path)
                sftp.chmod(remote_path, statmod.S_IMODE(file_stat.st_mode))
        except Exception as e:
            logger.warning(f"Skip upload {local_path}: {e}")


def _read_env_file(project_dir: str) -> str:
    env_path = os.path.join(project_dir, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def _bytes_io(content: str):
    import io
    return io.BytesIO(content.encode("utf-8"))

#!/usr/bin/env bash
#
# MigMig VPN Bot - Ubuntu update script
# Safely updates the bot code on the server WITHOUT touching .env or the
# database. Takes a backup of .env and the DB first, then restarts.
#
# Schema migrations (ALTER/CREATE IF NOT EXISTS) run automatically on startup,
# so existing data is preserved.
#
# Usage:  sudo bash update.sh
#
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[x]${NC} $1"; }

if [ "$(id -u)" -ne 0 ]; then
    err "با دسترسی root اجرا کنید:  sudo bash update.sh"
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="migmig-bot"
DB_NAME="newbot"
BACKUP_DIR="${PROJECT_DIR}/backups"
TS="$(date +%Y%m%d-%H%M%S)"

if [ ! -f "$PROJECT_DIR/run.py" ]; then
    err "run.py پیدا نشد. اسکریپت را داخل پوشه‌ی پروژه اجرا کنید."
    exit 1
fi
if [ ! -f "$PROJECT_DIR/.env" ]; then
    err ".env پیدا نشد. انگار هنوز deploy نکرده‌اید. اول deploy.sh را اجرا کنید."
    exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "=================================================="
echo "        MigMig VPN Bot - Update"
echo "=================================================="

# ---------- 1) Backups (safety) ----------
info "بکاپ‌گیری از .env و دیتابیس..."
cp "$PROJECT_DIR/.env" "$BACKUP_DIR/env-${TS}.bak"
if sudo -u postgres pg_dump "$DB_NAME" > "$BACKUP_DIR/db-${TS}.sql" 2>/dev/null; then
    info "بکاپ دیتابیس: $BACKUP_DIR/db-${TS}.sql"
else
    warn "بکاپ دیتابیس گرفته نشد (اشکالی ندارد اگر دیتابیس هنوز خالی است)."
    rm -f "$BACKUP_DIR/db-${TS}.sql"
fi

# ---------- 2) Update code ----------
if [ -d "$PROJECT_DIR/.git" ]; then
    info "دریافت آخرین تغییرات از git..."
    if git -C "$PROJECT_DIR" pull --rebase --autostash; then
        info "کد از git به‌روز شد."
    else
        warn "git pull ناموفق بود. تغییرات را دستی کپی کنید و دوباره اجرا کنید."
    fi
else
    warn "این پوشه مخزن git نیست."
    warn "فرض می‌شود فایل‌های جدید را قبلاً روی سرور کپی کرده‌اید (scp/rsync)."
fi

# ---------- 3) Normalize line endings (in case edited on Windows) ----------
sed -i 's/\r$//' "$PROJECT_DIR"/*.sh 2>/dev/null || true

# ---------- 4) Update Python dependencies ----------
if [ ! -x "$PROJECT_DIR/venv/bin/pip" ]; then
    err "venv پیدا نشد. اول deploy.sh را اجرا کنید."
    exit 1
fi
info "به‌روزرسانی کتابخانه‌ها..."
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt" >/dev/null
if grep -qE '^PROXY_URL=.+' "$PROJECT_DIR/.env"; then
    "$PROJECT_DIR/venv/bin/pip" install "aiohttp_socks>=0.8" >/dev/null || true
fi

# ---------- 5) Restart service (migrations run on startup) ----------
info "ری‌استارت سرویس ${SERVICE_NAME}..."
systemctl restart "$SERVICE_NAME"
sleep 3

echo ""
echo "=================================================="
if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "به‌روزرسانی انجام شد و ربات در حال اجراست ✅"
else
    err "ربات بالا نیامد! برای بازگردانی:"
    echo "   - آخرین بکاپ .env:  $BACKUP_DIR/env-${TS}.bak"
    echo "   - لاگ خطا:          journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
fi
echo "=================================================="
echo ""
echo "لاگ زنده:   journalctl -u ${SERVICE_NAME} -f"
echo "بکاپ‌ها در: ${BACKUP_DIR}"

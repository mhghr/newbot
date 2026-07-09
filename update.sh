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
    err "Please run as root:  sudo bash update.sh"
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="migmig-bot"
DB_NAME="newbot"
BACKUP_DIR="${PROJECT_DIR}/backups"
TS="$(date +%Y%m%d-%H%M%S)"

if [ ! -f "$PROJECT_DIR/run.py" ]; then
    err "run.py not found. Run this script from the project directory."
    exit 1
fi
if [ ! -f "$PROJECT_DIR/.env" ]; then
    err ".env not found. Looks like it is not deployed yet. Run deploy.sh first."
    exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "=================================================="
echo "        MigMig VPN Bot - Update"
echo "=================================================="

# ---------- 1) Backups (safety) ----------
info "Backing up .env and database..."
cp "$PROJECT_DIR/.env" "$BACKUP_DIR/env-${TS}.bak"
if sudo -u postgres pg_dump "$DB_NAME" > "$BACKUP_DIR/db-${TS}.sql" 2>/dev/null; then
    info "Database backup: $BACKUP_DIR/db-${TS}.sql"
else
    warn "Database backup skipped (ok if DB is empty/new)."
    rm -f "$BACKUP_DIR/db-${TS}.sql"
fi

# ---------- 2) Update code ----------
if [ -d "$PROJECT_DIR/.git" ]; then
    info "Pulling latest changes from git..."
    if git -C "$PROJECT_DIR" pull --rebase --autostash; then
        info "Code updated from git."
    else
        warn "git pull failed. Copy the new files manually and re-run."
    fi
else
    warn "This directory is not a git repo."
    warn "Assuming new files were already copied to the server (scp/rsync)."
fi

# ---------- 3) Normalize line endings (in case edited on Windows) ----------
sed -i 's/\r$//' "$PROJECT_DIR"/*.sh 2>/dev/null || true

# ---------- 4) Update Python dependencies ----------
if [ ! -x "$PROJECT_DIR/venv/bin/pip" ]; then
    err "venv not found. Run deploy.sh first."
    exit 1
fi
info "Updating Python dependencies..."
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt" >/dev/null
if grep -qE '^PROXY_URL=.+' "$PROJECT_DIR/.env"; then
    "$PROJECT_DIR/venv/bin/pip" install "aiohttp_socks>=0.8" >/dev/null || true
fi

# ---------- 5) Restart service (migrations run on startup) ----------
info "Restarting service ${SERVICE_NAME}..."
systemctl restart "$SERVICE_NAME"
sleep 3

echo ""
echo "=================================================="
if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Update complete. Bot is running."
else
    err "Bot did not start. To recover:"
    echo "   - Last .env backup: $BACKUP_DIR/env-${TS}.bak"
    echo "   - Error log:        journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
fi
echo "=================================================="
echo ""
echo "Live logs: journalctl -u ${SERVICE_NAME} -f"
echo "Backups:   ${BACKUP_DIR}"

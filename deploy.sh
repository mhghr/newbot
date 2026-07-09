#!/usr/bin/env bash
#
# MigMig VPN Bot - Ubuntu deploy script
# Installs dependencies, sets up PostgreSQL, creates .env, and runs the bot
# as a systemd service. Re-runnable (idempotent).
#
# Usage:  sudo bash deploy.sh
#
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
info() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[x]${NC} $1"; }
ask()  { echo -e "${BLUE}?${NC} $1"; }

if [ "$(id -u)" -ne 0 ]; then
    err "Please run as root:  sudo bash deploy.sh"
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="migmig-bot"
DB_NAME="newbot"
DB_USER="botuser"

if [ ! -f "$PROJECT_DIR/run.py" ]; then
    err "run.py not found. Run this script from the project directory."
    exit 1
fi

echo "=================================================="
echo "        MigMig VPN Bot - Ubuntu Deploy"
echo "=================================================="

# ---------- 1) System dependencies ----------
info "Updating apt and installing dependencies (python, postgresql)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip postgresql postgresql-contrib openssl curl
systemctl enable postgresql >/dev/null 2>&1 || true
systemctl start postgresql

# ---------- 2) Collect configuration ----------
echo ""
echo "--------------------------------------------------"
echo "  Configuration"
echo "--------------------------------------------------"

read -rp "$(ask 'Bot token (BOT_TOKEN from @BotFather): ')" BOT_TOKEN
while [ -z "${BOT_TOKEN:-}" ]; do
    read -rp "$(ask 'Token cannot be empty. Enter again: ')" BOT_TOKEN
done

echo ""
warn "Admin ID must be numeric (get it from @userinfobot)."
read -rp "$(ask 'Admin numeric IDs (comma-separated): ')" ADMIN_IDS
while [ -z "${ADMIN_IDS:-}" ]; do
    read -rp "$(ask 'At least one admin ID is required: ')" ADMIN_IDS
done

echo ""
echo "Mandatory-join channel:"
echo "  - Username is simpler and recommended  (e.g. @mychannel)"
echo "  - Or numeric channel ID                (e.g. -1001234567890)"
echo "  - Leave empty to disable forced-join"
read -rp "$(ask 'Channel (username or ID): ')" CHANNEL_INPUT
CHANNEL_INPUT="${CHANNEL_INPUT:-}"

CHANNEL_ID=""
CHANNEL_URL=""
if [ -n "$CHANNEL_INPUT" ]; then
    if [[ "$CHANNEL_INPUT" =~ ^-?[0-9]+$ ]]; then
        CHANNEL_ID="$CHANNEL_INPUT"
        read -rp "$(ask 'Channel invite link (e.g. https://t.me/joinchat/...): ')" CHANNEL_URL
    else
        UNAME="${CHANNEL_INPUT#@}"
        CHANNEL_ID="@${UNAME}"
        CHANNEL_URL="https://t.me/${UNAME}"
        info "Channel link auto-set to: ${CHANNEL_URL}"
    fi
    warn "Important: make the bot an ADMIN of that channel so it can check membership."
fi

echo ""
echo "Master panel subscription base URL (your panel sub link base):"
echo "  e.g. http://SERVER_IP:2096"
read -rp "$(ask 'SUB_BASE_URL: ')" SUB_BASE_URL
SUB_BASE_URL="${SUB_BASE_URL:-http://127.0.0.1:2096}"

echo ""
echo "Telegram proxy (leave empty if the server can reach Telegram directly):"
echo "  e.g. socks5://127.0.0.1:1080"
read -rp "$(ask 'PROXY_URL (optional): ')" PROXY_URL
PROXY_URL="${PROXY_URL:-}"

read -rp "$(ask 'Internal sub-server port [8080]: ')" SUB_PORT
SUB_PORT="${SUB_PORT:-8080}"

# ---------- 3) PostgreSQL setup ----------
info "Configuring PostgreSQL database..."
DB_PASS="$(openssl rand -hex 16)"

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 \
    && sudo -u postgres psql -c "ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';" \
    || sudo -u postgres psql -c "CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
    || sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};" >/dev/null

DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}"

# ---------- 4) Write .env ----------
info "Writing .env ..."
cat > "$PROJECT_DIR/.env" <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
CHANNEL_ID=${CHANNEL_ID}
CHANNEL_URL=${CHANNEL_URL}
DATABASE_URL=${DATABASE_URL}
PROXY_URL=${PROXY_URL}
SUB_BASE_URL=${SUB_BASE_URL}
SUB_HOST=0.0.0.0
SUB_PORT=${SUB_PORT}
EOF
chmod 600 "$PROJECT_DIR/.env"

# ---------- 5) Python venv + deps ----------
info "Creating virtualenv and installing requirements..."
python3 -m venv "$PROJECT_DIR/venv"
"$PROJECT_DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
if [ -n "$PROXY_URL" ]; then
    info "Proxy configured; installing aiohttp_socks..."
    "$PROJECT_DIR/venv/bin/pip" install "aiohttp_socks>=0.8"
fi

# ---------- 6) systemd service ----------
info "Creating systemd service (${SERVICE_NAME})..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=MigMig VPN Telegram Bot
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null
systemctl restart "${SERVICE_NAME}"

sleep 3

# ---------- 7) Result ----------
echo ""
echo "=================================================="
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    info "Bot is running. Deploy successful."
else
    err "Bot failed to start. Check the logs below."
fi
echo "=================================================="
echo ""
echo "Useful commands:"
echo "  Status:   systemctl status ${SERVICE_NAME}"
echo "  Logs:     journalctl -u ${SERVICE_NAME} -f"
echo "  Log file: tail -f ${PROJECT_DIR}/bot.log"
echo "  Restart:  systemctl restart ${SERVICE_NAME}"
echo "  Stop:     systemctl stop ${SERVICE_NAME}"
echo ""
if [ -n "$CHANNEL_ID" ]; then
    warn "Reminder: make the bot an admin of channel ${CHANNEL_ID}."
fi

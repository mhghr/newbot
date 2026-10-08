import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")


def _parse_admin_ids(value: str):
    ids = []
    for x in (value or "").split(","):
        x = x.strip()
        if x.lstrip("-").isdigit():
            ids.append(int(x))
    return ids


ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))


def _parse_channel(value: str):
    value = (value or "").strip()
    if not value:
        return 0
    if value.lstrip("-").isdigit():
        return int(value)
    return value if value.startswith("@") else "@" + value


def _parse_channel_url(value: str) -> str:
    """Normalize CHANNEL_URL into a valid clickable https://t.me/... link.

    Telegram rejects a bare ``@username`` (or ``username``) as an inline button
    URL ("Wrong HTTP URL"), so accept all common forms and normalize them:
    ``https://t.me/x``, ``t.me/x``, ``@x`` and ``x`` all become
    ``https://t.me/x``.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    username = value.lstrip("@").lstrip("/")
    if username.startswith("t.me/"):
        return "https://" + username
    return f"https://t.me/{username}"


CHANNEL_ID = _parse_channel(os.getenv("CHANNEL_ID", ""))
CHANNEL_URL = _parse_channel_url(os.getenv("CHANNEL_URL", ""))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/newbot")
PROXY_URL = os.getenv("PROXY_URL", "")

# Optional SOCKS5 proxy used for the SSH connections to the MikroTik WireGuard
# routers. Some bot servers cannot reach the routers directly (the path drops
# the SSH key exchange), but they can reach a relay that has access to both the
# routers and the bot. Configuring the relay's SOCKS5 proxy here routes the
# router SSH through it. Format: "host:port" (e.g. "46.28.70.157:1080").
# Empty disables proxying entirely.
WG_SSH_PROXY = os.getenv("WG_SSH_PROXY", "").strip()
WG_SSH_PROXY_USER = os.getenv("WG_SSH_PROXY_USER", "").strip()
WG_SSH_PROXY_PASS = os.getenv("WG_SSH_PROXY_PASS", "").strip()
BASE_URL = os.getenv("BASE_URL", "")
SUB_HOST = os.getenv("SUB_HOST", "0.0.0.0")
SUB_PORT = int(os.getenv("SUB_PORT", "8080"))

# ArvanCloud CDN API — used for automatic DNS failover between WireGuard entry
# routers. The endpoint domain is read from servers.wg_endpoint (a shared domain),
# so it is not configured here unless DNS_FAILOVER_DOMAIN is set to override it.
#
# TEMPORARY: values are committed here so `update.sh` (git reset) applies them on
# the server without touching .env. Move them to .env and clear these later.
ARVAN_API_KEY = os.getenv("ARVAN_API_KEY", "c9ed8672-8410-56c6-9502-71511ceb3226")
ARVAN_API_BASE = os.getenv("ARVAN_API_BASE", "https://napi.arvancloud.ir/cdn/4.0")
DNS_FAILOVER_DOMAIN = os.getenv("DNS_FAILOVER_DOMAIN", "")
DNS_FAILOVER_TTL = int(os.getenv("DNS_FAILOVER_TTL", "120"))
# Check every 30s; switch after 3 consecutive bad checks, at most once per 30s
# (i.e. up to ~3 switches within 3 minutes for testing).
DNS_FAILOVER_INTERVAL = int(os.getenv("DNS_FAILOVER_INTERVAL", "30"))
DNS_FAILOVER_STREAK = int(os.getenv("DNS_FAILOVER_STREAK", "3"))
DNS_FAILOVER_MIN_SWITCH_INTERVAL = int(os.getenv("DNS_FAILOVER_MIN_SWITCH_INTERVAL", "30"))
# Health thresholds used to decide whether the active router is still good.
# "degraded" triggers a switch when a clearly better router exists; "unhealthy"
# triggers it as a plain outage.
DNS_FAILOVER_HANDSHAKE_MAX = int(os.getenv("DNS_FAILOVER_HANDSHAKE_MAX", "180"))
DNS_FAILOVER_LOSS_DEGRADED = int(os.getenv("DNS_FAILOVER_LOSS_DEGRADED", "15"))
DNS_FAILOVER_RTT_DEGRADED = int(os.getenv("DNS_FAILOVER_RTT_DEGRADED", "300"))
DNS_FAILOVER_LOSS_UNHEALTHY = int(os.getenv("DNS_FAILOVER_LOSS_UNHEALTHY", "40"))
DNS_FAILOVER_ENABLED = os.getenv(
    "DNS_FAILOVER_ENABLED", "1" if ARVAN_API_KEY else "0"
).strip().lower() in ("1", "true", "yes", "on")

# Telethon userbot used to read proxy source channels the bot itself is not a
# member of. Leave empty to disable the proxy relay entirely.
try:
    TG_API_ID = int(os.getenv("TG_API_ID", "0") or 0)
except ValueError:
    TG_API_ID = 0
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_PHONE = os.getenv("TG_PHONE", "")
TG_SESSION = os.getenv("TG_SESSION", "proxy_userbot")


def _parse_csv(value: str) -> list:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


PROXY_SOURCE_CHANNELS = _parse_csv(os.getenv("PROXY_SOURCE_CHANNELS", ""))

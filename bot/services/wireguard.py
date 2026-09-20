"""MikroTik WireGuard account management over the RouterOS API.

The RouterOS API client is synchronous, so every network operation is executed
in a worker thread via ``asyncio.to_thread`` to keep the aiogram event loop free.

A WireGuard "server" is a MikroTik router whose ``servers`` row has
``service_type='wireguard'`` and the ``wg_*`` / ``api_port`` columns filled in.
"""
import asyncio
import base64
import ipaddress
import logging

logger = logging.getLogger(__name__)

DEFAULT_DNS = "1.1.1.1,8.8.8.8"

# Seconds between keepalive packets, applied both to the router-side peer and
# the generated client .conf so the tunnel stays alive behind NAT.
PERSISTENT_KEEPALIVE = 25


class WireGuardError(Exception):
    """Raised for any expected/validation failure while talking to MikroTik."""


def _load_deps():
    try:
        from routeros_api import RouterOsApiPool
    except ImportError:
        RouterOsApiPool = None
    try:
        from cryptography.hazmat.primitives.asymmetric import x25519
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        x25519 = None
        serialization = None
    return RouterOsApiPool, x25519, serialization


def _s(server, key, default=""):
    try:
        value = server[key]
    except Exception:
        value = default
    return default if value is None else value


def _to_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower()
    units = {
        "tib": 1024 ** 4, "gib": 1024 ** 3, "mib": 1024 ** 2, "kib": 1024,
        "tb": 1000 ** 4, "gb": 1000 ** 3, "mb": 1000 ** 2, "kb": 1000, "b": 1,
    }
    for unit in sorted(units, key=len, reverse=True):
        if text.endswith(unit):
            try:
                return int(float(text[: -len(unit)].strip()) * units[unit])
            except ValueError:
                return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _is_disabled(value) -> bool:
    return str(value).strip().lower() in ("true", "yes")


def generate_keypair():
    """Return (public_key, private_key) base64 encoded X25519 keys."""
    _, x25519, serialization = _load_deps()
    if x25519 is None:
        raise WireGuardError("کتابخانه cryptography نصب نیست (pip install cryptography)")

    private = x25519.X25519PrivateKey.generate()
    public = private.public_key()
    priv_bytes = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(pub_bytes).decode("ascii"),
        base64.b64encode(priv_bytes).decode("ascii"),
    )


def _parse_network(subnet: str):
    value = (subnet or "").strip()
    if not value:
        return None
    if "/" not in value:
        value += "/24"
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None


def _host_number(ip: str, net):
    try:
        return int(ipaddress.ip_address(ip)) - int(net.network_address)
    except (ValueError, TypeError):
        return None


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "authentication" in low or "login" in low or "invalid user" in low or "password" in low:
        return "یوزرنیم یا پسورد API اشتباه است."
    if "timed out" in low or "timeout" in low:
        return "اتصال به روتر Timeout شد. IP/پورت و دسترسی شبکه را بررسی کنید."
    if "refused" in low:
        return "اتصال رد شد. احتمالاً سرویس API روی روتر فعال نیست یا پورت اشتباه است."
    if "unreachable" in low or "no route" in low:
        return "روتر در دسترس نیست. IP را بررسی کنید."
    return text


def format_endpoint_host(host: str) -> str:
    if host and ":" in host and not (host.startswith("[") and host.endswith("]")):
        return f"[{host}]"
    return host


def _open_pool(server):
    RouterOsApiPool, _, _ = _load_deps()
    if RouterOsApiPool is None:
        raise WireGuardError("کتابخانه routeros-api نصب نیست (pip install routeros-api)")

    host = _s(server, "url")
    if not host:
        raise WireGuardError("آدرس روتر تنظیم نشده است")
    port = _to_int(_s(server, "api_port", 8728)) or 8728
    kwargs = {
        "username": _s(server, "username"),
        "password": _s(server, "password"),
        "port": port,
        "plaintext_login": True,
    }
    if port == 8729:
        kwargs["use_ssl"] = True
        kwargs["ssl_verify"] = False
        kwargs["ssl_verify_hostname"] = False
    pool = RouterOsApiPool(host, **kwargs)
    return pool


def _disconnect(pool):
    try:
        pool.disconnect()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Synchronous worker functions (run inside a thread)
# --------------------------------------------------------------------------- #

def _test_connection_sync(server):
    iface = _s(server, "wg_interface")
    pool = _open_pool(server)
    try:
        api = pool.get_api()
        interfaces = api.get_resource("/interface/wireguard").get()
        names = [i.get("name") for i in interfaces]
        public_key = ""
        for i in interfaces:
            if i.get("name") == iface:
                public_key = i.get("public-key", "") or ""
        if iface and iface not in names:
            raise WireGuardError(f"اینترفیس WireGuard «{iface}» روی روتر پیدا نشد")
        return {"interfaces": names, "public_key": public_key}
    finally:
        _disconnect(pool)


def _inspect_sync(server):
    """Connect, verify access and auto-detect every WireGuard setting.

    Reads the interface (public key + listen port), its IP address on the
    router (/ip/address) and the router DNS. Derives the client subnet and the
    usable IP range, reserving the first and last usable addresses.
    """
    iface = _s(server, "wg_interface")
    pool = _open_pool(server)
    try:
        api = pool.get_api()
        interfaces = api.get_resource("/interface/wireguard").get()
        match = next((i for i in interfaces if i.get("name") == iface), None)
        if not match:
            names = ", ".join(i.get("name", "?") for i in interfaces) or "-"
            raise WireGuardError(
                f"اینترفیس WireGuard «{iface}» روی روتر پیدا نشد.\n"
                f"اینترفیس‌های موجود: {names}"
            )

        public_key = match.get("public-key", "") or ""
        listen_port = _to_int(match.get("listen-port")) or 51820

        addresses = api.get_resource("/ip/address").get()
        cidr = ""
        for item in addresses:
            if item.get("interface") == iface and item.get("address"):
                cidr = item.get("address", "").strip()
                break
        if not cidr:
            raise WireGuardError(
                f"اینترفیس «{iface}» هیچ آدرس IP ندارد.\n"
                "اول روی روتر یک آدرس مثل 10.66.66.1/24 به اینترفیس بده."
            )

        net = _parse_network(cidr)
        if net is None:
            raise WireGuardError(f"آدرس اینترفیس نامعتبر است: {cidr}")

        server_ip = cidr.split("/")[0].strip()
        server_host = _host_number(server_ip, net)
        total_hosts = net.num_addresses - 2  # exclude network + broadcast
        if total_hosts < 2:
            raise WireGuardError(f"شبکه {cidr} برای تخصیص کلاینت کافی نیست.")

        # Reserve the first usable (the router itself) and the last usable.
        start = max(2, (server_host + 1) if server_host and server_host >= 1 else 2)
        end = total_hosts - 1
        if end < start:
            start, end = 1, total_hosts

        dns = ""
        try:
            dns_rows = api.get_resource("/ip/dns").get()
            if dns_rows:
                dns = (dns_rows[0].get("servers") or "").strip()
        except Exception:
            dns = ""
        if not dns:
            dns = DEFAULT_DNS

        return {
            "public_key": public_key,
            "listen_port": listen_port,
            "cidr": f"{net.network_address}/{net.prefixlen}",
            "subnet": str(net.network_address),
            "server_ip": server_ip,
            "prefix": net.prefixlen,
            "range_start": start,
            "range_end": end,
            "dns": dns,
            "interfaces": [i.get("name") for i in interfaces],
        }
    finally:
        _disconnect(pool)


def _create_sync(server, used_host_numbers, user_telegram_id):
    iface = _s(server, "wg_interface")
    net = _parse_network(_s(server, "wg_client_subnet"))
    if net is None:
        raise WireGuardError("subnet وایرگارد تنظیم نشده یا نامعتبر است (مثال: 10.66.66.0/24)")
    total_hosts = net.num_addresses - 2
    start = _to_int(_s(server, "wg_ip_range_start", 2)) or 2
    end = _to_int(_s(server, "wg_ip_range_end", total_hosts - 1)) or (total_hosts - 1)
    start = max(1, min(start, total_hosts))
    end = max(start, min(end, total_hosts))

    public_key, private_key = generate_keypair()

    pool = _open_pool(server)
    try:
        api = pool.get_api()
        interfaces = api.get_resource("/interface/wireguard").get()
        match = next((i for i in interfaces if i.get("name") == iface), None)
        if not match:
            raise WireGuardError(f"اینترفیس WireGuard «{iface}» روی روتر پیدا نشد")
        server_public_key = match.get("public-key", "") or ""

        peers_res = api.get_resource("/interface/wireguard/peers")
        peers = peers_res.get()

        used = set(used_host_numbers or set())
        for peer in peers:
            addr = (peer.get("allowed-address") or "").split("/")[0].strip()
            number = _host_number(addr, net)
            if number is not None and number > 0:
                used.add(number)

        client_ip = None
        for number in range(start, end + 1):
            if number not in used:
                client_ip = str(net.network_address + number)
                break
        if not client_ip:
            raise WireGuardError("IP آزادی در بازه تعیین‌شده یافت نشد")

        # The peer comment carries the user identifier so an admin can map a
        # peer back to its owner directly from the router.
        comment = f"user={user_telegram_id} wg={client_ip}"
        peers_res.add(
            **{
                "interface": iface,
                "public-key": public_key,
                "allowed-address": f"{client_ip}/32",
                "persistent-keepalive": f"{PERSISTENT_KEEPALIVE}s",
                "comment": comment,
            }
        )

        peer_id = ""
        for peer in peers_res.get():
            if (peer.get("public-key") or "") == public_key:
                peer_id = peer.get(".id", "") or ""
                break

        return {
            "client_ip": client_ip,
            "public_key": public_key,
            "private_key": private_key,
            "server_public_key": server_public_key,
            "peer_id": peer_id,
            "comment": comment,
        }
    finally:
        _disconnect(pool)


def _fetch_usage_sync(server):
    iface = _s(server, "wg_interface")
    pool = _open_pool(server)
    try:
        api = pool.get_api()
        peers = api.get_resource("/interface/wireguard/peers").get()
        usage = {}
        for peer in peers:
            if iface and peer.get("interface") != iface:
                continue
            public_key = peer.get("public-key")
            if not public_key:
                continue
            usage[public_key] = {
                "rx": _to_int(peer.get("rx") or peer.get("rx-byte")),
                "tx": _to_int(peer.get("tx") or peer.get("tx-byte")),
                "disabled": _is_disabled(peer.get("disabled")),
                "peer_id": peer.get(".id", "") or "",
                "client_ip": (peer.get("allowed-address") or "").split("/")[0].strip(),
            }
        return usage
    finally:
        _disconnect(pool)


def _find_peer(peers, public_key=None, peer_id=None, client_ip=None):
    for peer in peers:
        if peer_id and peer.get(".id") == peer_id:
            return peer
    for peer in peers:
        if public_key and peer.get("public-key") == public_key:
            return peer
    for peer in peers:
        if client_ip:
            addr = (peer.get("allowed-address") or "").split("/")[0].strip()
            if addr == client_ip:
                return peer
    return None


def _peer_action_sync(server, action, public_key=None, peer_id=None, client_ip=None):
    pool = _open_pool(server)
    try:
        api = pool.get_api()
        res = api.get_resource("/interface/wireguard/peers")
        peer = _find_peer(res.get(), public_key, peer_id, client_ip)
        if not peer:
            return False
        pid = peer.get(".id")
        if not pid:
            return False
        if action == "disable":
            res.set(**{".id": pid, "disabled": "yes"})
        elif action == "enable":
            res.set(**{".id": pid, "disabled": "no"})
        elif action == "delete":
            res.remove(**{".id": pid})
        elif action == "reset":
            res.set(**{".id": pid, "disabled": "yes"})
            res.set(**{".id": pid, "disabled": "no"})
        else:
            raise WireGuardError(f"عملیات نامعتبر: {action}")
        return True
    finally:
        _disconnect(pool)


# --------------------------------------------------------------------------- #
# Async public API
# --------------------------------------------------------------------------- #

CONNECT_TIMEOUT = 25.0


async def _run_with_timeout(func, *args, timeout: float = CONNECT_TIMEOUT):
    """Run a blocking RouterOS operation with a hard timeout.

    routeros-api has no built-in timeout, so an unreachable router would hang
    forever. wait_for returns control (and lets us report) after `timeout`.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=timeout)
    except asyncio.TimeoutError:
        raise WireGuardError(
            "اتصال به روتر Timeout خورد؛ آدرس/پورت API و دسترسی شبکه را بررسی کنید."
        )


async def test_connection(server):
    """Connect to the router and verify the WireGuard interface exists."""
    try:
        return await _run_with_timeout(_test_connection_sync, dict(server))
    except WireGuardError:
        raise
    except Exception as e:
        raise WireGuardError(_friendly_error(e))


async def inspect_interface(server):
    """Connect, verify access and return auto-detected WireGuard settings."""
    try:
        return await _run_with_timeout(_inspect_sync, dict(server))
    except WireGuardError:
        raise
    except Exception as e:
        raise WireGuardError(_friendly_error(e))


async def create_account(server, user_telegram_id, used_host_numbers=None):
    """Create a WireGuard peer for the user and return its details."""
    if used_host_numbers is None:
        from bot.database import db
        used_host_numbers = await db.get_wg_used_host_numbers(
            server["id"], _s(server, "wg_client_subnet")
        )
    try:
        return await _run_with_timeout(
            _create_sync, dict(server), set(used_host_numbers or set()), str(user_telegram_id)
        )
    except WireGuardError:
        raise
    except Exception as e:
        raise WireGuardError(_friendly_error(e))


async def fetch_usage(server):
    """Return {public_key: {rx, tx, disabled, peer_id, client_ip}} for peers."""
    return await _run_with_timeout(_fetch_usage_sync, dict(server), timeout=15.0)


async def set_peer_enabled(server, enabled: bool, public_key=None, peer_id=None, client_ip=None):
    action = "enable" if enabled else "disable"
    return await _run_with_timeout(
        _peer_action_sync, dict(server), action, public_key, peer_id, client_ip, timeout=15.0
    )


async def delete_peer(server, public_key=None, peer_id=None, client_ip=None):
    return await _run_with_timeout(
        _peer_action_sync, dict(server), "delete", public_key, peer_id, client_ip, timeout=15.0
    )


async def reset_peer(server, public_key=None, peer_id=None, client_ip=None):
    return await _run_with_timeout(
        _peer_action_sync, dict(server), "reset", public_key, peer_id, client_ip, timeout=15.0
    )


def build_config_text(private_key: str, client_ip: str, dns: str,
                      server_public_key: str, endpoint: str, port: int) -> str:
    dns = (dns or DEFAULT_DNS).strip()
    endpoint_host = format_endpoint_host(endpoint)
    lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {client_ip}/32",
    ]
    if dns:
        lines.append(f"DNS = {dns}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
        "AllowedIPs = 0.0.0.0/0, ::/0",
        f"Endpoint = {endpoint_host}:{port}",
        f"PersistentKeepalive = {PERSISTENT_KEEPALIVE}",
    ]
    return "\n".join(lines)


def make_qr_png(data: str):
    """Return PNG bytes of a QR code for the config, or None if unavailable."""
    try:
        import qrcode
        from io import BytesIO
    except ImportError:
        return None
    image = qrcode.make(data)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


WIREGUARD_APPS = (
    ("اندروید", "https://play.google.com/store/apps/details?id=com.wireguard.android"),
    ("آیفون", "https://apps.apple.com/us/app/wireguard/id1441195209"),
    ("ویندوز", "https://www.wireguard.com/install/"),
)


def build_delivery_caption(action_word: str, plan_name: str = "",
                           duration_days: int = 0, traffic_gb: int = 0,
                           expiry_text: str = "") -> str:
    """User-facing caption for a delivered WireGuard config.

    Intentionally does not print the client IP / endpoint; those live in
    «کانفیگ‌های من» and the .conf file.
    """
    lines = [f"✅ اشتراک وایرگارد شما {action_word} شد!", ""]
    if plan_name:
        lines.append(f"📦 پلن: {plan_name}")
    if duration_days and duration_days > 0:
        lines.append(f"📅 مدت: {duration_days} روز")
    if expiry_text:
        lines.append(f"⏳ تاریخ انقضا: {expiry_text}")
    lines.append(f"📊 حجم: {'نامحدود' if not traffic_gb else str(traffic_gb) + ' GB'}")
    lines += [
        "",
        "📲 نحوه اتصال:",
        "۱) اپلیکیشن WireGuard را نصب کنید.",
        "۲) فایل کانفیگ را ایمپورت کنید (یا QR را اسکن کنید).",
        "۳) اتصال را روشن کنید.",
        "",
        "🔗 دانلود اپلیکیشن:",
    ]
    for label, url in WIREGUARD_APPS:
        lines.append(f"• {label}: {url}")
    lines += [
        "",
        "ℹ️ مشخصات اکانت شما همیشه در «📋 کانفیگ های من» قابل مشاهده است.",
    ]
    return "\n".join(lines)

"""MikroTik WireGuard account management over the RouterOS API.

The RouterOS API client is synchronous, so every network operation is executed
in a worker thread via ``asyncio.to_thread`` to keep the aiogram event loop free.

A WireGuard "server" is a MikroTik router whose ``servers`` row has
``service_type='wireguard'`` and the ``wg_*`` / ``api_port`` columns filled in.
"""
import asyncio
import base64
import logging

logger = logging.getLogger(__name__)

DEFAULT_DNS = "1.1.1.1,8.8.8.8"


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


def _split_prefix(subnet: str) -> str:
    base = (subnet or "").split("/")[0].strip()
    parts = base.rsplit(".", 1)
    if len(parts) != 2:
        raise WireGuardError("subnet وایرگارد نامعتبر است (مثال: 10.66.66.0)")
    return parts[0] + "."


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


def _create_sync(server, used_last_octets, user_telegram_id):
    iface = _s(server, "wg_interface")
    prefix = _split_prefix(_s(server, "wg_client_subnet"))
    start = _to_int(_s(server, "wg_ip_range_start", 10)) or 10
    end = _to_int(_s(server, "wg_ip_range_end", 250)) or 250
    if end < start:
        start, end = end, start

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

        used = set(used_last_octets or set())
        for peer in peers:
            addr = (peer.get("allowed-address") or "").split("/")[0].strip()
            if addr.count(".") == 3 and addr.startswith(prefix):
                try:
                    used.add(int(addr.rsplit(".", 1)[-1]))
                except ValueError:
                    continue

        client_ip = None
        for i in range(start, end + 1):
            if i not in used:
                client_ip = f"{prefix}{i}"
                break
        if not client_ip:
            raise WireGuardError("IP آزادی در بازه تعیین‌شده یافت نشد")

        comment = f"{user_telegram_id}-wg-{client_ip.rsplit('.', 1)[-1]}"
        peers_res.add(
            **{
                "interface": iface,
                "public-key": public_key,
                "allowed-address": f"{client_ip}/32",
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

async def test_connection(server):
    """Connect to the router and verify the WireGuard interface exists."""
    return await asyncio.to_thread(_test_connection_sync, dict(server))


async def create_account(server, user_telegram_id, used_last_octets=None):
    """Create a WireGuard peer for the user and return its details."""
    if used_last_octets is None:
        from bot.database import db
        used_last_octets = await db.get_wg_used_last_octets(
            server["id"], _s(server, "wg_client_subnet")
        )
    return await asyncio.to_thread(
        _create_sync, dict(server), set(used_last_octets or set()), str(user_telegram_id)
    )


async def fetch_usage(server):
    """Return {public_key: {rx, tx, disabled, peer_id, client_ip}} for peers."""
    return await asyncio.to_thread(_fetch_usage_sync, dict(server))


async def set_peer_enabled(server, enabled: bool, public_key=None, peer_id=None, client_ip=None):
    action = "enable" if enabled else "disable"
    return await asyncio.to_thread(
        _peer_action_sync, dict(server), action, public_key, peer_id, client_ip
    )


async def delete_peer(server, public_key=None, peer_id=None, client_ip=None):
    return await asyncio.to_thread(
        _peer_action_sync, dict(server), "delete", public_key, peer_id, client_ip
    )


async def reset_peer(server, public_key=None, peer_id=None, client_ip=None):
    return await asyncio.to_thread(
        _peer_action_sync, dict(server), "reset", public_key, peer_id, client_ip
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
        "PersistentKeepalive = 25",
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

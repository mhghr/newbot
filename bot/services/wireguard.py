"""MikroTik WireGuard account management over **SSH**.

We talk to RouterOS through its SSH CLI (paramiko) rather than the binary API:
on some RouterOS/CHR builds ``/interface/wireguard/peers/print`` hangs over the
binary API while the same command over SSH returns instantly. Every network
operation is blocking, so it runs in a worker thread via ``asyncio.to_thread``.

A "server" is a MikroTik router whose ``servers`` row has
``service_type='wireguard'`` and the ``wg_*`` columns filled in. ``api_port``
holds the **SSH** port (default 22), ``username``/``password`` are the SSH
credentials.
"""
import asyncio
import base64
import ipaddress
import logging
import re

logger = logging.getLogger(__name__)

DEFAULT_DNS = "1.1.1.1,8.8.8.8"

# Seconds between keepalive packets, applied both to the router-side peer and
# the generated client .conf so the tunnel stays alive behind NAT.
PERSISTENT_KEEPALIVE = 25

CONNECT_TIMEOUT = 25.0
DEFAULT_SSH_PORT = 22
CLI_TIMEOUT = 15.0


class WireGuardError(Exception):
    """Raised for any expected/validation failure while talking to MikroTik."""


def _load_paramiko():
    try:
        import paramiko
    except ImportError:
        return None
    return paramiko


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


def merge_usage(used_bytes, last_rx, last_tx, rx, tx):
    """Fold fresh peer counters into the stored usage baseline.

    RouterOS peer counters are cumulative but reset to zero on peer reset or
    router reboot. Each direction is handled independently, so a reset of one
    counter cannot corrupt the other. Returns ``(new_used_bytes, rx, tx)``.
    """
    used = _to_int(used_bytes)
    prev_rx = _to_int(last_rx)
    prev_tx = _to_int(last_tx)
    rx = _to_int(rx)
    tx = _to_int(tx)
    delta_rx = rx - prev_rx if rx >= prev_rx else rx
    delta_tx = tx - prev_tx if tx >= prev_tx else tx
    return max(0, used + delta_rx + delta_tx), rx, tx


def generate_keypair():
    """Return (public_key, private_key) base64 encoded X25519 keys."""
    try:
        from cryptography.hazmat.primitives.asymmetric import x25519
        from cryptography.hazmat.primitives import serialization
    except ImportError:
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
    if "authentication" in low or "auth" in low or "login" in low or "invalid user" in low or "password" in low:
        return "یوزرنیم یا پسورد SSH روتر اشتباه است."
    if "banner" in low or "not a valid ssh" in low or "protocol banner" in low:
        return "پورت اشتباه است یا سرویس SSH روی روتر فعال نیست."
    if "timed out" in low or "timeout" in low:
        return "اتصال SSH به روتر Timeout شد. آدرس/پورت SSH و دسترسی شبکه را بررسی کنید."
    if "refused" in low:
        return "اتصال SSH رد شد. پورت یا فعال بودن SSH روی روتر را بررسی کنید."
    if "unreachable" in low or "no route" in low or "name or service not known" in low:
        return "روتر در دسترس نیست. آدرس را بررسی کنید."
    return text


def format_endpoint_host(host: str) -> str:
    if host and ":" in host and not (host.startswith("[") and host.endswith("]")):
        return f"[{host}]"
    return host


# --------------------------------------------------------------------------- #
# SSH transport
# --------------------------------------------------------------------------- #

def _recv_exact(sock, count: int) -> bytes:
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise WireGuardError("اتصال پروکسی SOCKS قطع شد")
        buf += chunk
    return buf


def _socks5_connect(proxy_host, proxy_port, dst_host, dst_port,
                    timeout, username="", password=""):
    """Open a TCP connection to (dst_host, dst_port) through a SOCKS5 proxy.

    Returns the connected socket so paramiko can run SSH over it. This is used
    when the bot server cannot reach the routers directly but can reach a relay
    that can (see ``config.WG_SSH_PROXY``).
    """
    import socket
    import struct

    sock = socket.create_connection((proxy_host, int(proxy_port)), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(b"\x05\x02\x00\x02" if username else b"\x05\x01\x00")
        ver, method = _recv_exact(sock, 2)
        if ver != 5:
            raise WireGuardError("پاسخ نامعتبر از پروکسی SOCKS")
        if method == 0x02:
            user_b = username.encode("utf-8")
            pass_b = password.encode("utf-8")
            sock.sendall(b"\x01" + bytes([len(user_b)]) + user_b
                         + bytes([len(pass_b)]) + pass_b)
            if _recv_exact(sock, 2)[1] != 0:
                raise WireGuardError("احراز هویت پروکسی SOCKS ناموفق بود")
        elif method != 0x00:
            raise WireGuardError("پروکسی SOCKS روش احراز هویت ناشناس را رد کرد")

        try:
            addr = socket.inet_aton(dst_host)
            request = b"\x05\x01\x00\x01" + addr
        except OSError:
            host_b = dst_host.encode("idna")
            request = b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b
        request += struct.pack(">H", int(dst_port))
        sock.sendall(request)

        _ver, reply, _rsv, atyp = _recv_exact(sock, 4)
        if reply != 0:
            raise WireGuardError(f"پروکسی SOCKS نتوانست به روتر وصل شود (کد {reply})")
        if atyp == 0x01:
            _recv_exact(sock, 4)
        elif atyp == 0x03:
            _recv_exact(sock, _recv_exact(sock, 1)[0])
        elif atyp == 0x04:
            _recv_exact(sock, 16)
        _recv_exact(sock, 2)  # bound port
        return sock
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        raise


def _proxy_socket(host, port):
    """Return a SOCKS5-tunneled socket to (host, port), or None if no proxy.

    The proxy is configured globally with ``WG_SSH_PROXY`` ("host:port"); it is
    only needed when the routers are unreachable directly from the bot server.
    """
    from bot import config
    raw = (getattr(config, "WG_SSH_PROXY", "") or "").strip()
    if not raw:
        return None
    proxy_host, _, proxy_port = raw.rpartition(":")
    if not proxy_host or not proxy_port.isdigit():
        raise WireGuardError("تنظیم پروکسی SOCKS نامعتبر است (مثال: 1.2.3.4:1080)")
    return _socks5_connect(
        proxy_host, int(proxy_port), host, port, CONNECT_TIMEOUT,
        getattr(config, "WG_SSH_PROXY_USER", ""),
        getattr(config, "WG_SSH_PROXY_PASS", ""),
    )


def _open_ssh(server):
    paramiko = _load_paramiko()
    if paramiko is None:
        raise WireGuardError("کتابخانه paramiko نصب نیست (pip install paramiko)")

    host = _s(server, "url")
    if not host:
        raise WireGuardError("آدرس روتر تنظیم نشده است")
    port = _to_int(_s(server, "api_port", DEFAULT_SSH_PORT)) or DEFAULT_SSH_PORT

    sock = _proxy_socket(host, port)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=_s(server, "username"),
            password=_s(server, "password"),
            timeout=CONNECT_TIMEOUT,
            banner_timeout=CONNECT_TIMEOUT,
            auth_timeout=CONNECT_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
            sock=sock,
        )
    except Exception:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        raise
    return client


def _close_ssh(client):
    try:
        client.close()
    except Exception:
        pass


def _run_cli(ssh, command: str, timeout: float = CLI_TIMEOUT) -> str:
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    text = (out + "\n" + err).strip()
    low = text.lower()
    for marker in ("failure:", "syntax error", "no such item", "bad command",
                   "expected end of command", "unknown command"):
        if marker in low:
            raise WireGuardError(text.splitlines()[0].strip() if text else "خطای نامشخص روتر")
    return out


_TERSE_KV = re.compile(r'([A-Za-z0-9._\-]+)=("[^"]*"|\S+)')


def _parse_terse(text: str) -> list:
    """Parse RouterOS ``print terse`` output into a list of dicts."""
    records = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Flags:") or line.startswith("Columns:"):
            continue
        line = re.sub(r'^\d+\s+[A-Z]*\s*', '', line)
        fields = {}
        for m in _TERSE_KV.finditer(line):
            key, val = m.group(1), m.group(2)
            if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                val = val[1:-1]
            fields[key] = val
        if fields:
            records.append(fields)
    return records


def _interfaces(ssh):
    return _parse_terse(_run_cli(ssh, "/interface/wireguard/print terse"))


def _peers(ssh):
    return _parse_terse(_run_cli(ssh, "/interface/wireguard/peers/print terse"))


def _addresses(ssh):
    return _parse_terse(_run_cli(ssh, "/ip/address/print terse"))


def _dns_servers(ssh) -> str:
    text = _run_cli(ssh, "/ip/dns/print")
    servers = []
    capture = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("servers:"):
            capture = True
            rest = line.split(":", 1)[1].strip()
            servers += [s.strip() for s in re.split(r"[,\s]+", rest) if s.strip()]
            continue
        if capture:
            if ":" in line:
                break
            servers += [s.strip() for s in re.split(r"[,\s]+", line) if s.strip()]
    return ",".join(servers)


def _find_peer(peers, public_key=None, client_ip=None):
    if public_key:
        for peer in peers:
            if peer.get("public-key") == public_key:
                return peer
    if client_ip:
        wanted = client_ip.split("/")[0]
        for peer in peers:
            addr = (peer.get("allowed-address") or "").split("/")[0].strip()
            if addr == wanted:
                return peer
    return None


def _find_expr(public_key=None, client_ip=None) -> str:
    if public_key:
        return f'[find public-key="{public_key}"]'
    if client_ip:
        return f'[find allowed-address="{client_ip.split("/")[0]}/32"]'
    raise WireGuardError("شناسه peer برای عملیات مشخص نیست")


# --------------------------------------------------------------------------- #
# Synchronous worker functions (run inside a thread)
# --------------------------------------------------------------------------- #

def _test_connection_sync(server):
    iface = _s(server, "wg_interface")
    ssh = _open_ssh(server)
    try:
        interfaces = _interfaces(ssh)
        names = [i.get("name") for i in interfaces]
        if iface and iface not in names:
            raise WireGuardError(f"اینترفیس WireGuard «{iface}» روی روتر پیدا نشد")
        public_key = ""
        for i in interfaces:
            if i.get("name") == iface:
                public_key = i.get("public-key", "") or ""
        return {"interfaces": names, "public_key": public_key}
    finally:
        _close_ssh(ssh)


def _inspect_sync(server):
    """Connect over SSH, verify access and auto-detect every WireGuard setting."""
    iface = _s(server, "wg_interface")
    ssh = _open_ssh(server)
    try:
        interfaces = _interfaces(ssh)
        match = next((i for i in interfaces if i.get("name") == iface), None)
        if not match:
            names = ", ".join(i.get("name", "?") for i in interfaces) or "-"
            raise WireGuardError(
                f"اینترفیس WireGuard «{iface}» روی روتر پیدا نشد.\n"
                f"اینترفیس‌های موجود: {names}"
            )

        public_key = match.get("public-key", "") or ""
        listen_port = _to_int(match.get("listen-port")) or 51820

        cidr = ""
        for item in _addresses(ssh):
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

        dns = _dns_servers(ssh)
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
        _close_ssh(ssh)


def _create_sync(server, used_host_numbers, user_telegram_id):
    iface = _s(server, "wg_interface")
    net = _parse_network(_s(server, "wg_client_subnet"))
    if net is None:
        raise WireGuardError("subnet WireGuard تنظیم نشده یا نامعتبر است (مثال: 10.66.66.0/24)")
    total_hosts = net.num_addresses - 2
    start = _to_int(_s(server, "wg_ip_range_start", 2)) or 2
    end = _to_int(_s(server, "wg_ip_range_end", total_hosts - 1)) or (total_hosts - 1)
    start = max(1, min(start, total_hosts))
    end = max(start, min(end, total_hosts))

    public_key, private_key = generate_keypair()

    ssh = _open_ssh(server)
    try:
        interfaces = _interfaces(ssh)
        match = next((i for i in interfaces if i.get("name") == iface), None)
        if not match:
            raise WireGuardError(f"اینترفیس WireGuard «{iface}» روی روتر پیدا نشد")
        server_public_key = match.get("public-key", "") or ""

        used = set(used_host_numbers or set())
        for peer in _peers(ssh):
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
        _run_cli(
            ssh,
            f'/interface/wireguard/peers/add interface="{iface}" public-key="{public_key}" '
            f'allowed-address={client_ip}/32 persistent-keepalive={PERSISTENT_KEEPALIVE}s '
            f'comment="{comment}"',
        )

        peer_id = ""
        for peer in _peers(ssh):
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
        _close_ssh(ssh)


def _fetch_usage_sync(server):
    iface = _s(server, "wg_interface")
    ssh = _open_ssh(server)
    try:
        usage = {}
        for peer in _peers(ssh):
            if iface and peer.get("interface") != iface:
                continue
            public_key = peer.get("public-key")
            if not public_key:
                continue
            usage[public_key] = {
                "rx": _to_int(peer.get("rx")),
                "tx": _to_int(peer.get("tx")),
                "disabled": _is_disabled(peer.get("disabled")),
                "peer_id": peer.get(".id", "") or "",
                "client_ip": (peer.get("allowed-address") or "").split("/")[0].strip(),
            }
        return usage
    finally:
        _close_ssh(ssh)


def _peer_action_sync(server, action, public_key=None, peer_id=None, client_ip=None):
    ssh = _open_ssh(server)
    try:
        peer = _find_peer(_peers(ssh), public_key, client_ip)
        if not peer:
            return False
        expr = _find_expr(public_key or peer.get("public-key"),
                          client_ip or peer.get("allowed-address"))
        if action == "disable":
            _run_cli(ssh, f"/interface/wireguard/peers/set {expr} disabled=yes")
        elif action == "enable":
            _run_cli(ssh, f"/interface/wireguard/peers/set {expr} disabled=no")
        elif action == "delete":
            _run_cli(ssh, f"/interface/wireguard/peers/remove {expr}")
        elif action == "reset":
            _run_cli(ssh, f"/interface/wireguard/peers/set {expr} disabled=yes")
            _run_cli(ssh, f"/interface/wireguard/peers/set {expr} disabled=no")
        else:
            raise WireGuardError(f"عملیات نامعتبر: {action}")
        return True
    finally:
        _close_ssh(ssh)


# --------------------------------------------------------------------------- #
# Async public API
# --------------------------------------------------------------------------- #

async def _run_with_timeout(func, *args, timeout: float = CONNECT_TIMEOUT):
    """Run a blocking MicroTik SSH operation with a hard timeout."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=timeout)
    except asyncio.TimeoutError:
        raise WireGuardError(
            "اتصال SSH به روتر Timeout خورد؛ آدرس/پورت SSH و دسترسی شبکه را بررسی کنید."
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
    return await _run_with_timeout(_fetch_usage_sync, dict(server), timeout=CONNECT_TIMEOUT)


async def set_peer_enabled(server, enabled: bool, public_key=None, peer_id=None, client_ip=None):
    action = "enable" if enabled else "disable"
    return await _run_with_timeout(
        _peer_action_sync, dict(server), action, public_key, peer_id, client_ip, timeout=CONNECT_TIMEOUT
    )


async def delete_peer(server, public_key=None, peer_id=None, client_ip=None):
    return await _run_with_timeout(
        _peer_action_sync, dict(server), "delete", public_key, peer_id, client_ip, timeout=CONNECT_TIMEOUT
    )


async def reset_peer(server, public_key=None, peer_id=None, client_ip=None):
    return await _run_with_timeout(
        _peer_action_sync, dict(server), "reset", public_key, peer_id, client_ip, timeout=CONNECT_TIMEOUT
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


# --------------------------------------------------------------------------- #
# Multi-router (cluster) operations
#
# The same user peer is provisioned on every entry router of a cluster so a
# client works whichever router the endpoint domain points to. All helpers here
# operate by public key (stable across routers).
# --------------------------------------------------------------------------- #

def _all_used_host_numbers(ssh, net):
    used = set()
    for peer in _peers(ssh):
        addr = (peer.get("allowed-address") or "").split("/")[0].strip()
        number = _host_number(addr, net)
        if number is not None and number > 0:
            used.add(number)
    return used


def _create_multi_sync(servers, used_host_numbers, user_telegram_id):
    if not servers:
        raise WireGuardError("سروری برای ساخت اکانت مشخص نشد")
    primary = servers[0]
    iface = _s(primary, "wg_interface")
    net = _parse_network(_s(primary, "wg_client_subnet"))
    if net is None:
        raise WireGuardError("subnet WireGuard تنظیم نشده یا نامعتبر است (مثال: 10.66.66.0/24)")
    total_hosts = net.num_addresses - 2
    start = _to_int(_s(primary, "wg_ip_range_start", 2)) or 2
    end = _to_int(_s(primary, "wg_ip_range_end", total_hosts - 1)) or (total_hosts - 1)
    start = max(1, min(start, total_hosts))
    end = max(start, min(end, total_hosts))

    public_key, private_key = generate_keypair()
    open_ssh = []
    try:
        used = set(used_host_numbers or set())
        server_public_key = ""
        for server in servers:
            ssh = _open_ssh(server)
            open_ssh.append((server, ssh))
            match = next((i for i in _interfaces(ssh) if i.get("name") == iface), None)
            if not match:
                raise WireGuardError(
                    f'اینترفیس WireGuard «{iface}» روی روتر {_s(server, "url")} پیدا نشد'
                )
            if not server_public_key:
                server_public_key = match.get("public-key", "") or ""
            used |= _all_used_host_numbers(ssh, net)

        client_ip = None
        for number in range(start, end + 1):
            if number not in used:
                client_ip = str(net.network_address + number)
                break
        if not client_ip:
            raise WireGuardError("IP آزادی در بازه تعیین‌شده یافت نشد")

        comment = f"user={user_telegram_id} wg={client_ip}"
        for server, ssh in open_ssh:
            _run_cli(
                ssh,
                f'/interface/wireguard/peers/add interface="{iface}" public-key="{public_key}" '
                f'allowed-address={client_ip}/32 persistent-keepalive={PERSISTENT_KEEPALIVE}s '
                f'comment="{comment}"',
            )

        peer_ids = {}
        for server, ssh in open_ssh:
            for peer in _peers(ssh):
                if (peer.get("public-key") or "") == public_key:
                    peer_ids[_s(server, "id")] = peer.get(".id", "") or ""
                    break

        return {
            "client_ip": client_ip,
            "public_key": public_key,
            "private_key": private_key,
            "server_public_key": server_public_key,
            "comment": comment,
            "peer_id": peer_ids.get(_s(primary, "id"), ""),
            "peer_ids": peer_ids,
            "server_ids": [_s(s, "id") for s, _ in open_ssh],
        }
    except Exception:
        # Roll back so a partially provisioned cluster never stays inconsistent.
        for _server, ssh in open_ssh:
            try:
                _run_cli(ssh, f'/interface/wireguard/peers/remove [find public-key="{public_key}"]')
            except Exception:
                pass
        raise
    finally:
        for _server, ssh in open_ssh:
            _close_ssh(ssh)


def _multi_action_sync(servers, action, public_key=None, client_ip=None):
    if not public_key and not client_ip:
        raise WireGuardError("شناسه peer برای عملیات مشخص نیست")
    done = 0
    errors = []
    for server in servers:
        try:
            ssh = _open_ssh(server)
            try:
                peer = _find_peer(_peers(ssh), public_key, client_ip)
                if not peer:
                    continue
                pk = public_key or peer.get("public-key")
                expr = f'[find public-key="{pk}"]'
                if action == "disable":
                    _run_cli(ssh, f"/interface/wireguard/peers/set {expr} disabled=yes")
                elif action == "enable":
                    _run_cli(ssh, f"/interface/wireguard/peers/set {expr} disabled=no")
                elif action == "delete":
                    _run_cli(ssh, f"/interface/wireguard/peers/remove {expr}")
                elif action == "reset":
                    _run_cli(ssh, f"/interface/wireguard/peers/set {expr} disabled=yes")
                    _run_cli(ssh, f"/interface/wireguard/peers/set {expr} disabled=no")
                else:
                    raise WireGuardError(f"عملیات نامعتبر: {action}")
                done += 1
            finally:
                _close_ssh(ssh)
        except Exception as e:
            errors.append(f'{_s(server, "url")}: {e}')
    if done == 0 and errors:
        raise WireGuardError("; ".join(errors))
    return done


async def create_account_multi(servers, user_telegram_id, used_host_numbers=None):
    """Create the same peer on every router of a cluster. Returns peer details."""
    servers = [dict(s) for s in servers]
    if not servers:
        raise WireGuardError("سروری برای ساخت اکانت مشخص نشد")
    if used_host_numbers is None:
        from bot.database import db
        subnet = _s(servers[0], "wg_client_subnet")
        server_ids = [_s(s, "id") for s in servers]
        used_host_numbers = await db.get_wg_used_host_numbers_multi(server_ids, subnet)
    try:
        return await _run_with_timeout(
            _create_multi_sync, servers, set(used_host_numbers or set()), str(user_telegram_id),
            timeout=60.0,
        )
    except WireGuardError:
        raise
    except Exception as e:
        raise WireGuardError(_friendly_error(e))


async def _multi_action_async(servers, action, public_key=None, client_ip=None):
    servers = [dict(s) for s in servers]
    if not servers:
        return 0
    return await _run_with_timeout(
        _multi_action_sync, servers, action, public_key, client_ip, timeout=60.0
    )


async def set_peer_enabled_multi(servers, enabled: bool, public_key=None, client_ip=None):
    return await _multi_action_async(
        servers, "enable" if enabled else "disable", public_key, client_ip
    )


async def delete_peer_multi(servers, public_key=None, client_ip=None):
    return await _multi_action_async(servers, "delete", public_key, client_ip)


async def reset_peer_multi(servers, public_key=None, client_ip=None):
    return await _multi_action_async(servers, "reset", public_key, client_ip)


# --------------------------------------------------------------------------- #
# Health check (for DNS failover)
# --------------------------------------------------------------------------- #

_DURATION_RE = re.compile(r'(\d+)([wdhms])', re.IGNORECASE)


def _parse_router_duration(value) -> int:
    """Parse RouterOS durations like ``1m2s``/``2h``/``3d`` into seconds.

    Returns a very large number for ``never``/empty so a peer that never
    handshaked is treated as stale.
    """
    text = str(value or "").strip().lower()
    if not text or text in ("never", "none", "n/a"):
        return 10 ** 9
    total = 0
    for amount, unit in _DURATION_RE.findall(text):
        unit = unit.lower()
        factor = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        total += int(amount) * factor
    return total if total else 10 ** 9


def _ping_stats(ssh, ip: str, count: int = 5):
    """Return ``{"loss": int|None, "rtt": float|None}`` for a RouterOS ping."""
    try:
        text = _run_cli(ssh, f"/ping {ip} count={count}", timeout=25.0)
    except Exception:
        return None
    loss = None
    m = re.search(r'packet-loss=(\d+)%', text)
    if m:
        loss = int(m.group(1))
    else:
        m = re.search(r'(\d+)%', text)
        if m:
            loss = int(m.group(1))
    rtt = None
    m = re.search(r'avg-rtt=([\d.]+)ms', text)
    if m:
        try:
            rtt = float(m.group(1))
        except ValueError:
            rtt = None
    if loss is None and rtt is None:
        return None
    return {"loss": loss, "rtt": rtt}


def _better_path(tunnel, e2e):
    """Pick the metric used to judge whether the router still serves users.

    Prefer the tunnel metric (ping to the exit peer's inner IP): end-to-end
    ICMP to public IPs is frequently blocked by the exit or the ISP, which
    produced false "high-loss" readings and stopped failover from switching to
    a healthy router. Fall back to e2e only when the tunnel has no reading.
    """
    if tunnel and tunnel.get("loss") is not None:
        return tunnel
    candidates = [v for v in (e2e or {}).values() if v and v.get("loss") is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda v: (v["loss"], v.get("rtt") or 1e9))


def _other_host_in_subnet(cidr: str):
    net = _parse_network(cidr)
    if net is None:
        return None
    local = cidr.split("/")[0].strip()
    try:
        hosts = [str(h) for h in net.hosts()]
    except Exception:
        return None
    for host in hosts:
        if host != local:
            return host
    return None


def _ensure_health_mark(ssh, test_ips, routing_table="wg-table"):
    """Make locally-originated test pings follow the users' routing table."""
    try:
        existing = _run_cli(ssh, '/ip/firewall/mangle/print terse where comment="BOT-HC"')
    except Exception:
        existing = ""
    for ip in test_ips:
        if f'dst-address={ip}' in existing:
            continue
        try:
            _run_cli(
                ssh,
                f'/ip/firewall/mangle/add chain=output action=mark-routing '
                f'new-routing-mark={routing_table} dst-address={ip} protocol=icmp '
                f'comment="BOT-HC"',
            )
        except Exception:
            pass


def _health_sync(server, test_ips, handshake_max, loss_degraded, rtt_degraded, loss_unhealthy):
    iface = _s(server, "wg_interface")
    ssh = _open_ssh(server)
    metrics = {
        "ssh": True, "healthy": False, "degraded": False,
        "loss": None, "rtt": None, "reason": "",
    }
    try:
        interfaces = _interfaces(ssh)
        names = [i.get("name") for i in interfaces]
        if iface not in names:
            metrics["reason"] = "wg-interface-missing"
            return metrics

        peers = _peers(ssh)
        upstream_iface = None
        upstream_age = 10 ** 9
        for peer in peers:
            p_iface = peer.get("interface")
            if not p_iface or p_iface == iface:
                continue
            allowed = peer.get("allowed-address") or ""
            if "0.0.0.0/0" in allowed or "::/0" in allowed:
                age = _parse_router_duration(peer.get("last-handshake"))
                if age < upstream_age:
                    upstream_age = age
                    upstream_iface = p_iface
        metrics["upstream_interface"] = upstream_iface
        metrics["upstream_handshake_age"] = upstream_age if upstream_age < 10 ** 8 else None

        tunnel = None
        if upstream_iface:
            remote_ip = None
            for item in _addresses(ssh):
                if item.get("interface") == upstream_iface and item.get("address"):
                    remote_ip = _other_host_in_subnet(item["address"])
                    break
            if remote_ip:
                tunnel = _ping_stats(ssh, remote_ip)
        metrics["tunnel"] = tunnel

        _ensure_health_mark(ssh, test_ips)
        e2e = {ip: _ping_stats(ssh, ip) for ip in test_ips}
        metrics["e2e"] = e2e

        primary = _better_path(tunnel, e2e)
        if primary:
            metrics["loss"] = primary.get("loss")
            metrics["rtt"] = primary.get("rtt")

        handshake_ok = upstream_age <= handshake_max
        reachable = primary is not None and primary.get("loss") is not None
        metrics["healthy"] = bool(
            handshake_ok and reachable and primary["loss"] < loss_unhealthy
        )
        metrics["degraded"] = bool(
            metrics["healthy"]
            and (
                primary["loss"] >= loss_degraded
                or (primary.get("rtt") is not None and primary["rtt"] >= rtt_degraded)
            )
        )
        if not metrics["healthy"]:
            reasons = []
            if not handshake_ok:
                reasons.append("tunnel-handshake-stale")
            if not reachable:
                reasons.append("path-unreachable")
            elif primary["loss"] >= loss_unhealthy:
                reasons.append("high-loss")
            metrics["reason"] = ",".join(reasons)
        elif metrics["degraded"]:
            metrics["reason"] = "slow-or-lossy"
        return metrics
    finally:
        _close_ssh(ssh)


async def check_health(server, test_ips=("9.9.9.9", "1.1.1.1"), *,
                       handshake_max=180, loss_degraded=15,
                       rtt_degraded=300, loss_unhealthy=40):
    try:
        return await _run_with_timeout(
            _health_sync, dict(server), tuple(test_ips),
            handshake_max, loss_degraded, rtt_degraded, loss_unhealthy,
            timeout=60.0,
        )
    except Exception as e:
        return {
            "ssh": False, "healthy": False, "degraded": False,
            "loss": None, "rtt": None, "reason": f"{type(e).__name__}: {e}",
        }


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
    lines = [f"✅ اشتراک WireGuard شما {action_word} شد!", ""]
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

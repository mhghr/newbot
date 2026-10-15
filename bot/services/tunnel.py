"""Establish a SoftEther site-to-site tunnel between an Iran node and a foreign node.

Runs over SSH (paramiko). Everything is best-effort idempotent so re-running a
tunnel that is already up does not break it.
"""
import logging
import time

logger = logging.getLogger(__name__)

try:
    import paramiko
except Exception:  # pragma: no cover
    paramiko = None

from bot.services.xui import XUIClient

SOFTETHER_URL = (
    "https://www.softether-download.com/files/softether/"
    "v4.43-9799-beta-2023.08.31-tree/Linux/SoftEther_VPN_Server/"
    "64bit_-_Intel_x64_or_AMD64/"
    "softether-vpnserver-v4.43-9799-beta-2023.08.31-linux-x64-64bit.tar.gz"
)


class TunnelError(Exception):
    pass


def _connect(node, timeout: int = 15):
    if paramiko is None:
        raise TunnelError("paramiko is not installed on the bot host")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=node["ip"],
        port=int(node.get("ssh_port") or 22),
        username=node.get("ssh_user") or "root",
        password=node.get("ssh_pass") or "",
        timeout=timeout,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def test_ssh(node, timeout: int = 15) -> str:
    """Return 'root' / 'sudo' style info by running a trivial command."""
    client = _connect(node, timeout)
    try:
        _in, out, err = client.exec_command("id -un; whoami 2>/dev/null", timeout=timeout)
        user = (out.read().decode(errors="replace").strip().splitlines() or [""])[0]
        return user
    finally:
        client.close()


def _run(client, script: str, timeout: int = 900) -> str:
    """Upload and run a bash script as root (via sudo if not already root)."""
    sftp = client.open_sftp()
    remote = f"/tmp/tunnel_{int(time.time())}.sh"
    with sftp.file(remote, "w") as f:
        f.write(script)
    sftp.close()
    _in, out, err = client.exec_command(
        f"chmod +x {remote}; if [ \"$(id -u)\" = 0 ]; then bash {remote}; else sudo bash {remote}; fi; rc=$?; rm -f {remote}; exit $rc",
        timeout=timeout,
    )
    stdout = out.read().decode(errors="replace")
    stderr = err.read().decode(errors="replace")
    rc = out.channel.recv_exit_status()
    if rc != 0:
        raise TunnelError(f"remote script failed (rc={rc}): {stderr[-400:] or stdout[-400:]}")
    return stdout


def _install_softether_script() -> str:
    """Bash snippet that installs SoftEther into /usr/local/softether if missing."""
    return f"""
if [ ! -x /usr/local/softether/vpnserver/vpnserver ]; then
  set -e
  cd /tmp
  ARCH=$(uname -m)
  apt-get update -qq >/dev/null 2>&1 || true
  for pkg in libreadline8 libssl3 zlib1g; do apt-get install -y -qq $pkg >/dev/null 2>&1 || true; done
  curl -fsSL --retry 3 -o softether.tar.gz "{SOFTETHER_URL}"
  rm -rf /tmp/softether-extract && mkdir -p /tmp/softether-extract
  tar xzf softether.tar.gz -C /tmp/softether-extract
  mkdir -p /usr/local/softether
  cp -a /tmp/softether-extract/vpnserver /usr/local/softether/
  chmod +x /usr/local/softether/vpnserver/vpnserver /usr/local/softether/vpnserver/vpncmd
fi
"""


def _systemd_unit_script() -> str:
    return """
cat > /etc/systemd/system/softether-vpnserver.service <<'UNIT'
[Unit]
Description=SoftEther VPN Server
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
TasksMax=infinity
ExecStart=/usr/local/softether/vpnserver/vpnserver execsvc
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
"""


def provision_iran(iran, tunnel):
    """Set up the SoftEther hub + tap + listener + cascade user on the Iran node."""
    tid = tunnel["id"]
    port = 4300 + tid
    hub = f"IRHUB{tid}"
    user = f"s2s{tid}"
    userpass = tunnel["userpass"]
    subnet = tunnel["subnet"]        # e.g. 10.240.5.0/30
    tap_ip = tunnel["iran_tap_ip"]   # e.g. 10.240.5.1
    script = f"""
set -e
{_install_softether_script()}
{_systemd_unit_script()}
systemctl enable --now softether-vpnserver >/dev/null 2>&1 || /usr/local/softether/vpnserver/vpnserver start
sleep 3
cd /usr/local/softether/vpnserver
printf 'HubCreate {hub} /PASSWORD:{userpass}\\nListenerCreate {port}\\nListenerList\\nexit\\n' | ./vpncmd /SERVER localhost >/dev/null 2>&1 || true
# tap + local bridge
ip tuntap add dev tap_soft mode tap 2>/dev/null || true
ip link set dev tap_soft up
ip addr add {tap_ip}/30 dev tap_soft 2>/dev/null || true
printf 'Hub {hub}\\nBridgeCreate {hub} /DEVICE:soft /TAP:yes\\nUserCreate {user} /GROUP:none /REALNAME:{user} /NOTE:cascade\\nUserPasswordSet {user} /PASSWORD:{userpass}\\nexit\\n' | ./vpncmd /SERVER localhost >/dev/null 2>&1 || true
echo "IP_FORWARD"
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-tunnel-forward.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null
echo "IRAN_DONE port={port} hub={hub} user={user}"
"""
    client = _connect(iran)
    try:
        return _run(client, script, timeout=1200)
    finally:
        client.close()


def provision_foreign(foreign, tunnel, iran_ip):
    """Set up the SoftEther hub + tap + cascade to the Iran node on the foreign node."""
    tid = tunnel["id"]
    port = 4300 + tid
    hub = f"FRHUB{tid}"
    iran_hub = f"IRHUB{tid}"
    user = f"s2s{tid}"
    userpass = tunnel["userpass"]
    tap_ip = tunnel["foreign_tap_ip"]  # e.g. 10.240.5.2
    script = f"""
set -e
{_install_softether_script()}
{_systemd_unit_script()}
systemctl enable --now softether-vpnserver >/dev/null 2>&1 || /usr/local/softether/vpnserver/vpnserver start
sleep 3
cd /usr/local/softether/vpnserver
printf 'HubCreate {hub} /PASSWORD:{userpass}\\nexit\\n' | ./vpncmd /SERVER localhost >/dev/null 2>&1 || true
ip tuntap add dev tap_soft{tid} mode tap 2>/dev/null || true
ip link set dev tap_soft{tid} up
ip addr add {tap_ip}/30 dev tap_soft{tid} 2>/dev/null || true
printf 'Hub {hub}\\nBridgeCreate {hub} /DEVICE:soft{tid} /TAP:yes\\nCascadeCreate toIR{tid} /SERVER:{iran_ip}:{port} /HUB:{iran_hub} /USERNAME:{user}\\nCascadePasswordSet toIR{tid} /PASSWORD:{userpass} /TYPE:standard\\nCascadeOnline toIR{tid}\\nexit\\n' | ./vpncmd /SERVER localhost >/dev/null 2>&1 || true
echo "IP_FORWARD"
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-tunnel-forward.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null
echo "FOREIGN_DONE port={port} hub={hub} cascade=toIR{tid}"
"""
    client = _connect(foreign)
    try:
        return _run(client, script, timeout=1200)
    finally:
        client.close()


def test_tunnel(foreign, tunnel) -> str:
    """Ping the Iran tap IP from the foreign node to verify the tunnel."""
    tid = tunnel["id"]
    iran_tap_ip = tunnel["iran_tap_ip"]
    foreign_tap_ip = tunnel["foreign_tap_ip"]
    script = f"""
sleep 6
ping -I {foreign_tap_ip} -c 4 -W 3 {iran_tap_ip} | tail -3
"""
    client = _connect(foreign)
    try:
        return _run(client, script, timeout=60)
    finally:
        client.close()


async def create_location_inbound(panel_url: str, token: str, wg_port: int, remark: str) -> dict:
    """Create a WireGuard inbound for a location, cloning the panel's WG schema.

    The existing WireGuard inbound is used as a template for settings/
    streamSettings/sniffing; its peers/clients are dropped so the new location
    starts empty.
    """
    xui = XUIClient(panel_url, api_token=token)
    template = await xui.find_wireguard_inbound()
    if not template:
        raise TunnelError("no WireGuard inbound on the panel to use as a template")

    import json as _json

    def _as_obj(v):
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except Exception:
                return {}
        return v or {}

    settings = _as_obj(template.get("settings"))
    for k in ("peers", "clients"):
        settings.pop(k, None)

    payload = {
        "remark": remark,
        "enable": True,
        "port": wg_port,
        "protocol": "wireguard",
        "listen": "",
        "total": 0,
        "up": 0,
        "down": 0,
        "expiryTime": 0,
        "settings": settings,
        "streamSettings": _as_obj(template.get("streamSettings")),
        "sniffing": _as_obj(template.get("sniffing")),
    }
    return await xui.add_inbound(payload)


def _ssh_run(node, script: str, timeout: int = 300) -> str:
    client = _connect(node)
    try:
        return _run(client, script, timeout=timeout)
    finally:
        client.close()


def check_existing(iran: dict, foreign: dict) -> str:
    """Best-effort health report of the SoftEther state on both nodes.

    Detects whether vpnserver runs and lists the foreign node's cascades with
    their destination + status (so an already-established tunnel to the same
    Iran node can be recognised instead of duplicated).
    """
    lines = []
    for name, node in (("ایران", iran), ("خارج", foreign)):
        try:
            r = _ssh_run(node, "pgrep -x vpnserver >/dev/null && echo RUNNING || echo NOT_RUNNING", timeout=30).strip()
            lines.append(f"SoftEther {name}: {r}")
        except Exception as e:
            lines.append(f"SSH {name}: {type(e).__name__} {str(e)[:80]}")

    script = r'''
cd /usr/local/softether/vpnserver 2>/dev/null || { echo NO_SOFTETHER; exit 0; }
PORT=""
for p in 443 4300 992 5555 1194; do
  if printf 'exit\n' | timeout 5 ./vpncmd /SERVER localhost:$p >/dev/null 2>&1; then PORT=$p; break; fi
done
[ -z "$PORT" ] && { echo NO_MGMT_PORT; exit 0; }
echo "MGMT_PORT=$PORT"
HUBS=$(printf 'HubList\nexit\n' | ./vpncmd /SERVER localhost:$PORT 2>/dev/null | awk -F'|' '/\|/{print $2}' | tr -d ' ' | grep -vE 'Name|^$|^-')
for h in $HUBS; do
  echo "HUB=$h"
  printf 'Hub %s\nCascadeStatusGet\nExit\n' "$h" | ./vpncmd /SERVER localhost:$PORT 2>/dev/null | grep -E 'Session Status|Destination VPN Server'
done
'''
    try:
        lines.append("— cascades خارج —")
        lines.append(_ssh_run(foreign, script, timeout=90).strip() or "(no output)")
    except Exception as e:
        lines.append(f"check cascades: {type(e).__name__} {str(e)[:120]}")
    return "\n".join(lines)


async def setup_location(node_panel_url: str, token: str, iran: dict, foreign: dict, tunnel: dict) -> dict:
    """Full location wiring for one tunnel:
    1. Iran node: Linux policy route (fwmark -> foreign tap) + guard.
    2. Foreign node: NAT/forward for the tap subnet.
    3. Iran node panel: create the WireGuard inbound.
    4. Iran node panel: add a freedom outbound (marked) + routing rule for the inbound.

    Returns {mark, port, tag, inbound_ok, note}.
    """
    import asyncio
    import json as _json

    tid = tunnel["id"]
    mark = 200 + tid
    port = 8600 + tid
    subnet = tunnel["subnet"]
    foreign_tap_ip = tunnel["foreign_tap_ip"]

    note = []

    # 1) Iran routing
    iran_script = f"""
set -e
ip route replace {subnet} dev tap_soft table {mark}
ip route replace default via {foreign_tap_ip} dev tap_soft table {mark}
ip route replace blackhole default metric 1000 table {mark}
ip rule del fwmark {mark} lookup {mark} 2>/dev/null || true
ip rule add fwmark {mark} lookup {mark} pref {mark}
echo ROUTING_OK
"""
    await asyncio.to_thread(_ssh_run, iran, iran_script)
    note.append("ایران routing ok")

    # 2) Foreign NAT/forward
    foreign_script = f"""
set -e
IFACE=$(ip route show default | awk '{{print $5}}' | head -1)
nft list table ip tunnel_exit >/dev/null 2>&1 || nft add table ip tunnel_exit
nft list chain ip tunnel_exit postrouting >/dev/null 2>&1 || nft 'add chain ip tunnel_exit postrouting {{ type nat hook postrouting priority 110 ; policy accept ; }}'
nft list chain ip tunnel_exit forward >/dev/null 2>&1 || nft 'add chain ip tunnel_exit forward {{ type filter hook forward priority -10 ; policy accept ; }}'
nft add rule ip tunnel_exit postrouting ip saddr {subnet} oifname "$IFACE" masquerade
echo NAT_OK
"""
    await asyncio.to_thread(_ssh_run, foreign, foreign_script)
    note.append("خارج NAT ok")

    # 3) Create the WireGuard inbound on the Iran node's panel (skip if it exists)
    xui = XUIClient(node_panel_url, api_token=token)

    def _as_obj(v):
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except Exception:
                return {}
        return v or {}

    tag = ""
    inbound_id = None
    for ib in await xui.get_inbounds():
        if (ib.get("protocol") or "").lower() == "wireguard" and ib.get("port") == port:
            tag = ib.get("tag") or ""
            inbound_id = ib.get("id")
            break

    if inbound_id is not None:
        note.append(f"اینباند WG از قبل موجود بود (id={inbound_id} tag={tag or '-'}) — ساخته نشد")
    else:
        tpl = await xui.find_wireguard_inbound()
        if not tpl:
            raise TunnelError("no WireGuard inbound template on the panel")
        settings = _as_obj(tpl.get("settings"))
        for k in ("peers", "clients"):
            settings.pop(k, None)
        res = await xui.add_inbound({
            "remark": f"loc-{tid}",
            "enable": True,
            "port": port,
            "protocol": "wireguard",
            "listen": "",
            "total": 0, "up": 0, "down": 0, "expiryTime": 0,
            "settings": settings,
            "streamSettings": _as_obj(tpl.get("streamSettings")),
            "sniffing": _as_obj(tpl.get("sniffing")),
        })
        inbound_ok = bool(res.get("success")) if isinstance(res, dict) else False
        for ib in await xui.get_inbounds():
            if (ib.get("protocol") or "").lower() == "wireguard" and ib.get("port") == port:
                tag = ib.get("tag") or ""
                inbound_id = ib.get("id")
                break
        note.append(f"اینباند WG {'ساخته شد' if inbound_ok else 'ناموفق'} (id={inbound_id} tag={tag or '-'})")

    # 4) Add marked outbound + routing rule
    if tag:
        obj = await xui.get_xray_template()
        xset = obj.get("xraySetting") or {}
        outbounds = [o for o in (xset.get("outbounds") or []) if o.get("tag") != f"loc-{tid}"]
        outbounds.append({
            "tag": f"loc-{tid}", "protocol": "freedom", "settings": {},
            "streamSettings": {"sockopt": {"mark": mark}},
        })
        xset["outbounds"] = outbounds
        routing = xset.setdefault("routing", {})
        rules = [r for r in (routing.get("rules") or [])
                 if not (r.get("inboundTag") and tag in r.get("inboundTag"))]
        rules.append({"type": "field", "enabled": True, "inboundTag": [tag], "outboundTag": f"loc-{tid}"})
        routing["rules"] = rules
        ok = await xui.update_xray_config(xset, obj.get("outboundTestUrl"))
        note.append(f"outbound/routing {'ok' if ok else 'fail'} (mark={mark})")

    return {"mark": mark, "port": port, "tag": tag, "inbound_id": inbound_id,
            "inbound_ok": inbound_ok, "note": "; ".join(note)}

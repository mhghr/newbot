"""Establish a SoftEther site-to-site tunnel between an Iran node and a foreign node.

Runs over SSH (paramiko). All vpncmd calls use the shared SoftEther admin
password and auto-detect the management port, so they work on servers that were
set up manually too.
"""
import logging
import time

logger = logging.getLogger(__name__)

try:
    import paramiko
except Exception:  # pragma: no cover
    paramiko = None

from bot.services.xui import XUIClient
from bot.config import SOFTETHER_ADMIN_PASSWORD as SE_PASS

# SoftEther release used when installing on a fresh node.
SOFTETHER_URL = (
    "https://www.softether-download.com/files/softether/"
    "v4.43-9799-beta-2023.08.31-tree/Linux/SoftEther_VPN_Server/"
    "64bit_-_Intel_x64_or_AMD64/"
    "softether-vpnserver-v4.43-9799-beta-2023.08.31-linux-x64-64bit.tar.gz"
)

# Bash snippet: locate the SoftEther management port (using the admin password).
_SE_MGMT = (
    'MGMT=""\n'
    'for p in 443 4300 992 5555 1194 4301 4302 4303 4304 4305; do\n'
    '  if printf "exit\\n" | timeout 5 ./vpncmd /SERVER localhost:$p /PASSWORD:' + SE_PASS + ' >/dev/null 2>&1; then MGMT=$p; break; fi\n'
    'done\n'
)

# Bash snippet: install SoftEther into /usr/local/softether if missing.
_SE_INSTALL = (
    'if [ ! -x /usr/local/softether/vpnserver/vpnserver ]; then\n'
    '  set -e\n'
    '  apt-get update -qq >/dev/null 2>&1 || true\n'
    '  for pkg in libreadline8 libssl3 zlib1g libsodium23; do apt-get install -y -qq $pkg >/dev/null 2>&1 || true; done\n'
    '  cd /tmp && curl -fsSL --retry 3 -o se.tar.gz "' + SOFTETHER_URL + '"\n'
    '  rm -rf /tmp/se_x && mkdir -p /tmp/se_x && tar xzf se.tar.gz -C /tmp/se_x\n'
    '  mkdir -p /usr/local/softether && cp -a /tmp/se_x/vpnserver /usr/local/softether/\n'
    '  chmod +x /usr/local/softether/vpnserver/vpnserver /usr/local/softether/vpnserver/vpncmd\n'
    '  set +e\n'
    'fi\n'
)

_SE_UNIT = (
    'cat > /etc/systemd/system/softether-vpnserver.service <<"UNIT"\n'
    '[Unit]\nDescription=SoftEther VPN Server\nAfter=network-online.target\nWants=network-online.target\n'
    '[Service]\nType=exec\nTasksMax=infinity\n'
    'ExecStart=/usr/local/softether/vpnserver/vpnserver execsvc\nRestart=on-failure\nRestartSec=3\nLimitNOFILE=1048576\n'
    '[Install]\nWantedBy=multi-user.target\nUNIT\n'
    'systemctl daemon-reload\n'
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
    client = _connect(node, timeout)
    try:
        _in, out, err = client.exec_command("id -un", timeout=timeout)
        return out.read().decode(errors="replace").strip()
    finally:
        client.close()


def _run(client, script: str, timeout: int = 900) -> str:
    sftp = client.open_sftp()
    remote = f"/tmp/tunnel_{int(time.time() * 1000)}.sh"
    with sftp.file(remote, "w") as f:
        f.write(script)
    sftp.close()
    _in, out, err = client.exec_command(
        f"chmod +x {remote}; bash {remote}; rc=$?; rm -f {remote}; exit $rc",
        timeout=timeout,
    )
    stdout = out.read().decode(errors="replace")
    stderr = err.read().decode(errors="replace")
    rc = out.channel.recv_exit_status()
    if rc != 0:
        raise TunnelError(f"remote script failed (rc={rc}): {stderr[-400:] or stdout[-400:]}")
    return stdout


def _ssh_run(node, script: str, timeout: int = 300) -> str:
    client = _connect(node)
    try:
        return _run(client, script, timeout=timeout)
    finally:
        client.close()


def check_existing(iran: dict, foreign: dict) -> str:
    """Best-effort report of SoftEther state + the foreign node's cascades."""
    lines = []
    for name, node in (("ایران", iran), ("خارج", foreign)):
        try:
            r = _ssh_run(node, "pgrep -x vpnserver >/dev/null && echo RUNNING || echo NOT_RUNNING", timeout=30).strip()
            lines.append(f"SoftEther {name}: {r}")
        except Exception as e:
            lines.append(f"SSH {name}: {str(e)[:80]}")

    script = (
        'cd /usr/local/softether/vpnserver 2>/dev/null || { echo NO_SOFTETHER; exit 0; }\n'
        + _SE_MGMT +
        '[ -z "$MGMT" ] && { echo NO_MGMT_PORT; exit 0; }\n'
        'echo "MGMT_PORT=$MGMT"\n'
        'HUBS=$(printf "HubList\\nexit\\n" | ./vpncmd /SERVER localhost:$MGMT /PASSWORD:' + SE_PASS + ' 2>/dev/null | cut -d"|" -f2 | tr -d " " | grep -vE "Name|^$")\n'
        'for h in $HUBS; do\n'
        '  echo "HUB=$h"\n'
        '  printf "Hub %s\\nCascadeStatusGet\\nExit\\n" "$h" | ./vpncmd /SERVER localhost:$MGMT /PASSWORD:' + SE_PASS + ' 2>/dev/null | grep -E "Session Status|Destination VPN Server"\n'
        'done\n'
    )
    try:
        lines.append("— cascades خارج —")
        lines.append(_ssh_run(foreign, script, timeout=120).strip() or "(no output)")
    except Exception as e:
        lines.append(f"check cascades: {str(e)[:120]}")
    return "\n".join(lines)


def provision_iran(iran, tunnel):
    tid = tunnel["id"]
    port = 4300 + tid
    hub = f"IRHUB{tid}"
    user = f"s2s{tid}"
    passw = tunnel["userpass"]
    tap_ip = tunnel["iran_tap_ip"]
    script = (
        'set -e\n'
        + _SE_INSTALL + _SE_UNIT +
        'systemctl enable --now softether-vpnserver >/dev/null 2>&1 || /usr/local/softether/vpnserver/vpnserver start\n'
        'sleep 3\n'
        'cd /usr/local/softether/vpnserver\n'
        # set a known admin password on a fresh install (fails harmlessly if already set)
        'printf "ServerPasswordSet\\n' + SE_PASS + '\\n' + SE_PASS + '\\nexit\\n" | ./vpncmd /SERVER localhost >/dev/null 2>&1 || true\n'
        + _SE_MGMT +
        '[ -z "$MGMT" ] && MGMT=443\n'
        'printf "HubCreate ' + hub + ' /PASSWORD:' + passw + '\\nListenerCreate ' + str(port) + '\\nexit\\n" | ./vpncmd /SERVER localhost:$MGMT /PASSWORD:' + SE_PASS + ' >/dev/null 2>&1 || true\n'
        'ip tuntap add dev tap_soft mode tap 2>/dev/null || true\n'
        'ip link set dev tap_soft up\n'
        'ip addr add ' + tap_ip + '/30 dev tap_soft 2>/dev/null || true\n'
        'printf "Hub ' + hub + '\\nBridgeCreate ' + hub + ' /DEVICE:soft /TAP:yes\\nUserCreate ' + user + ' /GROUP:none /REALNAME:' + user + ' /NOTE:cascade\\nUserPasswordSet ' + user + ' /PASSWORD:' + passw + '\\nexit\\n" | ./vpncmd /SERVER localhost:$MGMT /PASSWORD:' + SE_PASS + ' >/dev/null 2>&1 || true\n'
        'echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-tunnel-forward.conf; sysctl -w net.ipv4.ip_forward=1 >/dev/null\n'
        'printf "ListenerList\\nexit\\n" | ./vpncmd /SERVER localhost:$MGMT /PASSWORD:' + SE_PASS + ' 2>/dev/null | grep -E "TCP ' + str(port) + '" || echo "listener_missing"\n'
        'echo IRAN_DONE port=' + str(port) + ' hub=' + hub + '\n'
    )
    client = _connect(iran)
    try:
        return _run(client, script, timeout=1200)
    finally:
        client.close()


def provision_foreign(foreign, tunnel, iran_ip):
    tid = tunnel["id"]
    port = 4300 + tid
    hub = f"FRHUB{tid}"
    iran_hub = f"IRHUB{tid}"
    user = f"s2s{tid}"
    passw = tunnel["userpass"]
    tap_ip = tunnel["foreign_tap_ip"]
    script = (
        'set -e\n'
        + _SE_INSTALL + _SE_UNIT +
        'systemctl enable --now softether-vpnserver >/dev/null 2>&1 || /usr/local/softether/vpnserver/vpnserver start\n'
        'sleep 3\n'
        'cd /usr/local/softether/vpnserver\n'
        'printf "ServerPasswordSet\\n' + SE_PASS + '\\n' + SE_PASS + '\\nexit\\n" | ./vpncmd /SERVER localhost >/dev/null 2>&1 || true\n'
        + _SE_MGMT +
        '[ -z "$MGMT" ] && MGMT=443\n'
        'printf "HubCreate ' + hub + ' /PASSWORD:' + passw + '\\nexit\\n" | ./vpncmd /SERVER localhost:$MGMT /PASSWORD:' + SE_PASS + ' >/dev/null 2>&1 || true\n'
        'ip tuntap add dev tap_soft' + str(tid) + ' mode tap 2>/dev/null || true\n'
        'ip link set dev tap_soft' + str(tid) + ' up\n'
        'ip addr add ' + tap_ip + '/30 dev tap_soft' + str(tid) + ' 2>/dev/null || true\n'
        'printf "Hub ' + hub + '\\nBridgeCreate ' + hub + ' /DEVICE:soft' + str(tid) + ' /TAP:yes\\nCascadeCreate toIR' + str(tid) + ' /SERVER:' + iran_ip + ':' + str(port) + ' /HUB:' + iran_hub + ' /USERNAME:' + user + '\\nCascadePasswordSet toIR' + str(tid) + ' /PASSWORD:' + passw + ' /TYPE:standard\\nCascadeOnline toIR' + str(tid) + '\\nexit\\n" | ./vpncmd /SERVER localhost:$MGMT /PASSWORD:' + SE_PASS + ' >/dev/null 2>&1 || true\n'
        'echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-tunnel-forward.conf; sysctl -w net.ipv4.ip_forward=1 >/dev/null\n'
        'sleep 4\n'
        'printf "Hub ' + hub + '\\nCascadeStatusGet toIR' + str(tid) + '\\nexit\\n" | ./vpncmd /SERVER localhost:$MGMT /PASSWORD:' + SE_PASS + ' 2>/dev/null | grep -E "Session Status|Destination VPN Server"\n'
        'echo FOREIGN_DONE port=' + str(port) + ' hub=' + hub + '\n'
    )
    client = _connect(foreign)
    try:
        return _run(client, script, timeout=1200)
    finally:
        client.close()


def test_tunnel(foreign, tunnel) -> str:
    tid = tunnel["id"]
    script = f"""
sleep 6
ping -I {tunnel['foreign_tap_ip']} -c 4 -W 3 {tunnel['iran_tap_ip']} | tail -3
"""
    client = _connect(foreign)
    try:
        return _run(client, script, timeout=60)
    finally:
        client.close()


async def _find_node_panel(node_ip: str):
    """Find (url, token) of a panel reachable at the node's IP.

    Tries every configured panel's api_token against the node IP and returns the
    first that authenticates (panels are not necessarily identical).
    """
    from bot.database import db
    base_urls = []
    for s in await db.get_all_servers():
        if s.get("api_token"):
            base_urls.append(s["url"])
    from bot.services.panel_sync import node_panel_url
    for base in base_urls:
        url = node_panel_url(base, node_ip)
        for s in await db.get_all_servers():
            tok = s.get("api_token")
            if not tok:
                continue
            try:
                xui = XUIClient(url, api_token=tok)
                data = await xui._request("GET", "/panel/api/inbounds/list")
                if isinstance(data, dict) and data.get("success"):
                    return url, tok
            except Exception:
                continue
    return None, None


async def create_location_inbound(panel_url, token, wg_port, remark):
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
    return await xui.add_inbound({
        "remark": remark, "enable": True, "port": wg_port, "protocol": "wireguard",
        "listen": "", "total": 0, "up": 0, "down": 0, "expiryTime": 0,
        "settings": settings,
        "streamSettings": _as_obj(template.get("streamSettings")),
        "sniffing": _as_obj(template.get("sniffing")),
    })


async def setup_location(node_panel_url: str, token: str, iran: dict, foreign: dict, tunnel: dict) -> dict:
    """Wire the location: Iran routing + WG inbound + xray outbound/routing + foreign NAT."""
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
IFACE=$(ip route show default | awk '{{print $5}}' | head -1)
nft list table ip tunnel_exit >/dev/null 2>&1 || nft add table ip tunnel_exit
nft list chain ip tunnel_exit postrouting >/dev/null 2>&1 || nft 'add chain ip tunnel_exit postrouting {{ type nat hook postrouting priority 110 ; policy accept ; }}'
nft add rule ip tunnel_exit postrouting ip saddr {subnet} oifname "$IFACE" masquerade
echo NAT_OK
"""
    await asyncio.to_thread(_ssh_run, foreign, foreign_script)
    note.append("خارج NAT ok")

    # 3) WG inbound: reuse the node's WireGuard inbound (renamed to the location
    #    name) so a location never spawns a duplicate inbound. Only create a new
    #    inbound when the node has none.
    xui = XUIClient(node_panel_url, api_token=token)

    def _as_obj(v):
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except Exception:
                return {}
        return v or {}

    loc_name = (foreign.get("name") or "").strip() or f"loc-{tid}"

    tag = ""
    inbound_id = None
    wg_inbounds = [
        ib for ib in await xui.get_inbounds()
        if (ib.get("protocol") or "").lower() == "wireguard"
    ]
    target = None
    for ib in wg_inbounds:
        if (ib.get("remark") or "").strip() == loc_name:
            target = ib
            break
    if target is None and len(wg_inbounds) == 1:
        target = wg_inbounds[0]

    if target is not None:
        inbound_id = target.get("id")
        tag = target.get("tag") or ""
        port = target.get("port") or port
        if (target.get("remark") or "").strip() != loc_name:
            try:
                obj = await xui.get_inbound(inbound_id)
                obj["remark"] = loc_name
                await xui.update_inbound(inbound_id, obj)
                note.append(f"اینباند موجود استفاده و به نام «{loc_name}» تغییر یافت (id={inbound_id})")
            except Exception as e:
                note.append(f"از اینباند موجود (id={inbound_id}) استفاده شد؛ تغییر نام ناموفق: {str(e)[:100]}")
        else:
            note.append(f"اینباند لوکیشن از قبل بود (id={inbound_id} tag={tag or '-'})")
    else:
        tpl = await xui.find_wireguard_inbound()
        if not tpl:
            raise TunnelError("no WireGuard inbound template on the panel")
        settings = _as_obj(tpl.get("settings"))
        for k in ("peers", "clients"):
            settings.pop(k, None)
        res = await xui.add_inbound({
            "remark": loc_name, "enable": True, "port": port, "protocol": "wireguard",
            "listen": "", "total": 0, "up": 0, "down": 0, "expiryTime": 0,
            "settings": settings,
            "streamSettings": _as_obj(tpl.get("streamSettings")),
            "sniffing": _as_obj(tpl.get("sniffing")),
        })
        ok = bool(res.get("success")) if isinstance(res, dict) else False
        for ib in await xui.get_inbounds():
            if (ib.get("protocol") or "").lower() == "wireguard" and ib.get("port") == port:
                tag = ib.get("tag") or ""
                inbound_id = ib.get("id")
                break
        note.append(f"اینباند WG {'ساخته شد' if ok else 'ناموفق'} (id={inbound_id} tag={tag or '-'} name={loc_name})")

    # 4) outbound + routing (tagged after the location name; stale loc-{tid}
    #    entries from older runs are dropped). The inbound is only routed
    #    through the tunnel when the far side is actually reachable — a dead
    #    tunnel would otherwise black-hole all of the location's traffic.
    if tag:
        reachable = False
        try:
            out = await asyncio.to_thread(
                _ssh_run, iran,
                f"ping -c1 -W2 {foreign_tap_ip} >/dev/null 2>&1 && echo UP || echo DOWN",
            )
            reachable = "UP" in out
        except Exception as e:
            logger.warning("tunnel reachability check failed: %s", e)
        if not reachable:
            note.append("تانل در دسترس نیست؛ اینباند بدون روتینگ (مستقیم) رها شد")
        else:
            out_tag = loc_name
            obj = await xui.get_xray_template()
            xset = obj.get("xraySetting") or {}
            outbounds = [
                o for o in (xset.get("outbounds") or [])
                if o.get("tag") not in (out_tag, f"loc-{tid}")
            ]
            outbounds.append({"tag": out_tag, "protocol": "freedom", "settings": {},
                              "streamSettings": {"sockopt": {"mark": mark}}})
            xset["outbounds"] = outbounds
            routing = xset.setdefault("routing", {})
            rules = [
                r for r in (routing.get("rules") or [])
                if not (r.get("inboundTag") and tag in r.get("inboundTag"))
                and (r.get("outboundTag") or "") not in (out_tag, f"loc-{tid}")
            ]
            rules.append({"type": "field", "enabled": True, "inboundTag": [tag], "outboundTag": out_tag})
            routing["rules"] = rules
            ok = await xui.update_xray_config(xset, obj.get("outboundTestUrl"))
            note.append(f"outbound/routing {'ok' if ok else 'fail'} ({out_tag}, mark={mark})")

    return {"mark": mark, "port": port, "tag": tag, "inbound_id": inbound_id,
            "name": loc_name, "note": "; ".join(note)}

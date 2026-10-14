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

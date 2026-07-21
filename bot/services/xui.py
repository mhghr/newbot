import httpx
import uuid
import json
import logging
from urllib.parse import urlparse
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def panel_sub_base(panel_url: str, sub_port=2096, sub_domain: str = "") -> str:
    source = sub_domain.strip() if sub_domain and sub_domain.strip() else panel_url
    raw = source if "://" in source else "http://" + source
    parsed = urlparse(raw)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or source
    port = sub_port or 2096
    return f"{scheme}://{host}:{port}"


class XUIClient:
    def __init__(self, url: str, username: str = "", password: str = "", api_token: str = ""):
        self.base_url = url.rstrip("/")
        self.username = username
        self.password = password
        self.api_token = api_token
        self.session_cookie = None

    async def login(self) -> bool:
        if self.api_token:
            return True

        async with httpx.AsyncClient(verify=False) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/login",
                    headers={"accept": "application/json", "Content-Type": "application/json"},
                    json={
                        "username": self.username,
                        "password": self.password,
                        "twoFactorCode": "123456",
                    },
                    timeout=15
                )
                logger.info(f"Login: {resp.status_code} - {resp.text[:200]}")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        for name, value in resp.cookies.items():
                            self.session_cookie = value
                            break
                        if self.session_cookie:
                            return True
                return False
            except Exception as e:
                logger.error(f"Login error: {e}")
                return False

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {"accept": "application/json"}

        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        else:
            if not self.session_cookie:
                if not await self.login():
                    raise Exception("Failed to login to 3x-ui panel")

        async with httpx.AsyncClient(verify=False) as client:
            cookies = {}
            if not self.api_token and self.session_cookie:
                cookies = {"3x-ui": self.session_cookie}

            resp = await client.request(
                method,
                f"{self.base_url}{path}",
                cookies=cookies,
                headers=headers,
                timeout=15,
                **kwargs
            )
            logger.info(f"API {method} {path}: {resp.status_code}")

            if resp.status_code in [401, 403] and not self.api_token:
                await self.login()
                cookies = {"3x-ui": self.session_cookie} if self.session_cookie else {}
                resp = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    cookies=cookies,
                    headers=headers,
                    timeout=15,
                    **kwargs
                )
            return resp.json()

    async def get_inbounds(self) -> list:
        try:
            data = await self._request("GET", "/panel/api/inbounds/list")
            return data.get("obj", [])
        except Exception:
            return []

    @staticmethod
    def _coerce_tg_id(tg_id) -> int:
        try:
            return int(tg_id)
        except (TypeError, ValueError):
            return 0

    async def add_client(self, inbound_id: int, email: str, traffic_gb: int, expire_days: int) -> str:
        client_uuid = str(uuid.uuid4())
        await self.add_client_full(
            inbound_id=inbound_id,
            client_uuid=client_uuid,
            email=email,
            sub_id=email,
            traffic_gb=traffic_gb,
            expire_days=expire_days,
        )
        return client_uuid

    async def add_client_full(self, email: str, client_uuid: str = "", sub_id: str = "",
                              traffic_gb: int = 0, expire_days: int = 0,
                              flow: str = "", tg_id=0, comment: str = "",
                              all_inbound_ids: list = None, inbound_id: int = 0,
                              limit_ip: int = 0) -> bool:
        if expire_days and expire_days > 0:
            expire_ms = int((datetime.now() + timedelta(days=expire_days)).timestamp() * 1000)
        else:
            expire_ms = 0
        traffic_bytes = traffic_gb * 1024 * 1024 * 1024 if traffic_gb and traffic_gb > 0 else 0

        inbound_ids = all_inbound_ids if all_inbound_ids else [inbound_id]

        client = {
            "email": email,
            "limitIp": limit_ip if limit_ip and limit_ip > 0 else 0,
            "totalGB": traffic_bytes,
            "expiryTime": expire_ms,
            "enable": True,
        }
        if client_uuid:
            client["id"] = client_uuid
        if sub_id:
            client["subId"] = sub_id
        if flow:
            client["flow"] = flow
        if comment:
            client["comment"] = comment
        if tg_id:
            client["tgId"] = self._coerce_tg_id(tg_id)

        data = await self._request(
            "POST",
            "/panel/api/clients/add",
            json={
                "client": client,
                "inboundIds": inbound_ids,
            }
        )
        if not data.get("success"):
            raise Exception(f"Failed to add client: {data}")
        return True

    async def get_client_sub_id(self, email: str) -> str:
        data = await self._request("GET", "/panel/api/inbounds/list")
        for inbound in data.get("obj", []):
            for cs in (inbound.get("clientStats") or []):
                if cs.get("email") == email and cs.get("subId"):
                    return cs["subId"]
            settings = inbound.get("settings")
            if isinstance(settings, str):
                try:
                    settings = json.loads(settings)
                except Exception:
                    settings = {}
            if isinstance(settings, dict):
                for c in settings.get("clients", []):
                    if c.get("email") == email and c.get("subId"):
                        return c["subId"]
        return ""

    async def delete_client(self, email: str) -> bool:
        data = await self._request("POST", f"/panel/api/clients/del/{email}")
        return data.get("success", False)

    async def get_client_traffic(self, email: str) -> dict:
        data = await self._request("GET", f"/panel/api/clients/traffic/{email}")
        obj = data.get("obj", {})
        if obj:
            up = obj.get("up", 0)
            down = obj.get("down", 0)
            total = obj.get("total", 0)
            return {
                "up": up,
                "down": down,
                "total": total,
                "used": up + down,
                "remaining": max(0, total - (up + down)) if total > 0 else 0,
            }
        return {"up": 0, "down": 0, "total": 0, "used": 0, "remaining": 0}

    async def reset_traffic(self, email: str) -> bool:
        data = await self._request("POST", f"/panel/api/clients/resetTraffic/{email}")
        return data.get("success", False)

    async def update_traffic(self, email: str) -> bool:
        data = await self._request("POST", f"/panel/api/clients/updateTraffic/{email}")
        return data.get("success", False)

    async def get_client_links(self, email: str) -> list:
        data = await self._request("GET", f"/panel/api/clients/links/{email}")
        return data.get("obj", [])

    async def get_sub_links(self, sub_id: str) -> list:
        data = await self._request("GET", f"/panel/api/clients/subLinks/{sub_id}")
        return data.get("obj", [])

    async def get_sub_link(self, email: str) -> str:
        try:
            links = await self.get_sub_links(email)
            if links and isinstance(links, list) and len(links) > 0:
                return links[0]
            links = await self.get_client_links(email)
            if links and isinstance(links, list) and len(links) > 0:
                return links[0]
        except Exception:
            pass
        return f"{self.base_url}/sub/{email}"

    async def get_client_inbounds(self, email: str) -> list:
        # The modern clients endpoint is the authoritative source for a
        # client's attachments. clientStats can be incomplete/stale (for
        # example before the client has generated any traffic).
        try:
            data = await self._request("GET", f"/panel/api/clients/get/{email}")
            obj = data.get("obj") or {}
            if data.get("success") and isinstance(obj, dict) and "inboundIds" in obj:
                return [int(i) for i in (obj.get("inboundIds") or [])]
        except Exception as e:
            logger.warning("Modern client lookup failed for %s; using inbound list: %s", email, e)

        # Compatibility fallback for older panel versions.
        data = await self._request("GET", "/panel/api/inbounds/list")
        inbound_ids = []
        for inbound in data.get("obj", []):
            for cs in (inbound.get("clientStats") or []):
                if cs.get("email") == email:
                    inbound_ids.append(inbound["id"])
                    break
        return inbound_ids

    async def attach_client(self, email: str, inbound_ids: list) -> bool:
        data = await self._request(
            "POST",
            f"/panel/api/clients/{email}/attach",
            json={"inboundIds": inbound_ids}
        )
        return data.get("success", False)

    async def detach_client(self, email: str, inbound_ids: list) -> bool:
        data = await self._request(
            "POST",
            f"/panel/api/clients/{email}/detach",
            json={"inboundIds": inbound_ids}
        )
        return data.get("success", False)


def format_bytes(b: int) -> str:
    if b <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(b)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

"""ArvanCloud DNS helper — points the entry domain's A record at the active node."""
import logging

import httpx

from bot.config import ARVAN_API_KEY, ARVAN_API_BASE, ARVAN_DOMAIN, ARVAN_RECORD

logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {
        "Authorization": f"Apikey {ARVAN_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _base() -> str:
    return ARVAN_API_BASE.rstrip("/")


def configured() -> bool:
    return bool(ARVAN_API_KEY and ARVAN_DOMAIN and ARVAN_RECORD)


async def _list_records() -> list:
    url = f"{_base()}/domains/{ARVAN_DOMAIN}/dns-records"
    async with httpx.AsyncClient(verify=False, timeout=20) as client:
        r = await client.get(url, headers=_headers())
        r.raise_for_status()
        data = r.json()
    return data.get("data", data) if isinstance(data, dict) else data


async def set_a_record(ip: str) -> bool:
    """Set the configured A record (ARVAN_RECORD in ARVAN_DOMAIN) to ``ip``.

    Creates the record if it does not exist. Returns True on success.
    """
    if not configured():
        raise RuntimeError("Arvan is not configured (need ARVAN_API_KEY/DOMAIN/RECORD)")

    name = (ARVAN_RECORD or "").strip()
    if name in ("@", ARVAN_DOMAIN):
        name = "@"
    payload = {
        "type": "a",
        "name": name,
        "value": [{"ip": ip}],
        "ttl": 120,
        "cloud": False,
    }

    records = await _list_records()
    existing = None
    for rec in records:
        if (rec.get("type") or "").lower() == "a" and (rec.get("name") or "") == name:
            existing = rec
            break

    async with httpx.AsyncClient(verify=False, timeout=20) as client:
        if existing:
            url = f"{_base()}/domains/{ARVAN_DOMAIN}/dns-records/{existing.get('id')}"
            r = await client.put(url, headers=_headers(), json=payload)
        else:
            url = f"{_base()}/domains/{ARVAN_DOMAIN}/dns-records"
            r = await client.post(url, headers=_headers(), json=payload)
        ok = r.status_code in (200, 201, 202)
        if not ok:
            logger.warning("Arvan set_a_record failed: %s %s", r.status_code, r.text[:200])
        return ok

import base64
import logging
from aiohttp import web

from bot.database import db
from bot.services.xui import XUIClient

logger = logging.getLogger(__name__)


async def handle_sub(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "")
    user = await db.get_user_by_sub_token(token)
    if not user:
        return web.Response(status=404, text="Not found")

    master = await db.get_master_server()
    if not master:
        return web.Response(status=500, text="No master server")

    if not user.get("sub_token"):
        return web.Response(status=404, text="No subscription")

    try:
        xui = XUIClient(master["url"], api_token=master["api_token"])
        links = await xui.get_sub_links(user["sub_token"])
    except Exception as e:
        logger.error(f"subLinks proxy failed for {token}: {e}")
        return web.Response(status=502, text="Upstream error")

    if not links:
        return web.Response(status=404, text="No configs")

    body = "\n".join(links)
    encoded = base64.b64encode(body.encode("utf-8")).decode("utf-8")

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Profile-Title": f"VPN-{token[:6]}",
        "Profile-Update-Interval": "12",
        "Cache-Control": "no-store",
    }
    return web.Response(text=encoded, headers=headers)


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/sub/{token}", handle_sub)
    return app


async def start_sub_server(host: str, port: int) -> web.AppRunner:
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"Subscription server started on {host}:{port}")
    return runner

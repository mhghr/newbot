import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("vpn_bot")


def admin_order_caption(order, service_type: str = None, kind: str = "new") -> str:
    """Caption shown to admins for a new order or a renewal.

    Includes the customer's name, username and numeric id, the selected service
    type (V2Ray / WireGuard) and the chosen plan. The caption intentionally
    avoids mentioning inbounds or "all active servers".
    """
    from bot.keyboards.inline import service_label

    stype = (
        service_type
        or order.get("service_type")
        or order.get("plan_service_type")
        or "v2ray"
    )
    title = "🔄 تمدید" if kind == "renew" else "🆕 سفارش جدید"
    first = order.get("first_name") or ""
    last = order.get("last_name") or ""
    name = " ".join(p for p in (first, last) if p) or "-"
    username = order.get("username")
    return (
        f"\u200f{title} #{order['id']}\n\n"
        f"\u200f👤 نام: {name}\n"
        f"\u200f🔗 یوزرنیم: @{username or 'ندارد'}\n"
        f"\u200f🆔 آیدی: {order['telegram_id']}\n"
        f"\u200f🧩 نوع سرویس: {service_label(stype)}\n"
        f"\u200f📦 پلن: {order['plan_name']}\n"
        f"\u200f💰 مبلغ: {order['price']:,} تومان"
    )

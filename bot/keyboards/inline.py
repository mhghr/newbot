from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.config import CHANNEL_URL, ADMIN_IDS
from urllib.parse import urlparse


def _rows(buttons: list, per_row: int = 2) -> list:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def _app_label(url: str) -> str:
    host = urlparse(url).hostname or url
    return host[:40]


def _trunc(value, n: int = 30) -> str:
    s = str(value) if value not in (None, "") else "-"
    return s if len(s) <= n else s[:n - 1] + "…"


def landing_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🛒 پروکسی", callback_data="main:configs")],
        [InlineKeyboardButton(text="🎬 دانلود ویدیو", callback_data="main:download")],
    ]
    if user_id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton(text="⚙️ مدیریت", callback_data="main:admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🛒 خرید کانفیگ", callback_data="main:buy"),
        InlineKeyboardButton(text="📋 کانفیگ های من", callback_data="main:my_configs"),
        InlineKeyboardButton(text="📖 آموزش اتصال", callback_data="main:tutorial"),
        InlineKeyboardButton(text="🧩 نرم‌افزارها", callback_data="main:apps"),
        InlineKeyboardButton(text="💵 عودت وجه", callback_data="main:refund"),
        InlineKeyboardButton(text="🆘 پشتیبانی", callback_data="main:support"),
            ]
    if user_id in ADMIN_IDS:
        buttons.append(InlineKeyboardButton(text="⚙️ مدیریت", callback_data="main:admin"))
    return InlineKeyboardMarkup(inline_keyboard=_rows(buttons))


def download_platforms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ یوتیوب", callback_data="dl:youtube")],
        [InlineKeyboardButton(text="📸 اینستاگرام", callback_data="dl:instagram")],
        [InlineKeyboardButton(text="🎵 تیک‌تاک", callback_data="dl:tiktok")],
        [InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="main:configs")],
    ])


def refund_configs_keyboard(configs: list) -> InlineKeyboardMarkup:
    buttons = []
    for c in configs:
        label = c.get("client_email") or c.get("plan_name") or "کانفیگ"
        buttons.append(InlineKeyboardButton(text=f"🔑 {label}", callback_data=f"refund_cfg:{c['id']}"))
    rows = _rows(buttons)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="main:configs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def refund_approval_keyboard(refund_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تایید عودت", callback_data=f"refund_ok:{refund_id}"),
            InlineKeyboardButton(text="❌ عدم تایید", callback_data=f"refund_no:{refund_id}"),
        ]
    ])


def refund_upload_keyboard(refund_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📎 آپلود فیش واریز", callback_data=f"refund_upload:{refund_id}")]
    ])


def proxy_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ افزودن کانال منبع", callback_data="admin:proxy_add")],
        [InlineKeyboardButton(text="📋 لیست کانال‌های منبع", callback_data="admin:proxy_list")],
        [InlineKeyboardButton(text="🎯 تنظیم کانال مقصد", callback_data="admin:proxy_target")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
    ])


def proxy_sources_keyboard(sources: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📡 {s['channel']}", callback_data=f"admin:proxy_askdel:{s['id']}")]
        for s in sources
    ]
    rows.append([InlineKeyboardButton(text="➕ افزودن کانال", callback_data="admin:proxy_add")])
    rows.append([InlineKeyboardButton(text="🧪 تست ارسال", callback_data="admin:proxy_test")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def proxy_delete_confirm_keyboard(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ بله، حذف شود", callback_data=f"admin:proxy_del:{source_id}"),
            InlineKeyboardButton(text="❌ انصراف", callback_data="admin:proxy"),
        ],
    ])


APP_PLATFORMS = (("android", "📱 اندروید"), ("ios", "🍎 آیفون"), ("windows", "💻 ویندوز"))


def user_apps_platforms_keyboard() -> InlineKeyboardMarkup:
    rows = _rows([
        InlineKeyboardButton(text=label, callback_data=f"apps:{code}")
        for code, label in APP_PLATFORMS
    ])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="main:configs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_apps_list_keyboard(platform: str, apps: list) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"⬇️ {a['title'] or _app_label(a['url'])}", url=a["url"])] for a in apps]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="main:apps")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_apps_platforms_keyboard() -> InlineKeyboardMarkup:
    rows = _rows([
        InlineKeyboardButton(text=label, callback_data=f"admin:applist:{code}")
        for code, label in APP_PLATFORMS
    ])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_apps_list_keyboard(platform: str, apps: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{a['title'] or _app_label(a['url'])}", callback_data=f"admin:app:{a['id']}")]
        for a in apps
    ]
    rows.append([InlineKeyboardButton(text="➕ افزودن نرم‌افزار", callback_data=f"admin:app_add:{platform}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:apps")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_app_detail_keyboard(app, platform: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📛 نام : {_trunc(app['title'] or '-')}", callback_data=f"admin:app_edit:{app['id']}:title")],
        [InlineKeyboardButton(text=f"🔗 لینک : {_trunc(app['url'])}", callback_data=f"admin:app_edit:{app['id']}:url")],
        [InlineKeyboardButton(text="🗑 حذف", callback_data=f"admin:app_del:{platform}:{app['id']}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"admin:applist:{platform}")],
    ])


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="main:configs")],
    ])


def join_channel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 عضویت در کانال", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ عضو شدم، بررسی کن", callback_data="check_membership")],
    ])


def plans_keyboard(plans: list) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{plan['name']} | {plan['price']:,} تومان",
            callback_data=f"buy_plan:{plan['id']}"
        )
        for plan in plans
    ]
    rows = _rows(buttons)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="cancel_buy")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_approval_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تایید", callback_data=f"approve_order:{order_id}"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"reject_order:{order_id}"),
        ]
    ])


def configs_keyboard(configs: list) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"📍 {config['location']} | {config['server_name']}",
            callback_data=f"view_config:{config['id']}"
        )
        for config in configs
    ]
    return InlineKeyboardMarkup(inline_keyboard=_rows(buttons))


def my_configs_keyboard(configs: list) -> InlineKeyboardMarkup:
    buttons = []
    for c in configs:
        label = c.get("client_email") or c.get("plan_name") or "کانفیگ"
        buttons.append(InlineKeyboardButton(text=f"🔑 {label}", callback_data=f"cfg:{c['id']}"))
    rows = _rows(buttons)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="main:configs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def config_detail_keyboard(config_id: int, show_renew: bool) -> InlineKeyboardMarkup:
    rows = []
    if show_renew:
        rows.append([InlineKeyboardButton(text="🔄 تمدید", callback_data=f"renew:{config_id}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="main:my_configs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def renew_choice_keyboard(config_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="♻️ پلن فعلی", callback_data=f"renew_same:{config_id}"),
            InlineKeyboardButton(text="🔀 تغییر پلن", callback_data=f"renew_change:{config_id}"),
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"cfg:{config_id}")],
    ])


def renew_plans_keyboard(config_id: int, plans: list) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{plan['name']} | {plan['price']:,} تومان",
            callback_data=f"renew_plan:{config_id}:{plan['id']}"
        )
        for plan in plans
    ]
    rows = _rows(buttons)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"renew:{config_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_action")],
    ])


def tutorial_platforms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_rows([
        InlineKeyboardButton(text="📱 اندروید", callback_data="tutorial:android"),
        InlineKeyboardButton(text="🍎 iOS", callback_data="tutorial:ios"),
        InlineKeyboardButton(text="💻 ویندوز", callback_data="tutorial:windows"),
    ]))


def tutorial_apps_keyboard(platform: str, apps: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📱 {a['name']}", callback_data=f"tut_app:{platform}:{a['slug']}")]
        for a in apps
    ]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="main:tutorial")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_tutorial_apps_keyboard(platform: str, apps: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📱 {a['name']}", callback_data=f"admin:tut_app:{platform}:{a['slug']}")]
        for a in apps
    ]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:tutorials")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_tutorial_app_keyboard(platform: str, slug: str, has_video: bool) -> InlineKeyboardMarkup:
    video_label = "🎬 تغییر ویدیو" if has_video else "🎬 افزودن ویدیو"
    rows = [
        [InlineKeyboardButton(text="✏️ ویرایش متن", callback_data=f"admin:tut_text:{platform}:{slug}")],
        [InlineKeyboardButton(text=video_label, callback_data=f"admin:tut_video:{platform}:{slug}")],
    ]
    if has_video:
        rows.append([InlineKeyboardButton(text="🗑 حذف ویدیو", callback_data=f"admin:tut_video_del:{platform}:{slug}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"admin:edit_tutorial:{platform}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_rows([
        InlineKeyboardButton(text="🖥 مدیریت سرورها", callback_data="admin:servers"),
        InlineKeyboardButton(text="📦 مدیریت پلن‌ها", callback_data="admin:plans"),
        InlineKeyboardButton(text="🛠 ساخت اکانت", callback_data="admin:create_account"),
        InlineKeyboardButton(text="💳 شماره کارت", callback_data="admin:card"),
        InlineKeyboardButton(text="🧩 نرم‌افزارها", callback_data="admin:apps"),
        InlineKeyboardButton(text="🔄 پروکسی تلگرام", callback_data="admin:proxy"),
        InlineKeyboardButton(text="🔍 جستجوی کاربر", callback_data="admin:search_user"),
        InlineKeyboardButton(text="📖 مدیریت آموزش", callback_data="admin:tutorials"),
        InlineKeyboardButton(text="⚙️ تنظیمات", callback_data="admin:settings"),
    ]))


def create_account_plans_keyboard(plans: list) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{plan['name']} | {plan['traffic_gb']}GB | {plan['duration_days']} روز",
            callback_data=f"acc_plan:{plan['id']}"
        )
        for plan in plans
    ]
    buttons.append(InlineKeyboardButton(text="🎛 پلن دلخواه", callback_data="acc_plan:custom"))
    rows = _rows(buttons)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_servers_keyboard() -> InlineKeyboardMarkup:
    rows = _rows([
        InlineKeyboardButton(text="➕ افزودن سرور", callback_data="admin:add_server"),
        InlineKeyboardButton(text="📋 لیست سرورها", callback_data="admin:list_servers"),
    ])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_plans_keyboard() -> InlineKeyboardMarkup:
    rows = _rows([
        InlineKeyboardButton(text="➕ افزودن پلن", callback_data="admin:add_plan"),
        InlineKeyboardButton(text="📋 لیست پلن‌ها", callback_data="admin:list_plans"),
    ])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def server_actions_keyboard(server) -> InlineKeyboardMarkup:
    sid = server["id"]
    toggle_text = "🔴 غیرفعال کردن" if server["is_active"] else "🟢 فعال کردن"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📛 نام سرور : {_trunc(server['name'])}", callback_data=f"admin:edit_server:{sid}:name")],
        [InlineKeyboardButton(text=f"🔗 آدرس : {_trunc(server['url'])}", callback_data=f"admin:edit_server:{sid}:url")],
        [InlineKeyboardButton(text=f"🔑 کلید اتصال : {_trunc(server['api_token'])}", callback_data=f"admin:edit_server:{sid}:api_token")],
        [InlineKeyboardButton(text=f"📍 لوکیشن : {_trunc(server['location'])}", callback_data=f"admin:edit_server:{sid}:location")],
        [InlineKeyboardButton(text=f"🌐 آدرس ساب : {_trunc(server['sub_domain'] or '-')}", callback_data=f"admin:edit_server:{sid}:sub_domain")],
        [InlineKeyboardButton(text=f"🔢 پورت ساب : {server['sub_port'] or 2096}", callback_data=f"admin:edit_server:{sid}:sub_port")],
        [InlineKeyboardButton(text=f"📡 اینباندها : {_trunc(server['inbound_ids'] or '-')}", callback_data=f"admin:inbounds:{sid}")],
        [InlineKeyboardButton(text="🔄 سینک اینباندها", callback_data=f"admin:sync_inbounds:{sid}")],
        [
            InlineKeyboardButton(text=toggle_text, callback_data=f"admin:toggle_server:{sid}"),
            InlineKeyboardButton(text="🗑 حذف", callback_data=f"admin:delete_server:{sid}"),
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:servers")],
    ])


def plans_list_keyboard(plans: list) -> InlineKeyboardMarkup:
    rows = []
    for p in plans:
        status = "🟢" if p["is_active"] else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{status} {p['name']} | {p['price']:,} تومان",
            callback_data=f"admin:plan_detail:{p['id']}"
        )])
    rows.append([InlineKeyboardButton(text="➕ افزودن پلن", callback_data="admin:add_plan")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def servers_list_keyboard(servers: list) -> InlineKeyboardMarkup:
    rows = []
    for s in servers:
        status = "🟢" if s["is_active"] else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{status} {s['name']} - {s['location']}",
            callback_data=f"admin:server_detail:{s['id']}"
        )])
    rows.append([InlineKeyboardButton(text="➕ افزودن سرور", callback_data="admin:add_server")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:servers")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def server_delete_confirm_keyboard(server_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ بله، حذف شود", callback_data=f"admin:delete_server_confirm:{server_id}"),
            InlineKeyboardButton(text="❌ انصراف", callback_data=f"admin:server_detail:{server_id}"),
        ],
    ])


def server_inbounds_keyboard(server_id: int, inbound_ids: list) -> InlineKeyboardMarkup:
    del_buttons = [
        InlineKeyboardButton(text=f"🗑 {iid}", callback_data=f"admin:inbound_del:{server_id}:{iid}")
        for iid in inbound_ids
    ]
    rows = _rows(del_buttons)
    rows.append([
        InlineKeyboardButton(text="➕ افزودن اینباند", callback_data=f"admin:inbound_add:{server_id}"),
        InlineKeyboardButton(text="✏️ ویرایش لیست", callback_data=f"admin:set_inbounds:{server_id}"),
    ])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"admin:server_detail:{server_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_actions_keyboard(plan) -> InlineKeyboardMarkup:
    pid = plan["id"]
    toggle_text = "🔴 غیرفعال کردن" if plan["is_active"] else "🟢 فعال کردن"
    traffic = "نامحدود" if (plan["traffic_gb"] or 0) == 0 else f"{plan['traffic_gb']} GB"
    duration = "نامحدود" if (plan["duration_days"] or 0) == 0 else f"{plan['duration_days']} روز"
    users = "نامحدود" if (plan["max_users"] or 0) == 0 else f"{plan['max_users']} کاربر"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📛 نام : {_trunc(plan['name'])}", callback_data=f"admin:edit_plan_field:{pid}:name")],
        [InlineKeyboardButton(text=f"📊 حجم : {traffic}", callback_data=f"admin:edit_plan_field:{pid}:traffic_gb")],
        [InlineKeyboardButton(text=f"📅 مدت : {duration}", callback_data=f"admin:edit_plan_field:{pid}:duration_days")],
        [InlineKeyboardButton(text=f"👥 تعداد کاربر : {users}", callback_data=f"admin:edit_plan_field:{pid}:max_users")],
        [InlineKeyboardButton(text=f"💰 قیمت : {plan['price']:,} تومان", callback_data=f"admin:edit_plan_field:{pid}:price")],
        [
            InlineKeyboardButton(text=toggle_text, callback_data=f"admin:toggle_plan:{pid}"),
            InlineKeyboardButton(text="🗑 حذف", callback_data=f"admin:delete_plan:{pid}"),
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:plans")],
    ])


def plan_edit_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📛 نام", callback_data=f"admin:edit_plan_field:{plan_id}:name"),
            InlineKeyboardButton(text="📊 حجم", callback_data=f"admin:edit_plan_field:{plan_id}:traffic_gb"),
        ],
        [
            InlineKeyboardButton(text="📅 مدت", callback_data=f"admin:edit_plan_field:{plan_id}:duration_days"),
            InlineKeyboardButton(text="👥 تعداد کاربر", callback_data=f"admin:edit_plan_field:{plan_id}:max_users"),
        ],
        [
            InlineKeyboardButton(text="💰 قیمت", callback_data=f"admin:edit_plan_field:{plan_id}:price"),
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"admin:plan_detail:{plan_id}")],
    ])


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    rows = _rows([
        InlineKeyboardButton(text="💳 شماره کارت", callback_data="admin:set_card_number"),
        InlineKeyboardButton(text="👤 نام صاحب کارت", callback_data="admin:set_card_holder"),
    ])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_tutorial_keyboard() -> InlineKeyboardMarkup:
    rows = _rows([
        InlineKeyboardButton(text="📱 اندروید", callback_data="admin:edit_tutorial:android"),
        InlineKeyboardButton(text="🍎 iOS", callback_data="admin:edit_tutorial:ios"),
        InlineKeyboardButton(text="💻 ویندوز", callback_data="admin:edit_tutorial:windows"),
    ])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_detail_keyboard(user, configs: list) -> InlineKeyboardMarkup:
    first = user.get("first_name") or "-"
    last = user.get("last_name") or ""
    name = first
    if last:
        name = f"{first} {last}"
    rows = [
        [InlineKeyboardButton(text=f"🆔 آیدی: {user['telegram_id']}", callback_data="admin:noop")],
        [InlineKeyboardButton(text=f"📛 نام: {_trunc(name)}", callback_data="admin:noop")],
        [InlineKeyboardButton(text=f"👤 یوزرنیم: @{user.get('username') or 'ندارد'}", callback_data="admin:noop")],
        [InlineKeyboardButton(text=f"📅 تاریخ عضویت: {_trunc(str(user.get('created_at') or '-'))}", callback_data="admin:noop")],
    ]
    for c in configs:
        label = c.get("client_email") or c.get("plan_name") or "کانفیگ"
        rows.append([InlineKeyboardButton(text=f"🔑 {label}", callback_data=f"admin:config_detail:{c['id']}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:search_user")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_config_detail_keyboard(config) -> InlineKeyboardMarkup:
    cid = config["id"]
    traffic_str = "نامحدود" if (config.get("traffic_gb") or 0) == 0 else f"{config.get('traffic_gb')} GB"
    expire_str = str(config.get("expire_date") or "نامحدود")
    rows = [
        [InlineKeyboardButton(text=f"📦 پلن: {_trunc(config.get('plan_name') or '-')}", callback_data="admin:noop")],
        [InlineKeyboardButton(text=f"📊 حجم: {traffic_str}", callback_data="admin:noop")],
        [InlineKeyboardButton(text=f"📅 تاریخ انقضا: {_trunc(expire_str)}", callback_data="admin:noop")],
        [InlineKeyboardButton(text=f"👤 کلاینت: {_trunc(config.get('client_email') or '-')}", callback_data="admin:noop")],
        [InlineKeyboardButton(text=f"🔗 لینک: {_trunc(config.get('sub_url') or '-')}", callback_data="admin:noop")],
        [InlineKeyboardButton(text=f"🌐 ساب‌دامین: {_trunc(config.get('config_link') or config.get('sub_url') or '-')}", callback_data="admin:noop")],
        [InlineKeyboardButton(text="🗑 حذف کانفیگ", callback_data=f"admin:delete_config:{cid}")],
        [InlineKeyboardButton(text="🔙 بازگشت به کاربر", callback_data=f"admin:user_detail:{config.get('user_id')}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_config_delete_confirm_keyboard(config_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ بله، حذف شود", callback_data=f"admin:delete_config_confirm:{config_id}"),
            InlineKeyboardButton(text="❌ انصراف", callback_data=f"admin:config_detail:{config_id}"),
        ],
        [InlineKeyboardButton(text="🔙 بازگشت به کاربر", callback_data=f"admin:user_detail:{user_id}")],
    ])

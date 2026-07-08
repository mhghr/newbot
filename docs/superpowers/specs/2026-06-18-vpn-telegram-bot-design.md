# VPN Sales Telegram Bot - Design Specification

## Overview

A Python Telegram bot for selling VPN services. The bot integrates with 3x-ui panel API to create and manage VPN configurations. Users purchase configs via card-to-card payment with admin approval.

## Tech Stack

- **Language:** Python 3.11+
- **Bot Framework:** aiogram 3
- **Database:** SQLite + aiosqlite
- **HTTP Client:** httpx (for 3x-ui API)
- **Config:** python-dotenv

## Project Structure

```
D:\newbot/
├── bot/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   └── db.py
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── start.py
│   │   ├── buy.py
│   │   ├── my_configs.py
│   │   ├── tutorial.py
│   │   └── admin/
│   │       ├── __init__.py
│   │       ├── menu.py
│   │       ├── servers.py
│   │       ├── plans.py
│   │       ├── users.py
│   │       └── payments.py
│   ├── keyboards/
│   │   ├── __init__.py
│   │   ├── reply.py
│   │   └── inline.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── xui.py
│   ├── middlewares/
│   │   ├── __init__.py
│   │   └── membership.py
│   └── utils/
│       ├── __init__.py
│       └── helpers.py
├── .env
├── requirements.txt
└── run.py
```

## Environment Variables (.env)

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `ADMIN_IDS` | Comma-separated list of admin Telegram IDs |
| `CHANNEL_ID` | Channel/group ID for membership check |
| `CHANNEL_URL` | Channel invite link |

## Database Schema

### users
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| telegram_id | INTEGER UNIQUE | Telegram user ID |
| username | TEXT | Telegram username |
| first_name | TEXT | First name |
| last_name | TEXT | Last name |
| created_at | TIMESTAMP | Registration date |

### servers
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| name | TEXT | Display name |
| url | TEXT | 3x-ui panel URL |
| username | TEXT | Panel login username |
| password | TEXT | Panel login password |
| location | TEXT | Server location (e.g., Germany, Netherlands) |
| inbound_id | INTEGER | Default inbound ID on this panel |
| is_active | BOOLEAN | Active status |
| created_at | TIMESTAMP | Added date |

### plans
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| name | TEXT | Plan display name |
| traffic_gb | INTEGER | Traffic limit in GB |
| duration_days | INTEGER | Duration in days |
| price | INTEGER | Price in Toman |
| is_active | BOOLEAN | Active status |
| created_at | TIMESTAMP | Created date |

### orders
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| user_id | INTEGER FK | Reference to users.id |
| plan_id | INTEGER FK | Reference to plans.id |
| server_id | INTEGER FK | Reference to servers.id |
| status | TEXT | pending / approved / rejected |
| receipt_photo_id | TEXT | Telegram file_id of receipt photo |
| config_link | TEXT | Generated config link (after approval) |
| created_at | TIMESTAMP | Order date |
| reviewed_at | TIMESTAMP | Review date |

### configs
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| user_id | INTEGER FK | Reference to users.id |
| order_id | INTEGER FK | Reference to orders.id |
| server_id | INTEGER FK | Reference to servers.id |
| client_email | TEXT | Client identifier in 3x-ui |
| config_link | TEXT | VPN config/subscription link |
| expire_date | TIMESTAMP | Expiration date |
| traffic_limit_gb | INTEGER | Traffic limit in GB |
| is_active | BOOLEAN | Active status |
| created_at | TIMESTAMP | Created date |

### settings
| Column | Type | Description |
|--------|------|-------------|
| key | TEXT PK | Setting key |
| value | TEXT | Setting value |

Predefined keys: `card_number`, `card_holder`, `tutorial_android`, `tutorial_ios`, `tutorial_windows`

## User Flows

### 1. Start (/start)

1. User sends /start
2. Bot checks if user is member of configured channel via `getChatMember` API
3. If NOT member:
   - Show message: "برای استفاده از ربات ابتدا در کانال ما عضو شوید"
   - Show inline keyboard: [عضویت در کانال] (URL button) + [بررسی عضویت ✅] (callback)
4. If member:
   - Save/update user info in `users` table
   - Show main menu (reply keyboard)

### 2. Main Menu (Reply Keyboard)

| Button | Visible to |
|--------|-----------|
| خرید کانفیگ | All users |
| کانفیگ های من | All users |
| آموزش اتصال | All users |
| مدیریت | Admin only |

### 3. Buy Config Flow

1. User taps "خرید کانفیگ"
2. Bot shows list of active plans as inline buttons:
   - Each button: `{name} | {traffic_gb}GB | {duration_days} روز | {price} تومان`
3. User selects a plan
4. Bot shows list of available locations (from active servers) as inline buttons
5. User selects a location
6. Bot shows payment info:
   - Card number (from settings)
   - Card holder name (from settings)
   - Amount to pay
   - Message: "لطفا پس از واریز، تصویر رسید را ارسال کنید"
7. User uploads receipt photo
8. Bot saves order (status=pending) and confirms to user
9. Bot sends notification to admin(s):
   - User info + plan + server + receipt photo
   - Inline buttons: [تایید ✅] [رد ❌]
10. Admin taps approve:
    - Bot calls 3x-ui API to create client on selected server
    - Bot saves config in `configs` table
    - Bot sends config link to user
11. Admin taps reject:
    - Bot notifies user that payment was rejected

### 4. My Configs

1. User taps "کانفیگ های من"
2. Bot queries `configs` table for user's active configs
3. For each config, bot fetches real-time data from 3x-ui API:
   - Remaining days (calculated from expire_date)
   - Traffic used / remaining (from 3x-ui API)
4. Display list with inline buttons per config
5. User taps a config → show full details + config link

### 5. Connection Tutorial

1. User taps "آموزش اتصال"
2. Bot shows platform selection: [اندروید] [iOS] [ویندوز]
3. User selects platform
4. Bot shows tutorial text from `settings` table

## Admin Flows

### 1. Server Management

**Add Server:**
1. Admin taps "مدیریت سرورها" → "افزودن سرور"
2. Bot asks for panel URL
3. Bot asks for username
4. Bot asks for password
5. Bot asks for location name
6. Bot tests login via 3x-ui API (`/login` endpoint)
7. If successful: save server, confirm
8. If failed: show error, ask to retry

**List Servers:**
- Show all servers with status
- Options per server: enable/disable, delete

### 2. Plan Management

**Add Plan:**
1. Admin taps "مدیریت پلن‌ها" → "افزودن پلن"
2. Bot asks for plan name
3. Bot asks for traffic (GB)
4. Bot asks for duration (days)
5. Bot asks for price (Toman)
6. Save and confirm

**List Plans:**
- Show all plans
- Options per plan: edit, enable/disable, delete

### 3. User Search

1. Admin taps "جستجوی کاربر"
2. Bot asks for Telegram ID or username
3. Bot searches `users` table
4. If found: show user info + list of their configs + order history
5. If not found: show message

### 4. Tutorial Management

1. Admin taps "مدیریت آموزش"
2. Bot shows platform buttons: [اندروید] [iOS] [ویندوز]
3. Admin selects platform
4. Bot shows current tutorial text
5. Admin sends new text → save to `settings` table

### 5. Settings Management

1. Admin taps "تنظیمات"
2. Bot shows: [شماره کارت] [نام صاحب کارت]
3. Admin selects item → bot shows current value → admin sends new value → save

## 3x-ui API Integration

The `services/xui.py` module handles all communication with 3x-ui panels.

**Key operations:**
- `login(url, username, password) -> session_cookie`: Authenticate and get session
- `get_inbounds(url, session) -> list`: Get list of inbounds
- `add_client(url, session, inbound_id, email, traffic_gb, expire_days) -> config_link`: Create new client
- `get_client_traffic(url, session, email) -> (up, down)`: Get client traffic usage
- `get_client_info(url, session, email) -> dict`: Get client details

**Inbound selection:** When creating a client, the bot uses the first VLESS/VMess inbound available on the server. During server setup, the bot fetches inbounds and stores the default inbound ID in the `servers` table.

**Authentication:** 3x-ui uses cookie-based sessions. The bot logs in, stores the session cookie, and uses it for subsequent requests. Sessions are refreshed on 401 responses.

## Middleware

### Membership Middleware
- Applied to all handlers except /start
- Checks channel membership before processing any user action
- If not member, redirects to join flow

## Error Handling

- 3x-ui API failures: notify admin, inform user to try later
- Invalid receipt: ask user to resend
- Server unavailable: mark server as inactive, suggest different location
- Database errors: log and inform user

## Security

- Admin IDs stored in .env, not in database
- Panel passwords stored in database (SQLite file should be protected)
- No sensitive data logged
- Bot token never exposed in code

## Out of Scope (Phase 1)

- Online payment gateway
- Wallet/balance system
- Referral system
- Auto-renewal
- Discount codes
- Multi-language support
- Statistics/reports dashboard
- Broadcast messaging

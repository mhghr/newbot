# Multi-Server Subscription Provisioning - Design Specification

## Overview

Rework the purchase and provisioning flow of the VPN sales Telegram bot so that a
single approved order provisions the user across **all active servers** and gives
the user **one unique subscription URL** that aggregates every link.

Current behaviour (before this change):

- Buy flow: plan -> pick one location/server -> card -> receipt -> admin approve.
- On approval: one client is created on the single selected server's `inbound_id`,
  producing one link.

New behaviour (this change):

- Buy flow: plan -> card -> receipt -> admin approve (no location selection).
- On approval: for every active server, for every detected VLESS+Reality inbound
  (grpc / tcp / xhttp), a client is created (2 servers = 6 links).
- All links are stored in the DB and served from one per-user subscription URL
  hosted by the bot itself.

## Confirmed Decisions

1. Reality inbounds are **pre-created** on each server; the bot only calls
   `clients/add` (not `inbounds/add`).
2. Target inbounds are **auto-detected** per server: `protocol == "vless"` and
   `security == "reality"`, grouped by transport `network` (grpc/tcp/xhttp).
3. The **bot hosts the subscription** via a small aiohttp server at
   `GET /sub/{token}`, returning a base64-encoded newline-joined list of links.
4. The location-selection step is **removed** from the buy flow.
5. Each user has **one fixed `sub_token`** (persistent). Re-purchase/renewal
   re-provisions under the same token.

## Identity Model

- `users.sub_token`: random hex, unique per user. Used both in the sub URL
  (`{SUB_BASE_URL}/sub/{sub_token}`) and as the 3x-ui client `subId`.
- `users.client_uuid`: one uuid4 per user, reused as the VLESS `id` across all
  inbounds/servers.
- Client `email` per inbound: `{tgId}-{location}-{net}` (e.g. `123456789-Germany-grpc`).
  Email is unique within a panel; on re-provision, existing client with that email
  is deleted first, then re-added (fresh traffic/expiry).
- Link `remark` (fragment after `#`): `{location}-{tgId}-{NET}`
  (e.g. `Germany-123456789-GRPC`). Must contain telegram id and location.

## Traffic Model

Each link/inbound gets the plan's full `totalGB` and `expiryTime`. Traffic is
tracked per link (not shared across the 3 transports of a server). Known
limitation for phase 1; shared quota is out of scope.

## Buy Flow (`handlers/buy.py`)

1. `main:buy` -> membership check -> list active plans.
2. `buy_plan:{id}` -> store plan_id -> show payment info (card number, card
   holder, amount, plan summary) -> set state `waiting_receipt`.
   (The `buy_server:` step is removed.)
3. On receipt photo -> `create_order(user_id, plan_id, receipt_photo_id)`
   (no server_id) -> confirm to user -> notify admins with approve/reject buttons.
4. Admin notification caption: user, plan, amount (no single server/location).

## Provisioning on Approval (`handlers/admin/payments.py`)

On `approve_order:{id}`:

1. Ensure the user has `sub_token` and `client_uuid` (create if missing).
2. For each active server:
   - `inbounds/list`; detect VLESS+Reality inbounds by transport.
   - For each detected inbound:
     - `email = {tgId}-{location}-{net}`; if it exists, delete then add.
     - `clients/add` with `id=client_uuid`, `email`, `subId=sub_token`,
       `totalGB` and `expiryTime` from plan, `enable=true`.
     - Build the VLESS Reality link from the inbound stream settings.
     - Upsert into `config_links`.
   - Collect per-server errors; continue with remaining servers.
3. `update_order_status(order_id, "approved")`.
4. Send the user the single sub URL `{SUB_BASE_URL}/sub/{sub_token}` plus a
   summary. Report any per-server failures to the admin.

On `reject_order:{id}`: unchanged.

## VLESS Reality Link Builder (`services/links.py`, new)

Pure function that, given a server host, an inbound object, a client uuid and a
remark, returns a `vless://` URI.

From the inbound:

- `port` = inbound.port
- `host` = hostname parsed from the server URL
- `type` = network (tcp/grpc/xhttp)
- `security` = reality
- `pbk` = realitySettings.settings.publicKey
- `fp` = realitySettings.settings.fingerprint
- `sni` = realitySettings.serverNames[0]
- `sid` = realitySettings.shortIds[0]
- `spx` = realitySettings.settings.spiderX (url-encoded)
- tcp: `flow=xtls-rprx-vision`, `headerType` from tcpSettings
- grpc: `serviceName` (+ `mode`) from grpcSettings
- xhttp: `path` / `host` / `mode` from xhttpSettings

Output: `vless://{uuid}@{host}:{port}?{params}#{remark}`.

The builder must be tolerant of missing/renamed keys across 3x-ui versions
(nested `realitySettings.settings` vs flat variants; `xhttp` vs `splithttp`).

## Subscription Server (`services/subserver.py`, new)

- aiohttp app with route `GET /sub/{token}`.
- Look up user by `sub_token`; fetch active `config_links`; join links with `\n`;
  return `base64(text)` with `content-type: text/plain; charset=utf-8` and a
  `profile-title` header.
- Unknown token -> 404.
- Started from `main.py` alongside `dp.start_polling` using
  `web.AppRunner` / `TCPSite` on `SUB_HOST:SUB_PORT`.

## Config / Env (`config.py`, `.env.example`)

New variables:

| Variable | Description |
|----------|-------------|
| `SUB_BASE_URL` | Public base URL for the sub server (e.g. `https://sub.example.com`) |
| `SUB_HOST` | Bind host for the aiohttp sub server (default `0.0.0.0`) |
| `SUB_PORT` | Bind port for the aiohttp sub server (default `8080`) |

## Database Changes (`database/models.py`, `database/db.py`)

- `users`: add `sub_token TEXT UNIQUE`, `client_uuid TEXT`
  (via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`).
- `orders.server_id`: make nullable (drop NOT NULL) since an order is no longer
  tied to one server.
- New table `config_links`:
  `id, user_id, order_id, server_id, inbound_id, network, client_email,
   client_uuid, link, remark, is_active, created_at`.
- `configs` table: left intact.
- New helpers: get-or-create sub identity, upsert config link, delete config
  links for a (user, server), fetch links by `sub_token`, create order without
  server_id.

## My Configs (`handlers/my_configs.py`)

Rewrite to show the single subscription URL plus a per-server traffic summary
(pulled live from each server for that user's email), instead of one link per
config row.

## 3x-ui Client (`services/xui.py`)

- Reuse existing `login`/`_request`.
- Add: detect reality inbounds from `inbounds/list`; `add_client` variant that
  accepts an explicit `client_uuid`, `email`, `sub_id`, `remark`, `total_gb`,
  `expire_days` and a target `inbound_id`; keep existing `delete_client`.
- Do not change existing method signatures used elsewhere unless needed; add new
  methods to avoid breaking `servers.py` / `my_configs.py`.

## Files Touched

Edit: `config.py`, `.env.example`, `database/models.py`, `database/db.py`,
`services/xui.py`, `handlers/buy.py`, `handlers/admin/payments.py`,
`handlers/my_configs.py`, `keyboards/inline.py`, `main.py`.

New: `services/links.py`, `services/subserver.py`.

## Acceptance Criteria

- Buy works without a location step (plan -> card -> receipt).
- Admin approval provisions clients on all active servers, 3 links per server,
  stored in `config_links`.
- The single sub URL returns all of the user's links (base64) and imports into
  a client app.
- Each link's remark contains the telegram id and the server location.
- Existing admin server/plan/settings flows keep working.

## Risks

- Sub serving needs a reachable public domain/port (`SUB_BASE_URL`).
- Reality settings shape varies between 3x-ui forks; link builder must be lenient.
- Panel email uniqueness -> delete-then-add strategy on re-purchase.
- Per-link traffic is separate (not shared) - accepted for phase 1.

## Out of Scope

- Creating inbounds via `inbounds/add`.
- Online payment gateway, wallet, referrals, auto-renewal, shared quota.

# Per-App Connection Tutorials — Design

Date: 2026-08-07

## Context

Currently the "آموزش اتصال" section shows one combined text per platform
(Android / iOS / Windows). The default text for a platform contains separate
sections for each app, all in a single message. Admins can override the whole
platform text via "مدیریت آموزش".

Requirement: split the tutorials per app. In the user flow, tapping a platform
shows a list of app buttons; tapping an app shows that app's tutorial text and,
optionally, a tutorial video. In the admin flow, each app's tutorial text can be
edited and an optional video can be attached. Omitting the video must not cause
an error.

## Requirements

1. User flow: "آموزش اتصال" → platform → app buttons → per-app tutorial
   (text + optional video).
2. Admin flow: "مدیریت آموزش" → platform → app buttons → per-app management:
   edit text and optionally add/replace/remove a video.
3. Video is optional. Saving text without a video must not error.
4. Add a v2rayNG tutorial text for Android.
5. Split existing per-platform default texts into per-app default texts
   (Android: v2box, NPV Tunnel, V2App, NetMod, v2rayNG; iOS: V2rayTun,
   v2rayBox; Windows: v2rayN).

## Design

### Data storage

No new database table. Reuse the existing `settings` key/value table:

- `tut_text_{platform}_{slug}` → tutorial text for one app ("" = not set)
- `tut_video_{platform}_{slug}` → Telegram video file_id ("" = none)

`get_setting` / `set_setting` in `bot/database/db.py` are sufficient; no schema
change required.

### Default per-app tutorials

`DEFAULT_APP_TUTORIALS` constant (in `bot/handlers/tutorial.py`), keyed by
platform → list of `{slug, name, text}`. The text is the existing per-app
section from the current combined default, e.g. for Android:

- v2box
- NPV Tunnel
- V2App
- NetMod
- v2rayNG (new, same style as the others)

The old combined `DEFAULT_TUTORIALS` and old `tutorial_{platform}` setting are
deprecated. If neither admin override nor default exists for an app, the user
sees "⚠️ متن آموزش برای این نرم‌افزار هنوز تنظیم نشده است."

### User flow

1. `main:tutorial` → platform selection (`tutorial_platforms_keyboard`,
   unchanged).
2. `tutorial:{platform}` → instead of showing one combined text, show app
   buttons: `tut_app:{platform}:{slug}` for each known app on that platform.
3. `tut_app:{platform}:{slug}` → resolve text
   (`settings tut_text_...` else default) and video
   (`settings tut_video_...`):
   - If video file_id exists → `bot.send_video(file_id, caption=text)`
     (text omitted from caption if empty).
   - Else → `message.edit_text(text)`.
   - If no text and no video → "not set yet" message.
   - "🔙 بازگشت" returns to the app list for that platform.

### Admin flow

1. `admin:tutorials` → platform selection (`admin_tutorial_keyboard`,
   unchanged).
2. `admin:edit_tutorial:{platform}` → now shows app buttons:
   `admin:tut_app:{platform}:{slug}` (one per known app).
3. `admin:tut_app:{platform}:{slug}` → app detail:
   - "✏️ ویرایش متن" → FSM asks for new text, saves to `tut_text_...`.
   - "🎬 ویدیو آموزشی" → FSM asks for a video; admin sends a video → saves
     `file_id` to `tut_video_...`. Video is entirely optional: saving text
     alone does not error, and the app detail shows current video status.
   - "🗑 حذف ویدیو" if a video exists.
   - "🔙 بازگشت".

Editing text and adding video are independent states; neither requires the
other.

### Files touched

- `bot/handlers/tutorial.py` — restructure user flow; add
  `DEFAULT_APP_TUTORIALS` (including v2rayNG), app-list and app-detail
  callbacks, video sending.
- `bot/handlers/admin/settings.py` — per-app tutorial management (text edit +
  optional video via FSM states).
- `bot/keyboards/inline.py` — keyboards: user per-app tutorial list, admin
  per-app tutorial list, admin per-app tutorial detail.
- `bot/database/db.py` — no changes needed (existing get/set_setting).

### Error handling

- No video attached → fine; text is shown without video.
- App without text and without video → "not set yet" message, no crash.
- Admin sends a non-video message while in "waiting for video" state → prompt
  again ("🎬 یک ویدیو ارسال کنید یا «انصراف» را بزنید"), no crash.

## Migration / Back-compat

- Old `tutorial_{platform}` settings are deprecated and no longer displayed.
- Existing databases need no migration (settings table reused).

## Testing

- Manual: full user flow (platform → app list → app tutorial), admin flow
  (edit text, add video, remove video), and the "no video" case.
- No automated tests currently cover handlers; existing test files
  (`tests/test_media_downloader.py`, `tests/test_xui.py`) are unrelated.

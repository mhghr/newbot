# Server Transfer Feature Design

## Purpose
Add a "Server Transfer" button to the admin panel that migrates the entire bot (code, database, configs) to a new raw Ubuntu server via SSH.

## Behavior

1. Admin presses **"📡 انتقال سرور"** in admin menu
2. FSM collects: server IP, SSH username, SSH password (one-time, never stored)
3. Admin confirms and transfer begins
4. Progress updates sent as Telegram messages
5. Transfer runs in background thread to avoid blocking the bot loop
6. SSH credentials purged from memory after completion (success or failure)

## Transfer Steps

| # | Step | Details |
|---|------|---------|
| 1 | SSH Handshake | Connect to new server, verify authentication |
| 2 | System Deps | `apt-get install python3 python3-venv python3-pip postgresql postgresql-contrib openssl curl ffmpeg` |
| 3 | DB Backup | `pg_dump` locally → temp `.sql` file |
| 4 | File Upload | SFTP project files (exclude `venv/`, `__pycache__/`, `backups/`, `.git/`) to `/opt/migmig-bot/` |
| 5 | DB Restore | Create DB user + database on new server, restore from dump |
| 6 | Venv Setup | `python3 -m venv && pip install -r requirements.txt` |
| 7 | Systemd | Write `.service` file, `daemon-reload`, `enable`, `start` |
| 8 | Verify | `systemctl is-active migmig-bot` returns active |

## Files Changed

| File | Action |
|------|--------|
| `requirements.txt` | Add `paramiko` for SSH |
| `bot/handlers/admin/transfer.py` | New file (FSM handler + transfer logic) |
| `bot/keyboards/inline.py` | Add `📡 انتقال سرور` button to `admin_menu_keyboard()` |
| `bot/main.py` | Register transfer router |

## FSM States

```
waiting_for_host     → ask IP/hostname
waiting_for_username  → ask SSH user (root)
waiting_for_password  → ask SSH password
waiting_for_confirm   → show summary + "شروع انتقال" button
```

## Security

- Password held only in FSM memory (not persisted to DB/disk)
- Cleared from FSM context immediately after transfer completes or fails
- SSH connection uses password authentication (no key file)

## Edge Cases

- **SSH unreachable**: abort with clear error message
- **Authentication failure**: abort, admin retries with correct credentials
- **Disk space**: check free space before upload
- **Transfer interrupted**: admin can retry (previous partial state cleaned up)
- **Old bot**: continues running; admin must manually stop it after verifying the new bot works

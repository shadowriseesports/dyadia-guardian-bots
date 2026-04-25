# Dyadia Guardian Bot

Clean Python Discord bot for moderation, verification, modmail, staff applications, anti-raid protection, server activity logs, chat-based leveling, Instagram feed notifications, and a Railway-ready web dashboard.

## Kept Features

- Slash commands: `help`, `warn`, `mute`, `kick`, `ban`, `unban`, `addrole`, `removerole`, `clear`, `modlogs`, `verificationpanel`, `invites`, `inviteleaderboard`, `staffapplypanel`, and `antiraid ...`
- DM-based modmail with an `Open Modmail` button
- Persistent HOK Dyadia verification panel that assigns the `Verified` role
- Forum-thread modmail relay between moderators and users
- Modmail inactivity cleanup
- Simple in-memory moderation log history
- Server activity logs for message deletes/edits, image deletes, bulk deletes, invites, moderator commands, member updates, role changes, channel changes, emoji changes, voice joins/leaves/moves, and ban/unban events
- Staff application panel with 2-page modal workflow
- Anti-raid detection for join bursts with temporary raid mode and auto-timeout for suspicious fresh accounts
- Persistent leveling and invite tracking data with `/rank`, `/leaderboard`, `/levelpanel`, `/invites`, and `/inviteleaderboard`
- Automatic rank-role rewards based on your Honor of Kings leveling ladder
- Instagram post/reel notifications from a configured RSS or Atom feed to a Discord text channel
- FastAPI dashboard with Discord login for managing guild settings from the browser

## Project Structure

```text
Dyadia-Guardian-Bot/
|-- bot.py
|-- config.py
|-- requirements.txt
|-- .env
|-- .env.example
`-- README.md
```

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Fill in `.env`.
4. Start the bot:

```powershell
python bot.py
```

## Discord Developer Portal

Enable these intents for the bot:

- `MESSAGE CONTENT INTENT`
- `SERVER MEMBERS INTENT`

## Notes

- `modlogs` is in-memory only. Restarting the bot clears past entries.
- `MODMAIL_FORUM_ID` must point to a forum channel.
- Anti-raid settings can be adjusted through `.env` without editing code.
- Use `/antiraid status` to check whether raid mode is active.
- Set `SERVER_LOG_CHANNEL_ID` if you want server activity logs in a dedicated text channel. If it is not set, the bot falls back to `MOD_LOG_CHANNEL_ID`.
- Set `INVITE_LOG_CHANNEL_ID` if you want invite create/delete and invite-used join logs in a dedicated text channel. If it is not set, invite logs fall back to `SERVER_LOG_CHANNEL_ID`, then `MOD_LOG_CHANNEL_ID`.
- Set `VERIFICATION_LOG_CHANNEL_ID` if you want successful verification logs in a dedicated text channel. If it is not set, verification logs fall back to `SERVER_LOG_CHANNEL_ID`, then `MOD_LOG_CHANNEL_ID`.
- Set `WELCOME_CHANNEL_ID` if you want automatic welcome messages for new members in a dedicated text channel.
- Set `INSTAGRAM_NOTIFICATION_CHANNEL_ID`, `INSTAGRAM_FEED_URL`, and optionally `INSTAGRAM_PROFILE_NAME` / `INSTAGRAM_POLL_MINUTES` if you want Instagram post or reel notifications in a dedicated text channel.
- Set `LEVEL_UP_CHANNEL_ID` if you want level-up announcements to go to one dedicated text channel.
- Set `VERIFIED_ROLE_ID` if you want the verification button to target a specific role ID. If it is not set, the bot falls back to a role named `Verified`.
- Set `WELCOME_BANNER_URL` if you want a custom image banner on the welcome embed.
- Set `LEVEL_XP_INCREMENT` to control how much more XP each next level requires. Level 1 requires this amount, Level 2 requires double, and so on.
- If `DATABASE_URL` is set, leveling and invite tracking data are stored in PostgreSQL automatically.
- If `DATABASE_URL` is not set, leveling falls back to `level_data.json` and invite tracking falls back to `invite_data.json` for local use.
- Instagram notifications use `instagram_state.json` to remember already-sent feed items and avoid reposting them after restarts.
- Invite tracking requires the bot to have `Manage Server` permission so it can read server invites.
- Reward roles are matched by role name, so create the reward roles in Discord using the exact names from the leveling panel.
- Instagram does not provide a simple public feed by itself, so `INSTAGRAM_FEED_URL` should point to an RSS or Atom feed for the Instagram account you want to watch.
- For Railway dashboard deployment, run a second service with the start command `uvicorn dashboard.main:app --host 0.0.0.0 --port $PORT`.
- The dashboard uses `DATABASE_URL`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, and `SESSION_SECRET`.

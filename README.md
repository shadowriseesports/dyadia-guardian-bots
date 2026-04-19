# Dyadia Guardian Bot

Clean Python Discord bot for moderation, modmail, staff applications, anti-raid protection, and chat-based leveling.

## Kept Features

- Slash commands: `help`, `warn`, `mute`, `kick`, `ban`, `unban`, `clear`, `modlogs`, `staffapplypanel`, and `antiraid ...`
- DM-based modmail with an `Open Modmail` button
- Forum-thread modmail relay between moderators and users
- Modmail inactivity cleanup
- Simple in-memory moderation log history
- Staff application panel with 2-page modal workflow
- Anti-raid detection for join bursts with temporary raid mode and auto-timeout for suspicious fresh accounts
- Persistent local leveling data with `/rank`, `/leaderboard`, and `/levelpanel`
- Automatic rank-role rewards based on your Honor of Kings leveling ladder

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
- Leveling data is stored in `level_data.json` in this project.
- On Railway, `level_data.json` is not durable across fresh deploys or ephemeral filesystem resets. Use a database later if you want permanent XP.
- Reward roles are matched by role name, so create the reward roles in Discord using the exact names from the leveling panel.

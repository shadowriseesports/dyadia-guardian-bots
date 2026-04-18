# Dyadia Guardian Bot

Clean Python Discord bot for moderation and modmail.

## Kept Features

- Slash commands: `help`, `warn`, `mute`, `kick`, `ban`, `unban`, `clear`, `modlogs`, `close`
- DM-based modmail with an `Open Modmail` button
- Forum-thread modmail relay between moderators and users
- Modmail inactivity cleanup
- Simple in-memory moderation log history

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
- `close` must be used inside an active modmail thread.
- `MODMAIL_FORUM_ID` must point to a forum channel.

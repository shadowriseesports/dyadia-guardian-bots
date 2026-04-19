# Quickstart

## 1. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

## 2. Configure `.env`

Fill these values:

- `DISCORD_TOKEN`
- `MODMAIL_FORUM_ID`
- `MOD_LOG_CHANNEL_ID`
- `STAFF_APPLICATION_CHANNEL_ID`
- `MODERATOR_ROLE_ID`
- `ADMIN_ROLE_ID`
- `LEVEL_UP_CHANNEL_ID` if you want level-up messages in a dedicated text channel
- `DATABASE_URL` if you want PostgreSQL-backed leveling on Railway
- `LEVEL_XP_INCREMENT` to control how much extra XP each next level requires
- Optional anti-raid tuning:
- `ANTI_RAID_ENABLED`
- `ANTI_RAID_JOIN_THRESHOLD`
- `ANTI_RAID_WINDOW_SECONDS`
- `ANTI_RAID_LOCKDOWN_MINUTES`
- `ANTI_RAID_ACCOUNT_AGE_MINUTES`
- `ANTI_RAID_TIMEOUT_MINUTES`

## 3. Enable Discord bot intents

In the Discord Developer Portal for this bot:

- Turn on `MESSAGE CONTENT INTENT`
- Turn on `SERVER MEMBERS INTENT`
- Save changes

## 4. Invite permissions

Make sure the bot can:

- View channels
- Send messages
- Use application commands
- Create public threads
- Send messages in threads
- Manage threads
- Moderate members
- Kick members
- Ban members
- Manage messages

## 5. Start the bot

```powershell
python bot.py
```

## 6. Expected startup checks

You should see logs confirming:

- the bot logged in
- slash commands synced
- modmail forum channel found
- mod log channel found
- staff application channel found
- level-up channel found or source-channel fallback selected
- anti-raid config values loaded
- leveling data file loaded or created
- leveling storage backend selected

## 7. Test modmail

1. DM the bot with `hi`
2. The bot should send a support embed with an `Open Modmail` button
3. Click the button
4. A forum thread should appear in your modmail forum
5. Reply in DM and in the thread to confirm both directions work

## 8. Test anti-raid

1. Use `/antiraid status` to confirm the feature is enabled
2. Use `/antiraid activate` to manually turn on raid mode
3. Join with a fresh test account and confirm it gets timed out
4. Use `/antiraid deactivate` to end raid mode

## 9. Test leveling

1. Create the rank roles in Discord using the exact names shown by `/levelpanel`
2. Use `/levelpanel` to post the progression panel in your server
3. Chat with a member account in the server for a few minutes
4. Use `/rank` to confirm XP and level progress
5. Use `/leaderboard` to confirm the server ranking updates

## 10. Railway note

If you add a Railway PostgreSQL service and expose `DATABASE_URL`, leveling data will be stored in PostgreSQL automatically. Without `DATABASE_URL`, the bot falls back to `level_data.json` for local use.

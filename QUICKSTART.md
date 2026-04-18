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

## 7. Test modmail

1. DM the bot with `hi`
2. The bot should send a support embed with an `Open Modmail` button
3. Click the button
4. A forum thread should appear in your modmail forum
5. Reply in DM and in the thread to confirm both directions work

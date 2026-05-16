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
- `SERVER_STATS_CHANNEL_ID` if you want the bot to rename a dedicated stats channel
- `SERVER_STATS_CHANNEL_FORMAT` if you want a custom stats channel name like `members-{total}` or `online-{online}`
- `ALL_MEMBERS_STATS_CHANNEL_ID`, `MEMBERS_STATS_CHANNEL_ID`, `BOTS_STATS_CHANNEL_ID`, `BOOSTS_STATS_CHANNEL_ID`, and `ONLINE_MEMBERS_STATS_CHANNEL_ID` if you want five separate auto-updating stat channels
- `SERVER_LOG_CHANNEL_ID` if you want server activity logs in a separate text channel
- `INVITE_LOG_CHANNEL_ID` if you want invite create and delete logs in a separate text channel
- `VERIFICATION_LOG_CHANNEL_ID` if you want successful verification logs in a separate text channel
- `WELCOME_CHANNEL_ID` if you want automatic welcome messages in a separate text channel
- `INSTAGRAM_NOTIFICATION_CHANNEL_ID` if you want Instagram notifications in a separate text channel
- `STAFF_APPLICATION_CHANNEL_ID`
- `MODERATOR_ROLE_ID`
- `ADMIN_ROLE_ID`
- `VERIFIED_ROLE_ID` if you want the verification button to assign a specific role ID
- `DATABASE_URL` if you want PostgreSQL storage for modlogs, auto-reactions, no-link channels, and Instagram notifier state
- `WELCOME_BANNER_URL` if you want a custom banner image on the welcome embed
- `INSTAGRAM_FEED_URL` for the Instagram RSS or Atom feed you want to monitor
- `INSTAGRAM_PROFILE_NAME` if you want a custom label on Instagram notification embeds
- `INSTAGRAM_POLL_MINUTES` to control how often the bot checks the feed
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
- server log channel found or mod-log fallback selected
- invite log channel found or server or mod-log fallback selected
- verification log channel found or server or mod-log fallback selected
- welcome channel found or welcome messages disabled
- staff application channel found
- Instagram notification channel found or Instagram notifier disabled
- verified role found or `Verified` role-name fallback selected
- persistent storage backend selected
- anti-raid config values loaded

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

## 9. Test verification

1. Create a `Verified` role in Discord, or set `VERIFIED_ROLE_ID` to the role you want to assign
2. Use `/verificationpanel` to post the HOK Dyadia verification panel
3. Click the `HOK Dyadia Verification` button with a test member
4. Confirm the member receives the verified role
5. Confirm a verification log message appears in `VERIFICATION_LOG_CHANNEL_ID`, or the server-log fallback channel

## 10. Test staff applications

1. Use `/staffapplypanel post` to post the staff application button panel
2. Choose either Community Moderator or Support Moderator
3. Complete the 2-page application form
4. Use `/staffapplypanel disable` if you need to disable the posted panel later

## 11. Test Instagram notifications

1. Set `INSTAGRAM_NOTIFICATION_CHANNEL_ID` to the text channel where you want updates
2. Set `INSTAGRAM_FEED_URL` to an RSS or Atom feed for the Instagram account you want to monitor
3. Optional: set `INSTAGRAM_PROFILE_NAME` and `INSTAGRAM_POLL_MINUTES`
4. Start the bot and confirm the startup log says the Instagram notifier is enabled
5. Use `/instagramstatus` to confirm the feed URL, target channel, and poll interval
6. Use `/instagramcheck` to run a manual poll
7. Post a new Instagram reel or post, then wait for the next poll cycle and confirm the bot sends it to your configured channel

## 12. PostgreSQL note

If `DATABASE_URL` is set, the bot stores moderation logs, auto-reaction rules, no-link channel rules, and Instagram notifier state in PostgreSQL. If those database tables are empty, the bot imports existing local JSON data on startup. Without `DATABASE_URL`, the bot falls back to local JSON files and in-memory modlogs.

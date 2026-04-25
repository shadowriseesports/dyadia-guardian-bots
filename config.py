from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    discord_token: str
    modmail_forum_id: int
    mod_log_channel_id: int
    staff_application_channel_id: int
    moderator_role_id: int
    admin_role_id: int
    server_log_channel_id: int = 0
    invite_log_channel_id: int = 0
    level_up_channel_id: int = 0
    verification_log_channel_id: int = 0
    welcome_channel_id: int = 0
    instagram_notification_channel_id: int = 0
    verified_role_id: int = 0
    database_url: str = ""
    welcome_banner_url: str = ""
    instagram_feed_url: str = ""
    instagram_profile_name: str = "Instagram"
    instagram_poll_minutes: int = 10
    level_xp_increment: int = 10
    anti_raid_enabled: bool = True
    anti_raid_join_threshold: int = 5
    anti_raid_window_seconds: int = 20
    anti_raid_lockdown_minutes: int = 10
    anti_raid_account_age_minutes: int = 30
    anti_raid_timeout_minutes: int = 30
    server_name: str = "Honor Of Kings | Northeast India"
    bot_status_text: str = "Guardian of Honor of Kings | Northeast india"


@dataclass(frozen=True)
class DashboardSettings:
    database_url: str
    discord_client_id: str
    discord_client_secret: str
    discord_redirect_uri: str
    session_secret: str
    bot_api_token: str = ""
    locked_guild_id: int = 0


def _require_int(name: str) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def _get_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Environment variable {name} must be a boolean value like true/false.")


def _get_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        value = default
    else:
        try:
            value = int(raw_value.strip())
        except ValueError as exc:
            raise RuntimeError(f"Environment variable {name} must be an integer.") from exc

    if value < minimum:
        raise RuntimeError(f"Environment variable {name} must be at least {minimum}.")
    return value


def _get_optional_int(name: str) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return 0

    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def _require_str(name: str) -> str:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return raw_value


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing required environment variable: DISCORD_TOKEN")

    return Settings(
        discord_token=token,
        modmail_forum_id=_require_int("MODMAIL_FORUM_ID"),
        mod_log_channel_id=_require_int("MOD_LOG_CHANNEL_ID"),
        server_log_channel_id=_get_optional_int("SERVER_LOG_CHANNEL_ID"),
        invite_log_channel_id=_get_optional_int("INVITE_LOG_CHANNEL_ID"),
        verification_log_channel_id=_get_optional_int("VERIFICATION_LOG_CHANNEL_ID"),
        welcome_channel_id=_get_optional_int("WELCOME_CHANNEL_ID"),
        instagram_notification_channel_id=_get_optional_int("INSTAGRAM_NOTIFICATION_CHANNEL_ID"),
        staff_application_channel_id=_require_int("STAFF_APPLICATION_CHANNEL_ID"),
        moderator_role_id=_require_int("MODERATOR_ROLE_ID"),
        admin_role_id=_require_int("ADMIN_ROLE_ID"),
        level_up_channel_id=_get_optional_int("LEVEL_UP_CHANNEL_ID"),
        verified_role_id=_get_optional_int("VERIFIED_ROLE_ID"),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        welcome_banner_url=os.getenv("WELCOME_BANNER_URL", "").strip(),
        instagram_feed_url=os.getenv("INSTAGRAM_FEED_URL", "").strip(),
        instagram_profile_name=os.getenv("INSTAGRAM_PROFILE_NAME", "Instagram").strip() or "Instagram",
        instagram_poll_minutes=_get_int("INSTAGRAM_POLL_MINUTES", 10, minimum=1),
        level_xp_increment=_get_int("LEVEL_XP_INCREMENT", 10, minimum=1),
        anti_raid_enabled=_get_bool("ANTI_RAID_ENABLED", True),
        anti_raid_join_threshold=_get_int("ANTI_RAID_JOIN_THRESHOLD", 5, minimum=2),
        anti_raid_window_seconds=_get_int("ANTI_RAID_WINDOW_SECONDS", 20, minimum=5),
        anti_raid_lockdown_minutes=_get_int("ANTI_RAID_LOCKDOWN_MINUTES", 10, minimum=1),
        anti_raid_account_age_minutes=_get_int("ANTI_RAID_ACCOUNT_AGE_MINUTES", 30, minimum=0),
        anti_raid_timeout_minutes=_get_int("ANTI_RAID_TIMEOUT_MINUTES", 30, minimum=1),
        bot_status_text=os.getenv("BOT_STATUS_TEXT", "Guardian of Honor of Kings | Northeast india").strip()
        or "Guardian of Honor of Kings | Northeast india",
    )


def load_dashboard_settings() -> DashboardSettings:
    return DashboardSettings(
        database_url=_require_str("DATABASE_URL"),
        discord_client_id=_require_str("DISCORD_CLIENT_ID"),
        discord_client_secret=_require_str("DISCORD_CLIENT_SECRET"),
        discord_redirect_uri=_require_str("DISCORD_REDIRECT_URI"),
        session_secret=_require_str("SESSION_SECRET"),
        bot_api_token=os.getenv("DISCORD_TOKEN", "").strip(),
        locked_guild_id=_get_optional_int("DASHBOARD_LOCKED_GUILD_ID"),
    )

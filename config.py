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
    server_name: str = "Honor Of Kings | Northeast India"
    bot_status_text: str = "Guardian of Honor of Kings | Northeast india"


def _require_int(name: str) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing required environment variable: DISCORD_TOKEN")

    return Settings(
        discord_token=token,
        modmail_forum_id=_require_int("MODMAIL_FORUM_ID"),
        mod_log_channel_id=_require_int("MOD_LOG_CHANNEL_ID"),
        staff_application_channel_id=_require_int("STAFF_APPLICATION_CHANNEL_ID"),
        moderator_role_id=_require_int("MODERATOR_ROLE_ID"),
        admin_role_id=_require_int("ADMIN_ROLE_ID"),
        bot_status_text=os.getenv("BOT_STATUS_TEXT", "Guardian of Honor of Kings | Northeast india").strip()
        or "Guardian of Honor of Kings | Northeast india",
    )

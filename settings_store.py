from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class GuildSettings:
    modmail_forum_id: int = 0
    mod_log_channel_id: int = 0
    staff_application_channel_id: int = 0
    moderator_role_id: int = 0
    admin_role_id: int = 0
    server_log_channel_id: int = 0
    invite_log_channel_id: int = 0
    level_up_channel_id: int = 0
    verification_log_channel_id: int = 0
    welcome_channel_id: int = 0
    instagram_notification_channel_id: int = 0
    verified_role_id: int = 0
    mod_log_enabled: bool = True
    server_log_enabled: bool = True
    invite_log_enabled: bool = True
    verification_log_enabled: bool = True
    welcome_enabled: bool = True
    instagram_enabled: bool = True
    leveling_enabled: bool = True
    welcome_banner_url: str = ""
    welcome_title: str = "Welcome to {server}"
    welcome_description: str = (
        "Hey {member}! Glad to have you here!\n\n"
        "**Verification Required**\n"
        "Before accessing all channels, please complete verification.\n\n"
        "Go to {verify_channel} and tap the **HOK Dyadia Verification** button.\n"
        "After completing it, you will automatically receive the {verified_role} role and unlock the server.\n\n"
        "Start here:\n"
        "1. Verify yourself in {verify_channel}\n"
        "2. Read {server_info_channel} to understand the rules\n"
        "3. Introduce yourself in {intro_channel}\n"
        "4. Jump into chats and start making teammates!\n\n"
        "Let's build the strongest Honor of Kings community in Northeast India."
    )
    modmail_intro_title: str = "Support Desk"
    modmail_intro_description: str = (
        "Welcome to **{server}**.\n\n"
        "If you need assistance, please use **Open Modmail** to contact the moderation team privately.\n\n"
        "This system can be used for reports, appeals, rule clarifications, or safety-related concerns.\n\n"
        "All moderator replies will be sent here in direct messages."
    )
    staff_panel_title: str = "Honor of Kings | Northeast India"
    staff_panel_description: str = (
        "**Staff Application Form**\n"
        "(Community Moderator & Support Moderator)\n\n"
        "Want to join the staff team?\n\n"
        "Press the role you want below and fill out the form in 2 pages. "
        "Your application will be sent privately to the review team."
    )
    verification_title: str = "HOK Dyadia Verification"
    verification_description: str = (
        "Welcome! To unlock full access to the server, simply complete a quick verification.\n\n"
        "**How It Works**\n"
        "- Tap the HOK Dyadia Verification button below\n"
        "- Verification will be completed instantly\n\n"
        "**After Verification**\n"
        "- You will receive the {verified_role} role\n"
        "- Full access to all channels and features will be unlocked\n\n"
        "**Note**\n"
        "- Do not spam the button\n"
        "- Contact staff if you face any issues\n\n"
        "Tap the button below to get verified.\n\n"
        "Quick | Simple | Secure"
    )
    level_panel_title: str = "Honor Of Kings Northeast India Leveling System"
    level_panel_description: str = (
        "This server uses a leveling system where members gain XP by chatting and participating in the "
        "community. As you earn XP, you level up and unlock Honor of Kings themed ranks that show your "
        "progress and activity in the server.\n\n"
        "The more active you are, the higher your level becomes."
    )
    instagram_feed_url: str = ""
    instagram_profile_name: str = "Instagram"
    auto_react_rules: str = ""
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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], defaults: Optional["GuildSettings"] = None) -> "GuildSettings":
        base = defaults.to_dict() if defaults is not None else cls().to_dict()
        cleaned = dict(base)
        for key, default_value in base.items():
            if key not in raw:
                continue
            cleaned[key] = _coerce_setting_value(key, raw[key], default_value)
        return cls(**cleaned)


def _coerce_setting_value(key: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return default

    if isinstance(default, int):
        if value in ("", None):
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, parsed)

    if isinstance(default, str):
        if value is None:
            return default
        return str(value).strip()

    return value


def ensure_settings_tables(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_by BIGINT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings_audit (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    updated_by BIGINT,
                    changed_keys TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    previous_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                    new_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_actions (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    action_type TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    requested_by BIGINT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result_message TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    processed_at TIMESTAMPTZ
                )
                """
            )
        conn.commit()


def load_all_guild_settings(database_url: str, defaults: GuildSettings) -> Dict[int, GuildSettings]:
    loaded: Dict[int, GuildSettings] = {}
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT guild_id, settings FROM guild_settings")
            for row in cur.fetchall():
                guild_id = int(row["guild_id"])
                payload = row["settings"] if isinstance(row["settings"], dict) else {}
                loaded[guild_id] = GuildSettings.from_dict(payload, defaults)
    return loaded


def load_guild_settings(database_url: str, guild_id: int, defaults: GuildSettings) -> GuildSettings:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT settings FROM guild_settings WHERE guild_id = %s", (guild_id,))
            row = cur.fetchone()
            if row is None:
                return defaults
            payload = row["settings"] if isinstance(row["settings"], dict) else {}
            return GuildSettings.from_dict(payload, defaults)


def save_guild_settings(
    database_url: str,
    guild_id: int,
    settings: GuildSettings,
    *,
    updated_by: int,
    previous: Optional[GuildSettings] = None,
) -> List[str]:
    previous_settings = previous or GuildSettings()
    previous_payload = previous_settings.to_dict()
    new_payload = settings.to_dict()
    changed_keys = sorted(key for key in new_payload if previous_payload.get(key) != new_payload.get(key))

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO guild_settings (guild_id, settings, updated_by, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (guild_id)
                DO UPDATE SET settings = EXCLUDED.settings, updated_by = EXCLUDED.updated_by, updated_at = NOW()
                """,
                (guild_id, Jsonb(new_payload), updated_by),
            )
            cur.execute(
                """
                INSERT INTO guild_settings_audit (
                    guild_id,
                    updated_by,
                    changed_keys,
                    previous_settings,
                    new_settings,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (
                    guild_id,
                    updated_by,
                    changed_keys,
                    Jsonb(previous_payload),
                    Jsonb(new_payload),
                ),
            )
        conn.commit()

    return changed_keys


def load_guild_settings_audit(database_url: str, guild_id: int, *, limit: int = 20) -> List[Dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT updated_by, changed_keys, created_at
                FROM guild_settings_audit
                WHERE guild_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (guild_id, limit),
            )
            rows = cur.fetchall()

    results: List[Dict[str, Any]] = []
    for row in rows:
        created_at = row["created_at"]
        if isinstance(created_at, datetime):
            created = created_at.isoformat()
        else:
            created = str(created_at)
        results.append(
            {
                "updated_by": int(row["updated_by"]) if row["updated_by"] is not None else None,
                "changed_keys": list(row["changed_keys"] or []),
                "created_at": created,
            }
        )
    return results


def enqueue_dashboard_action(
    database_url: str,
    guild_id: int,
    action_type: str,
    *,
    requested_by: int,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dashboard_actions (guild_id, action_type, payload, requested_by, status, created_at)
                VALUES (%s, %s, %s, %s, 'pending', NOW())
                RETURNING id
                """,
                (guild_id, action_type, Jsonb(payload or {}), requested_by),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["id"]) if row is not None else 0


def load_pending_dashboard_actions(database_url: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, guild_id, action_type, payload, requested_by, created_at
                FROM dashboard_actions
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    results: List[Dict[str, Any]] = []
    for row in rows:
        created_at = row["created_at"]
        results.append(
            {
                "id": int(row["id"]),
                "guild_id": int(row["guild_id"]),
                "action_type": str(row["action_type"]),
                "payload": row["payload"] if isinstance(row["payload"], dict) else {},
                "requested_by": int(row["requested_by"]) if row["requested_by"] is not None else None,
                "created_at": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
            }
        )
    return results


def complete_dashboard_action(
    database_url: str,
    action_id: int,
    *,
    success: bool,
    result_message: str,
) -> None:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dashboard_actions
                SET status = %s,
                    result_message = %s,
                    processed_at = NOW()
                WHERE id = %s
                """,
                ("completed" if success else "failed", result_message, action_id),
            )
        conn.commit()


def load_dashboard_actions(database_url: str, guild_id: int, *, limit: int = 20) -> List[Dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT action_type, requested_by, status, result_message, created_at, processed_at
                FROM dashboard_actions
                WHERE guild_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (guild_id, limit),
            )
            rows = cur.fetchall()

    results: List[Dict[str, Any]] = []
    for row in rows:
        created_at = row["created_at"]
        processed_at = row["processed_at"]
        results.append(
            {
                "action_type": str(row["action_type"]),
                "requested_by": int(row["requested_by"]) if row["requested_by"] is not None else None,
                "status": str(row["status"]),
                "result_message": str(row["result_message"] or ""),
                "created_at": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
                "processed_at": processed_at.isoformat() if isinstance(processed_at, datetime) else None,
            }
        )
    return results

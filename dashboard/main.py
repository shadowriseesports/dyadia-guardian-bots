from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from config import DashboardSettings, load_dashboard_settings
from settings_store import (
    GuildSettings,
    enqueue_dashboard_action,
    ensure_settings_tables,
    load_dashboard_actions,
    load_guild_settings,
    load_guild_settings_audit,
    save_guild_settings,
)


DISCORD_API_BASE = "https://discord.com/api"
DISCORD_OAUTH_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
MANAGE_GUILD_PERMISSION = 0x20
ADMINISTRATOR_PERMISSION = 0x8
DEFAULT_APP_LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1494675962497859624/"
    "1494773502362783936/hokne_community_logo_realistic.png"
    "?ex=69e3d3ce&is=69e2824e&hm=7aa24fffd3c796925bc3947573334f7667874f2899c9a72d70aae32c3ee1215a&"
)
SETTINGS_FIELDS = list(GuildSettings().to_dict().keys())
BOOLEAN_FIELDS = {key for key, value in GuildSettings().to_dict().items() if isinstance(value, bool)}
CHANNEL_SETTING_FIELDS = {
    "modmail_forum_id",
    "mod_log_channel_id",
    "staff_application_channel_id",
    "server_log_channel_id",
    "invite_log_channel_id",
    "level_up_channel_id",
    "verification_log_channel_id",
    "welcome_channel_id",
    "instagram_notification_channel_id",
}
ROLE_SETTING_FIELDS = {"moderator_role_id", "admin_role_id", "verified_role_id"}
TEXT_SETTING_FIELDS = {
    "welcome_banner_url",
    "welcome_title",
    "welcome_description",
    "modmail_intro_title",
    "modmail_intro_description",
    "staff_panel_title",
    "staff_panel_description",
    "verification_title",
    "verification_description",
    "level_panel_title",
    "level_panel_description",
    "instagram_feed_url",
    "instagram_profile_name",
    "auto_react_rules",
    "server_name",
    "bot_status_text",
}
SECTION_NAV = [
    {"id": "feature-toggles", "label": "Systems"},
    {"id": "core-channels", "label": "Channels"},
    {"id": "roles-access", "label": "Roles"},
    {"id": "branding", "label": "Brand"},
    {"id": "message-copy", "label": "Copy"},
    {"id": "instagram-settings", "label": "Instagram"},
    {"id": "leveling", "label": "Leveling"},
    {"id": "anti-raid", "label": "Anti-Raid"},
]

ACTION_OPTIONS = [
    {"value": "post_staff_panel", "label": "Post Staff Panel", "needs_channel": True},
    {"value": "post_verification_panel", "label": "Post Verification Panel", "needs_channel": True},
    {"value": "post_level_panel", "label": "Post Level Panel", "needs_channel": True},
    {"value": "post_help", "label": "Post Help Overview", "needs_channel": True},
    {"value": "post_autoreact_summary", "label": "Post Auto-Reaction Summary", "needs_channel": True},
    {"value": "post_instagram_status", "label": "Post Instagram Status", "needs_channel": True},
    {"value": "post_antiraid_status", "label": "Post Anti-Raid Status", "needs_channel": True},
    {"value": "post_level_leaderboard", "label": "Post Level Leaderboard", "needs_channel": True},
    {"value": "post_invite_leaderboard", "label": "Post Invite Leaderboard", "needs_channel": True},
    {"value": "post_rank_card", "label": "Post Rank Card", "needs_channel": True, "needs_member": True},
    {"value": "post_invite_stats", "label": "Post Invite Stats", "needs_channel": True, "needs_member": True},
    {"value": "run_instagram_check", "label": "Run Instagram Check", "needs_channel": False},
    {"value": "refresh_settings", "label": "Refresh Bot Cache", "needs_channel": False},
    {"value": "antiraid_activate", "label": "Activate Anti-Raid", "needs_channel": False},
    {"value": "antiraid_deactivate", "label": "Deactivate Anti-Raid", "needs_channel": False},
    {"value": "send_custom_embed", "label": "Send Custom Embed", "needs_channel": True},
]
ACTION_OPTION_MAP = {str(option["value"]): option for option in ACTION_OPTIONS}

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app = FastAPI(title="Dyadia Guardian Dashboard")
app.add_middleware(SessionMiddleware, secret_key=load_dashboard_settings().session_secret)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def get_dashboard_settings() -> DashboardSettings:
    return load_dashboard_settings()


@app.on_event("startup")
async def startup() -> None:
    settings = get_dashboard_settings()
    ensure_settings_tables(settings.database_url)


async def discord_request(url: str, *, access_token: Optional[str] = None, bot_token: Optional[str] = None) -> Any:
    headers: Dict[str, str] = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if bot_token:
        headers["Authorization"] = f"Bot {bot_token}"

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def exchange_code_for_token(code: str, settings: DashboardSettings) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{DISCORD_API_BASE}/oauth2/token",
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return response.json()


def summarize_http_error(exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    error_code = str(payload.get("error") or "").strip()
    error_description = str(payload.get("error_description") or "").strip()
    summary = error_description or error_code or f"HTTP {response.status_code}"
    return summary.replace(" ", "_")[:80]


def is_manageable_guild(guild: Dict[str, Any]) -> bool:
    try:
        permissions = int(guild.get("permissions", "0"))
    except (TypeError, ValueError):
        return False
    return bool(permissions & ADMINISTRATOR_PERMISSION or permissions & MANAGE_GUILD_PERMISSION)


def require_login(request: Request) -> Optional[RedirectResponse]:
    if "user" not in request.session or "guilds" not in request.session:
        return RedirectResponse("/login", status_code=303)
    return None


def parse_form_to_settings(form_data: Dict[str, Any]) -> GuildSettings:
    payload: Dict[str, Any] = {}
    for field in SETTINGS_FIELDS:
        if field in BOOLEAN_FIELDS:
            payload[field] = field in form_data
        else:
            payload[field] = form_data.get(field, "")
    return GuildSettings.from_dict(payload, GuildSettings())


async def fetch_manageable_guilds(request: Request, settings: DashboardSettings) -> List[Dict[str, Any]]:
    cached_guilds = request.session.get("guilds", [])
    if not isinstance(cached_guilds, list):
        return []

    guilds = [guild for guild in cached_guilds if isinstance(guild, dict)]
    if settings.locked_guild_id:
        guilds = [guild for guild in guilds if int(guild.get("id", 0)) == settings.locked_guild_id]
    return with_guild_visuals(guilds)


async def fetch_bot_resources(guild_id: int, settings: DashboardSettings) -> Dict[str, List[Dict[str, Any]]]:
    if not settings.bot_api_token:
        return {"channels": [], "forum_channels": [], "roles": []}

    channels: List[Dict[str, Any]] = []
    roles: List[Dict[str, Any]] = []
    try:
        channels = await discord_request(f"{DISCORD_API_BASE}/guilds/{guild_id}/channels", bot_token=settings.bot_api_token)
    except Exception:
        channels = []

    try:
        roles = await discord_request(f"{DISCORD_API_BASE}/guilds/{guild_id}/roles", bot_token=settings.bot_api_token)
    except Exception:
        roles = []

    text_channels = [channel for channel in channels if int(channel.get("type", -1)) in {0, 5, 11, 15}]
    forum_channels = [channel for channel in channels if int(channel.get("type", -1)) == 15]
    roles_sorted = sorted(roles, key=lambda item: int(item.get("position", 0)), reverse=True)
    return {
        "channels": text_channels,
        "forum_channels": forum_channels,
        "roles": roles_sorted,
    }


async def fetch_bot_profile(settings: DashboardSettings) -> Optional[Dict[str, str]]:
    if not settings.bot_api_token:
        return None
    try:
        payload = await discord_request(f"{DISCORD_API_BASE}/users/@me", bot_token=settings.bot_api_token)
    except Exception:
        return None

    bot_id = str(payload.get("id") or "").strip()
    avatar_hash = str(payload.get("avatar") or "").strip()
    avatar_url = DEFAULT_APP_LOGO_URL
    if bot_id and avatar_hash:
        extension = "gif" if avatar_hash.startswith("a_") else "png"
        avatar_url = f"https://cdn.discordapp.com/avatars/{bot_id}/{avatar_hash}.{extension}?size=256"

    display_name = str(payload.get("global_name") or payload.get("username") or "Dyadia Guardian").strip() or "Dyadia Guardian"
    return {
        "id": bot_id,
        "username": str(payload.get("username") or display_name),
        "display_name": display_name,
        "avatar_url": avatar_url,
    }


def render(request: Request, template_name: str, context: Dict[str, Any]) -> HTMLResponse:
    base_context = {
        "request": request,
        "session_user": request.session.get("user"),
        "app_logo_url": DEFAULT_APP_LOGO_URL,
    }
    base_context.update(context)
    return templates.TemplateResponse(request=request, name=template_name, context=base_context)


def action_label(action_type: str) -> str:
    for option in ACTION_OPTIONS:
        if option["value"] == action_type:
            return str(option["label"])
    return action_type.replace("_", " ").title()


def truncate(value: str, limit: int = 280) -> str:
    cleaned = " ".join(value.replace("\r", "\n").split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit - 1]}..."


def build_preview_text(template: str, guild_name: str) -> str:
    preview = template
    replacements = {
        "{server}": guild_name,
        "{member}": "@PlayerOne",
        "{user}": "@PlayerOne",
        "{verified_role}": "@Verified",
        "{verify_channel}": "#verify",
        "{server_info_channel}": "#server-info",
        "{intro_channel}": "#intro",
    }
    for key, value in replacements.items():
        preview = preview.replace(key, value)
    return truncate(preview, 320)


def build_preview_cards(guild: Dict[str, Any], guild_settings: GuildSettings) -> List[Dict[str, str]]:
    guild_name = str(guild.get("name", "Your Server"))
    return [
        {
            "icon": "🎉",
            "title": guild_settings.welcome_title,
            "description": build_preview_text(guild_settings.welcome_description, guild_name),
            "tag": "Welcome",
        },
        {
            "icon": "✅",
            "title": guild_settings.verification_title,
            "description": build_preview_text(guild_settings.verification_description, guild_name),
            "tag": "Verification",
        },
        {
            "icon": "🧾",
            "title": guild_settings.staff_panel_title,
            "description": build_preview_text(guild_settings.staff_panel_description, guild_name),
            "tag": "Staff",
        },
        {
            "icon": "📈",
            "title": guild_settings.level_panel_title,
            "description": build_preview_text(guild_settings.level_panel_description, guild_name),
            "tag": "Leveling",
        },
        {
            "icon": "💬",
            "title": guild_settings.modmail_intro_title,
            "description": build_preview_text(guild_settings.modmail_intro_description, guild_name),
            "tag": "Modmail",
        },
    ]


def format_timestamp(value: Optional[str]) -> str:
    if not value:
        return "Not available"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return parsed.strftime("%b %d, %Y %I:%M %p")


def humanize_field_name(key: str) -> str:
    custom = {
        "modmail_forum_id": "Modmail forum",
        "mod_log_channel_id": "Moderation log channel",
        "staff_application_channel_id": "Staff application channel",
        "server_log_channel_id": "Server log channel",
        "invite_log_channel_id": "Invite log channel",
        "level_up_channel_id": "Level up channel",
        "verification_log_channel_id": "Verification log channel",
        "welcome_channel_id": "Welcome channel",
        "instagram_notification_channel_id": "Instagram notification channel",
        "moderator_role_id": "Moderator role",
        "admin_role_id": "Admin role",
        "verified_role_id": "Verified role",
        "bot_status_text": "Bot status text",
        "auto_react_rules": "Auto-reaction rules",
        "level_xp_increment": "Level XP increment",
        "instagram_poll_minutes": "Instagram poll minutes",
        "anti_raid_join_threshold": "Join threshold",
        "anti_raid_window_seconds": "Window seconds",
        "anti_raid_lockdown_minutes": "Lockdown minutes",
        "anti_raid_account_age_minutes": "Account age minutes",
        "anti_raid_timeout_minutes": "Timeout minutes",
    }
    if key in custom:
        return custom[key]
    label = key.replace("_", " ").strip().title()
    return label.replace(" Id", " ID").replace(" Xp ", " XP ")


def resolve_resource_name(items: List[Dict[str, Any]], resource_id: int, fallback: str) -> str:
    if resource_id <= 0:
        return "Not linked"
    for item in items:
        try:
            if int(item.get("id", 0)) == resource_id:
                name = str(item.get("name") or fallback)
                return f"{name} ({resource_id})"
        except (TypeError, ValueError):
            continue
    return str(resource_id)


def is_nonempty_setting(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value > 0
    return bool(str(value or "").strip())


def build_setup_checklist(guild_settings: GuildSettings) -> List[Dict[str, Any]]:
    items = [
        {
            "title": "Welcome experience",
            "done": guild_settings.welcome_enabled and guild_settings.welcome_channel_id > 0,
            "detail": "Enable welcome messages and set a welcome channel.",
        },
        {
            "title": "Moderation routing",
            "done": guild_settings.mod_log_enabled and guild_settings.mod_log_channel_id > 0,
            "detail": "Point moderation actions to a dedicated log channel.",
        },
        {
            "title": "Verification flow",
            "done": guild_settings.verified_role_id > 0 and guild_settings.verification_log_channel_id > 0,
            "detail": "Link a verified role and a verification log destination.",
        },
        {
            "title": "Support desk",
            "done": guild_settings.modmail_forum_id > 0,
            "detail": "Connect a forum channel for private support threads.",
        },
        {
            "title": "Growth systems",
            "done": guild_settings.leveling_enabled and guild_settings.level_up_channel_id > 0,
            "detail": "Turn on leveling and choose where milestones should post.",
        },
        {
            "title": "Anti-raid safety",
            "done": guild_settings.anti_raid_enabled and guild_settings.anti_raid_join_threshold > 0,
            "detail": "Keep raid protection enabled with a positive join threshold.",
        },
    ]
    return items


def build_system_cards(guild_settings: GuildSettings) -> List[Dict[str, str]]:
    systems = [
        (
            "Moderation",
            guild_settings.mod_log_enabled and guild_settings.mod_log_channel_id > 0,
            "Logs and staff visibility are ready." if guild_settings.mod_log_enabled and guild_settings.mod_log_channel_id > 0 else "Needs a moderation log channel or enablement.",
        ),
        (
            "Welcome",
            guild_settings.welcome_enabled and guild_settings.welcome_channel_id > 0,
            "New member onboarding is configured." if guild_settings.welcome_enabled and guild_settings.welcome_channel_id > 0 else "Connect a welcome channel to activate onboarding.",
        ),
        (
            "Verification",
            guild_settings.verified_role_id > 0 and guild_settings.verification_log_channel_id > 0,
            "Role access and verification tracking are linked." if guild_settings.verified_role_id > 0 and guild_settings.verification_log_channel_id > 0 else "Link the verified role and verification log channel.",
        ),
        (
            "Instagram",
            guild_settings.instagram_enabled and bool(guild_settings.instagram_feed_url.strip()) and guild_settings.instagram_notification_channel_id > 0,
            "Feed polling and delivery channel are connected." if guild_settings.instagram_enabled and bool(guild_settings.instagram_feed_url.strip()) and guild_settings.instagram_notification_channel_id > 0 else "Add a feed URL and notification channel to publish updates.",
        ),
        (
            "Leveling",
            guild_settings.leveling_enabled and guild_settings.level_up_channel_id > 0,
            "Progression announcements are ready to post." if guild_settings.leveling_enabled and guild_settings.level_up_channel_id > 0 else "Assign a level-up channel for visible progression updates.",
        ),
        (
            "Anti-Raid",
            guild_settings.anti_raid_enabled,
            "Automatic raid detection is active." if guild_settings.anti_raid_enabled else "Protection is currently disabled for this guild.",
        ),
    ]
    cards: List[Dict[str, str]] = []
    for title, ready, detail in systems:
        cards.append(
            {
                "title": title,
                "status": "Ready" if ready else "Needs attention",
                "tone": "good" if ready else "warn",
                "detail": detail,
            }
        )
    return cards


def build_resource_highlights(guild_settings: GuildSettings, resources: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    channels = resources["channels"]
    forums = resources["forum_channels"]
    roles = resources["roles"]
    return [
        {
            "label": "Welcome channel",
            "value": resolve_resource_name(channels, guild_settings.welcome_channel_id, "Channel"),
        },
        {
            "label": "Moderation log",
            "value": resolve_resource_name(channels, guild_settings.mod_log_channel_id, "Channel"),
        },
        {
            "label": "Support forum",
            "value": resolve_resource_name(forums, guild_settings.modmail_forum_id, "Forum"),
        },
        {
            "label": "Verified role",
            "value": resolve_resource_name(roles, guild_settings.verified_role_id, "Role"),
        },
    ]


def parse_dashboard_autoreact_rules(value: str, resources: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    parsed: List[Dict[str, str]] = []
    channels = resources["channels"]
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        raw_channel, raw_emojis = line.split("=", 1)
        channel_text = raw_channel.strip()
        if channel_text.startswith("<#") and channel_text.endswith(">"):
            channel_text = channel_text[2:-1]
        if not channel_text.isdigit():
            continue
        channel_id = int(channel_text)
        emoji_parts = [part.strip() for part in raw_emojis.split(",") if part.strip()]
        if channel_id > 0 and emoji_parts:
            parsed.append(
                {
                    "channel_id": str(channel_id),
                    "channel_name": resolve_resource_name(channels, channel_id, "Channel"),
                    "emojis": " ".join(emoji_parts),
                }
            )
    return parsed


def build_feature_coverage() -> Dict[str, List[str]]:
    return {
        "dashboard_supported": [
            "Panel posting for staff applications, verification, and leveling",
            "Auto-reaction rules through dashboard settings",
            "Instagram checks, status posting, and notifier configuration",
            "Anti-raid toggles, status posting, and threshold configuration",
            "Rank card, invite stats, and leaderboard posting through queued bot actions",
            "Custom embed sending and bot cache refresh",
        ],
        "discord_runtime": [
            "Interactive slash-command moderation like warn, mute, kick, ban, unban, and bulk clear",
            "Live modmail conversations and close actions",
            "Verification button presses and staff application submissions",
            "Ephemeral one-off responses such as /rank or /modlogs directly to staff members",
        ],
    }


def build_dashboard_summary(
    guild: Dict[str, Any],
    guild_settings: GuildSettings,
    audit_entries: List[Dict[str, Any]],
    action_history: List[Dict[str, Any]],
    resources: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    settings_payload = guild_settings.to_dict()
    enabled_toggles = sum(1 for key in BOOLEAN_FIELDS if settings_payload.get(key))
    configured_channels = sum(1 for key in CHANNEL_SETTING_FIELDS if int(settings_payload.get(key, 0) or 0) > 0)
    configured_roles = sum(1 for key in ROLE_SETTING_FIELDS if int(settings_payload.get(key, 0) or 0) > 0)
    configured_text = sum(1 for key in TEXT_SETTING_FIELDS if is_nonempty_setting(settings_payload.get(key)))
    progress_items = build_setup_checklist(guild_settings)
    autoreact_rules = parse_dashboard_autoreact_rules(guild_settings.auto_react_rules, resources)
    completed_items = sum(1 for item in progress_items if item["done"])
    readiness_score = int(round((completed_items / len(progress_items)) * 100)) if progress_items else 0

    latest_audit = audit_entries[0] if audit_entries else None
    latest_action = action_history[0] if action_history else None
    latest_change_summary = "No dashboard changes saved yet."
    if latest_audit:
        changed = list(latest_audit.get("changed_keys") or [])
        if changed:
            preview = ", ".join(humanize_field_name(key) for key in changed[:3])
            if len(changed) > 3:
                preview += f" and {len(changed) - 3} more"
            latest_change_summary = preview
        else:
            latest_change_summary = "Defaults were stored without field changes."

    last_action_summary = "No bot actions queued yet."
    if latest_action:
        last_action_summary = f"{action_label(str(latest_action['action_type']))} is {str(latest_action['status']).title()}."
        if latest_action.get("result_message"):
            last_action_summary += f" {latest_action['result_message']}"

    return {
        "guild_name": str(guild.get("name", "Server")),
        "readiness_score": readiness_score,
        "enabled_toggles": enabled_toggles,
        "configured_channels": configured_channels,
        "configured_roles": configured_roles,
        "configured_text": configured_text,
        "progress_items": progress_items,
        "completed_items": completed_items,
        "latest_change_summary": latest_change_summary,
        "latest_change_time": format_timestamp(latest_audit["created_at"]) if latest_audit else "No saves yet",
        "last_action_summary": last_action_summary,
        "last_action_time": format_timestamp(latest_action["created_at"]) if latest_action else "No actions yet",
        "system_cards": build_system_cards(guild_settings),
        "resource_highlights": build_resource_highlights(guild_settings, resources),
        "autoreact_rules": autoreact_rules,
        "autoreact_rule_count": len(autoreact_rules),
        "feature_coverage": build_feature_coverage(),
        "stats": [
            {"label": "Readiness score", "value": f"{readiness_score}%"},
            {"label": "Systems enabled", "value": f"{enabled_toggles}/{len(BOOLEAN_FIELDS)}"},
            {"label": "Channels linked", "value": f"{configured_channels}/{len(CHANNEL_SETTING_FIELDS)}"},
            {"label": "Roles linked", "value": f"{configured_roles}/{len(ROLE_SETTING_FIELDS)}"},
        ],
    }


def present_audit_entries(audit_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    presented: List[Dict[str, Any]] = []
    for entry in audit_entries:
        changed_keys = list(entry.get("changed_keys") or [])
        changed_labels = [humanize_field_name(key) for key in changed_keys]
        presented.append(
            {
                **entry,
                "changed_labels": changed_labels,
                "created_label": format_timestamp(entry.get("created_at")),
            }
        )
    return presented


def present_action_history(action_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    presented: List[Dict[str, Any]] = []
    for entry in action_history:
        status = str(entry.get("status") or "pending").lower()
        tone = "neutral"
        if status == "completed":
            tone = "good"
        elif status == "failed":
            tone = "warn"
        presented.append(
            {
                **entry,
                "label": action_label(str(entry.get("action_type") or "")),
                "status_tone": tone,
                "created_label": format_timestamp(entry.get("created_at")),
                "processed_label": format_timestamp(entry.get("processed_at")) if entry.get("processed_at") else "",
            }
        )
    return presented


def normalize_channel_id(raw_value: Any) -> int:
    cleaned = str(raw_value or "").strip()
    if not cleaned:
        return 0
    if cleaned.startswith("<#") and cleaned.endswith(">"):
        cleaned = cleaned[2:-1]
    return int(cleaned) if cleaned.isdigit() else 0


def normalize_user_id(raw_value: Any) -> int:
    cleaned = str(raw_value or "").strip()
    if not cleaned:
        return 0
    if cleaned.startswith("<@") and cleaned.endswith(">"):
        cleaned = cleaned[2:-1].lstrip("!")
    return int(cleaned) if cleaned.isdigit() else 0


def normalize_optional_text(value: Any, *, limit: int = 2000) -> str:
    cleaned = str(value or "").strip()
    return cleaned[:limit]


def build_action_payload(form: Dict[str, Any], action_type: str) -> Dict[str, Any]:
    option = ACTION_OPTION_MAP[action_type]
    payload: Dict[str, Any] = {}

    channel_id = normalize_channel_id(form.get("action_channel_id"))
    if option.get("needs_channel") and channel_id <= 0:
        raise ValueError("channel")
    if channel_id > 0:
        payload["channel_id"] = channel_id

    member_id = normalize_user_id(form.get("action_member_id"))
    if option.get("needs_member") and member_id <= 0:
        raise ValueError("member")
    if member_id > 0:
        payload["member_id"] = member_id

    if action_type == "send_custom_embed":
        message_content = normalize_optional_text(form.get("message_content"), limit=2000)
        embed_title = normalize_optional_text(form.get("embed_title"), limit=256)
        embed_description = normalize_optional_text(form.get("embed_description"), limit=4000)
        embed_color = normalize_optional_text(form.get("embed_color"), limit=32)
        embed_image_url = normalize_optional_text(form.get("embed_image_url"), limit=1200)

        if not any((message_content, embed_title, embed_description, embed_image_url)):
            raise ValueError("embed_content")

        payload.update(
            {
                "message_content": message_content,
                "embed_title": embed_title,
                "embed_description": embed_description,
                "embed_color": embed_color,
                "embed_image_url": embed_image_url,
            }
        )

    return payload


def build_guild_icon_url(guild: Dict[str, Any]) -> str:
    guild_id = str(guild.get("id") or "").strip()
    icon_hash = str(guild.get("icon") or "").strip()
    if guild_id and icon_hash:
        extension = "gif" if icon_hash.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/icons/{guild_id}/{icon_hash}.{extension}?size=256"
    return DEFAULT_APP_LOGO_URL


def with_guild_visuals(guilds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    decorated: List[Dict[str, Any]] = []
    for guild in guilds:
        entry = dict(guild)
        entry["icon_url"] = build_guild_icon_url(entry)
        decorated.append(entry)
    return decorated


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    settings = get_dashboard_settings()
    bot_profile = await fetch_bot_profile(settings)
    if "user" not in request.session:
        return render(
            request,
            "home.html",
            {
                "error": request.query_params.get("error", "").strip(),
                "reason": request.query_params.get("reason", "").strip(),
                "bot_profile": bot_profile,
            },
        )
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/login")
async def login(request: Request) -> RedirectResponse:
    settings = get_dashboard_settings()
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = urlencode(
        {
            "client_id": settings.discord_client_id,
            "response_type": "code",
            "redirect_uri": settings.discord_redirect_uri,
            "scope": "identify guilds",
            "state": state,
            "prompt": "consent",
        }
    )
    return RedirectResponse(f"{DISCORD_OAUTH_AUTHORIZE_URL}?{params}", status_code=303)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    settings = get_dashboard_settings()
    if not code or state != request.session.get("oauth_state"):
        return RedirectResponse("/?error=oauth_state", status_code=303)

    try:
        token_payload = await exchange_code_for_token(code, settings)
        access_token = token_payload["access_token"]
        user = await discord_request(f"{DISCORD_API_BASE}/users/@me", access_token=access_token)
        guilds = await discord_request(f"{DISCORD_API_BASE}/users/@me/guilds", access_token=access_token)
    except httpx.HTTPStatusError as exc:
        request.session.pop("oauth_state", None)
        reason = summarize_http_error(exc)
        return RedirectResponse(f"/?error=oauth_token&reason={reason}", status_code=303)
    except Exception:
        request.session.pop("oauth_state", None)
        return RedirectResponse("/?error=oauth_failed", status_code=303)

    request.session["user"] = user
    request.session["guilds"] = [guild for guild in guilds if is_manageable_guild(guild)]
    request.session.pop("oauth_state", None)
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    redirect = require_login(request)
    if redirect is not None:
        return redirect

    settings = get_dashboard_settings()
    guilds = await fetch_manageable_guilds(request, settings)
    bot_profile = await fetch_bot_profile(settings)
    return render(
        request,
        "dashboard.html",
        {
            "guilds": guilds,
            "locked_guild_id": settings.locked_guild_id,
            "bot_profile": bot_profile,
            "dashboard_stats": [
                {"label": "Servers ready", "value": str(len(guilds))},
                {"label": "Access level", "value": "Manage Server"},
                {"label": "Environment", "value": "Discord OAuth"},
            ],
        },
    )


@app.get("/dashboard/{guild_id}", response_class=HTMLResponse)
async def dashboard_guild(request: Request, guild_id: int) -> HTMLResponse:
    redirect = require_login(request)
    if redirect is not None:
        return redirect

    settings = get_dashboard_settings()
    guilds = await fetch_manageable_guilds(request, settings)
    guild = next((item for item in guilds if int(item["id"]) == guild_id), None)
    if guild is None:
        return RedirectResponse("/dashboard", status_code=303)

    guild_settings = load_guild_settings(settings.database_url, guild_id, GuildSettings())
    audit_entries = present_audit_entries(load_guild_settings_audit(settings.database_url, guild_id))
    action_history = present_action_history(load_dashboard_actions(settings.database_url, guild_id))
    bot_resources = await fetch_bot_resources(guild_id, settings)
    bot_profile = await fetch_bot_profile(settings)
    dashboard_summary = build_dashboard_summary(guild, guild_settings, audit_entries, action_history, bot_resources)
    return render(
        request,
        "guild.html",
        {
            "guild": guild,
            "guild_settings": guild_settings,
            "audit_entries": audit_entries,
            "action_history": action_history,
            "preview_cards": build_preview_cards(guild, guild_settings),
            "action_options": ACTION_OPTIONS,
            "text_channels": bot_resources["channels"],
            "forum_channels": bot_resources["forum_channels"],
            "roles": bot_resources["roles"],
            "bot_profile": bot_profile,
            "dashboard_summary": dashboard_summary,
            "section_nav": SECTION_NAV,
            "message": request.query_params.get("message"),
        },
    )


@app.post("/dashboard/{guild_id}/save")
async def save_dashboard_guild(request: Request, guild_id: int) -> RedirectResponse:
    redirect = require_login(request)
    if redirect is not None:
        return redirect

    settings = get_dashboard_settings()
    guilds = await fetch_manageable_guilds(request, settings)
    guild = next((item for item in guilds if int(item["id"]) == guild_id), None)
    if guild is None:
        return RedirectResponse("/dashboard", status_code=303)

    form = await request.form()
    previous = load_guild_settings(settings.database_url, guild_id, GuildSettings())
    current = parse_form_to_settings(dict(form))
    save_guild_settings(
        settings.database_url,
        guild_id,
        current,
        updated_by=int(request.session["user"]["id"]),
        previous=previous,
    )
    return RedirectResponse(f"/dashboard/{guild_id}?message=saved", status_code=303)


@app.post("/dashboard/{guild_id}/reset")
async def reset_dashboard_guild(request: Request, guild_id: int) -> RedirectResponse:
    redirect = require_login(request)
    if redirect is not None:
        return redirect

    settings = get_dashboard_settings()
    guilds = await fetch_manageable_guilds(request, settings)
    guild = next((item for item in guilds if int(item["id"]) == guild_id), None)
    if guild is None:
        return RedirectResponse("/dashboard", status_code=303)

    defaults = GuildSettings()
    previous = load_guild_settings(settings.database_url, guild_id, defaults)
    save_guild_settings(
        settings.database_url,
        guild_id,
        defaults,
        updated_by=int(request.session["user"]["id"]),
        previous=previous,
    )
    return RedirectResponse(f"/dashboard/{guild_id}?message=reset", status_code=303)


@app.post("/dashboard/{guild_id}/actions")
async def queue_dashboard_action(request: Request, guild_id: int) -> RedirectResponse:
    redirect = require_login(request)
    if redirect is not None:
        return redirect

    settings = get_dashboard_settings()
    guilds = await fetch_manageable_guilds(request, settings)
    guild = next((item for item in guilds if int(item["id"]) == guild_id), None)
    if guild is None:
        return RedirectResponse("/dashboard", status_code=303)

    form = await request.form()
    action_type = str(form.get("action_type", "")).strip()
    if action_type not in ACTION_OPTION_MAP:
        return RedirectResponse(f"/dashboard/{guild_id}?message=invalid_action", status_code=303)

    try:
        payload = build_action_payload(dict(form), action_type)
    except ValueError as exc:
        reason = str(exc)
        message = {
            "channel": "action_needs_channel",
            "member": "action_needs_member",
            "embed_content": "action_needs_embed_content",
        }.get(reason, "invalid_action")
        return RedirectResponse(f"/dashboard/{guild_id}?message={message}", status_code=303)

    enqueue_dashboard_action(
        settings.database_url,
        guild_id,
        action_type,
        requested_by=int(request.session["user"]["id"]),
        payload=payload,
    )
    return RedirectResponse(f"/dashboard/{guild_id}?message=action_queued", status_code=303)

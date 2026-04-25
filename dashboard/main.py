from __future__ import annotations

import secrets
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
SETTINGS_FIELDS = list(GuildSettings().to_dict().keys())
BOOLEAN_FIELDS = {key for key, value in GuildSettings().to_dict().items() if isinstance(value, bool)}

ACTION_OPTIONS = [
    {"value": "post_staff_panel", "label": "Post Staff Panel", "needs_channel": True},
    {"value": "post_verification_panel", "label": "Post Verification Panel", "needs_channel": True},
    {"value": "post_level_panel", "label": "Post Level Panel", "needs_channel": True},
    {"value": "run_instagram_check", "label": "Run Instagram Check", "needs_channel": False},
    {"value": "refresh_settings", "label": "Refresh Bot Cache", "needs_channel": False},
    {"value": "antiraid_activate", "label": "Activate Anti-Raid", "needs_channel": False},
    {"value": "antiraid_deactivate", "label": "Deactivate Anti-Raid", "needs_channel": False},
]

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
    return guilds


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


def render(request: Request, template_name: str, context: Dict[str, Any]) -> HTMLResponse:
    base_context = {
        "request": request,
        "session_user": request.session.get("user"),
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


def normalize_channel_id(raw_value: Any) -> int:
    cleaned = str(raw_value or "").strip()
    if not cleaned:
        return 0
    if cleaned.startswith("<#") and cleaned.endswith(">"):
        cleaned = cleaned[2:-1]
    return int(cleaned) if cleaned.isdigit() else 0


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    if "user" not in request.session:
        return render(request, "home.html", {})
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

    token_payload = await exchange_code_for_token(code, settings)
    access_token = token_payload["access_token"]
    user = await discord_request(f"{DISCORD_API_BASE}/users/@me", access_token=access_token)
    guilds = await discord_request(f"{DISCORD_API_BASE}/users/@me/guilds", access_token=access_token)
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
    return render(
        request,
        "dashboard.html",
        {
            "guilds": guilds,
            "locked_guild_id": settings.locked_guild_id,
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
    audit_entries = load_guild_settings_audit(settings.database_url, guild_id)
    action_history = load_dashboard_actions(settings.database_url, guild_id)
    bot_resources = await fetch_bot_resources(guild_id, settings)
    return render(
        request,
        "guild.html",
        {
            "guild": guild,
            "guild_settings": guild_settings,
            "audit_entries": audit_entries,
            "action_history": action_history,
            "action_label": action_label,
            "preview_cards": build_preview_cards(guild, guild_settings),
            "action_options": ACTION_OPTIONS,
            "text_channels": bot_resources["channels"],
            "forum_channels": bot_resources["forum_channels"],
            "roles": bot_resources["roles"],
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
    if action_type not in {option["value"] for option in ACTION_OPTIONS}:
        return RedirectResponse(f"/dashboard/{guild_id}?message=invalid_action", status_code=303)

    payload: Dict[str, Any] = {}
    channel_id = normalize_channel_id(form.get("action_channel_id"))
    if action_type.startswith("post_") and channel_id <= 0:
        return RedirectResponse(f"/dashboard/{guild_id}?message=action_needs_channel", status_code=303)
    if channel_id > 0:
        payload["channel_id"] = channel_id

    enqueue_dashboard_action(
        settings.database_url,
        guild_id,
        action_type,
        requested_by=int(request.session["user"]["id"]),
        payload=payload,
    )
    return RedirectResponse(f"/dashboard/{guild_id}?message=action_queued", status_code=303)

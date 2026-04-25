from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from config import DashboardSettings, load_dashboard_settings
from settings_store import GuildSettings, ensure_settings_tables, load_guild_settings, load_guild_settings_audit, save_guild_settings


DISCORD_API_BASE = "https://discord.com/api"
DISCORD_OAUTH_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
MANAGE_GUILD_PERMISSION = 0x20
ADMINISTRATOR_PERMISSION = 0x8
SETTINGS_FIELDS = list(GuildSettings().to_dict().keys())

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
    defaults = GuildSettings().to_dict()
    for field in SETTINGS_FIELDS:
        payload[field] = form_data.get(field, "")
    return GuildSettings.from_dict(payload, GuildSettings())


async def fetch_manageable_guilds(request: Request, settings: DashboardSettings) -> List[Dict[str, Any]]:
    cached_guilds = request.session.get("guilds", [])
    if isinstance(cached_guilds, list):
        return [guild for guild in cached_guilds if isinstance(guild, dict)]
    return []


async def fetch_bot_resources(guild_id: int, settings: DashboardSettings) -> Dict[str, List[Dict[str, Any]]]:
    if not settings.bot_api_token:
        return {"channels": [], "roles": []}

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

    text_channels = [
        channel for channel in channels if int(channel.get("type", -1)) in {0, 5, 11, 15}
    ]
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
    return render(request, "dashboard.html", {"guilds": guilds})


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
    bot_resources = await fetch_bot_resources(guild_id, settings)
    return render(
        request,
        "guild.html",
        {
            "guild": guild,
            "guild_settings": guild_settings,
            "audit_entries": audit_entries,
            "text_channels": bot_resources["channels"],
            "forum_channels": bot_resources["forum_channels"],
            "roles": bot_resources["roles"],
            "message": request.query_params.get("message"),
        },
    )


@app.post("/dashboard/{guild_id}/save")
async def save_dashboard_guild(
    request: Request,
    guild_id: int,
    modmail_forum_id: str = Form(""),
    mod_log_channel_id: str = Form(""),
    staff_application_channel_id: str = Form(""),
    moderator_role_id: str = Form(""),
    admin_role_id: str = Form(""),
    server_log_channel_id: str = Form(""),
    invite_log_channel_id: str = Form(""),
    level_up_channel_id: str = Form(""),
    verification_log_channel_id: str = Form(""),
    welcome_channel_id: str = Form(""),
    instagram_notification_channel_id: str = Form(""),
    verified_role_id: str = Form(""),
    welcome_banner_url: str = Form(""),
    instagram_feed_url: str = Form(""),
    instagram_profile_name: str = Form(""),
    instagram_poll_minutes: str = Form(""),
    level_xp_increment: str = Form(""),
    anti_raid_enabled: Optional[str] = Form(None),
    anti_raid_join_threshold: str = Form(""),
    anti_raid_window_seconds: str = Form(""),
    anti_raid_lockdown_minutes: str = Form(""),
    anti_raid_account_age_minutes: str = Form(""),
    anti_raid_timeout_minutes: str = Form(""),
    server_name: str = Form(""),
    bot_status_text: str = Form(""),
) -> RedirectResponse:
    redirect = require_login(request)
    if redirect is not None:
        return redirect

    settings = get_dashboard_settings()
    guilds = await fetch_manageable_guilds(request, settings)
    guild = next((item for item in guilds if int(item["id"]) == guild_id), None)
    if guild is None:
        return RedirectResponse("/dashboard", status_code=303)

    form_payload = {
        key: value
        for key, value in {
            "modmail_forum_id": modmail_forum_id,
            "mod_log_channel_id": mod_log_channel_id,
            "staff_application_channel_id": staff_application_channel_id,
            "moderator_role_id": moderator_role_id,
            "admin_role_id": admin_role_id,
            "server_log_channel_id": server_log_channel_id,
            "invite_log_channel_id": invite_log_channel_id,
            "level_up_channel_id": level_up_channel_id,
            "verification_log_channel_id": verification_log_channel_id,
            "welcome_channel_id": welcome_channel_id,
            "instagram_notification_channel_id": instagram_notification_channel_id,
            "verified_role_id": verified_role_id,
            "welcome_banner_url": welcome_banner_url,
            "instagram_feed_url": instagram_feed_url,
            "instagram_profile_name": instagram_profile_name,
            "instagram_poll_minutes": instagram_poll_minutes,
            "level_xp_increment": level_xp_increment,
            "anti_raid_enabled": anti_raid_enabled is not None,
            "anti_raid_join_threshold": anti_raid_join_threshold,
            "anti_raid_window_seconds": anti_raid_window_seconds,
            "anti_raid_lockdown_minutes": anti_raid_lockdown_minutes,
            "anti_raid_account_age_minutes": anti_raid_account_age_minutes,
            "anti_raid_timeout_minutes": anti_raid_timeout_minutes,
            "server_name": server_name,
            "bot_status_text": bot_status_text,
        }.items()
    }

    previous = load_guild_settings(settings.database_url, guild_id, GuildSettings())
    current = parse_form_to_settings(form_payload)
    save_guild_settings(
        settings.database_url,
        guild_id,
        current,
        updated_by=int(request.session["user"]["id"]),
        previous=previous,
    )
    return RedirectResponse(f"/dashboard/{guild_id}?message=saved", status_code=303)

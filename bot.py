from __future__ import annotations

import asyncio
import html
import json
import logging
import random
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request
from xml.etree import ElementTree as ET

import discord
import psycopg
from discord import app_commands
from discord.ext import commands, tasks

from config import Settings, load_settings


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
LOGGER = logging.getLogger("dyadia_guardian")

NO_PERMISSION = "You do not have permission to use this command."
INVALID_DURATION = "Invalid duration format. Use values like 10m, 1h, or 1d."
MODMAIL_THREAD_RE = re.compile(r"^modmail-(?P<user_id>\d+)$")
MAX_TIMEOUT_DAYS = 28
MODMAIL_COOLDOWN_SECONDS = 60
MODMAIL_INACTIVITY_HOURS = 72
DM_INTRO_COOLDOWN_SECONDS = 15
DEFAULT_THUMBNAIL_URL = (
    "https://cdn.discordapp.com/attachments/1494675962497859624/"
    "1494773502362783936/hokne_community_logo_realistic.png"
    "?ex=69e3d3ce&is=69e2824e&hm=7aa24fffd3c796925bc3947573334f7667874f2899c9a72d70aae32c3ee1215a&"
)
DEFAULT_WELCOME_BANNER_URL = (
    "https://cdn.discordapp.com/attachments/1304134025245364265/"
    "1490450142619242556/WELCOMEHOKNE_BANNEF_DC.png"
    "?ex=69eb2b9d&is=69e9da1d&hm=2e3160662ba66f477d6e690dab45e3b786e2a2ec82a33f51c20dc1318cb1b80c&"
)
BRAND_FOOTER = "Dyadia Guardian of HOK | NE India"
QOTD_ROLE_NAME = "❓QOTD"
LEVEL_DATA_PATH = Path("level_data.json")
INVITE_DATA_PATH = Path("invite_data.json")
AUTOREACT_DATA_PATH = Path("autoreact_data.json")
NO_LINK_DATA_PATH = Path("no_link_channels.json")
INSTAGRAM_STATE_PATH = Path("instagram_state.json")
LEVEL_XP_GAIN_MIN = 15
LEVEL_XP_GAIN_MAX = 25
LEADERBOARD_LIMIT = 10
INSTAGRAM_STATE_LIMIT = 200
INSTAGRAM_REQUEST_TIMEOUT_SECONDS = 20
XML_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
}
URL_RE = re.compile(
    r"(?i)\b(?:https?://|www\.|discord\.gg/|discord(?:app)?\.com/invite/)\S+"
)
LEVEL_REWARD_ROLES = [
    (1, "Battlefield Recruit [lvl 1]"),
    (50, "Rising Warrior [lvl 50]"),
    (100, "Elite Fighter [lvl 100]"),
    (200, "King's Knight [lvl 200]"),
    (300, "Dragon Knight [lvl 300]"),
    (400, "Realm Conqueror [lvl 400]"),
    (500, "Supreme Conqueror [lvl 500]"),
    (600, "Rising Legend [lvl 600]"),
    (700, "Legendary Warlord [lvl 700]"),
    (800, "Mythic Champion [lvl 800]"),
    (900, "Celestial Hero [lvl 900]"),
    (950, "Celestial Emperor [lvl 950]"),
    (990, "Divine Sovereign [lvl 990]"),
    (1000, "King of Kings [lvl 1000]"),
]


@dataclass
class ModmailSession:
    user_id: int
    thread_id: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message_count: int = 0


@dataclass
class ModLogEntry:
    action: str
    user_id: int
    moderator_id: int
    reason: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_text: Optional[str] = None


@dataclass
class StaffApplicationDraft:
    selected_role: str
    motivation: str = ""
    relevant_experience: str = ""
    core_competencies: str = ""
    situational_assessment: str = ""
    role_specific_responsibilities: str = ""
    activity_and_availability: str = ""
    decision_making_and_judgment: str = ""
    commitment_and_declaration: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AntiRaidState:
    enabled: bool
    join_events: Deque[datetime] = field(default_factory=deque)
    lockdown_until: Optional[datetime] = None
    manual_lockdown: bool = False
    last_trigger_count: int = 0


@dataclass
class LevelProgress:
    xp: int = 0
    messages: int = 0
    last_message_at: Optional[datetime] = None


@dataclass
class InviteSnapshot:
    code: str
    uses: int
    inviter_id: Optional[int] = None
    channel_id: Optional[int] = None


@dataclass
class AutoReactionConfig:
    emojis: List[str] = field(default_factory=list)


@dataclass
class InstagramFeedEntry:
    entry_id: str
    title: str
    link: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    published_at: Optional[datetime] = None
    is_reel: bool = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_duration(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if days:
        return f"{days} day{'s' if days != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if minutes:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def parse_duration(value: str) -> Optional[timedelta]:
    match = re.fullmatch(r"(\d+)([smhd])", value.strip().lower())
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    return {
        "s": timedelta(seconds=amount),
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def xp_for_level(level: int, xp_increment: int) -> int:
    if level <= 0:
        return 0
    return xp_increment * level * (level + 1) // 2


def level_from_xp(xp: int, xp_increment: int) -> int:
    level = 0
    while xp_for_level(level + 1, xp_increment) <= xp:
        level += 1
    return level


def get_reward_role_name(level: int) -> Optional[str]:
    reward_name: Optional[str] = None
    for required_level, role_name in LEVEL_REWARD_ROLES:
        if level >= required_level:
            reward_name = role_name
        else:
            break
    return reward_name


def find_reward_role(guild: discord.Guild, role_name: str, required_level: int) -> Optional[discord.Role]:
    exact_match = discord.utils.get(guild.roles, name=role_name)
    if exact_match is not None:
        return exact_match

    level_marker = f"[lvl {required_level}]".lower()
    normalized_target = role_name.lower()
    for role in guild.roles:
        normalized_name = role.name.lower()
        if normalized_name == normalized_target:
            return role
        if level_marker in normalized_name:
            return role
    return None


def is_reward_role(role: discord.Role) -> bool:
    normalized_name = role.name.lower()
    for required_level, role_name in LEVEL_REWARD_ROLES:
        if normalized_name == role_name.lower():
            return True
        if f"[lvl {required_level}]".lower() in normalized_name:
            return True
    return False


def make_embed(
    title: str,
    description: str,
    color: discord.Color,
    *,
    footer: str = BRAND_FOOTER,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color, timestamp=utc_now())
    embed.set_footer(text=footer)
    embed.set_thumbnail(url=DEFAULT_THUMBNAIL_URL)
    return embed


def truncate_text(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def slugify_text(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    cleaned = re.sub(r"[^\w\s-]", "", normalized, flags=re.UNICODE)
    collapsed = re.sub(r"[-\s]+", "-", cleaned).strip("-")
    return collapsed.lower()


def normalize_optional_text(value: str) -> Optional[str]:
    cleaned = value.strip()
    return cleaned or None


def parse_embed_color(value: str) -> Optional[discord.Color]:
    cleaned = value.strip().lower().removeprefix("#")
    if not cleaned:
        return discord.Color.blurple()
    if not re.fullmatch(r"[0-9a-f]{6}", cleaned):
        return None
    return discord.Color(int(cleaned, 16))


def is_valid_image_url(value: str) -> bool:
    return bool(re.fullmatch(r"https?://\S+", value.strip(), re.IGNORECASE))


def strip_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    collapsed = re.sub(r"\s+", " ", html.unescape(without_tags)).strip()
    return collapsed


TOKEN_REFERENCE_RE = re.compile(r"\{([#@&])([^{}]+)\}")


class OpenModmailView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Open Modmail",
                style=discord.ButtonStyle.primary,
                custom_id="modmail:open",
            )
        )


class CloseModmailView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Close Modmail",
                style=discord.ButtonStyle.danger,
                custom_id="modmail:close",
            )
        )


class VerificationView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="HOK Dyadia Verification",
                style=discord.ButtonStyle.success,
                custom_id="verification:start",
            )
        )


class StaffApplicationPageOneModal(discord.ui.Modal, title="Staff Application 1/2"):
    motivation = discord.ui.TextInput(
        label="1. Motivation",
        style=discord.TextStyle.paragraph,
        placeholder="Briefly explain why you are applying for this role.",
        max_length=1000,
    )
    relevant_experience = discord.ui.TextInput(
        label="2. Relevant Experience",
        style=discord.TextStyle.paragraph,
        placeholder="Share your moderation or support experience, platforms, and responsibilities.",
        max_length=1000,
    )
    core_competencies = discord.ui.TextInput(
        label="3. Core Competencies",
        style=discord.TextStyle.paragraph,
        placeholder="Share communication, conflict resolution, rule enforcement, and problem-solving skills.",
        max_length=1000,
    )
    situational_assessment = discord.ui.TextInput(
        label="4. Situational Assessment",
        style=discord.TextStyle.paragraph,
        placeholder="How would you handle violations, arguments, and unfair-treatment complaints?",
        max_length=1000,
    )
    role_specific_responsibilities = discord.ui.TextInput(
        label="5. Role Responsibilities",
        style=discord.TextStyle.paragraph,
        placeholder="Explain how you would handle the duties of the role you selected.",
        max_length=1000,
    )

    def __init__(self, bot: "DyadiaGuardianBot", user_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        draft = self.bot.staff_application_drafts.get(self.user_id)
        if draft is None:
            await interaction.response.send_message(
                "Your application session expired. Please start again from the panel.",
                ephemeral=True,
            )
            return

        draft.motivation = self.motivation.value
        draft.relevant_experience = self.relevant_experience.value
        draft.core_competencies = self.core_competencies.value
        draft.situational_assessment = self.situational_assessment.value
        draft.role_specific_responsibilities = self.role_specific_responsibilities.value
        await interaction.response.send_message(
            "Page 1 saved. Press `Open Final Page` to finish your application.",
            view=StaffApplicationContinueView(interaction.user.id, 2),
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.exception("Staff application page 1 failed for %s", interaction.user, exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("The application form failed. Please try again.", ephemeral=True)
        else:
            await interaction.response.send_message("The application form failed. Please try again.", ephemeral=True)


class StaffApplicationPageTwoModal(discord.ui.Modal, title="Staff Application 2/2"):
    activity_and_availability = discord.ui.TextInput(
        label="6. Availability",
        style=discord.TextStyle.paragraph,
        placeholder="Share daily hours, peak times, and how quickly you can respond to urgent issues.",
        max_length=1000,
    )
    decision_making_and_judgment = discord.ui.TextInput(
        label="7. Judgment",
        style=discord.TextStyle.paragraph,
        placeholder="Give an example of a quick decision in a difficult situation and its outcome.",
        max_length=1000,
    )
    commitment_and_declaration = discord.ui.TextInput(
        label="8. Commitment",
        style=discord.TextStyle.paragraph,
        placeholder="Confirm professionalism, 3-month commitment, and that your application is accurate.",
        max_length=1000,
    )

    def __init__(self, bot: "DyadiaGuardianBot", user_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        draft = self.bot.staff_application_drafts.get(self.user_id)
        if draft is None:
            await interaction.response.send_message(
                "Your application session expired. Please start again from the panel.",
                ephemeral=True,
            )
            return

        draft.activity_and_availability = self.activity_and_availability.value
        draft.decision_making_and_judgment = self.decision_making_and_judgment.value
        draft.commitment_and_declaration = self.commitment_and_declaration.value
        await self.bot.submit_staff_application(interaction, draft)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.exception("Staff application page 2 failed for %s", interaction.user, exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("The application form failed. Please try again.", ephemeral=True)
        else:
            await interaction.response.send_message("The application form failed. Please try again.", ephemeral=True)


class StaffApplicationView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Community Moderator",
                style=discord.ButtonStyle.success,
                custom_id="staff_application:community",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Support Moderator",
                style=discord.ButtonStyle.primary,
                custom_id="staff_application:support",
            )
        )


class StaffApplicationContinueView(discord.ui.View):
    def __init__(self, user_id: int, next_page: int) -> None:
        super().__init__(timeout=900)
        label = "Open Final Page" if next_page == 2 else f"Open Page {next_page}"
        self.add_item(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success,
                custom_id=f"staff_application:continue:{next_page}:{user_id}",
            )
        )


class EmbedBuilderModal(discord.ui.Modal, title="Embed Builder"):
    message_content = discord.ui.TextInput(
        label="Message Content",
        style=discord.TextStyle.paragraph,
        placeholder="Use {#channel}, {&role}, {@member} if needed.",
        required=False,
        max_length=2000,
    )
    embed_title = discord.ui.TextInput(
        label="Embed Title",
        placeholder="Optional embed title",
        required=False,
        max_length=256,
    )
    embed_description = discord.ui.TextInput(
        label="Embed Description",
        style=discord.TextStyle.paragraph,
        placeholder="Main embed content. Mentions: {#channel} {&role} {@member}",
        required=False,
        max_length=4000,
    )
    embed_color = discord.ui.TextInput(
        label="Embed Color",
        placeholder="Hex color like #5865F2",
        required=False,
        default="#5865F2",
        max_length=7,
    )
    image_url = discord.ui.TextInput(
        label="Image URL",
        placeholder="Optional https:// image URL",
        required=False,
        max_length=500,
    )

    def __init__(self, bot: "DyadiaGuardianBot", target_channel: discord.TextChannel) -> None:
        super().__init__()
        self.bot = bot
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        content = normalize_optional_text(self.message_content.value)
        title = normalize_optional_text(self.embed_title.value)
        description = normalize_optional_text(self.embed_description.value)
        image_url = normalize_optional_text(self.image_url.value)
        color = parse_embed_color(self.embed_color.value)

        if color is None:
            await interaction.response.send_message(
                "Please use a valid hex color like `#5865F2`.",
                ephemeral=True,
            )
            return

        if image_url is not None and not is_valid_image_url(image_url):
            await interaction.response.send_message(
                "Please use a valid `http://` or `https://` image URL.",
                ephemeral=True,
            )
            return

        if content is None and title is None and description is None and image_url is None:
            await interaction.response.send_message(
                "Add some message content or embed content before sending.",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "This embed builder can only be used inside a server.",
                ephemeral=True,
            )
            return

        content = self.bot.resolve_embed_references(interaction.guild, content)
        title = self.bot.resolve_embed_references(interaction.guild, title)
        description = self.bot.resolve_embed_references(interaction.guild, description)

        embed: Optional[discord.Embed] = None
        if title is not None or description is not None or image_url is not None:
            embed = discord.Embed(
                title=title,
                description=description,
                color=color,
                timestamp=utc_now(),
            )
            embed.set_footer(text=BRAND_FOOTER)
            if image_url is not None:
                embed.set_image(url=image_url)

        try:
            send_kwargs = {"content": content}
            if embed is not None:
                send_kwargs["embed"] = embed
            send_kwargs["allowed_mentions"] = discord.AllowedMentions(
                users=False,
                roles=False,
                everyone=False,
            )
            await self.target_channel.send(**send_kwargs)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I do not have permission to send messages in {self.target_channel.mention}.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            LOGGER.exception("Failed to send embed builder message to channel %s", self.target_channel.id)
            await interaction.response.send_message(
                "I could not send that embed right now. Please try again.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Embed sent in {self.target_channel.mention}.",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.exception("Embed builder modal failed for %s", interaction.user, exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("The embed builder failed. Please try again.", ephemeral=True)
        else:
            await interaction.response.send_message("The embed builder failed. Please try again.", ephemeral=True)


class DyadiaGuardianBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        intents.members = True
        intents.message_content = True
        intents.dm_messages = True
        intents.voice_states = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )

        self.settings = settings
        self.modmail_sessions: Dict[int, ModmailSession] = {}
        self.modmail_cooldowns: Dict[int, datetime] = {}
        self.dm_intro_cooldowns: Dict[int, datetime] = {}
        self.staff_application_drafts: Dict[int, StaffApplicationDraft] = {}
        self.mod_logs: List[ModLogEntry] = []
        self.anti_raid_states: Dict[int, AntiRaidState] = {}
        self.level_data: Dict[int, Dict[int, LevelProgress]] = {}
        self.invite_counts: Dict[int, Dict[int, int]] = {}
        self.invite_cache: Dict[int, Dict[str, InviteSnapshot]] = {}
        self.autoreact_configs: Dict[int, Dict[int, AutoReactionConfig]] = {}
        self.no_link_channels: Dict[int, set[int]] = {}
        self.instagram_seen_ids: set[str] = set()
        self.instagram_seen_order: List[str] = []
        self.instagram_last_checked_at: Optional[datetime] = None
        self.instagram_last_success_at: Optional[datetime] = None
        self.instagram_last_error: Optional[str] = None
        self.uses_postgres = bool(self.settings.database_url)
        self.modmail_view = OpenModmailView()
        self.close_modmail_view = CloseModmailView()
        self.verification_view = VerificationView()
        self.staff_application_view = StaffApplicationView()

    async def setup_hook(self) -> None:
        if self.uses_postgres:
            await asyncio.to_thread(self.ensure_postgres_schema)
        await self.load_level_data()
        await self.load_invite_data()
        await self.load_autoreact_data()
        await self.load_no_link_data()
        await self.load_instagram_state()
        self.register_commands()
        self.add_view(self.modmail_view)
        self.add_view(self.close_modmail_view)
        self.add_view(self.verification_view)
        self.add_view(self.staff_application_view)
        self.cleanup_inactive_modmail.start()
        self.instagram_feed_loop.change_interval(minutes=self.settings.instagram_poll_minutes)
        if self.instagram_notifications_enabled():
            self.instagram_feed_loop.start()

    def ensure_postgres_schema(self) -> None:
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS mod_logs (
                            id BIGSERIAL PRIMARY KEY,
                            guild_id BIGINT NOT NULL,
                            user_id BIGINT NOT NULL,
                            moderator_id BIGINT NOT NULL,
                            action TEXT NOT NULL,
                            reason TEXT NOT NULL,
                            duration_text TEXT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS autoreact_configs (
                            guild_id BIGINT NOT NULL,
                            channel_id BIGINT NOT NULL,
                            emojis TEXT[] NOT NULL,
                            PRIMARY KEY (guild_id, channel_id)
                        )
                        """
                    )
                conn.commit()
        except Exception:
            LOGGER.exception("Failed to ensure PostgreSQL schema.")

    async def on_ready(self) -> None:
        synced = await self.tree.sync()
        if self.user is not None:
            activity = discord.CustomActivity(name=self.settings.bot_status_text)
            await self.change_presence(status=discord.Status.idle, activity=activity)
        LOGGER.info("Bot online as %s (%s)", self.user, self.user.id if self.user else "unknown")
        LOGGER.info("Synced %s application commands", len(synced))
        await self.validate_runtime_configuration()
        await self.refresh_invite_caches()

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        LOGGER.exception("Application command failed", exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("An unexpected error occurred while running that command.", ephemeral=True)
            else:
                await interaction.response.send_message("An unexpected error occurred while running that command.", ephemeral=True)
        except discord.HTTPException:
            LOGGER.warning("Could not send command error response to %s", interaction.user)

    async def on_error(self, event_method: str, /, *args, **kwargs) -> None:
        LOGGER.exception("Unhandled Discord event error in %s", event_method)

    async def on_message(self, message: discord.Message) -> None:
        if isinstance(message.channel, discord.DMChannel):
            if message.author.bot:
                return
            LOGGER.info("DM received from %s (%s): %s", message.author, message.author.id, message.content or "[no text]")
            await self.handle_user_dm(message)
            return

        if message.guild is not None:
            await self.handle_autoreactions(message)

        if message.author.bot:
            return

        if message.guild is not None and isinstance(message.author, discord.Member):
            if await self.handle_no_link_message(message):
                return

        if isinstance(message.channel, discord.Thread):
            await self.handle_moderator_reply(message)

        if message.guild is not None and isinstance(message.author, discord.Member):
            await self.handle_leveling_message(message)

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild is None:
            return
        invite_info = await self.track_member_invite(member)
        await self.log_member_join(member)
        if invite_info is not None:
            await self.log_invite_join(member, invite_info)
        await self.handle_anti_raid_join(member)
        await self.sync_level_reward_role(member, announce=False)
        await self.send_welcome_message(member)

    async def on_member_remove(self, member: discord.Member) -> None:
        await self.log_member_leave(member)

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        await self.log_member_profile_update(before, after)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        await self.log_voice_state_update(member, before, after)

    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        await self.log_member_ban(guild, user)

    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        await self.log_member_unban(guild, user)

    async def on_message_delete(self, message: discord.Message) -> None:
        await self.log_message_delete(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        await self.log_message_edit(before, after)

    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self.log_channel_event("Channel Created", channel, discord.Color.green())

    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        await self.log_channel_update(before, after)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self.log_channel_event("Channel Deleted", channel, discord.Color.red())

    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self.log_role_event("Role Created", role, discord.Color.green())

    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self.log_role_event("Role Deleted", role, discord.Color.red())

    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        await self.log_role_update(before, after)

    async def on_guild_emojis_update(
        self,
        guild: discord.Guild,
        before: List[discord.Emoji],
        after: List[discord.Emoji],
    ) -> None:
        await self.log_emoji_update(guild, before, after)

    async def on_invite_create(self, invite: discord.Invite) -> None:
        await self.log_invite_create(invite)
        if invite.guild is not None:
            await self.cache_guild_invites(invite.guild)

    async def on_invite_delete(self, invite: discord.Invite) -> None:
        await self.log_invite_delete(invite)
        if invite.guild is not None:
            await self.cache_guild_invites(invite.guild)

    async def on_bulk_message_delete(self, messages: List[discord.Message]) -> None:
        await self.log_bulk_message_delete(messages)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is discord.InteractionType.component:
            custom_id = getattr(interaction.data, "get", lambda _key, _default=None: None)("custom_id")
            if custom_id == "modmail:open":
                LOGGER.info(
                    "Open Modmail button clicked by %s (%s) in %s",
                    interaction.user,
                    interaction.user.id,
                    "guild" if interaction.guild_id else "dm",
                )
                await self.open_modmail_from_button(interaction)
                return
            if custom_id == "modmail:close":
                LOGGER.info(
                    "Close Modmail button clicked by %s (%s) in %s",
                    interaction.user,
                    interaction.user.id,
                    "guild" if interaction.guild_id else "dm",
                )
                await self.close_modmail_from_button(interaction)
                return
            if custom_id == "staff_application:community":
                LOGGER.info("Community moderator application opened by %s (%s)", interaction.user, interaction.user.id)
                self.staff_application_drafts[interaction.user.id] = StaffApplicationDraft(selected_role="Community Moderator")
                await interaction.response.send_modal(StaffApplicationPageOneModal(self, interaction.user.id))
                return
            if custom_id == "staff_application:support":
                LOGGER.info("Support moderator application opened by %s (%s)", interaction.user, interaction.user.id)
                self.staff_application_drafts[interaction.user.id] = StaffApplicationDraft(selected_role="Support Moderator")
                await interaction.response.send_modal(StaffApplicationPageOneModal(self, interaction.user.id))
                return
            if custom_id and custom_id.startswith("staff_application:continue:"):
                await self.handle_staff_application_continue(interaction, custom_id)
                return
            if custom_id == "staff_application:open":
                await interaction.response.send_message(
                    "This staff application panel is outdated. Please use a newly posted panel.",
                    ephemeral=True,
                )
                return
            if custom_id == "verification:start":
                LOGGER.info("Verification button clicked by %s (%s)", interaction.user, interaction.user.id)
                await self.handle_verification_button(interaction)
                return

    def register_commands(self) -> None:
        tree = self.tree

        @tree.command(name="help", description="Show the available moderation and modmail commands")
        async def help_command(interaction: discord.Interaction) -> None:
            embed = discord.Embed(
                title="Dyadia Guardian Help",
                description="Moderation and modmail tools available in this server.",
                color=discord.Color.blurple(),
                timestamp=utc_now(),
            )
            embed.add_field(
                name="Moderation",
                value=(
                    "`/warn` warn a member\n"
                    "`/mute` timeout a member\n"
                    "`/kick` kick a member\n"
                    "`/ban` ban a member\n"
                    "`/unban` unban by user ID\n"
                    "`/addrole` add a role to a member\n"
                    "`/removerole` remove a role from a member\n"
                    "`/clear` bulk delete messages\n"
                    "`/modlogs` view in-memory moderation history"
                ),
                inline=False,
            )
            embed.add_field(
                name="Modmail",
                value="DM the bot and press `Open Modmail`. Staff can close active threads with the `Close Modmail` button.",
                inline=False,
            )
            embed.add_field(
                name="Staff Application",
                value=(
                    "`/staffapplypanel` post the staff application button panel\n"
                    "Members can press the role button to start the 2-page application form"
                ),
                inline=False,
            )
            embed.add_field(
                name="Verification",
                value="`/verificationpanel` post the HOK Dyadia verification panel.",
                inline=False,
            )
            embed.add_field(
                name="Anti-Raid",
                value=(
                    "`/antiraid status` show protection status\n"
                    "`/antiraid on` or `/antiraid off` enable or disable monitoring\n"
                    "`/antiraid activate` or `/antiraid deactivate` control raid mode manually"
                ),
                inline=False,
            )
            embed.add_field(
                name="Leveling",
                value=(
                    "`/rank` view your level card\n"
                    "`/leaderboard` view the top XP members\n"
                    "`/levelpanel` post the leveling system information panel\n"
                    "`/invites` view invite count\n"
                    "`/inviteleaderboard` view top inviters"
                ),
                inline=False,
            )
            embed.add_field(
                name="Embeds",
                value="`/embed` open a modal to build and send an embed message.",
                inline=False,
            )
            embed.add_field(
                name="Auto-Reactions",
                value=(
                    "`/autoreact activate` react to every message in a channel\n"
                    "`/autoreact deactivate` turn off auto-reactions in a channel"
                ),
                inline=False,
            )
            embed.add_field(
                name="QOTD",
                value="`/qotd` post a Question of the Day, ping the QOTD role, and open a reply thread",
                inline=False,
            )
            embed.add_field(
                name="No-Link Channels",
                value=(
                    "`/nolink activate` delete link messages in a selected channel\n"
                    "`/nolink deactivate` turn off link blocking in a selected channel"
                ),
                inline=False,
            )
            embed.add_field(
                name="Instagram",
                value=(
                    "`/instagramstatus` show Instagram notifier settings and health\n"
                    "`/instagramcheck` poll the configured Instagram feed right now"
                ),
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="warn", description="Warn a member")
        @app_commands.describe(user="Member to warn", reason="Reason for the warning")
        async def warn(interaction: discord.Interaction, user: discord.Member, reason: Optional[str] = None) -> None:
            await self.handle_warn(interaction, user, reason or "No reason provided")

        @tree.command(name="mute", description="Timeout a member")
        @app_commands.describe(user="Member to timeout", duration="Duration like 10m, 1h, 1d", reason="Reason for the timeout")
        async def mute(
            interaction: discord.Interaction,
            user: discord.Member,
            duration: str,
            reason: Optional[str] = None,
        ) -> None:
            await self.handle_mute(interaction, user, duration, reason or "No reason provided")

        @tree.command(name="kick", description="Kick a member")
        @app_commands.describe(user="Member to kick", reason="Reason for the kick")
        async def kick(interaction: discord.Interaction, user: discord.Member, reason: Optional[str] = None) -> None:
            await self.handle_kick(interaction, user, reason or "No reason provided")

        @tree.command(name="ban", description="Ban a member")
        @app_commands.describe(user="Member to ban", reason="Reason for the ban", delete_days="Delete up to 7 days of messages")
        async def ban(
            interaction: discord.Interaction,
            user: discord.Member,
            reason: Optional[str] = None,
            delete_days: app_commands.Range[int, 0, 7] = 0,
        ) -> None:
            await self.handle_ban(interaction, user, reason or "No reason provided", delete_days)

        @tree.command(name="unban", description="Unban a user by ID")
        @app_commands.describe(user_id="The user ID to unban", reason="Reason for the unban")
        async def unban(interaction: discord.Interaction, user_id: str, reason: Optional[str] = None) -> None:
            await self.handle_unban(interaction, user_id, reason or "No reason provided")

        @tree.command(name="addrole", description="Add a role to a member")
        @app_commands.describe(user="Member to update", role="Role to add", reason="Reason for adding the role")
        async def addrole(
            interaction: discord.Interaction,
            user: discord.Member,
            role: discord.Role,
            reason: Optional[str] = None,
        ) -> None:
            await self.handle_role_add(interaction, user, role, reason or "No reason provided")

        @tree.command(name="removerole", description="Remove a role from a member")
        @app_commands.describe(user="Member to update", role="Role to remove", reason="Reason for removing the role")
        async def removerole(
            interaction: discord.Interaction,
            user: discord.Member,
            role: discord.Role,
            reason: Optional[str] = None,
        ) -> None:
            await self.handle_role_remove(interaction, user, role, reason or "No reason provided")

        @tree.command(name="clear", description="Bulk delete recent messages")
        @app_commands.describe(amount="How many recent messages to remove", user="Only remove messages from this user")
        async def clear(
            interaction: discord.Interaction,
            amount: app_commands.Range[int, 1, 1000],
            user: Optional[discord.Member] = None,
        ) -> None:
            await self.handle_clear(interaction, amount, user)

        @tree.command(name="modlogs", description="Show recent in-memory moderation entries for a user")
        @app_commands.describe(user="Member to inspect")
        async def modlogs(interaction: discord.Interaction, user: discord.User) -> None:
            await self.handle_modlogs(interaction, user)

        @tree.command(name="staffapplypanel", description="Post the staff application form panel")
        @app_commands.describe(channel="Channel where the staff application panel should be posted")
        async def staffapplypanel(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await self.handle_staff_apply_panel(interaction, channel)

        @tree.command(name="verificationpanel", description="Post the HOK Dyadia verification panel")
        @app_commands.describe(channel="Channel where the verification panel should be posted")
        async def verificationpanel(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await self.handle_verification_panel(interaction, channel)

        @tree.command(name="rank", description="Show your level and XP progress")
        @app_commands.describe(user="Optional member to inspect")
        async def rank(interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
            await self.handle_rank(interaction, user)

        @tree.command(name="leaderboard", description="Show the server leveling leaderboard")
        async def leaderboard(interaction: discord.Interaction) -> None:
            await self.handle_leaderboard(interaction)

        @tree.command(name="invites", description="Show how many joins a member has invited")
        @app_commands.describe(user="Optional member to inspect")
        async def invites(interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
            await self.handle_invites(interaction, user)

        @tree.command(name="inviteleaderboard", description="Show the server invite leaderboard")
        async def inviteleaderboard(interaction: discord.Interaction) -> None:
            await self.handle_invite_leaderboard(interaction)

        @tree.command(name="levelpanel", description="Post the leveling system info panel")
        @app_commands.describe(channel="Channel where the leveling panel should be posted")
        async def levelpanel(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await self.handle_level_panel(interaction, channel)

        @tree.command(name="embed", description="Open an embed builder and send it to a channel")
        @app_commands.describe(channel="Channel where the embed should be posted")
        async def embed(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await self.handle_embed_builder(interaction, channel)

        @tree.command(name="qotd", description="Post a Question of the Day and open a reply thread")
        @app_commands.describe(
            question="The Question of the Day text",
            channel="Channel where the QOTD should be posted",
            auto_archive_hours="How long until the thread auto-archives",
        )
        async def qotd(
            interaction: discord.Interaction,
            question: str,
            channel: Optional[discord.TextChannel] = None,
            auto_archive_hours: app_commands.Range[int, 1, 168] = 24,
        ) -> None:
            await self.handle_qotd(interaction, question, channel, auto_archive_hours)

        @tree.command(name="instagramstatus", description="Show Instagram notification settings and status")
        async def instagramstatus(interaction: discord.Interaction) -> None:
            await self.handle_instagram_status(interaction)

        @tree.command(name="instagramcheck", description="Check the configured Instagram feed now")
        async def instagramcheck(interaction: discord.Interaction) -> None:
            await self.handle_instagram_check(interaction)

        autoreact = app_commands.Group(name="autoreact", description="Manage automatic message reactions")

        @autoreact.command(name="activate", description="React to every message in a channel")
        @app_commands.describe(
            emoji="One or more emojis, separated by commas, like 🔥,❤️,👍",
            channel="Channel where the bot should auto-react",
        )
        async def autoreact_activate(
            interaction: discord.Interaction,
            emoji: str,
            channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await self.handle_autoreact_activate(interaction, emoji, channel)

        @autoreact.command(name="deactivate", description="Turn off auto-reactions in a channel")
        @app_commands.describe(channel="Channel where the bot should stop auto-reacting")
        async def autoreact_deactivate(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await self.handle_autoreact_deactivate(interaction, channel)

        no_link = app_commands.Group(name="nolink", description="Manage link blocking in channels")

        @no_link.command(name="activate", description="Delete link messages in a channel")
        @app_commands.describe(channel="Channel where links should be blocked")
        async def nolink_activate(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await self.handle_no_link_activate(interaction, channel)

        @no_link.command(name="deactivate", description="Allow links again in a channel")
        @app_commands.describe(channel="Channel where link blocking should stop")
        async def nolink_deactivate(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await self.handle_no_link_deactivate(interaction, channel)

        anti_raid = app_commands.Group(name="antiraid", description="Manage anti-raid protection")

        @anti_raid.command(name="status", description="Show anti-raid status for this server")
        async def antiraid_status(interaction: discord.Interaction) -> None:
            await self.handle_antiraid_status(interaction)

        @anti_raid.command(name="on", description="Enable anti-raid monitoring")
        async def antiraid_on(interaction: discord.Interaction) -> None:
            await self.handle_antiraid_toggle(interaction, True)

        @anti_raid.command(name="off", description="Disable anti-raid monitoring")
        async def antiraid_off(interaction: discord.Interaction) -> None:
            await self.handle_antiraid_toggle(interaction, False)

        @anti_raid.command(name="activate", description="Manually activate raid mode now")
        async def antiraid_activate(interaction: discord.Interaction) -> None:
            await self.handle_antiraid_activate(interaction)

        @anti_raid.command(name="deactivate", description="Manually turn off active raid mode")
        async def antiraid_deactivate(interaction: discord.Interaction) -> None:
            await self.handle_antiraid_deactivate(interaction)

        tree.add_command(anti_raid)
        tree.add_command(autoreact)
        tree.add_command(no_link)

    def has_staff_access(self, member: discord.Member, permission: str) -> bool:
        if member.guild_permissions.administrator:
            return True

        role_ids = {role.id for role in member.roles}
        if self.settings.admin_role_id in role_ids or self.settings.moderator_role_id in role_ids:
            return True

        return getattr(member.guild_permissions, permission)

    def can_act_on_target(self, moderator: discord.Member, target: discord.Member) -> Optional[str]:
        if moderator.id == target.id:
            return "You cannot moderate yourself."
        if target.bot:
            return "You cannot use this moderation command on a bot."
        if target.guild.owner_id == target.id:
            return "You cannot moderate the server owner."
        if moderator.guild.owner_id != moderator.id and target.top_role >= moderator.top_role:
            return "You cannot moderate a member with an equal or higher role."
        me = target.guild.me
        if me is None:
            return "I could not verify my own server role."
        if target.top_role >= me.top_role:
            return "I cannot moderate that member because their role is higher than or equal to mine."
        return None

    def can_manage_role(self, moderator: discord.Member, role: discord.Role) -> Optional[str]:
        if role == moderator.guild.default_role:
            return "You cannot add or remove the default @everyone role."
        if role.managed:
            return "That role is managed by an integration and cannot be changed manually."
        if moderator.guild.owner_id != moderator.id and role >= moderator.top_role:
            return "You cannot manage a role that is equal to or higher than your top role."
        me = moderator.guild.me
        if me is None:
            return "I could not verify my own server role."
        if role >= me.top_role:
            return "I cannot manage that role because it is higher than or equal to my top role."
        return None

    def create_modmail_intro_embed(self) -> discord.Embed:
        return make_embed(
            "Support Desk",
            (
                f"Welcome to **{self.settings.server_name}**.\n\n"
                "If you need assistance, please use **Open Modmail** to contact the moderation team privately.\n\n"
                "This system can be used for reports, appeals, rule clarifications, or safety-related concerns.\n\n"
                "All moderator replies will be sent here in direct messages."
            ),
            discord.Color.purple(),
        )

    def create_modmail_thread_embed(self, user: discord.abc.User, reason: str) -> discord.Embed:
        embed = discord.Embed(
            title="New Modmail Thread",
            color=discord.Color.purple(),
            timestamp=utc_now(),
        )
        embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
        embed.add_field(name="Opened", value=reason, inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=BRAND_FOOTER)
        return embed

    def create_staff_application_panel_embed(self) -> discord.Embed:
        embed = make_embed(
            "Honor of Kings | Northeast India",
            (
                "**Staff Application Form**\n"
                "(Community Moderator & Support Moderator)\n\n"
                "Want to join the staff team?\n\n"
                "Press the role you want below and fill out the form in 2 pages. "
                "Your application will be sent privately to the review team."
            ),
            discord.Color.gold(),
        )
        embed.add_field(
            name="Application Sections",
            value=(
                "1. Position Applied For\n"
                "2. Relevant Experience\n"
                "3. Core Competencies\n"
                "4. Situational Assessment\n"
                "5. Role-Specific Responsibilities\n"
                "6. Activity & Availability\n"
                "7. Decision-Making & Judgment\n"
                "8. Commitment\n"
                "9. Declaration"
            ),
            inline=False,
        )
        embed.add_field(
            name="Before You Apply",
            value="Be honest, give complete answers, and keep your DMs open in case staff contact you.",
            inline=False,
        )
        return embed

    def create_verification_panel_embed(self, guild: discord.Guild) -> discord.Embed:
        verified_role = self.get_verified_role(guild)
        verified_role_text = verified_role.mention if verified_role is not None else "@Verified"
        return make_embed(
            "HOK Dyadia Verification",
            (
                "Welcome! To unlock full access to the server, simply complete a quick verification.\n\n"
                "**How It Works**\n"
                "- Tap the HOK Dyadia Verification button below\n"
                "- Verification will be completed instantly\n\n"
                "**After Verification**\n"
                f"- You will receive the {verified_role_text} role\n"
                "- Full access to all channels and features will be unlocked\n\n"
                "**Note**\n"
                "- Do not spam the button\n"
                "- Contact staff if you face any issues\n\n"
                "Tap the button below to get verified.\n\n"
                "Quick | Simple | Secure"
            ),
            discord.Color.green(),
            footer="Honor Of Kings | Northeast India - Verification",
        )

    def get_verified_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        if self.settings.verified_role_id:
            role = guild.get_role(self.settings.verified_role_id)
            if role is not None:
                return role

        return discord.utils.get(guild.roles, name="Verified")

    def find_text_channel_by_name(self, guild: discord.Guild, channel_name: str) -> Optional[discord.TextChannel]:
        normalized_target = channel_name.strip().lower().lstrip("#")
        for channel in guild.text_channels:
            if channel.name.lower() == normalized_target:
                return channel
        return None

    def format_channel_reference(self, guild: discord.Guild, channel_name: str) -> str:
        channel = self.find_text_channel_by_name(guild, channel_name)
        return channel.mention if channel is not None else f"#{channel_name.lstrip('#')}"

    def find_role_by_name(self, guild: discord.Guild, role_name: str) -> Optional[discord.Role]:
        normalized_target = role_name.strip().lower().lstrip("@&")
        for role in guild.roles:
            if role.name.lower() == normalized_target:
                return role
        return None

    def find_member_reference(self, guild: discord.Guild, member_text: str) -> Optional[discord.Member]:
        cleaned = member_text.strip().lstrip("@")
        if cleaned.isdigit():
            return guild.get_member(int(cleaned))

        normalized_target = cleaned.lower()
        for member in guild.members:
            if member.display_name.lower() == normalized_target or member.name.lower() == normalized_target:
                return member
        return None

    def resolve_embed_references(self, guild: discord.Guild, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        def replace_token(match: re.Match[str]) -> str:
            token_type = match.group(1)
            token_value = match.group(2).strip()

            if token_type == "#":
                channel = self.find_text_channel_by_name(guild, token_value)
                return channel.mention if channel is not None else match.group(0)
            if token_type == "&":
                role = self.find_role_by_name(guild, token_value)
                return role.mention if role is not None else match.group(0)

            member = self.find_member_reference(guild, token_value)
            return member.mention if member is not None else match.group(0)

        return TOKEN_REFERENCE_RE.sub(replace_token, value)

    async def get_welcome_channel(self) -> Optional[discord.TextChannel]:
        if not self.settings.welcome_channel_id:
            return None
        channel = self.get_channel(self.settings.welcome_channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self.settings.welcome_channel_id)
            except discord.HTTPException:
                LOGGER.exception("Could not fetch welcome channel %s", self.settings.welcome_channel_id)
                return None
        if isinstance(channel, discord.TextChannel):
            return channel
        LOGGER.warning("Configured welcome channel is not a text channel: %s", self.settings.welcome_channel_id)
        return None

    def create_welcome_embed(self, member: discord.Member) -> discord.Embed:
        verified_role = self.get_verified_role(member.guild)
        verified_role_text = verified_role.mention if verified_role is not None else "@Verified"
        verify_channel = self.format_channel_reference(member.guild, "verify")
        server_info_channel = self.format_channel_reference(member.guild, "server-info")
        intro_channel = self.format_channel_reference(member.guild, "intro")

        embed = discord.Embed(
            title=f"Welcome to {member.guild.name}",
            color=discord.Color.green(),
            timestamp=utc_now(),
        )
        embed.description = (
            f"Hey {member.mention}! Glad to have you here!\n\n"
            "**Verification Required**\n"
            "Before accessing all channels, please complete verification.\n\n"
            f"Go to {verify_channel} and tap the **HOK Dyadia Verification** button.\n"
            f"After completing it, you will automatically receive the {verified_role_text} role and unlock the server.\n\n"
            "Start here:\n"
            f"1. Verify yourself in {verify_channel}\n"
            f"2. Read {server_info_channel} to understand the rules\n"
            f"3. Introduce yourself in {intro_channel}\n"
            "4. Jump into chats and start making teammates!\n\n"
            "Let's build the strongest Honor of Kings community in Northeast India."
        )
        embed.set_footer(text=BRAND_FOOTER)
        embed.set_thumbnail(url=DEFAULT_THUMBNAIL_URL)
        embed.set_image(url=self.settings.welcome_banner_url or DEFAULT_WELCOME_BANNER_URL)
        return embed

    async def send_welcome_message(self, member: discord.Member) -> None:
        channel = await self.get_welcome_channel()
        if channel is None:
            return
        await channel.send(
            embed=self.create_welcome_embed(member),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

    def create_staff_application_embed(
        self,
        user: discord.abc.User,
        draft: StaffApplicationDraft,
        guild: Optional[discord.Guild],
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"Staff Application - {draft.selected_role}",
            color=discord.Color.gold(),
            timestamp=utc_now(),
        )
        embed.add_field(name="Applicant", value=f"{user} ({user.id})", inline=False)
        embed.add_field(name="Server", value=guild.name if guild else "Direct Message", inline=False)
        embed.add_field(name="1. Position Applied For", value=draft.selected_role, inline=False)
        embed.add_field(name="Motivation", value=draft.motivation, inline=False)
        embed.add_field(name="2. Relevant Experience", value=draft.relevant_experience, inline=False)
        embed.add_field(name="3. Core Competencies", value=draft.core_competencies, inline=False)
        embed.add_field(name="4. Situational Assessment", value=draft.situational_assessment, inline=False)
        embed.add_field(name="5. Role Responsibilities", value=draft.role_specific_responsibilities, inline=False)
        embed.add_field(name="6. Availability", value=draft.activity_and_availability, inline=False)
        embed.add_field(name="7. Decision-Making & Judgment", value=draft.decision_making_and_judgment, inline=False)
        embed.add_field(name="8. Commitment & Declaration", value=draft.commitment_and_declaration, inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=BRAND_FOOTER)
        return embed

    def create_leveling_panel_embed(self, guild: discord.Guild) -> discord.Embed:
        reward_lines = []
        for required_level, role_name in LEVEL_REWARD_ROLES:
            role = find_reward_role(guild, role_name, required_level)
            role_display = role.mention if role is not None else role_name
            reward_lines.append(f"Level {required_level} - {role_display}")

        embed = make_embed(
            "Honor Of Kings Northeast India Leveling System",
            (
                "This server uses a leveling system where members gain XP by chatting and participating in the "
                "community. As you earn XP, you level up and unlock Honor of Kings themed ranks that show your "
                "progress and activity in the server.\n\n"
                "The more active you are, the higher your level becomes."
            ),
            discord.Color.magenta(),
        )
        embed.add_field(name="Rank Progression", value="\n".join(reward_lines), inline=False)
        embed.add_field(
            name="How To Level Up",
            value=(
                "- Chat and interact with other members\n"
                "- Participate in discussions and community activities\n"
                "- Stay active in the server"
            ),
            inline=False,
        )
        embed.add_field(
            name="XP Rules",
            value=(
                f"- Gain {LEVEL_XP_GAIN_MIN}-{LEVEL_XP_GAIN_MAX} XP for active messages\n"
                "- Every qualifying message can earn XP\n"
                f"- Required XP increases by {self.settings.level_xp_increment} each level\n"
                "- Only the most dedicated members will reach Level 1000"
            ),
            inline=False,
        )
        return embed

    def create_modlog_embed(
        self,
        action: str,
        target: discord.abc.User,
        moderator: discord.abc.User,
        reason: str,
    ) -> discord.Embed:
        colors = {
            "WARN": discord.Color.yellow(),
            "MUTE": discord.Color.orange(),
            "KICK": discord.Color.red(),
            "BAN": discord.Color.dark_red(),
            "UNBAN": discord.Color.green(),
            "CLEAR": discord.Color.blurple(),
            "ANTI-RAID": discord.Color.dark_orange(),
        }
        embed = discord.Embed(
            title=f"{action} Action",
            color=colors.get(action, discord.Color.blurple()),
            timestamp=utc_now(),
        )
        embed.add_field(name="User", value=f"{target} ({target.id})", inline=False)
        embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=BRAND_FOOTER)
        embed.set_thumbnail(url=DEFAULT_THUMBNAIL_URL)
        return embed

    async def send_modlog(self, embed: discord.Embed) -> None:
        channel = self.get_channel(self.settings.mod_log_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.settings.mod_log_channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(embed=embed)
        else:
            LOGGER.warning("Configured mod log channel is not a text channel: %s", self.settings.mod_log_channel_id)

    async def get_server_log_channel(self) -> Optional[discord.TextChannel]:
        channel_id = self.settings.server_log_channel_id or self.settings.mod_log_channel_id
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.HTTPException:
                LOGGER.exception("Could not fetch server log channel %s", channel_id)
                return None
        if isinstance(channel, discord.TextChannel):
            return channel
        LOGGER.warning("Configured server log channel is not a text channel: %s", channel_id)
        return None

    async def get_invite_log_channel(self) -> Optional[discord.TextChannel]:
        channel_id = self.settings.invite_log_channel_id or self.settings.server_log_channel_id or self.settings.mod_log_channel_id
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.HTTPException:
                LOGGER.exception("Could not fetch invite log channel %s", channel_id)
                return None
        if isinstance(channel, discord.TextChannel):
            return channel
        LOGGER.warning("Configured invite log channel is not a text channel: %s", channel_id)
        return None

    async def get_verification_log_channel(self) -> Optional[discord.TextChannel]:
        channel_id = self.settings.verification_log_channel_id or self.settings.server_log_channel_id or self.settings.mod_log_channel_id
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.HTTPException:
                LOGGER.exception("Could not fetch verification log channel %s", channel_id)
                return None
        if isinstance(channel, discord.TextChannel):
            return channel
        LOGGER.warning("Configured verification log channel is not a text channel: %s", channel_id)
        return None

    async def send_server_log(self, embed: discord.Embed) -> Optional[discord.Message]:
        channel = await self.get_server_log_channel()
        if channel is not None:
            return await channel.send(embed=embed)
        return None

    async def send_invite_log(self, embed: discord.Embed) -> None:
        channel = await self.get_invite_log_channel()
        if channel is not None:
            await channel.send(embed=embed)

    async def send_verification_log(self, embed: discord.Embed) -> None:
        channel = await self.get_verification_log_channel()
        if channel is not None:
            await channel.send(embed=embed)

    def create_server_log_embed(self, title: str, color: discord.Color) -> discord.Embed:
        embed = discord.Embed(title=title, color=color, timestamp=utc_now())
        embed.set_footer(text=BRAND_FOOTER)
        embed.set_thumbnail(url=DEFAULT_THUMBNAIL_URL)
        return embed

    async def find_recent_audit_actor(
        self,
        guild: discord.Guild,
        target_id: int,
        *actions: discord.AuditLogAction,
        within_seconds: int = 10,
        attempts: int = 3,
        retry_delay: float = 1.0,
    ) -> Optional[discord.abc.User]:
        me = guild.me
        if me is None or not me.guild_permissions.view_audit_log:
            return None

        for attempt in range(attempts):
            now = utc_now()
            for action in actions:
                try:
                    async for entry in guild.audit_logs(limit=5, action=action):
                        entry_target_id = getattr(entry.target, "id", None)
                        if entry_target_id != target_id:
                            continue
                        if abs((now - entry.created_at).total_seconds()) > within_seconds:
                            continue
                        return entry.user
                except discord.Forbidden:
                    return None
                except discord.HTTPException:
                    LOGGER.warning("Could not read audit log for %s in guild %s", action, guild.id)
                    return None
            if attempt < attempts - 1:
                await asyncio.sleep(retry_delay)
        return None

    async def add_audit_actor_field(
        self,
        embed: discord.Embed,
        guild: discord.Guild,
        target_id: int,
        *actions: discord.AuditLogAction,
        within_seconds: int = 10,
    ) -> None:
        actor = await self.find_recent_audit_actor(
            guild,
            target_id,
            *actions,
            within_seconds=within_seconds,
        )
        if actor is not None:
            embed.add_field(name="Action By", value=actor.mention, inline=False)

    async def enrich_server_log_with_audit_actor(
        self,
        message: Optional[discord.Message],
        guild: discord.Guild,
        target_id: int,
        *actions: discord.AuditLogAction,
        within_seconds: int = 10,
    ) -> None:
        if message is None or not message.embeds:
            return

        actor = await self.find_recent_audit_actor(
            guild,
            target_id,
            *actions,
            within_seconds=within_seconds,
        )
        if actor is None:
            return

        embed = message.embeds[0].copy()
        if any(field.name == "Action By" for field in embed.fields):
            return
        embed.add_field(name="Action By", value=actor.mention, inline=False)

        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            LOGGER.warning("Could not update server log message %s with audit actor", message.id)

    def create_verification_log_embed(self, member: discord.Member, role: discord.Role) -> discord.Embed:
        embed = self.create_server_log_embed("Member Verified", discord.Color.green())
        embed.add_field(name="Member", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Role Granted", value=role.mention, inline=False)
        embed.add_field(name="Verified At", value=discord.utils.format_dt(utc_now(), "F"), inline=False)
        return embed

    def format_role_list(self, roles: List[discord.Role]) -> str:
        if not roles:
            return "None"
        sorted_roles = sorted(roles, key=lambda role: role.position, reverse=True)
        return truncate_text(", ".join(role.mention for role in sorted_roles), 1024)

    def format_voice_channel(self, channel: Optional[discord.abc.Connectable]) -> str:
        if channel is None:
            return "None"
        mention = getattr(channel, "mention", None)
        if mention is not None:
            return f"{mention} ({channel.name})"
        return f"{channel.name} ({channel.id})"

    def format_message_channel(self, channel: discord.abc.Messageable) -> str:
        if isinstance(channel, discord.Thread):
            return f"{channel.mention} (thread)"
        if isinstance(channel, discord.TextChannel):
            return channel.mention
        return str(channel)

    def format_channel(self, channel: discord.abc.GuildChannel) -> str:
        mention = getattr(channel, "mention", None)
        if mention is not None:
            return f"{mention} ({channel.id})"
        return f"{channel.name} ({channel.id})"

    def get_timeout_until(self, member: discord.Member) -> Optional[datetime]:
        value = getattr(member, "timed_out_until", None)
        if value is None:
            value = getattr(member, "communication_disabled_until", None)
        return value

    def is_image_attachment(self, attachment: discord.Attachment) -> bool:
        content_type = attachment.content_type or ""
        if content_type.startswith("image/"):
            return True
        return attachment.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"))

    def add_change_field(self, embed: discord.Embed, name: str, before: object, after: object) -> None:
        if before == after:
            return
        embed.add_field(name=name, value=truncate_text(f"{before} -> {after}", 1024), inline=False)

    def describe_invite(self, invite: discord.Invite) -> str:
        parts = [f"Code: `{invite.code}`"]
        if invite.channel is not None:
            parts.append(f"Channel: {self.format_channel(invite.channel)}")
        if invite.inviter is not None:
            parts.append(f"Inviter: {invite.inviter} ({invite.inviter.id})")
        max_uses = "Unlimited" if invite.max_uses == 0 else str(invite.max_uses)
        parts.append(f"Max Uses: {max_uses}")
        if invite.max_age:
            parts.append(f"Expires After: {format_duration(timedelta(seconds=invite.max_age))}")
        else:
            parts.append("Expires After: Never")
        return "\n".join(parts)

    async def log_member_join(self, member: discord.Member) -> None:
        embed = self.create_server_log_embed("Member Joined", discord.Color.green())
        embed.add_field(name="Member", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "F"), inline=False)
        embed.add_field(name="Joined Server", value=discord.utils.format_dt(utc_now(), "F"), inline=False)
        await self.send_server_log(embed)

    async def log_invite_join(self, member: discord.Member, invite_info: str) -> None:
        embed = self.create_server_log_embed("Invite Used", discord.Color.green())
        embed.add_field(name="Member", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Invite", value=truncate_text(invite_info, 1024), inline=False)
        await self.send_invite_log(embed)

    async def log_member_leave(self, member: discord.Member) -> None:
        embed = self.create_server_log_embed("Member Left", discord.Color.orange())
        embed.add_field(name="Member", value=f"{member} ({member.id})", inline=False)
        if member.joined_at is not None:
            embed.add_field(name="Joined Server", value=discord.utils.format_dt(member.joined_at, "F"), inline=False)
        if member.roles:
            role_mentions = [role.mention for role in member.roles if role != member.guild.default_role]
            if role_mentions:
                embed.add_field(name="Roles", value=truncate_text(", ".join(role_mentions), 1024), inline=False)
        await self.send_server_log(embed)

    async def log_member_profile_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick:
            embed = self.create_server_log_embed("Nickname Changed", discord.Color.blurple())
            embed.add_field(name="Member", value=f"{after} ({after.id})", inline=False)
            embed.add_field(name="Before", value=before.nick or before.name, inline=True)
            embed.add_field(name="After", value=after.nick or after.name, inline=True)
            await self.send_server_log(embed)

        before_timeout = self.get_timeout_until(before)
        after_timeout = self.get_timeout_until(after)
        if before_timeout != after_timeout:
            embed = self.create_server_log_embed("Member Timeout Updated", discord.Color.orange())
            embed.add_field(name="Member", value=f"{after} ({after.id})", inline=False)
            before_value = discord.utils.format_dt(before_timeout, "F") if before_timeout is not None else "None"
            after_value = discord.utils.format_dt(after_timeout, "F") if after_timeout is not None else "None"
            embed.add_field(name="Before", value=before_value, inline=True)
            embed.add_field(name="After", value=after_value, inline=True)
            message = await self.send_server_log(embed)
            asyncio.create_task(
                self.enrich_server_log_with_audit_actor(
                    message,
                    after.guild,
                    after.id,
                    discord.AuditLogAction.member_update,
                )
            )

        before_roles = {
            role.id: role
            for role in before.roles
            if role != before.guild.default_role
        }
        after_roles = {
            role.id: role
            for role in after.roles
            if role != after.guild.default_role
        }
        added_roles = [role for role_id, role in after_roles.items() if role_id not in before_roles]
        removed_roles = [role for role_id, role in before_roles.items() if role_id not in after_roles]
        if not added_roles and not removed_roles:
            return

        if added_roles:
            embed = self.create_server_log_embed("Member Role Added", discord.Color.green())
            embed.add_field(name="Member", value=f"{after} ({after.id})", inline=False)
            embed.add_field(name="Added", value=self.format_role_list(added_roles), inline=False)
            message = await self.send_server_log(embed)
            asyncio.create_task(
                self.enrich_server_log_with_audit_actor(
                    message,
                    after.guild,
                    after.id,
                    discord.AuditLogAction.member_role_update,
                )
            )
        if removed_roles:
            embed = self.create_server_log_embed("Member Role Removed", discord.Color.red())
            embed.add_field(name="Member", value=f"{after} ({after.id})", inline=False)
            embed.add_field(name="Removed", value=self.format_role_list(removed_roles), inline=False)
            message = await self.send_server_log(embed)
            asyncio.create_task(
                self.enrich_server_log_with_audit_actor(
                    message,
                    after.guild,
                    after.id,
                    discord.AuditLogAction.member_role_update,
                )
            )

    async def log_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel == after.channel:
            return

        if before.channel is None and after.channel is not None:
            title = "Voice Joined"
            color = discord.Color.green()
        elif before.channel is not None and after.channel is None:
            title = "Voice Left"
            color = discord.Color.orange()
        else:
            title = "Voice Moved"
            color = discord.Color.blurple()

        embed = self.create_server_log_embed(title, color)
        embed.add_field(name="Member", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Before", value=self.format_voice_channel(before.channel), inline=True)
        embed.add_field(name="After", value=self.format_voice_channel(after.channel), inline=True)
        message = await self.send_server_log(embed)
        asyncio.create_task(
            self.enrich_server_log_with_audit_actor(
                message,
                member.guild,
                member.id,
                discord.AuditLogAction.member_move,
                discord.AuditLogAction.member_disconnect,
            )
        )

    async def log_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = self.create_server_log_embed("Member Banned", discord.Color.dark_red())
        embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
        embed.add_field(name="Server", value=f"{guild.name} ({guild.id})", inline=False)
        message = await self.send_server_log(embed)
        asyncio.create_task(
            self.enrich_server_log_with_audit_actor(
                message,
                guild,
                user.id,
                discord.AuditLogAction.ban,
            )
        )

    async def log_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = self.create_server_log_embed("Member Unbanned", discord.Color.green())
        embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
        embed.add_field(name="Server", value=f"{guild.name} ({guild.id})", inline=False)
        message = await self.send_server_log(embed)
        asyncio.create_task(
            self.enrich_server_log_with_audit_actor(
                message,
                guild,
                user.id,
                discord.AuditLogAction.unban,
            )
        )

    async def log_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        channel_value = self.format_message_channel(message.channel)
        image_attachments = [attachment for attachment in message.attachments if self.is_image_attachment(attachment)]
        title = "Image Deleted" if image_attachments else "Message Deleted"
        embed = self.create_server_log_embed(title, discord.Color.red())
        embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
        embed.add_field(name="Channel", value=channel_value, inline=False)
        content = message.content.strip() if message.content else ""
        embed.add_field(name="Content", value=truncate_text(content or "[no text content]", 1024), inline=False)
        if image_attachments:
            image_names = ", ".join(attachment.filename for attachment in image_attachments)
            embed.add_field(name="Images", value=truncate_text(image_names, 1024), inline=False)
        if message.attachments:
            filenames = ", ".join(attachment.filename for attachment in message.attachments)
            embed.add_field(name="Attachments", value=truncate_text(filenames, 1024), inline=False)
        await self.send_server_log(embed)

    async def log_bulk_message_delete(self, messages: List[discord.Message]) -> None:
        if not messages:
            return
        first_message = messages[0]
        if first_message.guild is None:
            return
        channel_value = self.format_message_channel(first_message.channel)
        user_ids = {message.author.id for message in messages if message.author is not None}
        image_count = sum(
            1
            for message in messages
            for attachment in message.attachments
            if self.is_image_attachment(attachment)
        )
        embed = self.create_server_log_embed("Bulk Message Delete", discord.Color.dark_red())
        embed.add_field(name="Channel", value=channel_value, inline=False)
        embed.add_field(name="Deleted Messages", value=str(len(messages)), inline=True)
        embed.add_field(name="Unique Authors", value=str(len(user_ids)), inline=True)
        if image_count:
            embed.add_field(name="Deleted Images", value=str(image_count), inline=True)
        await self.send_server_log(embed)

    async def log_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return
        channel_value = self.format_message_channel(before.channel)
        embed = self.create_server_log_embed("Message Edited", discord.Color.gold())
        embed.add_field(name="Author", value=f"{before.author} ({before.author.id})", inline=False)
        embed.add_field(name="Channel", value=channel_value, inline=False)
        embed.add_field(name="Before", value=truncate_text(before.content or "[no text content]", 1024), inline=False)
        embed.add_field(name="After", value=truncate_text(after.content or "[no text content]", 1024), inline=False)
        await self.send_server_log(embed)

    async def log_channel_event(
        self,
        action: str,
        channel: discord.abc.GuildChannel,
        color: discord.Color,
    ) -> None:
        embed = self.create_server_log_embed(action, color)
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        embed.add_field(name="Type", value=str(channel.type), inline=False)
        category = channel.category.name if channel.category is not None else "No category"
        embed.add_field(name="Category", value=category, inline=False)
        await self.send_server_log(embed)

    async def log_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        embed = self.create_server_log_embed("Channel Updated", discord.Color.gold())
        embed.add_field(name="Channel", value=self.format_channel(after), inline=False)
        self.add_change_field(embed, "Name", before.name, after.name)
        self.add_change_field(embed, "Type", before.type, after.type)
        before_category = before.category.name if before.category is not None else "No category"
        after_category = after.category.name if after.category is not None else "No category"
        self.add_change_field(embed, "Category", before_category, after_category)
        for attr, label in (
            ("position", "Position"),
            ("slowmode_delay", "Slowmode"),
            ("nsfw", "NSFW"),
            ("bitrate", "Bitrate"),
            ("user_limit", "User Limit"),
        ):
            if hasattr(before, attr) and hasattr(after, attr):
                self.add_change_field(embed, label, getattr(before, attr), getattr(after, attr))
        if len(embed.fields) > 1:
            await self.send_server_log(embed)

    async def log_role_event(self, action: str, role: discord.Role, color: discord.Color) -> None:
        embed = self.create_server_log_embed(action, color)
        embed.add_field(name="Role", value=f"{role.mention} ({role.id})", inline=False)
        embed.add_field(name="Name", value=role.name, inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        embed.add_field(name="Mentionable", value=str(role.mentionable), inline=True)
        await self.send_server_log(embed)

    async def log_role_update(self, before: discord.Role, after: discord.Role) -> None:
        embed = self.create_server_log_embed("Role Updated", discord.Color.gold())
        embed.add_field(name="Role", value=f"{after.mention} ({after.id})", inline=False)
        self.add_change_field(embed, "Name", before.name, after.name)
        self.add_change_field(embed, "Color", before.color, after.color)
        self.add_change_field(embed, "Hoisted", before.hoist, after.hoist)
        self.add_change_field(embed, "Mentionable", before.mentionable, after.mentionable)
        self.add_change_field(embed, "Permissions", before.permissions.value, after.permissions.value)
        if len(embed.fields) > 1:
            await self.send_server_log(embed)

    async def log_emoji_update(
        self,
        guild: discord.Guild,
        before: List[discord.Emoji],
        after: List[discord.Emoji],
    ) -> None:
        before_by_id = {emoji.id: emoji for emoji in before}
        after_by_id = {emoji.id: emoji for emoji in after}

        for emoji_id, emoji in after_by_id.items():
            if emoji_id not in before_by_id:
                embed = self.create_server_log_embed("Emoji Created", discord.Color.green())
                embed.add_field(name="Emoji", value=f"{emoji} `{emoji.name}` ({emoji.id})", inline=False)
                embed.add_field(name="Server", value=f"{guild.name} ({guild.id})", inline=False)
                await self.send_server_log(embed)

        for emoji_id, emoji in before_by_id.items():
            if emoji_id not in after_by_id:
                embed = self.create_server_log_embed("Emoji Deleted", discord.Color.red())
                embed.add_field(name="Emoji", value=f"`{emoji.name}` ({emoji.id})", inline=False)
                embed.add_field(name="Server", value=f"{guild.name} ({guild.id})", inline=False)
                await self.send_server_log(embed)

        for emoji_id, before_emoji in before_by_id.items():
            after_emoji = after_by_id.get(emoji_id)
            if after_emoji is None or before_emoji.name == after_emoji.name:
                continue
            embed = self.create_server_log_embed("Emoji Name Changed", discord.Color.gold())
            embed.add_field(name="Emoji", value=f"{after_emoji} ({after_emoji.id})", inline=False)
            embed.add_field(name="Before", value=before_emoji.name, inline=True)
            embed.add_field(name="After", value=after_emoji.name, inline=True)
            await self.send_server_log(embed)

    async def log_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        embed = self.create_server_log_embed("Invite Created", discord.Color.green())
        embed.add_field(name="Invite Info", value=truncate_text(self.describe_invite(invite), 1024), inline=False)
        embed.add_field(name="Server", value=f"{invite.guild.name} ({invite.guild.id})", inline=False)
        await self.send_invite_log(embed)

    async def log_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        embed = self.create_server_log_embed("Invite Deleted", discord.Color.red())
        embed.add_field(name="Invite Info", value=truncate_text(self.describe_invite(invite), 1024), inline=False)
        embed.add_field(name="Server", value=f"{invite.guild.name} ({invite.guild.id})", inline=False)
        await self.send_invite_log(embed)

    async def log_moderator_command(
        self,
        interaction: discord.Interaction,
        action: str,
        target: discord.abc.User,
        reason: str,
    ) -> None:
        if interaction.guild is None:
            return
        embed = self.create_server_log_embed("Moderator Command", discord.Color.blurple())
        embed.add_field(name="Command", value=action, inline=True)
        embed.add_field(name="Moderator", value=f"{interaction.user} ({interaction.user.id})", inline=False)
        embed.add_field(name="Target", value=f"{target} ({target.id})", inline=False)
        embed.add_field(name="Reason/Details", value=truncate_text(reason, 1024), inline=False)
        await self.send_server_log(embed)

    async def get_staff_application_channel(self) -> Optional[discord.TextChannel]:
        channel = self.get_channel(self.settings.staff_application_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.settings.staff_application_channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        LOGGER.warning(
            "Configured staff application channel is not a text channel: %s",
            self.settings.staff_application_channel_id,
        )
        return None

    async def add_modlog(
        self,
        action: str,
        target: discord.abc.User,
        moderator: discord.abc.User,
        guild_id: Optional[int],
        reason: str,
        duration_text: Optional[str] = None,
    ) -> None:
        entry = ModLogEntry(
            action=action,
            user_id=target.id,
            moderator_id=moderator.id,
            reason=reason,
            duration_text=duration_text,
        )
        self.mod_logs.append(entry)
        if self.uses_postgres and guild_id is not None:
            await asyncio.to_thread(self.persist_modlog, guild_id, entry)

    def persist_modlog(self, guild_id: int, entry: ModLogEntry) -> None:
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS mod_logs (
                            id BIGSERIAL PRIMARY KEY,
                            guild_id BIGINT NOT NULL,
                            user_id BIGINT NOT NULL,
                            moderator_id BIGINT NOT NULL,
                            action TEXT NOT NULL,
                            reason TEXT NOT NULL,
                            duration_text TEXT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        """
                        INSERT INTO mod_logs (
                            guild_id,
                            user_id,
                            moderator_id,
                            action,
                            reason,
                            duration_text,
                            created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            guild_id,
                            entry.user_id,
                            entry.moderator_id,
                            entry.action,
                            entry.reason,
                            entry.duration_text,
                            entry.created_at,
                        ),
                    )
                conn.commit()
        except Exception:
            LOGGER.exception("Failed to persist moderation log for guild=%s user=%s", guild_id, entry.user_id)

    def load_modlogs_from_postgres(self, guild_id: int, user_id: int, *, limit: int = 10) -> List[ModLogEntry]:
        entries: List[ModLogEntry] = []
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS mod_logs (
                            id BIGSERIAL PRIMARY KEY,
                            guild_id BIGINT NOT NULL,
                            user_id BIGINT NOT NULL,
                            moderator_id BIGINT NOT NULL,
                            action TEXT NOT NULL,
                            reason TEXT NOT NULL,
                            duration_text TEXT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        """
                        SELECT action, user_id, moderator_id, reason, created_at, duration_text
                        FROM mod_logs
                        WHERE guild_id = %s AND user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (guild_id, user_id, limit),
                    )
                    for action, row_user_id, moderator_id, reason, created_at, duration_text in cur.fetchall():
                        entries.append(
                            ModLogEntry(
                                action=str(action),
                                user_id=int(row_user_id),
                                moderator_id=int(moderator_id),
                                reason=str(reason),
                                created_at=created_at if isinstance(created_at, datetime) else utc_now(),
                                duration_text=str(duration_text) if duration_text else None,
                            )
                        )
        except Exception:
            LOGGER.exception("Failed to load moderation logs from PostgreSQL for guild=%s user=%s", guild_id, user_id)
            return []
        return entries

    async def load_invite_data(self) -> None:
        self.invite_counts = await asyncio.to_thread(self._load_invite_data_sync)

    def _load_invite_data_sync(self) -> Dict[int, Dict[int, int]]:
        if self.uses_postgres:
            return self._load_invite_data_from_postgres()
        return self._load_invite_data_from_json()

    def _load_invite_data_from_json(self) -> Dict[int, Dict[int, int]]:
        loaded_data: Dict[int, Dict[int, int]] = {}
        if not INVITE_DATA_PATH.exists():
            LOGGER.info("Invite data file %s not found. A new one will be created on first tracked invite.", INVITE_DATA_PATH)
            return loaded_data

        try:
            raw = json.loads(INVITE_DATA_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Failed to load invite data from %s", INVITE_DATA_PATH)
            return loaded_data

        for guild_id, members in (raw if isinstance(raw, dict) else {}).items():
            try:
                parsed_guild_id = int(guild_id)
            except (TypeError, ValueError):
                continue
            if not isinstance(members, dict):
                continue
            guild_counts: Dict[int, int] = {}
            for user_id, count in members.items():
                try:
                    guild_counts[int(user_id)] = max(0, int(count))
                except (TypeError, ValueError):
                    continue
            loaded_data[parsed_guild_id] = guild_counts

        LOGGER.info("Loaded invite data for %s guild(s) from %s", len(loaded_data), INVITE_DATA_PATH)
        return loaded_data

    def _load_invite_data_from_postgres(self) -> Dict[int, Dict[int, int]]:
        loaded_data: Dict[int, Dict[int, int]] = {}
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS invite_counts (
                            guild_id BIGINT NOT NULL,
                            inviter_id BIGINT NOT NULL,
                            joins INTEGER NOT NULL DEFAULT 0,
                            PRIMARY KEY (guild_id, inviter_id)
                        )
                        """
                    )
                    cur.execute(
                        """
                        SELECT guild_id, inviter_id, joins
                        FROM invite_counts
                        """
                    )
                    for guild_id, inviter_id, joins in cur.fetchall():
                        guild_counts = loaded_data.setdefault(int(guild_id), {})
                        guild_counts[int(inviter_id)] = max(0, int(joins))
                conn.commit()
        except Exception:
            LOGGER.exception("Failed to load invite data from PostgreSQL.")
            return {}

        LOGGER.info("Loaded invite data for %s guild(s) from PostgreSQL", len(loaded_data))
        return loaded_data

    def save_invite_data(self) -> None:
        if self.uses_postgres:
            return
        serialized = {
            str(guild_id): {str(user_id): count for user_id, count in members.items()}
            for guild_id, members in self.invite_counts.items()
        }
        try:
            INVITE_DATA_PATH.write_text(json.dumps(serialized, indent=2), encoding="utf-8")
        except OSError:
            LOGGER.exception("Failed to save invite data to %s", INVITE_DATA_PATH)

    async def persist_invite_count(self, guild_id: int, inviter_id: int, joins: int) -> None:
        if self.uses_postgres:
            await asyncio.to_thread(self._persist_invite_count_postgres, guild_id, inviter_id, joins)
            return
        await asyncio.to_thread(self.save_invite_data)

    def _persist_invite_count_postgres(self, guild_id: int, inviter_id: int, joins: int) -> None:
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO invite_counts (guild_id, inviter_id, joins)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (guild_id, inviter_id) DO UPDATE
                        SET joins = EXCLUDED.joins
                        """,
                        (guild_id, inviter_id, joins),
                    )
                conn.commit()
        except Exception:
            LOGGER.exception("Failed to persist invite count for guild=%s inviter=%s", guild_id, inviter_id)

    def get_invite_count(self, guild_id: int, inviter_id: int) -> int:
        return self.invite_counts.setdefault(guild_id, {}).get(inviter_id, 0)

    async def increment_invite_count(self, guild_id: int, inviter_id: int) -> int:
        guild_counts = self.invite_counts.setdefault(guild_id, {})
        guild_counts[inviter_id] = guild_counts.get(inviter_id, 0) + 1
        await self.persist_invite_count(guild_id, inviter_id, guild_counts[inviter_id])
        return guild_counts[inviter_id]

    def snapshot_invite(self, invite: discord.Invite) -> InviteSnapshot:
        return InviteSnapshot(
            code=invite.code,
            uses=invite.uses or 0,
            inviter_id=invite.inviter.id if invite.inviter is not None else None,
            channel_id=invite.channel.id if invite.channel is not None else None,
        )

    async def cache_guild_invites(self, guild: discord.Guild) -> None:
        try:
            invites = await guild.invites()
        except discord.Forbidden:
            LOGGER.warning("Missing Manage Server permission to track invites in %s (%s)", guild.name, guild.id)
            self.invite_cache.setdefault(guild.id, {})
            return
        except discord.HTTPException:
            LOGGER.exception("Could not fetch invites for %s (%s)", guild.name, guild.id)
            self.invite_cache.setdefault(guild.id, {})
            return

        self.invite_cache[guild.id] = {invite.code: self.snapshot_invite(invite) for invite in invites}
        LOGGER.info("Cached %s invite(s) for %s (%s)", len(invites), guild.name, guild.id)

    async def refresh_invite_caches(self) -> None:
        for guild in self.guilds:
            await self.cache_guild_invites(guild)

    async def track_member_invite(self, member: discord.Member) -> Optional[str]:
        before_cache = self.invite_cache.get(member.guild.id, {})
        try:
            current_invites = await member.guild.invites()
        except discord.Forbidden:
            LOGGER.warning("Missing Manage Server permission to detect invite used by %s (%s)", member, member.id)
            return None
        except discord.HTTPException:
            LOGGER.exception("Could not detect invite used by %s (%s)", member, member.id)
            return None

        after_cache = {invite.code: self.snapshot_invite(invite) for invite in current_invites}
        used_invite: Optional[InviteSnapshot] = None
        highest_delta = 0
        for code, after_snapshot in after_cache.items():
            before_snapshot = before_cache.get(code)
            before_uses = before_snapshot.uses if before_snapshot is not None else after_snapshot.uses
            delta = after_snapshot.uses - before_uses
            if delta > highest_delta:
                highest_delta = delta
                used_invite = after_snapshot

        self.invite_cache[member.guild.id] = after_cache
        if used_invite is None or used_invite.inviter_id is None:
            return None

        total_joins = await self.increment_invite_count(member.guild.id, used_invite.inviter_id)
        inviter_text = f"<@{used_invite.inviter_id}>"
        channel_text = f"<#{used_invite.channel_id}>" if used_invite.channel_id is not None else "Unknown channel"
        return f"Code `{used_invite.code}` from {inviter_text} in {channel_text}\nInviter total: **{total_joins}**"

    async def load_autoreact_data(self) -> None:
        self.autoreact_configs = await asyncio.to_thread(self._load_autoreact_data_sync)

    def _load_autoreact_data_sync(self) -> Dict[int, Dict[int, AutoReactionConfig]]:
        if self.uses_postgres:
            return self._load_autoreact_data_from_postgres()
        return self._load_autoreact_data_from_json()

    def _load_autoreact_data_from_json(self) -> Dict[int, Dict[int, AutoReactionConfig]]:
        loaded_data: Dict[int, Dict[int, AutoReactionConfig]] = {}
        if not AUTOREACT_DATA_PATH.exists():
            LOGGER.info("Auto-reaction data file %s not found. A new one will be created on first activation.", AUTOREACT_DATA_PATH)
            return loaded_data

        try:
            raw = json.loads(AUTOREACT_DATA_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Failed to load auto-reaction data from %s", AUTOREACT_DATA_PATH)
            return loaded_data

        for guild_id, rules in (raw if isinstance(raw, dict) else {}).items():
            try:
                parsed_guild_id = int(guild_id)
            except (TypeError, ValueError):
                continue
            if not isinstance(rules, dict):
                continue

            parsed_configs: Dict[int, AutoReactionConfig] = {}
            for channel_id, emoji_value in rules.items():
                try:
                    parsed_channel_id = int(channel_id)
                except (TypeError, ValueError):
                    continue

                parsed_emojis: List[str] = []
                raw_emojis = emoji_value if isinstance(emoji_value, list) else [emoji_value]
                for raw_emoji in raw_emojis:
                    emoji = self.normalize_autoreact_emoji(str(raw_emoji))
                    if emoji is not None and emoji not in parsed_emojis:
                        parsed_emojis.append(emoji)

                if not parsed_emojis or parsed_channel_id <= 0:
                    continue
                parsed_configs[parsed_channel_id] = AutoReactionConfig(emojis=parsed_emojis)

            loaded_data[parsed_guild_id] = parsed_configs

        LOGGER.info("Loaded auto-reaction data for %s guild(s) from %s", len(loaded_data), AUTOREACT_DATA_PATH)
        return loaded_data

    def _load_autoreact_data_from_postgres(self) -> Dict[int, Dict[int, AutoReactionConfig]]:
        loaded_data: Dict[int, Dict[int, AutoReactionConfig]] = {}
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS autoreact_configs (
                            guild_id BIGINT NOT NULL,
                            channel_id BIGINT NOT NULL,
                            emojis TEXT[] NOT NULL,
                            PRIMARY KEY (guild_id, channel_id)
                        )
                        """
                    )
                    cur.execute(
                        """
                        SELECT guild_id, channel_id, emojis
                        FROM autoreact_configs
                        """
                    )
                    for guild_id, channel_id, raw_emojis in cur.fetchall():
                        parsed_emojis: List[str] = []
                        for raw_emoji in raw_emojis if isinstance(raw_emojis, list) else []:
                            emoji = self.normalize_autoreact_emoji(str(raw_emoji))
                            if emoji is not None and emoji not in parsed_emojis:
                                parsed_emojis.append(emoji)
                        if not parsed_emojis:
                            continue
                        guild_configs = loaded_data.setdefault(int(guild_id), {})
                        guild_configs[int(channel_id)] = AutoReactionConfig(emojis=parsed_emojis)
                conn.commit()
        except Exception:
            LOGGER.exception("Failed to load auto-reaction data from PostgreSQL.")
            return {}

        LOGGER.info("Loaded auto-reaction data for %s guild(s) from PostgreSQL", len(loaded_data))
        return loaded_data

    def save_autoreact_data(self) -> None:
        if self.uses_postgres:
            self._save_autoreact_data_to_postgres()
            return
        self._save_autoreact_data_to_json()

    def _save_autoreact_data_to_json(self) -> None:
        serialized = {
            str(guild_id): {
                str(channel_id): config.emojis
                for channel_id, config in channel_configs.items()
            }
            for guild_id, channel_configs in self.autoreact_configs.items()
        }
        try:
            AUTOREACT_DATA_PATH.write_text(json.dumps(serialized, indent=2), encoding="utf-8")
        except OSError:
            LOGGER.exception("Failed to save auto-reaction data to %s", AUTOREACT_DATA_PATH)

    def _save_autoreact_data_to_postgres(self) -> None:
        rows = [
            (guild_id, channel_id, config.emojis)
            for guild_id, channel_configs in self.autoreact_configs.items()
            for channel_id, config in channel_configs.items()
            if config.emojis
        ]
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS autoreact_configs (
                            guild_id BIGINT NOT NULL,
                            channel_id BIGINT NOT NULL,
                            emojis TEXT[] NOT NULL,
                            PRIMARY KEY (guild_id, channel_id)
                        )
                        """
                    )
                    cur.execute("DELETE FROM autoreact_configs")
                    if rows:
                        cur.executemany(
                            """
                            INSERT INTO autoreact_configs (guild_id, channel_id, emojis)
                            VALUES (%s, %s, %s)
                            """,
                            rows,
                        )
                conn.commit()
        except Exception:
            LOGGER.exception("Failed to save auto-reaction data to PostgreSQL.")

    async def persist_autoreact_data(self) -> None:
        await asyncio.to_thread(self.save_autoreact_data)

    async def handle_autoreactions(self, message: discord.Message) -> None:
        if message.guild is None:
            return

        channel_configs = self.autoreact_configs.get(message.guild.id, {})
        config = channel_configs.get(message.channel.id)
        if config is None:
            return

        for emoji in config.emojis:
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                LOGGER.warning(
                    "Failed to add auto-reaction %s in guild %s channel %s message %s",
                    emoji,
                    message.guild.id,
                    message.channel.id,
                    message.id,
                )

    async def load_no_link_data(self) -> None:
        self.no_link_channels = await asyncio.to_thread(self._load_no_link_data_sync)

    def _load_no_link_data_sync(self) -> Dict[int, set[int]]:
        loaded_data: Dict[int, set[int]] = {}
        if not NO_LINK_DATA_PATH.exists():
            LOGGER.info("No-link data file %s not found. A new one will be created on first activation.", NO_LINK_DATA_PATH)
            return loaded_data

        try:
            raw = json.loads(NO_LINK_DATA_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Failed to load no-link data from %s", NO_LINK_DATA_PATH)
            return loaded_data

        for guild_id, channels in (raw if isinstance(raw, dict) else {}).items():
            try:
                parsed_guild_id = int(guild_id)
            except (TypeError, ValueError):
                continue
            if not isinstance(channels, list):
                continue

            parsed_channels = {
                int(channel_id)
                for channel_id in channels
                if str(channel_id).isdigit() and int(channel_id) > 0
            }
            if parsed_channels:
                loaded_data[parsed_guild_id] = parsed_channels

        LOGGER.info("Loaded no-link channel data for %s guild(s) from %s", len(loaded_data), NO_LINK_DATA_PATH)
        return loaded_data

    def save_no_link_data(self) -> None:
        serialized = {
            str(guild_id): sorted(channel_ids)
            for guild_id, channel_ids in self.no_link_channels.items()
            if channel_ids
        }
        try:
            NO_LINK_DATA_PATH.write_text(json.dumps(serialized, indent=2), encoding="utf-8")
        except OSError:
            LOGGER.exception("Failed to save no-link data to %s", NO_LINK_DATA_PATH)

    async def persist_no_link_data(self) -> None:
        await asyncio.to_thread(self.save_no_link_data)

    def message_contains_blocked_link(self, content: str) -> bool:
        return bool(URL_RE.search(content))

    async def handle_no_link_message(self, message: discord.Message) -> bool:
        if message.guild is None or not isinstance(message.channel, discord.TextChannel):
            return False
        if message.author.guild_permissions.manage_messages:
            return False

        blocked_channels = self.no_link_channels.get(message.guild.id, set())
        if message.channel.id not in blocked_channels:
            return False
        if not self.message_contains_blocked_link(message.content):
            return False

        try:
            await message.delete()
        except discord.HTTPException:
            LOGGER.warning(
                "Failed to delete blocked link message in guild %s channel %s message %s",
                message.guild.id,
                message.channel.id,
                message.id,
            )
            return False

        try:
            warning = await message.channel.send(
                f"{message.author.mention} links are not allowed in this channel.",
                delete_after=8,
            )
            LOGGER.debug("Posted no-link warning message %s", warning.id)
        except discord.HTTPException:
            LOGGER.warning("Failed to send no-link warning in channel %s", message.channel.id)
        return True

    async def load_level_data(self) -> None:
        self.level_data = await asyncio.to_thread(self._load_level_data_sync)

    def _load_level_data_sync(self) -> Dict[int, Dict[int, LevelProgress]]:
        if self.uses_postgres:
            return self._load_level_data_from_postgres()
        return self._load_level_data_from_json()

    def _load_level_data_from_json(self) -> Dict[int, Dict[int, LevelProgress]]:
        loaded_data: Dict[int, Dict[int, LevelProgress]] = {}
        if not LEVEL_DATA_PATH.exists():
            LOGGER.info("Leveling data file %s not found. A new one will be created on first XP update.", LEVEL_DATA_PATH)
            return loaded_data

        try:
            raw = json.loads(LEVEL_DATA_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Failed to load leveling data from %s", LEVEL_DATA_PATH)
            return loaded_data

        guilds = raw if isinstance(raw, dict) else {}
        for guild_id, members in guilds.items():
            parsed_guild = self._parse_leveling_guild_payload(guild_id, members)
            if parsed_guild is not None:
                loaded_data[parsed_guild[0]] = parsed_guild[1]

        LOGGER.info("Loaded leveling data for %s guild(s) from %s", len(loaded_data), LEVEL_DATA_PATH)
        return loaded_data

    def _load_level_data_from_postgres(self) -> Dict[int, Dict[int, LevelProgress]]:
        loaded_data: Dict[int, Dict[int, LevelProgress]] = {}
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS level_progress (
                            guild_id BIGINT NOT NULL,
                            user_id BIGINT NOT NULL,
                            xp INTEGER NOT NULL DEFAULT 0,
                            messages INTEGER NOT NULL DEFAULT 0,
                            last_message_at TIMESTAMPTZ NULL,
                            PRIMARY KEY (guild_id, user_id)
                        )
                        """
                    )
                    cur.execute(
                        """
                        SELECT guild_id, user_id, xp, messages, last_message_at
                        FROM level_progress
                        """
                    )
                    for guild_id, user_id, xp, messages, last_message_at in cur.fetchall():
                        guild_progress = loaded_data.setdefault(int(guild_id), {})
                        guild_progress[int(user_id)] = LevelProgress(
                            xp=max(0, int(xp)),
                            messages=max(0, int(messages)),
                            last_message_at=last_message_at,
                        )
        except Exception:
            LOGGER.exception("Failed to load leveling data from PostgreSQL.")
            return {}

        LOGGER.info("Loaded leveling data for %s guild(s) from PostgreSQL", len(loaded_data))
        return loaded_data

    def _parse_leveling_guild_payload(
        self,
        guild_id: object,
        members: object,
    ) -> Optional[tuple[int, Dict[int, LevelProgress]]]:
        try:
            parsed_guild_id = int(guild_id)
        except (TypeError, ValueError):
            return None

        guild_progress: Dict[int, LevelProgress] = {}
        if not isinstance(members, dict):
            return parsed_guild_id, guild_progress

        for user_id, payload in members.items():
            try:
                parsed_user_id = int(user_id)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue

            last_message_at = None
            raw_last_message_at = payload.get("last_message_at")
            if isinstance(raw_last_message_at, str):
                try:
                    last_message_at = datetime.fromisoformat(raw_last_message_at)
                except ValueError:
                    last_message_at = None

            guild_progress[parsed_user_id] = LevelProgress(
                xp=max(0, int(payload.get("xp", 0) or 0)),
                messages=max(0, int(payload.get("messages", 0) or 0)),
                last_message_at=last_message_at,
            )

        return parsed_guild_id, guild_progress

    def save_level_data(self) -> None:
        if self.uses_postgres:
            return
        serialized: Dict[str, Dict[str, Dict[str, object]]] = {}
        for guild_id, members in self.level_data.items():
            serialized[str(guild_id)] = {}
            for user_id, progress in members.items():
                serialized[str(guild_id)][str(user_id)] = {
                    "xp": progress.xp,
                    "messages": progress.messages,
                    "last_message_at": progress.last_message_at.isoformat() if progress.last_message_at else None,
                }

        try:
            LEVEL_DATA_PATH.write_text(json.dumps(serialized, indent=2), encoding="utf-8")
        except OSError:
            LOGGER.exception("Failed to save leveling data to %s", LEVEL_DATA_PATH)

    async def persist_level_progress(self, guild_id: int, user_id: int, progress: LevelProgress) -> None:
        if self.uses_postgres:
            await asyncio.to_thread(self._persist_level_progress_postgres, guild_id, user_id, progress)
            return
        await asyncio.to_thread(self.save_level_data)

    def _persist_level_progress_postgres(self, guild_id: int, user_id: int, progress: LevelProgress) -> None:
        try:
            with psycopg.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO level_progress (guild_id, user_id, xp, messages, last_message_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (guild_id, user_id) DO UPDATE
                        SET xp = EXCLUDED.xp,
                            messages = EXCLUDED.messages,
                            last_message_at = EXCLUDED.last_message_at
                        """,
                        (guild_id, user_id, progress.xp, progress.messages, progress.last_message_at),
                    )
                conn.commit()
        except Exception:
            LOGGER.exception("Failed to persist leveling progress for guild=%s user=%s", guild_id, user_id)

    def get_level_progress(self, guild_id: int, user_id: int) -> LevelProgress:
        guild_progress = self.level_data.setdefault(guild_id, {})
        progress = guild_progress.get(user_id)
        if progress is None:
            progress = LevelProgress()
            guild_progress[user_id] = progress
        return progress

    def get_next_reward_role_name(self, level: int) -> Optional[str]:
        for required_level, role_name in LEVEL_REWARD_ROLES:
            if level < required_level:
                return role_name
        return None

    async def sync_level_reward_role(self, member: discord.Member, *, announce: bool) -> None:
        progress = self.get_level_progress(member.guild.id, member.id)
        level = level_from_xp(progress.xp, self.settings.level_xp_increment)
        eligible_role_name = get_reward_role_name(level)
        roles_to_remove = [role for role in member.roles if is_reward_role(role)]
        eligible_level = next((required_level for required_level, role_name in LEVEL_REWARD_ROLES if role_name == eligible_role_name), None)
        role_to_add = (
            find_reward_role(member.guild, eligible_role_name, eligible_level)
            if eligible_role_name is not None and eligible_level is not None
            else None
        )
        if role_to_add is not None and role_to_add in roles_to_remove:
            roles_to_remove.remove(role_to_add)

        if role_to_add is not None and member.guild.me is not None and role_to_add >= member.guild.me.top_role:
            LOGGER.warning("Cannot assign leveling role %s because it is above the bot's top role.", role_to_add.name)
            role_to_add = None

        if roles_to_remove:
            removable_roles = [role for role in roles_to_remove if member.guild.me is None or role < member.guild.me.top_role]
            if removable_roles:
                try:
                    await member.remove_roles(*removable_roles, reason="Leveling role update")
                except discord.HTTPException:
                    LOGGER.exception("Failed to remove old leveling roles from %s", member.id)

        if role_to_add is not None and role_to_add not in member.roles:
            try:
                await member.add_roles(role_to_add, reason="Leveling reward role earned")
            except discord.HTTPException:
                LOGGER.exception("Failed to add leveling role %s to %s", role_to_add.id, member.id)
                return

            if announce:
                try:
                    await member.send(
                        embed=make_embed(
                            "New Rank Unlocked",
                            f"You reached **Level {level}** in **{member.guild.name}** and unlocked **{role_to_add.name}**.",
                            discord.Color.gold(),
                        )
                    )
                except discord.HTTPException:
                    LOGGER.warning("Could not DM %s about their new leveling role.", member.id)

    async def award_message_xp(self, member: discord.Member) -> Optional[tuple[int, int]]:
        now = utc_now()
        progress = self.get_level_progress(member.guild.id, member.id)
        old_level = level_from_xp(progress.xp, self.settings.level_xp_increment)
        gained_xp = random.randint(LEVEL_XP_GAIN_MIN, LEVEL_XP_GAIN_MAX)
        progress.xp += gained_xp
        progress.messages += 1
        progress.last_message_at = now
        await self.persist_level_progress(member.guild.id, member.id, progress)

        new_level = level_from_xp(progress.xp, self.settings.level_xp_increment)
        return old_level, new_level

    async def handle_leveling_message(self, message: discord.Message) -> None:
        if not isinstance(message.author, discord.Member):
            return
        if isinstance(message.channel, discord.Thread) and MODMAIL_THREAD_RE.match(message.channel.name):
            return
        if len(message.content.strip()) < 3:
            return

        result = await self.award_message_xp(message.author)
        if result is None:
            return

        old_level, new_level = result
        if new_level <= old_level:
            return

        await self.sync_level_reward_role(message.author, announce=True)
        next_reward = self.get_next_reward_role_name(new_level)
        embed = make_embed(
            "Level Up",
            f"{message.author.mention} reached **Level {new_level}**.",
            discord.Color.gold(),
        )
        if next_reward:
            embed.add_field(name="Next Rank", value=next_reward, inline=False)
        await self.send_level_up_announcement(message.guild, message.channel, embed)

    async def send_level_up_announcement(
        self,
        guild: discord.Guild,
        fallback_channel: discord.abc.Messageable,
        embed: discord.Embed,
    ) -> None:
        if self.settings.level_up_channel_id:
            try:
                channel = self.get_channel(self.settings.level_up_channel_id)
                if channel is None:
                    channel = await self.fetch_channel(self.settings.level_up_channel_id)
                if isinstance(channel, discord.TextChannel):
                    await channel.send(embed=embed)
                    return
                LOGGER.warning(
                    "LEVEL_UP_CHANNEL_ID is not a text channel: %s",
                    self.settings.level_up_channel_id,
                )
            except discord.HTTPException:
                LOGGER.exception("Could not fetch level-up channel %s", self.settings.level_up_channel_id)

        if isinstance(fallback_channel, (discord.TextChannel, discord.Thread)):
            await fallback_channel.send(embed=embed, delete_after=20)

    def create_rank_embed(self, member: discord.Member) -> discord.Embed:
        progress = self.get_level_progress(member.guild.id, member.id)
        level = level_from_xp(progress.xp, self.settings.level_xp_increment)
        current_level_xp = xp_for_level(level, self.settings.level_xp_increment)
        next_level_xp = xp_for_level(level + 1, self.settings.level_xp_increment)
        xp_into_level = progress.xp - current_level_xp
        xp_needed = max(1, next_level_xp - current_level_xp)
        reward_name = get_reward_role_name(level) or "Unranked"
        next_reward = self.get_next_reward_role_name(level)

        embed = make_embed(
            f"{member.display_name}'s Rank",
            f"Current title: **{reward_name}**",
            discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Total XP", value=str(progress.xp), inline=True)
        embed.add_field(name="Messages", value=str(progress.messages), inline=True)
        embed.add_field(name="Progress", value=f"{xp_into_level}/{xp_needed} XP", inline=True)
        embed.add_field(
            name="Next Level",
            value=f"Level {level + 1} at {next_level_xp} total XP",
            inline=True,
        )
        embed.add_field(name="Next Rank Reward", value=next_reward or "Top rank reached", inline=True)
        return embed

    async def ensure_staff(self, interaction: discord.Interaction, permission: str) -> bool:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or not self.has_staff_access(member, permission):
            if interaction.response.is_done():
                await interaction.followup.send(NO_PERMISSION, ephemeral=True)
            else:
                await interaction.response.send_message(NO_PERMISSION, ephemeral=True)
            return False
        return True

    async def safe_dm(self, user: discord.abc.User, embed: discord.Embed) -> None:
        try:
            await user.send(embed=embed)
        except discord.HTTPException:
            LOGGER.warning("Could not DM %s (%s)", user, user.id)

    def normalize_autoreact_emoji(self, value: str) -> Optional[str]:
        cleaned = value.strip()
        if not cleaned:
            return None

        partial = discord.PartialEmoji.from_str(cleaned)
        if partial.id is not None:
            return str(partial)
        if partial.name:
            return partial.name
        return cleaned

    def parse_autoreact_emojis(self, value: str) -> List[str]:
        parsed_emojis: List[str] = []
        for part in value.split(","):
            emoji = self.normalize_autoreact_emoji(part)
            if emoji is not None and emoji not in parsed_emojis:
                parsed_emojis.append(emoji)
        return parsed_emojis

    def get_autoreact_configs(self, guild_id: int) -> Dict[int, AutoReactionConfig]:
        return self.autoreact_configs.setdefault(guild_id, {})

    def create_autoreact_embed(self, guild: discord.Guild) -> discord.Embed:
        channel_configs = self.get_autoreact_configs(guild.id)
        if not channel_configs:
            return make_embed(
                "Auto-Reactions",
                "No auto-reaction channels are configured for this server yet.",
                discord.Color.blurple(),
            )

        lines = []
        for channel_id, config in sorted(channel_configs.items()):
            lines.append(f"Channel: <#{channel_id}> | Emojis: {' '.join(config.emojis)}")

        return make_embed("Auto-Reactions", "\n".join(lines), discord.Color.blurple())

    def get_qotd_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        return discord.utils.get(guild.roles, name=QOTD_ROLE_NAME)

    def create_qotd_embed(self, question: str) -> discord.Embed:
        return make_embed(
            "📌 Question of the Day",
            question,
            discord.Color.gold(),
            footer="Reply in the thread below 👇",
        )

    def create_qotd_thread_name(self, question: str) -> str:
        short_text = truncate_text(question, 45)
        slug = slugify_text(short_text)
        if slug:
            return truncate_text(f"QOTD - {slug}", 100)
        return f"QOTD - {utc_now().strftime('%Y-%m-%d')}"

    def normalize_thread_archive_duration(self, hours: int) -> int:
        requested_minutes = max(60, hours * 60)
        valid_durations = (60, 1440, 4320, 10080)
        return min(valid_durations, key=lambda duration: (abs(duration - requested_minutes), duration))

    def instagram_notifications_enabled(self) -> bool:
        return bool(self.settings.instagram_feed_url and self.settings.instagram_notification_channel_id)

    async def load_instagram_state(self) -> None:
        seen_order = await asyncio.to_thread(self._load_instagram_state_sync)
        self.instagram_seen_order = seen_order[-INSTAGRAM_STATE_LIMIT:]
        self.instagram_seen_ids = set(self.instagram_seen_order)

    def _load_instagram_state_sync(self) -> List[str]:
        try:
            raw = json.loads(INSTAGRAM_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        if not isinstance(raw, dict):
            return []

        seen_items = raw.get("seen_ids", [])
        if not isinstance(seen_items, list):
            return []

        normalized: List[str] = []
        for item in seen_items:
            if isinstance(item, str) and item and item not in normalized:
                normalized.append(item)
        return normalized

    def save_instagram_state(self) -> None:
        payload = {"seen_ids": self.instagram_seen_order[-INSTAGRAM_STATE_LIMIT:]}
        try:
            INSTAGRAM_STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            LOGGER.exception("Failed to save Instagram notification state.")

    async def persist_instagram_state(self) -> None:
        await asyncio.to_thread(self.save_instagram_state)

    def remember_instagram_entry(self, entry_id: str) -> None:
        if entry_id in self.instagram_seen_ids:
            return
        self.instagram_seen_order.append(entry_id)
        self.instagram_seen_ids.add(entry_id)
        if len(self.instagram_seen_order) > INSTAGRAM_STATE_LIMIT:
            overflow = self.instagram_seen_order[:-INSTAGRAM_STATE_LIMIT]
            self.instagram_seen_order = self.instagram_seen_order[-INSTAGRAM_STATE_LIMIT:]
            for stale_id in overflow:
                if stale_id not in self.instagram_seen_order:
                    self.instagram_seen_ids.discard(stale_id)

    def parse_instagram_timestamp(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError, IndexError):
                return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def get_xml_child_text(self, element: ET.Element, *paths: str) -> Optional[str]:
        for path in paths:
            found = element.find(path, XML_NAMESPACES)
            if found is not None and found.text:
                cleaned = found.text.strip()
                if cleaned:
                    return cleaned
        return None

    def get_xml_attribute(self, element: ET.Element, path: str, attribute: str) -> Optional[str]:
        found = element.find(path, XML_NAMESPACES)
        if found is None:
            return None
        value = found.attrib.get(attribute, "").strip()
        return value or None

    def parse_instagram_feed(self, raw_xml: str) -> List[InstagramFeedEntry]:
        root = ET.fromstring(raw_xml)
        entries: List[InstagramFeedEntry] = []
        item_elements = root.findall("./channel/item")
        if not item_elements:
            item_elements = root.findall("./atom:entry", XML_NAMESPACES)

        for element in item_elements:
            title = self.get_xml_child_text(element, "title") or "New Instagram post"
            link = self.get_xml_child_text(element, "link") or self.get_xml_attribute(element, "atom:link", "href")
            if not link:
                continue

            entry_id = (
                self.get_xml_child_text(element, "guid", "atom:id")
                or link
                or title
            )
            description = self.get_xml_child_text(element, "description", "content:encoded", "summary")
            image_url = (
                self.get_xml_attribute(element, "media:content", "url")
                or self.get_xml_attribute(element, "media:thumbnail", "url")
                or self.get_xml_attribute(element, "enclosure", "url")
            )
            published_at = self.parse_instagram_timestamp(
                self.get_xml_child_text(element, "pubDate", "published", "updated", "atom:updated")
            )
            lowered_title = title.lower()
            lowered_link = link.lower()
            entries.append(
                InstagramFeedEntry(
                    entry_id=entry_id,
                    title=html.unescape(title),
                    link=link,
                    description=strip_html(description) if description else None,
                    image_url=image_url,
                    published_at=published_at,
                    is_reel="reel" in lowered_title or "/reel/" in lowered_link or " reels " in lowered_title,
                )
            )

        entries.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc))
        return entries

    def fetch_instagram_feed_sync(self) -> List[InstagramFeedEntry]:
        request = urllib_request.Request(
            self.settings.instagram_feed_url,
            headers={
                "User-Agent": "DyadiaGuardianBot/1.0 (+Discord Instagram notifier)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            },
        )
        with urllib_request.urlopen(request, timeout=INSTAGRAM_REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8", errors="replace")
        return self.parse_instagram_feed(payload)

    async def fetch_instagram_feed(self) -> List[InstagramFeedEntry]:
        return await asyncio.to_thread(self.fetch_instagram_feed_sync)

    async def get_instagram_notification_channel(self) -> Optional[discord.TextChannel]:
        channel_id = self.settings.instagram_notification_channel_id
        if not channel_id:
            return None

        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.HTTPException:
                LOGGER.exception("Could not fetch Instagram notification channel %s", channel_id)
                return None

        if isinstance(channel, discord.TextChannel):
            return channel

        LOGGER.warning("INSTAGRAM_NOTIFICATION_CHANNEL_ID is not a text channel: %s", channel_id)
        return None

    def create_instagram_notification_embed(self, entry: InstagramFeedEntry) -> discord.Embed:
        label = "New Instagram Reel" if entry.is_reel else "New Instagram Post"
        description_parts = [f"[Open on Instagram]({entry.link})"]
        if entry.description:
            description_parts.append(truncate_text(entry.description, 600))

        embed = make_embed(
            label,
            "\n\n".join(description_parts),
            discord.Color.magenta(),
            footer=f"{BRAND_FOOTER} | {self.settings.instagram_profile_name}",
        )
        embed.title = truncate_text(entry.title, 256)
        embed.url = entry.link
        if entry.published_at is not None:
            embed.timestamp = entry.published_at
        if entry.image_url:
            embed.set_image(url=entry.image_url)
        return embed

    def create_instagram_status_embed(self, channel: Optional[discord.TextChannel]) -> discord.Embed:
        enabled = self.instagram_notifications_enabled()
        lines = [
            f"Status: **{'Enabled' if enabled else 'Disabled'}**",
            f"Profile label: **{self.settings.instagram_profile_name}**",
            f"Poll interval: **{self.settings.instagram_poll_minutes} minute(s)**",
            f"Target channel: {channel.mention if channel is not None else 'Not available'}",
            f"Tracked sent items: **{len(self.instagram_seen_order)}**",
            f"Last successful check: **{self.instagram_last_success_at.isoformat() if self.instagram_last_success_at else 'Never'}**",
            f"Last check error: **{self.instagram_last_error or 'None'}**",
        ]
        if self.settings.instagram_feed_url:
            lines.insert(1, f"Feed URL: {self.settings.instagram_feed_url}")
        else:
            lines.insert(1, "Feed URL: Not configured")
        return make_embed("Instagram Notifications", "\n".join(lines), discord.Color.blurple())

    async def poll_instagram_feed_once(self) -> int:
        if not self.instagram_notifications_enabled():
            self.instagram_last_checked_at = utc_now()
            self.instagram_last_error = "Instagram feed URL or notification channel is not configured."
            return 0

        channel = await self.get_instagram_notification_channel()
        if channel is None:
            self.instagram_last_checked_at = utc_now()
            self.instagram_last_error = "Configured Instagram notification channel could not be resolved."
            return 0

        try:
            entries = await self.fetch_instagram_feed()
        except ET.ParseError:
            self.instagram_last_checked_at = utc_now()
            self.instagram_last_error = "Feed XML could not be parsed."
            LOGGER.exception("Instagram feed XML could not be parsed.")
            return 0
        except urllib_error.URLError as exc:
            self.instagram_last_checked_at = utc_now()
            self.instagram_last_error = str(exc.reason) if getattr(exc, "reason", None) else str(exc)
            LOGGER.exception("Instagram feed fetch failed.")
            return 0
        except Exception:
            self.instagram_last_checked_at = utc_now()
            self.instagram_last_error = "Unexpected feed polling error."
            LOGGER.exception("Unexpected Instagram feed polling error.")
            return 0

        self.instagram_last_checked_at = utc_now()
        self.instagram_last_success_at = self.instagram_last_checked_at
        self.instagram_last_error = None

        if not entries:
            return 0

        if not self.instagram_seen_order:
            for entry in entries:
                self.remember_instagram_entry(entry.entry_id)
            await self.persist_instagram_state()
            LOGGER.info("Instagram notifier seeded with %s existing feed item(s).", len(entries))
            return 0

        new_entries = [entry for entry in entries if entry.entry_id not in self.instagram_seen_ids]
        sent_count = 0
        for entry in new_entries:
            try:
                await channel.send(embed=self.create_instagram_notification_embed(entry))
            except discord.HTTPException:
                LOGGER.exception("Failed to send Instagram notification for %s", entry.link)
                self.instagram_last_error = f"Failed to send message for {entry.link}"
                continue

            self.remember_instagram_entry(entry.entry_id)
            sent_count += 1

        if sent_count:
            await self.persist_instagram_state()
            LOGGER.info("Sent %s Instagram notification(s) to %s", sent_count, channel.id)

        return sent_count

    async def handle_warn(self, interaction: discord.Interaction, user: discord.Member, reason: str) -> None:
        if not await self.ensure_staff(interaction, "moderate_members"):
            return
        moderator = interaction.user
        if not isinstance(moderator, discord.Member):
            await interaction.response.send_message(NO_PERMISSION, ephemeral=True)
            return
        blocked_reason = self.can_act_on_target(moderator, user)
        if blocked_reason:
            await interaction.response.send_message(blocked_reason, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.safe_dm(
            user,
            make_embed(
                "Warning",
                f"You have been warned in **{interaction.guild.name}**.\n\nReason: {reason}",
                discord.Color.yellow(),
            ),
        )

        embed = self.create_modlog_embed("WARN", user, interaction.user, reason)
        await self.send_modlog(embed)
        await self.add_modlog("WARN", user, interaction.user, interaction.guild.id if interaction.guild else None, reason)
        await self.log_moderator_command(interaction, "/warn", user, reason)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def handle_mute(self, interaction: discord.Interaction, user: discord.Member, duration_text: str, reason: str) -> None:
        if not await self.ensure_staff(interaction, "moderate_members"):
            return
        moderator = interaction.user
        if not isinstance(moderator, discord.Member):
            await interaction.response.send_message(NO_PERMISSION, ephemeral=True)
            return
        blocked_reason = self.can_act_on_target(moderator, user)
        if blocked_reason:
            await interaction.response.send_message(blocked_reason, ephemeral=True)
            return

        duration = parse_duration(duration_text)
        if duration is None:
            await interaction.response.send_message(INVALID_DURATION, ephemeral=True)
            return
        if duration > timedelta(days=MAX_TIMEOUT_DAYS):
            await interaction.response.send_message("Duration cannot exceed 28 days.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await user.timeout(duration, reason=reason)
        await self.safe_dm(
            user,
            make_embed(
                "Timeout",
                f"You have been timed out in **{interaction.guild.name}** for {format_duration(duration)}.\n\nReason: {reason}",
                discord.Color.orange(),
            ),
        )

        embed = self.create_modlog_embed("MUTE", user, interaction.user, reason)
        embed.add_field(name="Duration", value=format_duration(duration), inline=False)
        await self.send_modlog(embed)
        await self.add_modlog(
            "MUTE",
            user,
            interaction.user,
            interaction.guild.id if interaction.guild else None,
            reason,
            format_duration(duration),
        )
        await self.log_moderator_command(interaction, "/mute", user, f"{reason} | Duration: {format_duration(duration)}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def handle_kick(self, interaction: discord.Interaction, user: discord.Member, reason: str) -> None:
        if not await self.ensure_staff(interaction, "kick_members"):
            return
        moderator = interaction.user
        if not isinstance(moderator, discord.Member):
            await interaction.response.send_message(NO_PERMISSION, ephemeral=True)
            return
        blocked_reason = self.can_act_on_target(moderator, user)
        if blocked_reason:
            await interaction.response.send_message(blocked_reason, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.safe_dm(
            user,
            make_embed(
                "Kick",
                f"You have been kicked from **{interaction.guild.name}**.\n\nReason: {reason}",
                discord.Color.red(),
            ),
        )
        await user.kick(reason=reason)

        embed = self.create_modlog_embed("KICK", user, interaction.user, reason)
        await self.send_modlog(embed)
        await self.add_modlog("KICK", user, interaction.user, interaction.guild.id if interaction.guild else None, reason)
        await self.log_moderator_command(interaction, "/kick", user, reason)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def handle_ban(self, interaction: discord.Interaction, user: discord.Member, reason: str, delete_days: int) -> None:
        if not await self.ensure_staff(interaction, "ban_members"):
            return
        moderator = interaction.user
        if not isinstance(moderator, discord.Member):
            await interaction.response.send_message(NO_PERMISSION, ephemeral=True)
            return
        blocked_reason = self.can_act_on_target(moderator, user)
        if blocked_reason:
            await interaction.response.send_message(blocked_reason, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.safe_dm(
            user,
            make_embed(
                "Ban",
                f"You have been banned from **{interaction.guild.name}**.\n\nReason: {reason}",
                discord.Color.dark_red(),
            ),
        )
        await interaction.guild.ban(user, reason=reason, delete_message_seconds=delete_days * 86400)

        embed = self.create_modlog_embed("BAN", user, interaction.user, reason)
        if delete_days:
            embed.add_field(name="Deleted Messages", value=f"{delete_days} day(s)", inline=False)
        await self.send_modlog(embed)
        await self.add_modlog("BAN", user, interaction.user, interaction.guild.id if interaction.guild else None, reason)
        await self.log_moderator_command(interaction, "/ban", user, f"{reason} | Delete days: {delete_days}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def handle_unban(self, interaction: discord.Interaction, user_id: str, reason: str) -> None:
        if not await self.ensure_staff(interaction, "ban_members"):
            return
        if not re.fullmatch(r"\d{17,20}", user_id):
            await interaction.response.send_message("Please provide a valid Discord user ID.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        target = discord.Object(id=int(user_id))
        try:
            ban_entry = await interaction.guild.fetch_ban(target)
        except discord.NotFound:
            await interaction.followup.send("That user is not banned.", ephemeral=True)
            return

        await interaction.guild.unban(ban_entry.user, reason=reason)
        embed = self.create_modlog_embed("UNBAN", ban_entry.user, interaction.user, reason)
        await self.send_modlog(embed)
        await self.add_modlog("UNBAN", ban_entry.user, interaction.user, interaction.guild.id if interaction.guild else None, reason)
        await self.log_moderator_command(interaction, "/unban", ban_entry.user, reason)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def handle_role_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        role: discord.Role,
        reason: str,
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_roles"):
            return
        moderator = interaction.user
        if not isinstance(moderator, discord.Member):
            await interaction.response.send_message(NO_PERMISSION, ephemeral=True)
            return
        blocked_reason = self.can_act_on_target(moderator, user) or self.can_manage_role(moderator, role)
        if blocked_reason:
            await interaction.response.send_message(blocked_reason, ephemeral=True)
            return
        if role in user.roles:
            await interaction.response.send_message(f"{user.mention} already has {role.mention}.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await user.add_roles(role, reason=f"{reason} | Added by {interaction.user} ({interaction.user.id})")
        embed = self.create_modlog_embed("ROLE ADD", user, interaction.user, reason)
        embed.add_field(name="Role", value=f"{role.mention} ({role.id})", inline=False)
        await self.send_modlog(embed)
        await self.add_modlog(
            "ROLE ADD",
            user,
            interaction.user,
            interaction.guild.id if interaction.guild else None,
            f"{reason} | Role: {role.name}",
        )
        await self.log_moderator_command(interaction, "/addrole", user, f"{reason} | Role: {role.name}")
        await interaction.followup.send(f"Added {role.mention} to {user.mention}.", ephemeral=True)

    async def handle_role_remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        role: discord.Role,
        reason: str,
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_roles"):
            return
        moderator = interaction.user
        if not isinstance(moderator, discord.Member):
            await interaction.response.send_message(NO_PERMISSION, ephemeral=True)
            return
        blocked_reason = self.can_act_on_target(moderator, user) or self.can_manage_role(moderator, role)
        if blocked_reason:
            await interaction.response.send_message(blocked_reason, ephemeral=True)
            return
        if role not in user.roles:
            await interaction.response.send_message(f"{user.mention} does not have {role.mention}.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await user.remove_roles(role, reason=f"{reason} | Removed by {interaction.user} ({interaction.user.id})")
        embed = self.create_modlog_embed("ROLE REMOVE", user, interaction.user, reason)
        embed.add_field(name="Role", value=f"{role.mention} ({role.id})", inline=False)
        await self.send_modlog(embed)
        await self.add_modlog(
            "ROLE REMOVE",
            user,
            interaction.user,
            interaction.guild.id if interaction.guild else None,
            f"{reason} | Role: {role.name}",
        )
        await self.log_moderator_command(interaction, "/removerole", user, f"{reason} | Role: {role.name}")
        await interaction.followup.send(f"Removed {role.mention} from {user.mention}.", ephemeral=True)

    async def handle_clear(self, interaction: discord.Interaction, amount: int, user: Optional[discord.Member]) -> None:
        if not await self.ensure_staff(interaction, "manage_messages"):
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This command can only be used in a text channel.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        remaining = amount

        def should_delete(message: discord.Message) -> bool:
            nonlocal remaining
            if remaining <= 0:
                return False
            if user is not None and message.author.id != user.id:
                return False
            if (utc_now() - message.created_at) >= timedelta(days=14):
                return False
            remaining -= 1
            return True

        deleted = await interaction.channel.purge(limit=min(1000, amount + 200), check=should_delete, bulk=True)
        target = user or interaction.user
        embed = self.create_modlog_embed("CLEAR", target, interaction.user, f"Cleared {len(deleted)} message(s)")
        await self.send_modlog(embed)
        await self.add_modlog(
            "CLEAR",
            target,
            interaction.user,
            interaction.guild.id if interaction.guild else None,
            f"Cleared {len(deleted)} message(s)",
        )
        await self.log_moderator_command(interaction, "/clear", target, f"Cleared {len(deleted)} message(s)")
        await interaction.followup.send(f"Deleted {len(deleted)} message(s).", ephemeral=True)

    async def handle_modlogs(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await self.ensure_staff(interaction, "moderate_members"):
            return

        if interaction.guild is not None and self.uses_postgres:
            related = await asyncio.to_thread(self.load_modlogs_from_postgres, interaction.guild.id, user.id, limit=10)
        else:
            related = [entry for entry in reversed(self.mod_logs) if entry.user_id == user.id][:10]
        description = "\n".join(
            f"`{entry.action}` by <@{entry.moderator_id}> - {entry.reason}"
            + (f" ({entry.duration_text})" if entry.duration_text else "")
            for entry in related
        ) or "No moderation entries found for this user yet."

        embed = make_embed(
            "Moderation Logs",
            f"User: **{user}** (`{user.id}`)\n\n{description}",
            discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def handle_rank(self, interaction: discord.Interaction, user: Optional[discord.Member]) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        target = user or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("I could not resolve that member in this server.", ephemeral=True)
            return

        await interaction.response.send_message(embed=self.create_rank_embed(target), ephemeral=True)

    async def handle_leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        guild_progress = self.level_data.get(interaction.guild.id, {})
        ranked_members = sorted(guild_progress.items(), key=lambda item: item[1].xp, reverse=True)[:LEADERBOARD_LIMIT]
        if not ranked_members:
            await interaction.response.send_message("Nobody has earned XP yet.", ephemeral=True)
            return

        lines = []
        for index, (user_id, progress) in enumerate(ranked_members, start=1):
            member = interaction.guild.get_member(user_id)
            display_name = member.display_name if member is not None else f"User {user_id}"
            lines.append(
                f"**#{index}** {display_name} - Level {level_from_xp(progress.xp, self.settings.level_xp_increment)} ({progress.xp} XP)"
            )

        embed = make_embed(
            "Leveling Leaderboard",
            "\n".join(lines),
            discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

    async def handle_invites(self, interaction: discord.Interaction, user: Optional[discord.Member]) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        target = user or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("I could not resolve that member in this server.", ephemeral=True)
            return

        invite_count = self.get_invite_count(interaction.guild.id, target.id)
        embed = make_embed(
            "Invite Count",
            f"{target.mention} has invited **{invite_count}** member{'s' if invite_count != 1 else ''}.",
            discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def handle_invite_leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        guild_counts = self.invite_counts.get(interaction.guild.id, {})
        ranked_inviters = sorted(guild_counts.items(), key=lambda item: item[1], reverse=True)[:LEADERBOARD_LIMIT]
        if not ranked_inviters:
            await interaction.response.send_message("No tracked invite joins yet.", ephemeral=True)
            return

        lines = []
        for index, (user_id, joins) in enumerate(ranked_inviters, start=1):
            member = interaction.guild.get_member(user_id)
            display_name = member.display_name if member is not None else f"User {user_id}"
            lines.append(f"**#{index}** {display_name} - {joins} invite{'s' if joins != 1 else ''}")

        embed = make_embed(
            "Invite Leaderboard",
            "\n".join(lines),
            discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

    async def handle_level_panel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        target_channel = channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message("Please choose a text channel for the leveling panel.", ephemeral=True)
                return

        await target_channel.send(embed=self.create_leveling_panel_embed(interaction.guild))
        await interaction.response.send_message(
            f"Leveling panel posted in {target_channel.mention}.",
            ephemeral=True,
        )

    async def handle_qotd(
        self,
        interaction: discord.Interaction,
        question: str,
        channel: Optional[discord.TextChannel],
        auto_archive_hours: int,
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        cleaned_question = normalize_optional_text(question)
        if cleaned_question is None:
            await interaction.response.send_message("Please provide a Question of the Day.", ephemeral=True)
            return

        target_channel = channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message("Please choose a text channel for the QOTD post.", ephemeral=True)
                return

        qotd_role = self.get_qotd_role(interaction.guild)
        role_mention = qotd_role.mention if qotd_role is not None else f"@{QOTD_ROLE_NAME}"
        content = f"{role_mention}\nNew Question of the Day is up."
        embed = self.create_qotd_embed(cleaned_question)
        archive_duration = self.normalize_thread_archive_duration(auto_archive_hours)

        try:
            message = await target_channel.send(
                content=content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
            )
        except discord.HTTPException:
            LOGGER.exception("Failed to post QOTD message in channel %s", target_channel.id)
            await interaction.response.send_message(
                f"I could not send the QOTD message in {target_channel.mention}. Check my permissions there.",
                ephemeral=True,
            )
            return

        thread_name = self.create_qotd_thread_name(cleaned_question)
        try:
            thread = await message.create_thread(
                name=thread_name,
                auto_archive_duration=archive_duration,
            )
            try:
                await thread.send("Reply to today’s question here so the main channel stays clean.")
            except discord.HTTPException:
                LOGGER.warning("Failed to send QOTD thread prompt in thread %s", thread.id)
        except discord.HTTPException:
            LOGGER.exception("Failed to create QOTD thread for message %s", message.id)
            await interaction.response.send_message(
                (
                    f"QOTD posted in {target_channel.mention}, but I could not create the thread. "
                    "Check my thread permissions in that channel."
                ),
                ephemeral=True,
            )
            return

        archive_text = format_duration(timedelta(minutes=archive_duration))
        role_text = role_mention if qotd_role is not None else f"`{QOTD_ROLE_NAME}` role not found"
        await interaction.response.send_message(
            (
                f"QOTD posted in {target_channel.mention} and thread {thread.mention} opened. "
                f"Pinged: {role_text}. Auto-archive: {archive_text}."
            ),
            ephemeral=True,
        )

    async def handle_autoreact_activate(
        self,
        interaction: discord.Interaction,
        emoji: str,
        channel: Optional[discord.TextChannel],
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        normalized_emojis = self.parse_autoreact_emojis(emoji)
        if not normalized_emojis:
            await interaction.response.send_message(
                "Please provide one or more valid emojis, separated by commas.",
                ephemeral=True,
            )
            return

        target_channel = channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message("Please choose a text channel for auto-reaction.", ephemeral=True)
                return

        channel_configs = self.get_autoreact_configs(interaction.guild.id)
        config = channel_configs.setdefault(target_channel.id, AutoReactionConfig())
        added_emojis = [item for item in normalized_emojis if item not in config.emojis]
        if not added_emojis:
            await interaction.response.send_message(
                f"Those emojis are already active for auto-reactions in {target_channel.mention}.",
                ephemeral=True,
            )
            return

        config.emojis.extend(added_emojis)
        await self.persist_autoreact_data()
        await interaction.response.send_message(
            f"Auto-reaction updated in {target_channel.mention}. Added {' '.join(added_emojis)}. Active emojis: {' '.join(config.emojis)}.",
            ephemeral=True,
        )

    async def handle_autoreact_deactivate(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        target_channel = channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message("Please choose a text channel to deactivate.", ephemeral=True)
                return

        channel_configs = self.get_autoreact_configs(interaction.guild.id)
        if target_channel.id not in channel_configs:
            await interaction.response.send_message(
                f"Auto-reaction is not active in {target_channel.mention}.",
                ephemeral=True,
            )
            return

        channel_configs.pop(target_channel.id, None)
        await self.persist_autoreact_data()
        await interaction.response.send_message(
            f"Auto-reaction deactivated in {target_channel.mention}.",
            ephemeral=True,
        )

    async def handle_no_link_activate(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        target_channel = channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message("Please choose a text channel for no-link mode.", ephemeral=True)
                return

        blocked_channels = self.no_link_channels.setdefault(interaction.guild.id, set())
        if target_channel.id in blocked_channels:
            await interaction.response.send_message(
                f"No-link mode is already active in {target_channel.mention}.",
                ephemeral=True,
            )
            return

        blocked_channels.add(target_channel.id)
        await self.persist_no_link_data()
        await interaction.response.send_message(
            f"No-link mode activated in {target_channel.mention}. Messages containing links will be deleted there.",
            ephemeral=True,
        )

    async def handle_no_link_deactivate(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        target_channel = channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message("Please choose a text channel to disable no-link mode.", ephemeral=True)
                return

        blocked_channels = self.no_link_channels.setdefault(interaction.guild.id, set())
        if target_channel.id not in blocked_channels:
            await interaction.response.send_message(
                f"No-link mode is not active in {target_channel.mention}.",
                ephemeral=True,
            )
            return

        blocked_channels.discard(target_channel.id)
        if not blocked_channels:
            self.no_link_channels.pop(interaction.guild.id, None)
        await self.persist_no_link_data()
        await interaction.response.send_message(
            f"No-link mode deactivated in {target_channel.mention}.",
            ephemeral=True,
        )

    async def handle_embed_builder(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        target_channel = channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message("Please choose a text channel for the embed.", ephemeral=True)
                return

        bot_member = interaction.guild.me
        if bot_member is None and self.user is not None:
            bot_member = interaction.guild.get_member(self.user.id)
        if bot_member is None:
            await interaction.response.send_message("I could not verify my channel permissions right now.", ephemeral=True)
            return

        if not target_channel.permissions_for(bot_member).send_messages:
            await interaction.response.send_message(
                f"I do not have permission to send messages in {target_channel.mention}.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(EmbedBuilderModal(self, target_channel))

    async def handle_instagram_status(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return

        channel = await self.get_instagram_notification_channel()
        await interaction.response.send_message(embed=self.create_instagram_status_embed(channel), ephemeral=True)

    async def handle_instagram_check(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return

        await interaction.response.defer(ephemeral=True)
        sent_count = await self.poll_instagram_feed_once()
        channel = await self.get_instagram_notification_channel()
        embed = self.create_instagram_status_embed(channel)
        embed.add_field(
            name="Manual Check Result",
            value=f"Sent **{sent_count}** new notification(s).",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    def get_anti_raid_state(self, guild_id: int) -> AntiRaidState:
        state = self.anti_raid_states.get(guild_id)
        if state is None:
            state = AntiRaidState(enabled=self.settings.anti_raid_enabled)
            self.anti_raid_states[guild_id] = state
        return state

    def anti_raid_is_active(self, state: AntiRaidState) -> bool:
        return state.lockdown_until is not None and state.lockdown_until > utc_now()

    def prune_anti_raid_events(self, state: AntiRaidState, now: datetime) -> None:
        window = timedelta(seconds=self.settings.anti_raid_window_seconds)
        while state.join_events and (now - state.join_events[0]) > window:
            state.join_events.popleft()

    async def send_anti_raid_alert(self, guild: discord.Guild, title: str, description: str) -> None:
        embed = make_embed(title, description, discord.Color.dark_orange())
        embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=False)
        await self.send_modlog(embed)

    def create_anti_raid_status_embed(self, guild: discord.Guild, state: AntiRaidState) -> discord.Embed:
        active = self.anti_raid_is_active(state)
        remaining = "Inactive"
        if active and state.lockdown_until is not None:
            remaining = format_duration(state.lockdown_until - utc_now())

        embed = make_embed(
            "Anti-Raid Status",
            f"Protection for **{guild.name}** is {'enabled' if state.enabled else 'disabled'}.",
            discord.Color.dark_orange() if active else discord.Color.blurple(),
        )
        embed.add_field(name="Raid Mode", value="Active" if active else "Inactive", inline=True)
        embed.add_field(name="Remaining", value=remaining, inline=True)
        embed.add_field(
            name="Trigger Rule",
            value=(
                f"{self.settings.anti_raid_join_threshold} joins in "
                f"{self.settings.anti_raid_window_seconds} seconds"
            ),
            inline=False,
        )
        embed.add_field(
            name="Auto Timeout",
            value=(
                f"Accounts newer than {self.settings.anti_raid_account_age_minutes} minute(s) "
                f"are timed out for {self.settings.anti_raid_timeout_minutes} minute(s) during raid mode."
            ),
            inline=False,
        )
        if state.last_trigger_count:
            embed.add_field(name="Last Trigger Count", value=str(state.last_trigger_count), inline=True)
        return embed

    async def activate_anti_raid(
        self,
        guild: discord.Guild,
        triggered_by: Optional[discord.abc.User],
        reason: str,
        *,
        manual: bool = False,
        trigger_count: Optional[int] = None,
    ) -> AntiRaidState:
        state = self.get_anti_raid_state(guild.id)
        state.enabled = True
        state.manual_lockdown = manual
        state.lockdown_until = utc_now() + timedelta(minutes=self.settings.anti_raid_lockdown_minutes)
        if trigger_count is not None:
            state.last_trigger_count = trigger_count

        source = f"Activated by {triggered_by} ({triggered_by.id})" if triggered_by else "Activated automatically"
        description = (
            f"{reason}\n\n"
            f"{source}\n"
            f"Raid mode will stay active for {self.settings.anti_raid_lockdown_minutes} minute(s)."
        )
        if trigger_count is not None:
            description += f"\nObserved joins in window: {trigger_count}"
        await self.send_anti_raid_alert(guild, "Anti-Raid Activated", description)
        return state

    async def deactivate_anti_raid(
        self,
        guild: discord.Guild,
        actor: Optional[discord.abc.User],
        reason: str,
    ) -> AntiRaidState:
        state = self.get_anti_raid_state(guild.id)
        state.lockdown_until = None
        state.manual_lockdown = False
        await self.send_anti_raid_alert(
            guild,
            "Anti-Raid Deactivated",
            f"{reason}\n\nDeactivated by {actor} ({actor.id})" if actor else reason,
        )
        return state

    async def handle_anti_raid_join(self, member: discord.Member) -> None:
        state = self.get_anti_raid_state(member.guild.id)
        if not state.enabled or member.bot:
            return

        now = utc_now()
        self.prune_anti_raid_events(state, now)
        state.join_events.append(now)
        trigger_count = len(state.join_events)

        if trigger_count >= self.settings.anti_raid_join_threshold and not self.anti_raid_is_active(state):
            await self.activate_anti_raid(
                member.guild,
                None,
                "Join-rate threshold reached. Raid mode was enabled automatically.",
                manual=False,
                trigger_count=trigger_count,
            )

        if not self.anti_raid_is_active(state):
            return

        account_age = now - member.created_at
        minimum_age = timedelta(minutes=self.settings.anti_raid_account_age_minutes)
        if account_age > minimum_age:
            return

        timeout_for = timedelta(minutes=self.settings.anti_raid_timeout_minutes)
        try:
            await member.timeout(timeout_for, reason="Anti-raid protection triggered")
        except discord.HTTPException:
            LOGGER.exception("Failed to timeout suspected raid account %s in guild %s", member.id, member.guild.id)
            return

        reason = (
            f"Auto-timeout during raid mode. Account age: {format_duration(account_age)}. "
            f"Timeout: {format_duration(timeout_for)}."
        )
        embed = self.create_modlog_embed("ANTI-RAID", member, self.user or member.guild.me or member, reason)
        await self.send_modlog(embed)
        await self.add_modlog("ANTI-RAID", member, self.user or member.guild.me or member, member.guild.id, reason)

    async def handle_antiraid_status(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_staff(interaction, "moderate_members"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        state = self.get_anti_raid_state(interaction.guild.id)
        await interaction.response.send_message(embed=self.create_anti_raid_status_embed(interaction.guild, state), ephemeral=True)

    async def handle_antiraid_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        state = self.get_anti_raid_state(interaction.guild.id)
        state.enabled = enabled
        if not enabled:
            state.lockdown_until = None
            state.manual_lockdown = False

        await interaction.response.send_message(
            f"Anti-raid monitoring has been {'enabled' if enabled else 'disabled'} for this server.",
            ephemeral=True,
        )

    async def handle_antiraid_activate(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.activate_anti_raid(
            interaction.guild,
            interaction.user,
            "Raid mode was activated manually by staff.",
            manual=True,
        )
        await interaction.followup.send(
            f"Raid mode is now active for {self.settings.anti_raid_lockdown_minutes} minute(s).",
            ephemeral=True,
        )

    async def handle_antiraid_deactivate(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        state = self.get_anti_raid_state(interaction.guild.id)
        if not self.anti_raid_is_active(state):
            await interaction.response.send_message("Raid mode is not active right now.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.deactivate_anti_raid(interaction.guild, interaction.user, "Raid mode was turned off manually by staff.")
        await interaction.followup.send("Raid mode has been deactivated.", ephemeral=True)

    async def handle_close(self, interaction: discord.Interaction, reason: str) -> None:
        if not await self.ensure_staff(interaction, "moderate_members"):
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("This command can only be used in a modmail thread.", ephemeral=True)
            return

        session = self.get_session_by_thread(interaction.channel.id)
        if session is None:
            await interaction.response.send_message("This thread is not an active modmail session.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.close_modmail(session.user_id, interaction.user, reason)
        await interaction.followup.send("Modmail closed.", ephemeral=True)

    async def handle_staff_apply_panel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        target_channel = channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message("Please choose a text channel for the application panel.", ephemeral=True)
                return

        await target_channel.send(embed=self.create_staff_application_panel_embed(), view=self.staff_application_view)
        await interaction.response.send_message(
            f"Staff application panel posted in {target_channel.mention}.",
            ephemeral=True,
        )

    async def handle_verification_panel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> None:
        if not await self.ensure_staff(interaction, "manage_guild"):
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return

        target_channel = channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message("Please choose a text channel for the verification panel.", ephemeral=True)
                return

        await target_channel.send(embed=self.create_verification_panel_embed(interaction.guild), view=self.verification_view)
        await interaction.response.send_message(
            f"Verification panel posted in {target_channel.mention}.",
            ephemeral=True,
        )

    async def handle_verification_button(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This verification button can only be used inside the server.", ephemeral=True)
            return

        role = self.get_verified_role(interaction.guild)
        if role is None:
            await interaction.response.send_message(
                "The `Verified` role was not found. Ask a moderator to create it or set `VERIFIED_ROLE_ID`.",
                ephemeral=True,
            )
            return

        role_error = self.can_manage_role(interaction.guild.me or interaction.user, role)
        if role_error is not None:
            await interaction.response.send_message(role_error, ephemeral=True)
            return

        if role in interaction.user.roles:
            await interaction.response.send_message(
                "You have already verified and already have the Verified role.",
                ephemeral=True,
            )
            return

        try:
            await interaction.user.add_roles(role, reason="HOK Dyadia verification completed")
        except discord.HTTPException:
            LOGGER.exception("Failed to assign verified role to %s in guild %s", interaction.user.id, interaction.guild.id)
            await interaction.response.send_message(
                "I could not assign the verification role. Please contact a moderator.",
                ephemeral=True,
            )
            return

        await self.send_verification_log(self.create_verification_log_embed(interaction.user, role))
        await interaction.response.send_message(
            f"Verification complete. You have been given {role.mention} and can now access all server channels.",
            ephemeral=True,
        )

    async def handle_staff_application_continue(self, interaction: discord.Interaction, custom_id: str) -> None:
        match = re.fullmatch(r"staff_application:continue:(\d):(\d+)", custom_id)
        if match is None:
            await interaction.response.send_message("That application page is invalid. Please start again.", ephemeral=True)
            return

        next_page = int(match.group(1))
        owner_id = int(match.group(2))
        if interaction.user.id != owner_id:
            await interaction.response.send_message(
                "This application page belongs to someone else.",
                ephemeral=True,
            )
            return

        draft = self.staff_application_drafts.get(owner_id)
        if draft is None:
            await interaction.response.send_message(
                "Your application session expired. Please start again from the panel.",
                ephemeral=True,
            )
            return

        if next_page == 2:
            await interaction.response.send_modal(StaffApplicationPageTwoModal(self, owner_id))
            return

        await interaction.response.send_message("That application page is invalid. Please start again.", ephemeral=True)

    async def submit_staff_application(
        self,
        interaction: discord.Interaction,
        draft: StaffApplicationDraft,
    ) -> None:
        try:
            channel = await self.get_staff_application_channel()
        except discord.HTTPException:
            LOGGER.exception(
                "Could not fetch staff application review channel %s",
                self.settings.staff_application_channel_id,
            )
            await interaction.response.send_message(
                "I could not find the staff application review channel. Please tell an admin to check the channel ID.",
                ephemeral=True,
            )
            return

        if channel is None:
            await interaction.response.send_message(
                "The configured staff application review channel is invalid. Please tell an admin to update it.",
                ephemeral=True,
            )
            return

        embed = self.create_staff_application_embed(interaction.user, draft, interaction.guild)
        try:
            await channel.send(
                content=f"<@&{self.settings.admin_role_id}> New staff application received.",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.HTTPException:
            LOGGER.exception("Failed to send staff application for %s", interaction.user)
            await interaction.response.send_message(
                "I could not send your application right now. Please try again later.",
                ephemeral=True,
            )
            return

        self.staff_application_drafts.pop(interaction.user.id, None)
        await interaction.response.send_message(
            "Your staff application has been submitted successfully.",
            ephemeral=True,
        )

    def get_session_by_thread(self, thread_id: int) -> Optional[ModmailSession]:
        for session in self.modmail_sessions.values():
            if session.thread_id == thread_id:
                return session
        return None

    def is_on_cooldown(self, user_id: int) -> bool:
        started = self.modmail_cooldowns.get(user_id)
        return started is not None and (utc_now() - started) < timedelta(seconds=MODMAIL_COOLDOWN_SECONDS)

    @staticmethod
    def interaction_response_kwargs(interaction: discord.Interaction) -> dict:
        return {"ephemeral": True} if interaction.guild_id is not None else {}

    def should_send_dm_intro(self, user_id: int) -> bool:
        sent_at = self.dm_intro_cooldowns.get(user_id)
        return sent_at is None or (utc_now() - sent_at) >= timedelta(seconds=DM_INTRO_COOLDOWN_SECONDS)

    async def handle_user_dm(self, message: discord.Message) -> None:
        session = self.modmail_sessions.get(message.author.id)
        if session is None:
            if self.should_send_dm_intro(message.author.id):
                await message.author.send(embed=self.create_modmail_intro_embed(), view=self.modmail_view)
                self.dm_intro_cooldowns[message.author.id] = utc_now()
            return

        await self.relay_user_message(message, session)

    async def open_modmail_from_button(self, interaction: discord.Interaction) -> None:
        user_id = interaction.user.id
        response_kwargs = self.interaction_response_kwargs(interaction)

        try:
            if self.is_on_cooldown(user_id):
                await interaction.response.send_message("Please wait a moment before opening another modmail.", **response_kwargs)
                return

            if user_id in self.modmail_sessions:
                await interaction.response.send_message("You already have an active modmail thread.", **response_kwargs)
                return

            if interaction.guild_id is None:
                await interaction.response.send_message("Opening your modmail...", **response_kwargs)
            else:
                await interaction.response.defer(thinking=True, **response_kwargs)

            forum = self.get_channel(self.settings.modmail_forum_id)
            if forum is None:
                forum = await self.fetch_channel(self.settings.modmail_forum_id)
            if not isinstance(forum, discord.ForumChannel):
                if interaction.guild_id is None:
                    await interaction.channel.send("MODMAIL_FORUM_ID is not a forum channel.")
                else:
                    await interaction.followup.send("MODMAIL_FORUM_ID is not a forum channel.", **response_kwargs)
                return

            thread = await forum.create_thread(
                name=f"modmail-{interaction.user.id}",
                content=f"<@&{self.settings.moderator_role_id}> Modmail opened by {interaction.user.mention}",
                embed=self.create_modmail_thread_embed(interaction.user, "Opened via DM button"),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            await thread.thread.send(
                "Use the button below to close this modmail thread when the case is resolved.",
                view=self.close_modmail_view,
            )

            self.modmail_sessions[user_id] = ModmailSession(user_id=user_id, thread_id=thread.thread.id)
            self.modmail_cooldowns[user_id] = utc_now()
            await self.safe_dm(
                interaction.user,
                make_embed(
                    "Modmail Opened",
                    "Your private modmail thread has been created. Send messages here and the moderation team will receive them.",
                    discord.Color.green(),
                ),
            )
            if interaction.guild_id is not None:
                await interaction.followup.send("Your modmail has been opened.", **response_kwargs)
        except discord.HTTPException:
            LOGGER.exception("Failed to create modmail thread for %s", interaction.user)
            try:
                if interaction.response.is_done():
                    if interaction.guild_id is None:
                        await interaction.channel.send("I could not create a modmail thread. Check my forum permissions and channel IDs.")
                    else:
                        await interaction.followup.send(
                            "I could not create a modmail thread. Check my forum permissions and channel IDs.",
                            **response_kwargs,
                        )
                else:
                    await interaction.response.send_message(
                        "I could not create a modmail thread. Check my forum permissions and channel IDs.",
                        **response_kwargs,
                    )
            except discord.HTTPException:
                LOGGER.exception("Failed to send modmail creation error response to %s", interaction.user)
        except Exception:
            LOGGER.exception("Unexpected error while opening modmail for %s", interaction.user)
            try:
                if interaction.response.is_done():
                    if interaction.guild_id is None:
                        await interaction.channel.send("Something went wrong while opening modmail.")
                    else:
                        await interaction.followup.send("Something went wrong while opening modmail.", **response_kwargs)
                else:
                    await interaction.response.send_message("Something went wrong while opening modmail.", **response_kwargs)
            except discord.HTTPException:
                LOGGER.exception("Failed to send unexpected modmail error response to %s", interaction.user)

    async def close_modmail_from_button(self, interaction: discord.Interaction) -> None:
        response_kwargs = self.interaction_response_kwargs(interaction)

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("This button can only be used inside an active modmail thread.", **response_kwargs)
            return

        session = self.get_session_by_thread(interaction.channel.id)
        if session is None:
            await interaction.response.send_message("This thread is not an active modmail session.", **response_kwargs)
            return

        if interaction.guild_id is not None:
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            if member is None or not self.has_staff_access(member, "moderate_members"):
                await interaction.response.send_message(NO_PERMISSION, **response_kwargs)
                return

        await interaction.response.defer(**response_kwargs)
        await self.close_modmail(session.user_id, interaction.user, "Issue resolved by the moderation team")
        await interaction.followup.send("Modmail closed.", **response_kwargs)

    async def relay_user_message(self, message: discord.Message, session: ModmailSession) -> None:
        thread = self.get_channel(session.thread_id)
        if thread is None:
            thread = await self.fetch_channel(session.thread_id)
        if not isinstance(thread, discord.Thread):
            self.modmail_sessions.pop(message.author.id, None)
            await message.author.send("Your previous modmail thread is no longer available. Please open a new one.")
            return

        session.last_activity = utc_now()
        session.message_count += 1
        embed = discord.Embed(
            title="User Message",
            description=message.content or "*No text content*",
            color=discord.Color.blurple(),
            timestamp=utc_now(),
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.set_footer(text=BRAND_FOOTER)
        if message.attachments:
            embed.add_field(name="Attachments", value="\n".join(attachment.url for attachment in message.attachments), inline=False)

        await thread.send(embed=embed)

    async def handle_moderator_reply(self, message: discord.Message) -> None:
        match = MODMAIL_THREAD_RE.match(message.channel.name)
        if match is None:
            return

        session = self.modmail_sessions.get(int(match.group("user_id")))
        if session is None:
            return

        user = self.get_user(session.user_id) or await self.fetch_user(session.user_id)
        session.last_activity = utc_now()
        embed = discord.Embed(
            title=f"{self.settings.server_name} Moderator",
            description=message.content or "*No text content*",
            color=discord.Color.purple(),
            timestamp=utc_now(),
        )
        embed.set_footer(text=BRAND_FOOTER)
        embed.set_thumbnail(url=DEFAULT_THUMBNAIL_URL)
        if message.attachments:
            embed.add_field(name="Attachments", value="\n".join(attachment.url for attachment in message.attachments), inline=False)

        await user.send(embed=embed)

    async def close_modmail(self, user_id: int, closed_by: discord.abc.User, reason: str) -> None:
        session = self.modmail_sessions.pop(user_id, None)
        if session is None:
            return

        thread = self.get_channel(session.thread_id)
        if thread is None:
            thread = await self.fetch_channel(session.thread_id)

        if isinstance(thread, discord.Thread):
            await thread.send(
                embed=make_embed(
                    "Modmail Closed",
                    f"Closed by **{closed_by}**.\nReason: {reason}",
                    discord.Color.red(),
                )
            )
            await thread.edit(archived=True, locked=True)

        user = self.get_user(user_id) or await self.fetch_user(user_id)
        await self.safe_dm(
            user,
            make_embed(
                "Modmail Closed",
                f"Your modmail thread has been closed.\n\nReason: {reason}",
                discord.Color.red(),
            ),
        )

    async def validate_runtime_configuration(self) -> None:
        try:
            forum = self.get_channel(self.settings.modmail_forum_id) or await self.fetch_channel(self.settings.modmail_forum_id)
            if isinstance(forum, discord.ForumChannel):
                LOGGER.info("Modmail forum found: %s (%s)", forum.name, forum.id)
            else:
                LOGGER.warning("MODMAIL_FORUM_ID is not a forum channel: %s", self.settings.modmail_forum_id)
        except discord.HTTPException:
            LOGGER.exception("Could not fetch modmail forum channel %s", self.settings.modmail_forum_id)

        try:
            log_channel = self.get_channel(self.settings.mod_log_channel_id) or await self.fetch_channel(self.settings.mod_log_channel_id)
            if isinstance(log_channel, discord.TextChannel):
                LOGGER.info("Mod log channel found: %s (%s)", log_channel.name, log_channel.id)
            else:
                LOGGER.warning("MOD_LOG_CHANNEL_ID is not a text channel: %s", self.settings.mod_log_channel_id)
        except discord.HTTPException:
            LOGGER.exception("Could not fetch mod log channel %s", self.settings.mod_log_channel_id)

        server_log_channel_id = self.settings.server_log_channel_id or self.settings.mod_log_channel_id
        if self.settings.server_log_channel_id:
            try:
                server_log_channel = self.get_channel(server_log_channel_id) or await self.fetch_channel(server_log_channel_id)
                if isinstance(server_log_channel, discord.TextChannel):
                    LOGGER.info("Server log channel found: %s (%s)", server_log_channel.name, server_log_channel.id)
                else:
                    LOGGER.warning("SERVER_LOG_CHANNEL_ID is not a text channel: %s", server_log_channel_id)
            except discord.HTTPException:
                LOGGER.exception("Could not fetch server log channel %s", server_log_channel_id)
        else:
            LOGGER.info("SERVER_LOG_CHANNEL_ID not set. Server logs will use MOD_LOG_CHANNEL_ID.")

        invite_log_channel_id = (
            self.settings.invite_log_channel_id
            or self.settings.server_log_channel_id
            or self.settings.mod_log_channel_id
        )
        if self.settings.invite_log_channel_id:
            try:
                invite_log_channel = self.get_channel(invite_log_channel_id) or await self.fetch_channel(invite_log_channel_id)
                if isinstance(invite_log_channel, discord.TextChannel):
                    LOGGER.info("Invite log channel found: %s (%s)", invite_log_channel.name, invite_log_channel.id)
                else:
                    LOGGER.warning("INVITE_LOG_CHANNEL_ID is not a text channel: %s", invite_log_channel_id)
            except discord.HTTPException:
                LOGGER.exception("Could not fetch invite log channel %s", invite_log_channel_id)
        else:
            LOGGER.info("INVITE_LOG_CHANNEL_ID not set. Invite logs will use SERVER_LOG_CHANNEL_ID or MOD_LOG_CHANNEL_ID.")

        verification_log_channel_id = (
            self.settings.verification_log_channel_id
            or self.settings.server_log_channel_id
            or self.settings.mod_log_channel_id
        )
        if self.settings.verification_log_channel_id:
            try:
                verification_log_channel = self.get_channel(verification_log_channel_id) or await self.fetch_channel(
                    verification_log_channel_id
                )
                if isinstance(verification_log_channel, discord.TextChannel):
                    LOGGER.info("Verification log channel found: %s (%s)", verification_log_channel.name, verification_log_channel.id)
                else:
                    LOGGER.warning("VERIFICATION_LOG_CHANNEL_ID is not a text channel: %s", verification_log_channel_id)
            except discord.HTTPException:
                LOGGER.exception("Could not fetch verification log channel %s", verification_log_channel_id)
        else:
            LOGGER.info(
                "VERIFICATION_LOG_CHANNEL_ID not set. Verification logs will use SERVER_LOG_CHANNEL_ID or MOD_LOG_CHANNEL_ID."
            )

        if self.settings.welcome_channel_id:
            try:
                welcome_channel = self.get_channel(self.settings.welcome_channel_id) or await self.fetch_channel(
                    self.settings.welcome_channel_id
                )
                if isinstance(welcome_channel, discord.TextChannel):
                    LOGGER.info("Welcome channel found: %s (%s)", welcome_channel.name, welcome_channel.id)
                else:
                    LOGGER.warning("WELCOME_CHANNEL_ID is not a text channel: %s", self.settings.welcome_channel_id)
            except discord.HTTPException:
                LOGGER.exception("Could not fetch welcome channel %s", self.settings.welcome_channel_id)
        else:
            LOGGER.info("WELCOME_CHANNEL_ID not set. Automatic welcome messages are disabled.")

        try:
            application_channel = self.get_channel(self.settings.staff_application_channel_id) or await self.fetch_channel(
                self.settings.staff_application_channel_id
            )
            if isinstance(application_channel, discord.TextChannel):
                LOGGER.info(
                    "Staff application channel found: %s (%s)",
                    application_channel.name,
                    application_channel.id,
                )
            else:
                LOGGER.warning(
                    "STAFF_APPLICATION_CHANNEL_ID is not a text channel: %s",
                    self.settings.staff_application_channel_id,
                )
        except discord.HTTPException:
            LOGGER.exception(
                "Could not fetch staff application channel %s",
                self.settings.staff_application_channel_id,
            )

        if self.settings.level_up_channel_id:
            try:
                level_up_channel = self.get_channel(self.settings.level_up_channel_id) or await self.fetch_channel(
                    self.settings.level_up_channel_id
                )
                if isinstance(level_up_channel, discord.TextChannel):
                    LOGGER.info(
                        "Level-up channel found: %s (%s)",
                        level_up_channel.name,
                        level_up_channel.id,
                    )
                else:
                    LOGGER.warning(
                        "LEVEL_UP_CHANNEL_ID is not a text channel: %s",
                        self.settings.level_up_channel_id,
                    )
            except discord.HTTPException:
                LOGGER.exception("Could not fetch level-up channel %s", self.settings.level_up_channel_id)
        else:
            LOGGER.info("LEVEL_UP_CHANNEL_ID not set. Level-up messages will use the source chat channel.")

        if self.instagram_notifications_enabled():
            try:
                instagram_channel = self.get_channel(self.settings.instagram_notification_channel_id) or await self.fetch_channel(
                    self.settings.instagram_notification_channel_id
                )
                if isinstance(instagram_channel, discord.TextChannel):
                    LOGGER.info(
                        "Instagram notification channel found: %s (%s)",
                        instagram_channel.name,
                        instagram_channel.id,
                    )
                else:
                    LOGGER.warning(
                        "INSTAGRAM_NOTIFICATION_CHANNEL_ID is not a text channel: %s",
                        self.settings.instagram_notification_channel_id,
                    )
            except discord.HTTPException:
                LOGGER.exception(
                    "Could not fetch Instagram notification channel %s",
                    self.settings.instagram_notification_channel_id,
                )

            LOGGER.info(
                "Instagram notifier enabled | poll=%sm profile=%s feed=%s",
                self.settings.instagram_poll_minutes,
                self.settings.instagram_profile_name,
                self.settings.instagram_feed_url,
            )
        else:
            LOGGER.info("Instagram notifier disabled. Set INSTAGRAM_FEED_URL and INSTAGRAM_NOTIFICATION_CHANNEL_ID to enable it.")

        if self.settings.verified_role_id:
            for guild in self.guilds:
                role = guild.get_role(self.settings.verified_role_id)
                if role is not None:
                    LOGGER.info("Verified role found in %s: %s (%s)", guild.name, role.name, role.id)
                    break
            else:
                LOGGER.warning("VERIFIED_ROLE_ID was set but no matching role was found in the connected guilds.")
        else:
            LOGGER.info("VERIFIED_ROLE_ID not set. Verification falls back to the role name `Verified`.")

        LOGGER.info(
            "Anti-raid config | enabled=%s threshold=%s window=%ss lockdown=%sm account_age=%sm timeout=%sm",
            self.settings.anti_raid_enabled,
            self.settings.anti_raid_join_threshold,
            self.settings.anti_raid_window_seconds,
            self.settings.anti_raid_lockdown_minutes,
            self.settings.anti_raid_account_age_minutes,
            self.settings.anti_raid_timeout_minutes,
        )
        LOGGER.info(
            "Leveling storage backend: %s",
            "PostgreSQL" if self.uses_postgres else f"JSON file ({LEVEL_DATA_PATH})",
        )

    @tasks.loop(minutes=5)
    async def cleanup_inactive_modmail(self) -> None:
        expiry = utc_now() - timedelta(hours=MODMAIL_INACTIVITY_HOURS)
        stale_users = [user_id for user_id, session in self.modmail_sessions.items() if session.last_activity < expiry]
        if self.user is None:
            return
        for user_id in stale_users:
            await self.close_modmail(user_id, self.user, "Inactivity timeout")

    @cleanup_inactive_modmail.before_loop
    async def before_cleanup_inactive_modmail(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=10)
    async def instagram_feed_loop(self) -> None:
        await self.poll_instagram_feed_once()

    @instagram_feed_loop.before_loop
    async def before_instagram_feed_loop(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    settings = load_settings()
    bot = DyadiaGuardianBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

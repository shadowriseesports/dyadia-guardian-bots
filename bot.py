from __future__ import annotations

import json
import logging
import random
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Deque, Dict, List, Optional

import discord
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
BRAND_FOOTER = "Dyadia Guardian of HOK | NE India"
LEVEL_DATA_PATH = Path("level_data.json")
LEVEL_XP_COOLDOWN_SECONDS = 60
LEVEL_XP_GAIN_MIN = 15
LEVEL_XP_GAIN_MAX = 25
LEADERBOARD_LIMIT = 10
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


def xp_for_level(level: int) -> int:
    if level <= 0:
        return 0
    return (50 * level * level) + (100 * level)


def level_from_xp(xp: int) -> int:
    level = 0
    while xp_for_level(level + 1) <= xp:
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


class DyadiaGuardianBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        intents.members = True
        intents.message_content = True
        intents.dm_messages = True

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
        self.level_cooldowns: Dict[tuple[int, int], datetime] = {}
        self.modmail_view = OpenModmailView()
        self.close_modmail_view = CloseModmailView()
        self.staff_application_view = StaffApplicationView()

    async def setup_hook(self) -> None:
        self.load_level_data()
        self.register_commands()
        self.add_view(self.modmail_view)
        self.add_view(self.close_modmail_view)
        self.add_view(self.staff_application_view)
        self.cleanup_inactive_modmail.start()

    async def on_ready(self) -> None:
        synced = await self.tree.sync()
        if self.user is not None:
            activity = discord.CustomActivity(name=self.settings.bot_status_text)
            await self.change_presence(status=discord.Status.idle, activity=activity)
        LOGGER.info("Bot online as %s (%s)", self.user, self.user.id if self.user else "unknown")
        LOGGER.info("Synced %s application commands", len(synced))
        await self.validate_runtime_configuration()

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
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            LOGGER.info("DM received from %s (%s): %s", message.author, message.author.id, message.content or "[no text]")
            await self.handle_user_dm(message)
            return

        if isinstance(message.channel, discord.Thread):
            await self.handle_moderator_reply(message)

        if message.guild is not None and isinstance(message.author, discord.Member):
            await self.handle_leveling_message(message)

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild is None:
            return
        await self.handle_anti_raid_join(member)
        await self.sync_level_reward_role(member, announce=False)

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
                    "`/levelpanel` post the leveling system information panel"
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

        @tree.command(name="clear", description="Bulk delete recent messages")
        @app_commands.describe(amount="How many recent messages to remove", user="Only remove messages from this user")
        async def clear(
            interaction: discord.Interaction,
            amount: app_commands.Range[int, 1, 100],
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

        @tree.command(name="rank", description="Show your level and XP progress")
        @app_commands.describe(user="Optional member to inspect")
        async def rank(interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
            await self.handle_rank(interaction, user)

        @tree.command(name="leaderboard", description="Show the server leveling leaderboard")
        async def leaderboard(interaction: discord.Interaction) -> None:
            await self.handle_leaderboard(interaction)

        @tree.command(name="levelpanel", description="Post the leveling system info panel")
        @app_commands.describe(channel="Channel where the leveling panel should be posted")
        async def levelpanel(
            interaction: discord.Interaction,
            channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await self.handle_level_panel(interaction, channel)

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
            role = discord.utils.get(guild.roles, name=role_name)
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
                f"- XP can be earned once every {LEVEL_XP_COOLDOWN_SECONDS} seconds per member\n"
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
        reason: str,
        duration_text: Optional[str] = None,
    ) -> None:
        self.mod_logs.append(
            ModLogEntry(
                action=action,
                user_id=target.id,
                moderator_id=moderator.id,
                reason=reason,
                duration_text=duration_text,
            )
        )

    def load_level_data(self) -> None:
        self.level_data = {}
        if not LEVEL_DATA_PATH.exists():
            return

        try:
            raw = json.loads(LEVEL_DATA_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Failed to load leveling data from %s", LEVEL_DATA_PATH)
            return

        guilds = raw if isinstance(raw, dict) else {}
        for guild_id, members in guilds.items():
            try:
                parsed_guild_id = int(guild_id)
            except (TypeError, ValueError):
                continue

            guild_progress: Dict[int, LevelProgress] = {}
            if not isinstance(members, dict):
                continue
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

            self.level_data[parsed_guild_id] = guild_progress

        LOGGER.info("Loaded leveling data for %s guild(s) from %s", len(self.level_data), LEVEL_DATA_PATH)

    def save_level_data(self) -> None:
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
        level = level_from_xp(progress.xp)
        eligible_role_name = get_reward_role_name(level)
        reward_role_names = {role_name for _, role_name in LEVEL_REWARD_ROLES}
        roles_to_remove = [role for role in member.roles if role.name in reward_role_names]

        role_to_add = discord.utils.get(member.guild.roles, name=eligible_role_name) if eligible_role_name else None
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
        cooldown_key = (member.guild.id, member.id)
        last_award = self.level_cooldowns.get(cooldown_key)
        if last_award is not None and (now - last_award) < timedelta(seconds=LEVEL_XP_COOLDOWN_SECONDS):
            return None

        progress = self.get_level_progress(member.guild.id, member.id)
        old_level = level_from_xp(progress.xp)
        gained_xp = random.randint(LEVEL_XP_GAIN_MIN, LEVEL_XP_GAIN_MAX)
        progress.xp += gained_xp
        progress.messages += 1
        progress.last_message_at = now
        self.level_cooldowns[cooldown_key] = now
        self.save_level_data()

        new_level = level_from_xp(progress.xp)
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
        await message.channel.send(embed=embed, delete_after=20)

    def create_rank_embed(self, member: discord.Member) -> discord.Embed:
        progress = self.get_level_progress(member.guild.id, member.id)
        level = level_from_xp(progress.xp)
        current_level_xp = xp_for_level(level)
        next_level_xp = xp_for_level(level + 1)
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
        await self.add_modlog("WARN", user, interaction.user, reason)
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
        await self.add_modlog("MUTE", user, interaction.user, reason, format_duration(duration))
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
        await self.add_modlog("KICK", user, interaction.user, reason)
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
        await self.add_modlog("BAN", user, interaction.user, reason)
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
        await self.add_modlog("UNBAN", ban_entry.user, interaction.user, reason)
        await interaction.followup.send(embed=embed, ephemeral=True)

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

        deleted = await interaction.channel.purge(limit=min(100, amount + 50), check=should_delete, bulk=True)
        target = user or interaction.user
        embed = self.create_modlog_embed("CLEAR", target, interaction.user, f"Cleared {len(deleted)} message(s)")
        await self.send_modlog(embed)
        await self.add_modlog("CLEAR", target, interaction.user, f"Cleared {len(deleted)} message(s)")
        await interaction.followup.send(f"Deleted {len(deleted)} message(s).", ephemeral=True)

    async def handle_modlogs(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await self.ensure_staff(interaction, "moderate_members"):
            return

        related = [entry for entry in reversed(self.mod_logs) if entry.user_id == user.id][:10]
        description = "\n".join(
            f"`{entry.action}` by <@{entry.moderator_id}> - {entry.reason}"
            + (f" ({entry.duration_text})" if entry.duration_text else "")
            for entry in related
        ) or "No in-memory moderation entries found for this user yet."

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
                f"**#{index}** {display_name} - Level {level_from_xp(progress.xp)} ({progress.xp} XP)"
            )

        embed = make_embed(
            "Leveling Leaderboard",
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
        await self.add_modlog("ANTI-RAID", member, self.user or member.guild.me or member, reason)

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
        LOGGER.info(
            "Anti-raid config | enabled=%s threshold=%s window=%ss lockdown=%sm account_age=%sm timeout=%sm",
            self.settings.anti_raid_enabled,
            self.settings.anti_raid_join_threshold,
            self.settings.anti_raid_window_seconds,
            self.settings.anti_raid_lockdown_minutes,
            self.settings.anti_raid_account_age_minutes,
            self.settings.anti_raid_timeout_minutes,
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


def main() -> None:
    settings = load_settings()
    bot = DyadiaGuardianBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

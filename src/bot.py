import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from src.badge_watcher import BadgeWatcher
from src.checker import Checker, SendStatus
from src.config import Settings
from src.db.records import ALL_REWARDS, ChatBadge
from src.drops_api import DropsApiClient, GameDrops, Route, badge_offers
from src.notifications import (
    batch_embeds,
    build_badge_embed,
    build_badges_list_embeds,
    build_games_list_embeds,
    build_help_embed,
    build_offers_list_embeds,
    build_subscriptions_list_embed,
)
from src.storage import Storage
from src.twitch import TwitchClient, TwitchError

log = logging.getLogger(__name__)

ALL_REWARDS_NAME = "Бейджи: новые, из Drops и reward-кампаний"
BADGE_SEARCH_LIMIT = 50
NEWEST_BADGES = 25
AUTOCOMPLETE_LIMIT = 25
CHOICE_LIMIT = 100
NO_DATA = "Данные о кампаниях ещё не загружены — попробуйте через пару минут."


@dataclass(frozen=True, slots=True)
class Services:
    storage: Storage
    drops_api: DropsApiClient
    twitch: TwitchClient
    checker: Checker
    badge_watcher: BadgeWatcher


class DiscordNotifier:
    def __init__(self, client: discord.Client) -> None:
        self._client = client

    async def send(self, channel_id: int, embed: discord.Embed) -> SendStatus:
        try:
            channel = self._client.get_channel(channel_id) or await self._client.fetch_channel(
                channel_id
            )
        except discord.NotFound:
            return SendStatus.GONE
        except discord.HTTPException as exc:
            log.warning("cannot fetch channel %s: %s", channel_id, exc)
            return SendStatus.FAILED
        if not isinstance(channel, discord.abc.Messageable):
            return SendStatus.GONE
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.NotFound:
            return SendStatus.GONE
        except discord.Forbidden:
            log.warning("no permission to post in channel %s", channel_id)
            return SendStatus.FAILED
        except discord.HTTPException as exc:
            log.warning("sending to channel %s failed: %s", channel_id, exc)
            return SendStatus.FAILED
        return SendStatus.SENT


class BadgeBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True  # channel/guild lifecycle events; no privileged intents needed
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self._services: Services | None = None
        self._on_first_ready: Callable[[], Awaitable[None]] | None = None
        self._was_ready = False

    def attach(
        self, services: Services, *, on_first_ready: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        self._services = services
        self._on_first_ready = on_first_ready

    async def setup_hook(self) -> None:
        if self._services is None:
            raise RuntimeError("BadgeBot.attach() must be called before start()")
        await self.add_cog(BadgeBotCog(self, self._services))
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("synced %d slash commands", len(synced))

    async def on_ready(self) -> None:
        log.info("Bot is ready as %s (guilds: %d)", self.user, len(self.guilds))
        if self._was_ready:
            return
        self._was_ready = True
        if self._on_first_ready is not None:
            await self._on_first_ready()


class BadgeBotCog(commands.Cog):
    def __init__(self, bot: BadgeBot, services: Services) -> None:
        self.bot = bot
        self.storage = services.storage
        self.drops_api = services.drops_api
        self.twitch = services.twitch
        self.checker = services.checker
        self.badge_watcher = services.badge_watcher

    # --- browsing ---

    @app_commands.command(name="games", description="Игры с активными Twitch Drops")
    @app_commands.rename(search="поиск")
    @app_commands.describe(search="Часть названия игры")
    @app_commands.guild_only()
    async def games(self, interaction: discord.Interaction, search: str | None = None) -> None:
        snapshot = self.drops_api.drops
        if snapshot is None:
            await _reply(interaction, NO_DATA, ephemeral=True)
            return
        now = datetime.now(UTC)
        games = snapshot.active_games(now)
        if search:
            games = [g for g in games if search.casefold() in g.game_display_name.casefold()]
        await _reply_embeds(
            interaction, build_games_list_embeds(games, now), _freshness(snapshot.fetched_at)
        )

    @app_commands.command(
        name="rewards", description="Бейджи и награды, которые можно получить сейчас"
    )
    @app_commands.guild_only()
    async def rewards(self, interaction: discord.Interaction) -> None:
        drops, rewards = self.drops_api.drops, self.drops_api.rewards
        if drops is None and rewards is None:
            await _reply(interaction, NO_DATA, ephemeral=True)
            return
        offers = badge_offers(drops, rewards, datetime.now(UTC))
        fetched = min(s.fetched_at for s in (drops, rewards) if s is not None)
        await _reply_embeds(interaction, build_offers_list_embeds(offers), _freshness(fetched))

    @app_commands.command(name="badges", description="Каталог глобальных бейджей Twitch")
    @app_commands.rename(search="поиск")
    @app_commands.describe(search="Часть названия бейджа")
    @app_commands.guild_only()
    async def badges(self, interaction: discord.Interaction, search: str | None = None) -> None:
        if search:
            found = await self.storage.search_badges(search, BADGE_SEARCH_LIMIT)
            title = f"🏅 Бейджи: «{search}»"
        else:
            found = await self.storage.search_badges(None, NEWEST_BADGES)
            title = "🏅 Недавно обнаруженные бейджи"
        await _reply_embeds(interaction, build_badges_list_embeds(found, title), None)

    @app_commands.command(name="badge", description="Карточка бейджа: описание и как получить")
    @app_commands.rename(badge_key="бейдж")
    @app_commands.describe(badge_key="Название бейджа")
    @app_commands.guild_only()
    async def badge(self, interaction: discord.Interaction, badge_key: str) -> None:
        badge = await self._resolve_badge(badge_key)
        if badge is None:
            await _reply(
                interaction, f"Бейдж «{badge_key}» не найден. См. /badges.", ephemeral=True
            )
            return
        offers = self.badge_watcher.offers_for(badge)
        await _reply(interaction, embed=build_badge_embed(badge, offers))

    @badge.autocomplete("badge_key")
    async def badge_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        found = await self.storage.search_badges(current or None, AUTOCOMPLETE_LIMIT)
        return [
            app_commands.Choice(
                name=f"{b.title} ({b.set_id} v{b.version_id})"[:CHOICE_LIMIT],
                value=f"{b.set_id}/{b.version_id}"[:CHOICE_LIMIT],
            )
            for b in found
        ]

    @app_commands.command(name="list", description="Подписки этого канала")
    @app_commands.guild_only()
    async def list_subscriptions(self, interaction: discord.Interaction) -> None:
        subscriptions = await self.storage.list_subscriptions(interaction.channel_id or 0)
        await _reply(interaction, embed=build_subscriptions_list_embed(subscriptions))

    @app_commands.command(name="help", description="Справка по командам")
    async def help(self, interaction: discord.Interaction) -> None:
        await _reply(interaction, embed=build_help_embed(), ephemeral=True)

    # --- subscriptions ---

    @app_commands.command(name="follow", description="Подписать канал на Drops игры")
    @app_commands.rename(game="игра")
    @app_commands.describe(game="Название игры")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def follow(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer(thinking=True)
        channel_id = await self._register_channel(interaction)
        if channel_id is None:
            return
        resolved = await self._resolve_game(game)
        if resolved is None:
            await _reply(interaction, f"Игра «{game}» не найдена на Twitch.", ephemeral=True)
            return
        game_id, game_name = resolved
        subscription = await self.storage.add_subscription(channel_id, "game", game_id, game_name)
        if subscription is None:
            await _reply(interaction, f"Канал уже подписан на **{game_name}**.")
            return
        known = self.drops_api.drops.find_game(game_id) if self.drops_api.drops else None
        active = len(known.active_campaigns(datetime.now(UTC))) if known else 0
        await _reply(
            interaction,
            f"Подписал канал на **{game_name}** (ID `#{subscription.id}`). "
            + (f"Активных кампаний: {active}." if active else "Сейчас активных кампаний нет."),
        )
        await self.checker.deliver_game_to_channel(channel_id, game_id)

    @follow.autocomplete("game")
    async def follow_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        snapshot = self.drops_api.drops
        if snapshot is None:
            return []
        needle = current.casefold()
        games = [
            g
            for g in snapshot.active_games(datetime.now(UTC))
            if needle in g.game_display_name.casefold()
        ]
        games.sort(key=lambda g: g.game_display_name.casefold())
        return [
            app_commands.Choice(name=g.game_display_name[:CHOICE_LIMIT], value=g.game_id)
            for g in games[:AUTOCOMPLETE_LIMIT]
        ]

    @app_commands.command(name="unfollow", description="Отписать канал по ID подписки из /list")
    @app_commands.rename(subscription_id="id")
    @app_commands.describe(subscription_id="ID подписки из /list")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unfollow(self, interaction: discord.Interaction, subscription_id: int) -> None:
        removed = await self.storage.remove_subscription(
            interaction.channel_id or 0, subscription_id
        )
        message = (
            f"Подписка `#{subscription_id}` удалена."
            if removed
            else f"В этом канале нет подписки `#{subscription_id}`. См. /list."
        )
        await _reply(interaction, message, ephemeral=not removed)

    @app_commands.command(
        name="followbadges", description="Подписать канал на новые бейджи и badge-кампании"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def follow_badges(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        channel_id = await self._register_channel(interaction)
        if channel_id is None:
            return
        subscription = await self.storage.add_subscription(
            channel_id, "reward", ALL_REWARDS, ALL_REWARDS_NAME
        )
        if subscription is None:
            await _reply(interaction, "Канал уже подписан на badge-кампании.")
            return
        await _reply(
            interaction,
            f"Подписал канал на бейджи (ID `#{subscription.id}`): новые глобальные бейджи, "
            "бейджи из Drops и reward-кампании.",
        )
        await self.checker.deliver_badges_to_channel(channel_id)
        await self.badge_watcher.announce_to_channel(channel_id)

    @app_commands.command(name="unfollowbadges", description="Отписать канал от бейджей")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unfollow_badges(self, interaction: discord.Interaction) -> None:
        removed = await self.storage.remove_subscription_by_target(
            interaction.channel_id or 0, "reward", ALL_REWARDS
        )
        message = "Отписал канал от badge-кампаний." if removed else "Канал не был подписан."
        await _reply(interaction, message, ephemeral=not removed)

    @app_commands.command(name="checknow", description="Проверить кампании сейчас")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def check_now(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        report = await self.checker.run()
        lines = [f"Проверка завершена, отправлено уведомлений: {report.sent}."]
        if report.failed:
            lines.append(f"Не удалось отправить: {report.failed} (повторю при следующей проверке).")
        if not (report.drops_refreshed or report.rewards_refreshed):
            next_at = min(self.drops_api.next_allowed_at(r) for r in Route)
            lines.append(
                "Sunkwi API нельзя опрашивать чаще лимита, проверил по сохранённым данным. "
                f"Следующий запрос к API — {_ts(next_at)}."
            )
        await _reply(interaction, "\n".join(lines), ephemeral=True)

    # --- lifecycle ---

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if await self.storage.delete_channel(channel.id):
            log.info("channel %s deleted, subscriptions removed", channel.id)

    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        await self.storage.delete_channel(payload.thread_id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        removed = await self.storage.delete_guild_channels(guild.id)
        log.info("removed from guild %s, dropped %d channels", guild.id, removed)

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await _reply(interaction, "Нужно право «Управлять каналами».", ephemeral=True)
            return
        if isinstance(error, app_commands.NoPrivateMessage):
            await _reply(interaction, "Команда работает только на сервере.", ephemeral=True)
            return
        log.exception("command %s failed", interaction.command and interaction.command.name)
        await _reply(interaction, "Что-то пошло не так, попробуйте позже.", ephemeral=True)

    # --- helpers ---

    async def _register_channel(self, interaction: discord.Interaction) -> int | None:
        channel = interaction.channel
        if interaction.guild is None or not isinstance(channel, discord.abc.Messageable):
            await _reply(interaction, "Здесь нельзя оформить подписку.", ephemeral=True)
            return None
        name = getattr(channel, "name", None) or str(interaction.channel_id)
        stored = await self.storage.ensure_channel(channel.id, interaction.guild.id, name)
        return stored.id

    async def _resolve_badge(self, value: str) -> ChatBadge | None:
        """Autocomplete passes "set_id/version_id"; free text picks the newest title match."""
        set_id, sep, version_id = value.partition("/")
        if sep and (badge := await self.storage.get_badge(set_id, version_id)):
            return badge
        found = await self.storage.search_badges(value, BADGE_SEARCH_LIMIT)
        exact = [b for b in found if b.title.casefold() == value.strip().casefold()]
        return (exact or found or [None])[0]

    async def _resolve_game(self, value: str) -> tuple[str, str] | None:
        """Autocomplete passes a game id; free text is matched by name, then via Helix."""
        snapshot = self.drops_api.drops
        known: list[GameDrops] = snapshot.games if snapshot else []
        for game in known:
            if game.game_id == value:
                return game.game_id, game.game_display_name
        for game in known:
            if game.game_display_name.casefold() == value.strip().casefold():
                return game.game_id, game.game_display_name
        try:
            found = await self.twitch.get_game(value.strip())
        except TwitchError as exc:
            log.warning("Twitch game lookup failed: %s", exc)
            return None
        return (found.id, found.name) if found else None


async def _reply(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    ephemeral: bool = False,
) -> None:
    kwargs = {"content": content, "ephemeral": ephemeral}
    if embed is not None:
        kwargs["embed"] = embed
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


async def _reply_embeds(
    interaction: discord.Interaction, embeds: list[discord.Embed], content: str | None
) -> None:
    for index, batch in enumerate(batch_embeds(embeds)):
        text = content if index == 0 else None
        if index == 0 and not interaction.response.is_done():
            await interaction.response.send_message(content=text, embeds=batch)
        else:
            await interaction.followup.send(content=text, embeds=batch)


def _freshness(fetched_at: datetime) -> str:
    return f"Данные Sunkwi от {_ts(fetched_at)}."


def _ts(moment: datetime) -> str:
    return f"<t:{int(moment.timestamp())}:R>"

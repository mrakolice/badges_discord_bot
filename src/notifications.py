from collections.abc import Iterable, Sequence
from datetime import datetime

import discord

from src.db.records import Subscription
from src.drops_api import DropCampaign, GameDrops, RewardCampaign
from src.twitch import TwitchStream

# Discord limits: https://discord.com/developers/docs/resources/message#embed-object-embed-limits
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_VALUE_LIMIT = 1024
EMBED_TOTAL_LIMIT = 6000
EMBEDS_PER_MESSAGE = 10
LIST_CHUNK_LIMIT = 3900

DROP_COLOR = discord.Color.purple()
REWARD_COLOR = discord.Color.gold()
LIVE_COLOR = discord.Color.red()
ENDING_COLOR = discord.Color.orange()
INFO_COLOR = discord.Color.blurple()

DROPS_INVENTORY_URL = "https://www.twitch.tv/drops/inventory"
DROPS_CAMPAIGNS_URL = "https://www.twitch.tv/drops/campaigns"


def build_drop_embed(
    game: GameDrops, campaign: DropCampaign, *, ending_soon: bool = False
) -> discord.Embed:
    title = f"⏰ Скоро закончится: {campaign.name}" if ending_soon else f"🎁 {campaign.name}"
    embed = discord.Embed(
        title=_cut(title, TITLE_LIMIT),
        url=f"{DROPS_CAMPAIGNS_URL}?dropID={campaign.id}",
        description=_cut(campaign.description or "", DESCRIPTION_LIMIT) or None,
        color=ENDING_COLOR if ending_soon else DROP_COLOR,
    )
    embed.set_author(name=game.game_display_name, url=_game_url(campaign))
    if campaign.benefits:
        rewards = "\n".join(f"• {b.name}" for b in campaign.benefits)
        embed.add_field(name="Награды", value=_cut(rewards, FIELD_VALUE_LIMIT), inline=False)
    if campaign.max_minutes_watched:
        embed.add_field(name="Смотреть", value=_minutes(campaign.max_minutes_watched))
    embed.add_field(name="Заканчивается", value=_ts(campaign.end_at))
    if channels := campaign.restricted_channels:
        names = ", ".join(c.display_name or c.name for c in channels[:15])
        more = f" и ещё {len(channels) - 15}" if len(channels) > 15 else ""
        embed.add_field(
            name="Только на каналах", value=_cut(names + more, FIELD_VALUE_LIMIT), inline=False
        )
    if campaign.account_link_url:
        embed.add_field(name="Привязка аккаунта", value=f"[Привязать]({campaign.account_link_url})")
    if game.game_box_art_url:
        embed.set_thumbnail(url=game.game_box_art_url)
    return embed


def build_reward_embed(campaign: RewardCampaign, *, ending_soon: bool = False) -> discord.Embed:
    title = f"⏰ Скоро закончится: {campaign.name}" if ending_soon else f"🏅 {campaign.name}"
    embed = discord.Embed(
        title=_cut(title, TITLE_LIMIT),
        url=campaign.about_url or campaign.external_url or DROPS_CAMPAIGNS_URL,
        description=_cut(campaign.summary or "", DESCRIPTION_LIMIT) or None,
        color=ENDING_COLOR if ending_soon else REWARD_COLOR,
    )
    if campaign.brand:
        embed.set_author(name=campaign.brand)
    if campaign.rewards:
        rewards = "\n".join(f"• {r.name}" for r in campaign.rewards)
        embed.add_field(name="Награды", value=_cut(rewards, FIELD_VALUE_LIMIT), inline=False)
    requirements = campaign.unlock_requirements
    if requirements.minute_watched_goal:
        embed.add_field(name="Смотреть", value=_minutes(requirements.minute_watched_goal))
    if requirements.subs_goal:
        embed.add_field(name="Подписок", value=str(requirements.subs_goal))
    embed.add_field(name="Заканчивается", value=_ts(campaign.ends_at))
    if campaign.image and campaign.image.url:
        embed.set_thumbnail(url=campaign.image.url)
    return embed


def build_live_embed(game: GameDrops, stream: TwitchStream) -> discord.Embed:
    embed = discord.Embed(
        title=_cut(f"🔴 Сейчас в эфире: {stream.user_name}", TITLE_LIMIT),
        url=stream.url,
        description=_cut(stream.title, DESCRIPTION_LIMIT) or None,
        color=LIVE_COLOR,
    )
    embed.set_author(name=game.game_display_name)
    embed.add_field(name="Зрителей", value=str(stream.viewer_count))
    embed.add_field(name="Прогресс дропов", value=f"[Инвентарь]({DROPS_INVENTORY_URL})")
    return embed


def build_games_list_embeds(games: Sequence[GameDrops], now: datetime) -> list[discord.Embed]:
    lines = []
    for game in sorted(games, key=lambda g: g.game_display_name.lower()):
        active = game.active_campaigns(now)
        ends = min(c.end_at for c in active)
        lines.append(f"**{game.game_display_name}** — кампаний: {len(active)}, до {_ts(ends, 'd')}")
    return _list_embeds("🎮 Игры с активными Drops", lines, DROP_COLOR, empty="Сейчас пусто.")


def build_rewards_list_embeds(campaigns: Sequence[RewardCampaign]) -> list[discord.Embed]:
    lines = [
        f"**[{c.name}]({c.about_url or c.external_url or DROPS_CAMPAIGNS_URL})** — до "
        f"{_ts(c.ends_at, 'd')}"
        for c in sorted(campaigns, key=lambda c: c.ends_at)
    ]
    return _list_embeds(
        "🏅 Активные reward/badge-кампании", lines, REWARD_COLOR, empty="Сейчас пусто."
    )


def build_subscriptions_list_embed(subscriptions: Sequence[Subscription]) -> discord.Embed:
    lines = [
        f"`#{s.id}` {'🎮' if s.type == 'game' else '🏅'} {s.target_name}" for s in subscriptions
    ]
    return _list_embeds(
        "📋 Подписки канала", lines, INFO_COLOR, empty="Подписок нет. См. /follow и /followbadges."
    )[0]


def build_help_embed() -> discord.Embed:
    embed = discord.Embed(title="Twitch Drops бот", color=INFO_COLOR)
    embed.description = (
        "Сообщаю о Twitch Drops и reward/badge-кампаниях. Стримы я не смотрю — "
        "только присылаю ссылки, смотреть нужно самим."
    )
    embed.add_field(
        name="Просмотр",
        value="`/games` — игры с Drops\n`/rewards` — badge-кампании\n`/list` — подписки канала",
        inline=False,
    )
    embed.add_field(
        name="Подписки (нужно право «Управлять каналами»)",
        value=(
            "`/follow <игра>` — Drops игры\n`/unfollow <id>` — отписаться по ID из /list\n"
            "`/followbadges` / `/unfollowbadges` — badge-кампании\n"
            "`/checknow` — проверить сейчас"
        ),
        inline=False,
    )
    return embed


def batch_embeds(embeds: Iterable[discord.Embed]) -> list[list[discord.Embed]]:
    """Groups embeds into messages within Discord's per-message count and size limits."""
    batches: list[list[discord.Embed]] = []
    current: list[discord.Embed] = []
    size = 0
    for embed in embeds:
        length = len(embed)
        if current and (len(current) == EMBEDS_PER_MESSAGE or size + length > EMBED_TOTAL_LIMIT):
            batches.append(current)
            current, size = [], 0
        current.append(embed)
        size += length
    if current:
        batches.append(current)
    return batches


def _list_embeds(
    title: str, lines: Sequence[str], color: discord.Color, *, empty: str
) -> list[discord.Embed]:
    if not lines:
        return [discord.Embed(title=title, description=empty, color=color)]
    chunks: list[str] = []
    current = ""
    for line in lines:
        line = _cut(line, LIST_CHUNK_LIMIT)
        if current and len(current) + len(line) + 1 > LIST_CHUNK_LIMIT:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    chunks.append(current)
    total = len(chunks)
    return [
        discord.Embed(
            title=title if total == 1 else f"{title} ({i}/{total})", description=chunk, color=color
        )
        for i, chunk in enumerate(chunks, start=1)
    ]


def _game_url(campaign: DropCampaign) -> str | None:
    if campaign.game.slug:
        return f"https://www.twitch.tv/directory/category/{campaign.game.slug}"
    return None


def _ts(moment: datetime, style: str = "R") -> str:
    """Discord renders this in each reader's own timezone."""
    return f"<t:{int(moment.timestamp())}:{style}>"


def _minutes(total: int) -> str:
    hours, minutes = divmod(total, 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    return f"{hours} ч" if hours else f"{minutes} мин"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"

from collections.abc import Iterable, Sequence
from datetime import datetime

import discord

from src.db.records import ChatBadge, Subscription
from src.drops_api import BadgeOffer, DropCampaign, GameDrops, RewardCampaign
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


def build_drop_embed(
    game: GameDrops, campaign: DropCampaign, *, ending_soon: bool = False
) -> discord.Embed:
    badges = campaign.badge_benefits
    icon = "🏅" if badges else "🎁"
    title = f"⏰ Скоро закончится: {campaign.name}" if ending_soon else f"{icon} {campaign.name}"
    embed = discord.Embed(
        title=_cut(title, TITLE_LIMIT),
        url=campaign.url,
        description=_cut(campaign.description or "", DESCRIPTION_LIMIT) or None,
        color=ENDING_COLOR if ending_soon else DROP_COLOR,
    )
    embed.set_author(name=game.game_display_name, url=_game_url(campaign))
    if badges:
        lines = "\n".join(f"• {b.name} — {_watch(campaign.minutes_for(b.id))}" for b in badges)
        embed.add_field(name="🏅 Бейдж в чат", value=_cut(lines, FIELD_VALUE_LIMIT), inline=False)
    if others := [b for b in campaign.benefits if not b.is_badge]:
        rewards = "\n".join(f"• {b.name}" for b in others)
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
    thumbnail = next((b.image_asset_url for b in badges if b.image_asset_url), None)
    if thumbnail or game.game_box_art_url:
        embed.set_thumbnail(url=thumbnail or game.game_box_art_url)
    return embed


def build_reward_embed(campaign: RewardCampaign, *, ending_soon: bool = False) -> discord.Embed:
    title = f"⏰ Скоро закончится: {campaign.name}" if ending_soon else f"🏅 {campaign.name}"
    embed = discord.Embed(
        title=_cut(title, TITLE_LIMIT),
        url=campaign.url,
        description=_cut(campaign.summary or "", DESCRIPTION_LIMIT) or None,
        color=ENDING_COLOR if ending_soon else REWARD_COLOR,
    )
    if campaign.brand:
        embed.set_author(name=campaign.brand)
    if campaign.rewards:
        lines = []
        for item in campaign.rewards:
            line = f"[{item.name}]({item.redemption_url})" if item.redemption_url else item.name
            if item.earnable_until and item.earnable_until != campaign.ends_at:
                line += f" — до {_ts(item.earnable_until, 'd')}"
            lines.append(f"• {line}")
        embed.add_field(
            name="Награды", value=_cut("\n".join(lines), FIELD_VALUE_LIMIT), inline=False
        )
    instructions = [campaign.instructions or ""]
    instructions += [r.redemption_instructions or "" for r in campaign.rewards]
    if text := "\n".join(dict.fromkeys(i.strip() for i in instructions if i.strip())):
        embed.add_field(name="Как получить", value=_cut(text, FIELD_VALUE_LIMIT), inline=False)
    requirements = campaign.unlock_requirements
    if requirements.minute_watched_goal:
        embed.add_field(name="Смотреть", value=_minutes(requirements.minute_watched_goal))
    if requirements.subs_goal:
        embed.add_field(name="Подписок", value=str(requirements.subs_goal))
    embed.add_field(name="Заканчивается", value=_ts(campaign.ends_at))
    item_image = next((r.thumbnail_image.url for r in campaign.rewards if r.thumbnail_image), None)
    if image := item_image or (campaign.image.url if campaign.image else None):
        embed.set_thumbnail(url=image)
    return embed


def build_badge_embed(
    badge: ChatBadge, offers: Sequence[BadgeOffer], *, new: bool = False
) -> discord.Embed:
    """A global chat badge from the Helix catalog, with campaigns that grant it (if known)."""
    title = f"🆕 Новый бейдж: {badge.title}" if new else f"🏅 {badge.title}"
    embed = discord.Embed(
        title=_cut(title, TITLE_LIMIT),
        url=badge.click_url or (offers[0].campaign_url if offers else None),
        description=_cut(badge.description or "", DESCRIPTION_LIMIT) or None,
        color=REWARD_COLOR,
    )
    embed.set_thumbnail(url=badge.image_url_4x)
    if offers:
        embed.add_field(
            name="Как получить", value=_cut(_offer_lines(offers), FIELD_VALUE_LIMIT), inline=False
        )
    embed.add_field(name="Обнаружен", value=_ts(badge.first_seen_at, "D"))
    embed.add_field(name="ID", value=f"`{badge.set_id}` v{badge.version_id}")
    return embed


def build_badges_list_embeds(badges: Sequence[ChatBadge], title: str) -> list[discord.Embed]:
    lines = [
        f"**{b.title}** — с {_ts(b.first_seen_at, 'd')}"
        + (f" · [ссылка]({b.click_url})" if b.click_url else "")
        for b in badges
    ]
    return _list_embeds(title, lines, REWARD_COLOR, empty="Ничего не найдено.")


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


def build_offers_list_embeds(offers: Sequence[BadgeOffer]) -> list[discord.Embed]:
    lines = [_offer_line(o, with_name=True) for o in offers]
    return _list_embeds(
        "🏅 Бейджи и награды, которые можно получить сейчас",
        lines,
        REWARD_COLOR,
        empty="Сейчас пусто.",
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
        value=(
            "`/games` — игры с Drops\n`/rewards` — бейджи и награды, доступные сейчас\n"
            "`/badges` — каталог бейджей Twitch\n`/badge` — карточка бейджа\n"
            "`/list` — подписки канала"
        ),
        inline=False,
    )
    embed.add_field(
        name="Подписки (нужно право «Управлять каналами»)",
        value=(
            "`/follow <игра>` — Drops игры\n`/unfollow <id>` — отписаться по ID из /list\n"
            "`/followbadges` / `/unfollowbadges` — новые бейджи и badge-кампании\n"
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


def _offer_lines(offers: Sequence[BadgeOffer]) -> str:
    return "\n".join(f"• {_offer_line(o, with_name=False)}" for o in offers)


def _offer_line(offer: BadgeOffer, *, with_name: bool) -> str:
    where = f"{offer.game_name}: {offer.campaign_name}" if offer.game_name else offer.campaign_name
    head = (
        f"**[{offer.name}]({offer.campaign_url})** — {where}"
        if with_name
        else (f"[{where}]({offer.campaign_url})")
    )
    parts = [head, _watch(offer.minutes), f"до {_ts(offer.ends_at, 'd')}"]
    if offer.only_channels:
        shown = ", ".join(offer.only_channels[:3])
        more = f" +{len(offer.only_channels) - 3}" if len(offer.only_channels) > 3 else ""
        parts.append(f"только у {shown}{more}")
    return ", ".join(parts)


def _watch(minutes: int) -> str:
    return f"смотреть {_minutes(minutes)}" if minutes else "без просмотра"


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

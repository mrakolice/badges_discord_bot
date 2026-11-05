from collections.abc import Sequence
from typing import Any

from src.db.base import QueryExecutor, Row
from src.db.queries import (
    Announcements,
    BadgeAnnouncements,
    BadgeCatalog,
    BotState,
    CampaignTracking,
    ChannelRegistry,
    Follows,
    LiveAlerts,
    Query,
)
from src.db.records import (
    BadgeData,
    CampaignSeen,
    CampaignType,
    Channel,
    ChatBadge,
    DeliveryKind,
    Subscription,
    SubscriptionType,
)


class Storage:
    """Driver-agnostic repository: knows query enums and parameters, never SQL."""

    def __init__(self, executor: QueryExecutor) -> None:
        self._db = executor

    # channel_registry

    async def ensure_channel(self, channel_id: int, guild_id: int, name: str) -> Channel:
        row = await self._db.fetchrow(
            ChannelRegistry.UPSERT, channel_id=channel_id, guild_id=guild_id, name=name
        )
        return Channel.model_validate(_required(row, ChannelRegistry.UPSERT))

    async def get_channel(self, channel_id: int) -> Channel | None:
        row = await self._db.fetchrow(ChannelRegistry.GET, channel_id=channel_id)
        return Channel.model_validate(row) if row else None

    async def delete_channel(self, channel_id: int) -> bool:
        """Drops the channel with all its subscriptions and delivery history."""
        return await self._db.fetchval(ChannelRegistry.DELETE, channel_id=channel_id) is not None

    async def delete_guild_channels(self, guild_id: int) -> int:
        return len(await self._db.fetch(ChannelRegistry.DELETE_GUILD, guild_id=guild_id))

    # follows

    async def add_subscription(
        self, channel_id: int, type: SubscriptionType, target_id: str, target_name: str
    ) -> Subscription | None:
        """Returns None when the channel is already subscribed to this target."""
        row = await self._db.fetchrow(
            Follows.ADD,
            channel_id=channel_id,
            type=type,
            target_id=target_id,
            target_name=target_name,
        )
        return Subscription.model_validate(row) if row else None

    async def remove_subscription(self, channel_id: int, subscription_id: int) -> bool:
        removed = await self._db.fetchval(
            Follows.REMOVE, channel_id=channel_id, subscription_id=subscription_id
        )
        return removed is not None

    async def remove_subscription_by_target(
        self, channel_id: int, type: SubscriptionType, target_id: str
    ) -> bool:
        removed = await self._db.fetchval(
            Follows.REMOVE,
            channel_id=channel_id,
            subscription_id=None,
            type=type,
            target_id=target_id,
        )
        return removed is not None

    async def list_subscriptions(
        self, channel_id: int, type: SubscriptionType | None = None
    ) -> list[Subscription]:
        rows = await self._db.fetch(Follows.LIST_FOR_CHANNEL, channel_id=channel_id, type=type)
        return [Subscription.model_validate(r) for r in rows]

    async def get_all_subscriptions(
        self, type: SubscriptionType | None = None
    ) -> list[Subscription]:
        rows = await self._db.fetch(Follows.LIST_ALL, type=type)
        return [Subscription.model_validate(r) for r in rows]

    async def get_channels_for_target(self, type: SubscriptionType, target_id: str) -> list[int]:
        rows = await self._db.fetch(Follows.CHANNELS_FOR_TARGET, type=type, target_id=target_id)
        return [r["channel_id"] for r in rows]

    # campaign_tracking

    async def upsert_campaign(
        self, campaign_id: str, campaign_type: CampaignType, data: dict[str, Any]
    ) -> tuple[CampaignSeen, bool]:
        """Stores the latest payload; the flag is True when the campaign is seen first time."""
        row = _required(
            await self._db.fetchrow(
                CampaignTracking.UPSERT_SEEN,
                campaign_id=campaign_id,
                campaign_type=campaign_type,
                data=data,
            ),
            CampaignTracking.UPSERT_SEEN,
        )
        return CampaignSeen.model_validate(row), bool(row["inserted"])

    async def get_campaign(
        self, campaign_id: str, campaign_type: CampaignType
    ) -> CampaignSeen | None:
        row = await self._db.fetchrow(
            CampaignTracking.GET, campaign_id=campaign_id, campaign_type=campaign_type
        )
        return CampaignSeen.model_validate(row) if row else None

    async def prune_campaigns(self, unseen_for_days: int) -> int:
        return int(await self._db.fetchval(CampaignTracking.PRUNE_UNSEEN, days=unseen_for_days))

    # announcements

    async def claim_delivery(
        self, channel_id: int, campaign_id: str, campaign_type: CampaignType, kind: DeliveryKind
    ) -> bool:
        """True only for the first caller; the campaign must be upserted beforehand."""
        claimed = await self._db.fetchval(
            Announcements.CLAIM,
            channel_id=channel_id,
            campaign_id=campaign_id,
            campaign_type=campaign_type,
            kind=kind,
        )
        return claimed is not None

    async def release_delivery(
        self, channel_id: int, campaign_id: str, campaign_type: CampaignType, kind: DeliveryKind
    ) -> None:
        await self._db.execute(
            Announcements.RELEASE,
            channel_id=channel_id,
            campaign_id=campaign_id,
            campaign_type=campaign_type,
            kind=kind,
        )

    # live_alerts

    async def claim_stream_notification(
        self, channel_id: int, game_id: str, streamer_login: str, cooldown_hours: int
    ) -> bool:
        """True if the cooldown for this channel/game/streamer has passed (and restarts it)."""
        claimed = await self._db.fetchval(
            LiveAlerts.CLAIM,
            channel_id=channel_id,
            game_id=game_id,
            streamer_login=streamer_login,
            cooldown_hours=cooldown_hours,
        )
        return claimed is not None

    async def release_stream_notification(
        self, channel_id: int, game_id: str, streamer_login: str
    ) -> None:
        await self._db.execute(
            LiveAlerts.RELEASE,
            channel_id=channel_id,
            game_id=game_id,
            streamer_login=streamer_login,
        )

    # badge_catalog

    async def upsert_badges(self, badges: Sequence[BadgeData], *, announce: bool) -> int:
        """Stores the Helix catalog; returns how many badge versions are new."""
        if not badges:
            return 0
        rows = await self._db.fetch(
            BadgeCatalog.UPSERT_MANY,
            announce=announce,
            set_ids=[b.set_id for b in badges],
            version_ids=[b.version_id for b in badges],
            titles=[b.title for b in badges],
            descriptions=[b.description for b in badges],
            images_1x=[b.image_url_1x for b in badges],
            images_2x=[b.image_url_2x for b in badges],
            images_4x=[b.image_url_4x for b in badges],
            click_urls=[b.click_url for b in badges],
        )
        return sum(1 for r in rows if r["inserted"])

    async def count_badges(self) -> int:
        return int(await self._db.fetchval(BadgeCatalog.COUNT))

    async def announceable_badges(self, within_days: int) -> list[ChatBadge]:
        rows = await self._db.fetch(BadgeCatalog.LIST_ANNOUNCEABLE, days=within_days)
        return [ChatBadge.model_validate(r) for r in rows]

    async def search_badges(self, query: str | None, limit: int) -> list[ChatBadge]:
        """Newest first; `query` is a plain substring, wildcards in it are matched literally."""
        pattern = f"%{_escape_like(query.strip())}%" if query and query.strip() else None
        rows = await self._db.fetch(BadgeCatalog.SEARCH, pattern=pattern, limit=limit)
        return [ChatBadge.model_validate(r) for r in rows]

    async def get_badge(self, set_id: str, version_id: str) -> ChatBadge | None:
        row = await self._db.fetchrow(BadgeCatalog.GET, set_id=set_id, version_id=version_id)
        return ChatBadge.model_validate(row) if row else None

    # badge_announcements

    async def claim_badge_delivery(self, channel_id: int, set_id: str, version_id: str) -> bool:
        claimed = await self._db.fetchval(
            BadgeAnnouncements.CLAIM, channel_id=channel_id, set_id=set_id, version_id=version_id
        )
        return claimed is not None

    async def release_badge_delivery(self, channel_id: int, set_id: str, version_id: str) -> None:
        await self._db.execute(
            BadgeAnnouncements.RELEASE,
            channel_id=channel_id,
            set_id=set_id,
            version_id=version_id,
        )

    # bot_state

    async def get_state(self, key: str) -> Any:
        return await self._db.fetchval(BotState.GET, key=key)

    async def set_state(self, key: str, value: Any) -> None:
        await self._db.execute(BotState.SET, key=key, value=value)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _required(row: Row | None, query: Query) -> Row:
    if row is None:
        raise RuntimeError(f"{query} returned no row")
    return row

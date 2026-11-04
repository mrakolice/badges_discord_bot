from datetime import UTC, datetime, time
from typing import Any, Literal

from src.db.base import QueryExecutor
from src.db.queries import BotState, CampaignTracking, ChannelRegistry, Follows, LiveAlerts
from src.db.records import (
    CampaignSeen,
    CampaignType,
    Channel,
    Subscription,
    SubscriptionType,
)

NotifiedColumn = Literal["last_notified_at", "ending_notified_at"]


class Storage:
    """Driver-agnostic repository: knows query enums and parameters, never SQL."""

    def __init__(self, executor: QueryExecutor) -> None:
        self._db = executor

    # channel_registry

    async def ensure_channel(self, channel_id: int, guild_id: int, name: str) -> Channel:
        row = await self._db.fetchrow(
            ChannelRegistry.UPSERT, channel_id=channel_id, guild_id=guild_id, name=name
        )
        assert row is not None
        return Channel.model_validate(row)

    async def get_channel(self, channel_id: int) -> Channel | None:
        row = await self._db.fetchrow(ChannelRegistry.GET, channel_id=channel_id)
        return Channel.model_validate(row) if row else None

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
        """Returns the stored campaign and whether it was seen for the first time."""
        row = await self._db.fetchrow(
            CampaignTracking.UPSERT_SEEN,
            campaign_id=campaign_id,
            campaign_type=campaign_type,
            data=data,
        )
        assert row is not None
        return CampaignSeen.model_validate(row), bool(row["inserted"])

    async def get_campaign(
        self, campaign_id: str, campaign_type: CampaignType
    ) -> CampaignSeen | None:
        row = await self._db.fetchrow(
            CampaignTracking.GET, campaign_id=campaign_id, campaign_type=campaign_type
        )
        return CampaignSeen.model_validate(row) if row else None

    async def mark_campaign_notified(
        self,
        campaign_id: str,
        campaign_type: CampaignType,
        column: NotifiedColumn = "last_notified_at",
    ) -> None:
        if column not in ("last_notified_at", "ending_notified_at"):
            raise ValueError(f"unexpected column {column!r}")
        await self._db.execute(
            CampaignTracking.MARK_NOTIFIED,
            column=column,
            campaign_id=campaign_id,
            campaign_type=campaign_type,
        )

    # bot_state

    async def get_state(self, key: str) -> Any:
        return await self._db.fetchval(BotState.GET, key=key)

    async def set_state(self, key: str, value: Any) -> None:
        await self._db.execute(BotState.SET, key=key, value=value)

    # live_alerts

    async def was_stream_notified_since(
        self, channel_id: int, game_id: str, streamer_login: str, since: datetime
    ) -> bool:
        return bool(
            await self._db.fetchval(
                LiveAlerts.WAS_NOTIFIED_SINCE,
                channel_id=channel_id,
                game_id=game_id,
                streamer_login=streamer_login,
                since=since,
            )
        )

    async def was_stream_notified_today(
        self, channel_id: int, game_id: str, streamer_login: str
    ) -> bool:
        midnight_utc = datetime.combine(datetime.now(UTC).date(), time(), tzinfo=UTC)
        return await self.was_stream_notified_since(
            channel_id, game_id, streamer_login, midnight_utc
        )

    async def mark_stream_notified(
        self, channel_id: int, game_id: str, streamer_login: str
    ) -> bool:
        """Returns False if this stream was already recorded for the current UTC day."""
        inserted = await self._db.fetchval(
            LiveAlerts.RECORD,
            channel_id=channel_id,
            game_id=game_id,
            streamer_login=streamer_login,
        )
        return inserted is not None

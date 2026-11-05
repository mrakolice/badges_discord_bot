import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from typing import Protocol

import discord

from src.db.records import ALL_REWARDS, CampaignType, DeliveryKind
from src.drops_api import DropCampaign, DropsApiClient, GameDrops, RewardCampaign, Route
from src.notifications import build_drop_embed, build_live_embed, build_reward_embed
from src.storage import Storage
from src.twitch import TwitchClient, TwitchError, TwitchStream

log = logging.getLogger(__name__)


class SendStatus(Enum):
    SENT = auto()
    GONE = auto()  # channel no longer exists: forget it
    FAILED = auto()  # transient or permission problem: retry on the next check


class Notifier(Protocol):
    async def send(self, channel_id: int, embed: discord.Embed) -> SendStatus: ...


@dataclass(frozen=True, slots=True)
class CheckerSettings:
    ending_soon: timedelta = timedelta(hours=24)
    stream_cooldown_hours: int = 6
    stream_top_n: int = 3
    campaign_retention_days: int = 30


@dataclass(slots=True)
class CheckReport:
    drops_refreshed: bool = False
    rewards_refreshed: bool = False
    sent: int = 0
    failed: int = 0


class Checker:
    """Polls campaigns and delivers each one to each subscribed channel exactly once.

    Delivery state lives in the database (claim before send, release on failure), so a
    restart, a manual /checknow or a late subscriber never cause duplicates or gaps.
    """

    def __init__(
        self,
        storage: Storage,
        drops_api: DropsApiClient,
        twitch: TwitchClient,
        notifier: Notifier,
        settings: CheckerSettings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._storage = storage
        self._api = drops_api
        self._twitch = twitch
        self._notifier = notifier
        self._settings = settings
        self._clock = clock
        self._lock = asyncio.Lock()
        self.last_success_at: datetime | None = None

    async def run(self) -> CheckReport:
        async with self._lock:
            report = CheckReport()
            report.drops_refreshed = await self._api.refresh(Route.DROPS, self._clock())
            report.rewards_refreshed = await self._api.refresh(Route.REWARDS, self._clock())
            now = self._clock()
            gone: set[int] = set()
            await self._deliver_drops(now, report, gone)
            await self._deliver_rewards(now, report, gone)
            await self._deliver_live_streams(report, gone)
            if report.drops_refreshed:
                pruned = await self._storage.prune_campaigns(self._settings.campaign_retention_days)
                if pruned:
                    log.info("pruned %d campaigns not seen for a while", pruned)
            self.last_success_at = now
            log.info(
                "check done: drops refreshed=%s rewards refreshed=%s sent=%d failed=%d",
                report.drops_refreshed,
                report.rewards_refreshed,
                report.sent,
                report.failed,
            )
            return report

    async def deliver_game_to_channel(self, channel_id: int, game_id: str) -> int:
        """Sends active campaigns of the game to one channel right after /follow."""
        snapshot = self._api.drops
        game = snapshot.find_game(game_id) if snapshot else None
        if game is None:
            return 0
        report = CheckReport()
        for campaign in game.active_campaigns(self._clock()):
            await self._deliver_drop(game, campaign, [channel_id], report, set())
        return report.sent

    async def deliver_badges_to_channel(self, channel_id: int) -> int:
        """Right after /followbadges: active reward campaigns and Drops that give chat badges."""
        report = CheckReport()
        now = self._clock()
        for game in self._api.drops.games if self._api.drops else []:
            for campaign in game.active_campaigns(now):
                if campaign.badge_benefits:
                    await self._deliver_drop(game, campaign, [channel_id], report, set())
        for reward in self._api.rewards.active_campaigns(now) if self._api.rewards else []:
            await self._deliver_reward(reward, [channel_id], report, set())
        return report.sent

    # --- campaigns ---

    async def _deliver_drops(self, now: datetime, report: CheckReport, gone: set[int]) -> None:
        if self._api.drops is None:
            return
        badge_channels = await self._storage.get_channels_for_target("reward", ALL_REWARDS)
        for game in self._api.drops.games:
            campaigns = game.active_campaigns(now)
            if not campaigns:
                continue
            game_channels = await self._storage.get_channels_for_target("game", game.game_id)
            for campaign in campaigns:
                # Chat-badge drops also go to /followbadges; the delivery key is the same, so a
                # channel following both the game and badges gets a single message.
                channels = (
                    sorted({*game_channels, *badge_channels})
                    if campaign.badge_benefits
                    else game_channels
                )
                await self._deliver_drop(game, campaign, channels, report, gone)

    async def _deliver_rewards(self, now: datetime, report: CheckReport, gone: set[int]) -> None:
        if self._api.rewards is None:
            return
        channels = await self._storage.get_channels_for_target("reward", ALL_REWARDS)
        for campaign in self._api.rewards.active_campaigns(now):
            await self._deliver_reward(campaign, channels, report, gone)

    async def _deliver_drop(
        self,
        game: GameDrops,
        campaign: DropCampaign,
        channels: list[int],
        report: CheckReport,
        gone: set[int],
    ) -> None:
        await self._storage.upsert_campaign(
            campaign.id, "drop", campaign.model_dump(mode="json", by_alias=True)
        )
        ending_soon = campaign.end_at - self._clock() <= self._settings.ending_soon
        for channel_id in channels:
            await self._deliver_campaign(
                channel_id,
                campaign.id,
                "drop",
                ending_soon,
                lambda ending: build_drop_embed(game, campaign, ending_soon=ending),
                report,
                gone,
            )

    async def _deliver_reward(
        self, campaign: RewardCampaign, channels: list[int], report: CheckReport, gone: set[int]
    ) -> None:
        await self._storage.upsert_campaign(
            campaign.id, "reward", campaign.model_dump(mode="json", by_alias=True)
        )
        ending_soon = campaign.ends_at - self._clock() <= self._settings.ending_soon
        for channel_id in channels:
            await self._deliver_campaign(
                channel_id,
                campaign.id,
                "reward",
                ending_soon,
                lambda ending: build_reward_embed(campaign, ending_soon=ending),
                report,
                gone,
            )

    async def _deliver_campaign(
        self,
        channel_id: int,
        campaign_id: str,
        campaign_type: CampaignType,
        ending_soon: bool,
        build: Callable[[bool], discord.Embed],
        report: CheckReport,
        gone: set[int],
    ) -> None:
        if channel_id in gone:
            return
        kinds: list[DeliveryKind] = []
        if await self._storage.claim_delivery(channel_id, campaign_id, campaign_type, "new"):
            kinds.append("new")
        # A campaign first seen with < 24h left gets one combined message, not two.
        if ending_soon and await self._storage.claim_delivery(
            channel_id, campaign_id, campaign_type, "ending"
        ):
            kinds.append("ending")
        if not kinds:
            return
        status = await self._send(channel_id, build(ending_soon), report, gone)
        if status is SendStatus.FAILED:
            for kind in kinds:
                await self._storage.release_delivery(channel_id, campaign_id, campaign_type, kind)

    # --- live streams ---

    async def _deliver_live_streams(self, report: CheckReport, gone: set[int]) -> None:
        snapshot = self._api.drops
        if snapshot is None or self._settings.stream_top_n == 0:
            return
        now = self._clock()
        game_ids = {s.target_id for s in await self._storage.get_all_subscriptions("game")}
        for game_id in sorted(game_ids):
            game = snapshot.find_game(game_id)
            campaigns = game.active_campaigns(now) if game else []
            if game is None or not campaigns:
                continue
            try:
                streams = await self._find_drop_streams(game, campaigns)
            except TwitchError as exc:
                log.warning("Twitch lookup for game %s failed: %s", game_id, exc)
                continue
            if not streams:
                continue
            channels = await self._storage.get_channels_for_target("game", game_id)
            for channel_id in channels:
                for stream in streams:
                    await self._deliver_stream(channel_id, game, stream, report, gone)

    async def _find_drop_streams(
        self, game: GameDrops, campaigns: list[DropCampaign]
    ) -> list[TwitchStream]:
        """Only streams that can actually give drops: allow-listed channels or any channel."""
        limit = self._settings.stream_top_n
        found: dict[str, TwitchStream] = {}
        restricted = [ch.name for c in campaigns for ch in c.restricted_channels]
        if restricted:
            for stream in await self._twitch.get_streams_for_logins(restricted):
                if stream.game_id == game.game_id:
                    found.setdefault(stream.user_login, stream)
        if any(not c.restricted_channels for c in campaigns):
            for stream in await self._twitch.get_streams_for_game(game.game_id, limit):
                found.setdefault(stream.user_login, stream)
        return sorted(found.values(), key=lambda s: s.viewer_count, reverse=True)[:limit]

    async def _deliver_stream(
        self,
        channel_id: int,
        game: GameDrops,
        stream: TwitchStream,
        report: CheckReport,
        gone: set[int],
    ) -> None:
        if channel_id in gone:
            return
        login = stream.user_login
        if not await self._storage.claim_stream_notification(
            channel_id, game.game_id, login, self._settings.stream_cooldown_hours
        ):
            return
        status = await self._send(channel_id, build_live_embed(game, stream), report, gone)
        if status is SendStatus.FAILED:
            await self._storage.release_stream_notification(channel_id, game.game_id, login)

    # --- sending ---

    async def _send(
        self, channel_id: int, embed: discord.Embed, report: CheckReport, gone: set[int]
    ) -> SendStatus:
        status = await self._notifier.send(channel_id, embed)
        if status is SendStatus.SENT:
            report.sent += 1
        elif status is SendStatus.GONE:
            gone.add(channel_id)
            await self._storage.delete_channel(channel_id)
            log.info("channel %s is gone, its subscriptions were removed", channel_id)
        else:
            report.failed += 1
        return status

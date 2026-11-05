import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from src.checker import Notifier, SendStatus
from src.db.records import ALL_REWARDS, ChatBadge
from src.drops_api import BadgeOffer, DropsApiClient, badge_offers
from src.notifications import build_badge_embed
from src.storage import Storage
from src.twitch import TwitchClient

log = logging.getLogger(__name__)

_BADGE_WORD = re.compile(r"\b(chat\s+)?badges?\b", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class BadgeWatcherSettings:
    # Late /followbadges subscribers still get badges discovered within this window.
    announce_window_days: int = 14


@dataclass(slots=True)
class BadgeSyncReport:
    fetched: int = 0
    new: int = 0
    sent: int = 0
    failed: int = 0
    baseline: bool = False


class BadgeWatcher:
    """Tracks the Helix global badge catalog and announces new badges to badge subscribers.

    The first sync is a silent baseline: everything that already exists is stored with
    announce = false, so only badges that appear afterwards are announced.
    """

    def __init__(
        self,
        storage: Storage,
        twitch: TwitchClient,
        drops_api: DropsApiClient,
        notifier: Notifier,
        settings: BadgeWatcherSettings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._storage = storage
        self._twitch = twitch
        self._api = drops_api
        self._notifier = notifier
        self._settings = settings
        self._clock = clock
        self._lock = asyncio.Lock()
        self.last_success_at: datetime | None = None

    async def sync(self) -> BadgeSyncReport:
        async with self._lock:
            report = BadgeSyncReport()
            badges = await self._twitch.get_global_badges()
            report.fetched = len(badges)
            if not badges:
                # An empty catalog is a Helix hiccup, not "all badges were removed".
                log.warning("Helix returned no global badges, skipping sync")
                return report
            report.baseline = await self._storage.count_badges() == 0
            report.new = await self._storage.upsert_badges(badges, announce=not report.baseline)
            if report.baseline:
                log.info("imported %d existing badges as a silent baseline", report.new)
            channels = await self._storage.get_channels_for_target("reward", ALL_REWARDS)
            await self._announce(channels, report)
            self.last_success_at = self._clock()
            log.info(
                "badge sync: fetched=%d new=%d sent=%d failed=%d",
                report.fetched,
                report.new,
                report.sent,
                report.failed,
            )
            return report

    async def announce_to_channel(self, channel_id: int) -> int:
        """Right after /followbadges: recently discovered badges for this channel."""
        report = BadgeSyncReport()
        await self._announce([channel_id], report)
        return report.sent

    def offers_for(self, badge: ChatBadge) -> list[BadgeOffer]:
        offers = badge_offers(self._api.drops, self._api.rewards, self._clock())
        return matching_offers(badge.title, offers)

    async def _announce(self, channels: Sequence[int], report: BadgeSyncReport) -> None:
        if not channels:
            return
        badges = await self._storage.announceable_badges(self._settings.announce_window_days)
        gone: set[int] = set()
        for badge in badges:
            embed = build_badge_embed(badge, self.offers_for(badge), new=True)
            for channel_id in channels:
                if channel_id in gone or not await self._storage.claim_badge_delivery(
                    channel_id, badge.set_id, badge.version_id
                ):
                    continue
                status = await self._notifier.send(channel_id, embed)
                if status is SendStatus.SENT:
                    report.sent += 1
                elif status is SendStatus.GONE:
                    gone.add(channel_id)
                    await self._storage.delete_channel(channel_id)
                else:
                    report.failed += 1
                    await self._storage.release_badge_delivery(
                        channel_id, badge.set_id, badge.version_id
                    )


def matching_offers(title: str, offers: Sequence[BadgeOffer]) -> list[BadgeOffer]:
    """Helix titles and Drops benefit names are the same human-readable name in practice."""
    key = _normalize(title)
    return [o for o in offers if _normalize(o.name) == key] if key else []


def _normalize(name: str) -> str:
    return _SPACES.sub(" ", _BADGE_WORD.sub(" ", name)).strip().casefold()

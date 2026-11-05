import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

log = logging.getLogger(__name__)

BASE_URL = "https://twitch-drops-api.sunkwi.com"
USER_AGENT = "twitch-badge-bot/0.1"
# Requests at exactly the window edge risk a 429, and refused calls still count.
SAFETY_MARGIN = timedelta(seconds=5)
DROPS_CAMPAIGNS_URL = "https://www.twitch.tv/drops/campaigns"
BADGE_DISTRIBUTION = "BADGE"


# --- API models (field names come from Twitch via Sunkwi; extra fields are ignored) ---


class _ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)


class GameRef(_ApiModel):
    id: str
    display_name: str = Field(alias="displayName")
    slug: str | None = None


class AllowChannel(_ApiModel):
    id: str
    name: str
    display_name: str | None = Field(default=None, alias="displayName")


class Allow(_ApiModel):
    is_enabled: bool = Field(default=False, alias="isEnabled")
    channels: list[AllowChannel] = Field(default_factory=list)


class Benefit(_ApiModel):
    id: str
    name: str
    image_asset_url: str | None = Field(default=None, alias="imageAssetURL")
    distribution_type: str | None = Field(default=None, alias="distributionType")

    @property
    def is_badge(self) -> bool:
        return self.distribution_type == BADGE_DISTRIBUTION


class BenefitEdge(_ApiModel):
    benefit: Benefit


class TimeBasedDrop(_ApiModel):
    id: str
    name: str
    required_minutes_watched: int = Field(default=0, alias="requiredMinutesWatched")
    required_subs: int = Field(default=0, alias="requiredSubs")
    start_at: datetime | None = Field(default=None, alias="startAt")
    end_at: datetime | None = Field(default=None, alias="endAt")
    benefit_edges: list[BenefitEdge] = Field(default_factory=list, alias="benefitEdges")


class DropCampaign(_ApiModel):
    id: str
    name: str
    status: str
    start_at: datetime = Field(alias="startAt")
    end_at: datetime = Field(alias="endAt")
    game: GameRef
    allow: Allow = Field(default_factory=Allow)
    time_based_drops: list[TimeBasedDrop] = Field(default_factory=list, alias="timeBasedDrops")
    description: str | None = None
    image_url: str | None = Field(default=None, alias="imageURL")
    details_url: str | None = Field(default=None, alias="detailsURL")
    account_link_url: str | None = Field(default=None, alias="accountLinkURL")

    def is_active(self, now: datetime) -> bool:
        return self.status == "ACTIVE" and self.start_at <= now < self.end_at

    @property
    def benefits(self) -> list[Benefit]:
        seen: dict[str, Benefit] = {}
        for drop in self.time_based_drops:
            for edge in drop.benefit_edges:
                seen.setdefault(edge.benefit.id, edge.benefit)
        return list(seen.values())

    @property
    def badge_benefits(self) -> list[Benefit]:
        return [b for b in self.benefits if b.is_badge]

    @property
    def url(self) -> str:
        return f"{DROPS_CAMPAIGNS_URL}?dropID={self.id}"

    def minutes_for(self, benefit_id: str) -> int:
        """Watch time of the cheapest drop that grants this benefit."""
        return min(
            (
                d.required_minutes_watched
                for d in self.time_based_drops
                if any(e.benefit.id == benefit_id for e in d.benefit_edges)
            ),
            default=0,
        )

    @property
    def max_minutes_watched(self) -> int:
        return max((d.required_minutes_watched for d in self.time_based_drops), default=0)

    @property
    def restricted_channels(self) -> list[AllowChannel]:
        """Non-empty when only these channels can give the drops."""
        return self.allow.channels if self.allow.is_enabled else []


class GameDrops(_ApiModel):
    game_id: str = Field(alias="gameId")
    game_display_name: str = Field(alias="gameDisplayName")
    game_box_art_url: str | None = Field(default=None, alias="gameBoxArtURL")
    campaigns: list[DropCampaign] = Field(default_factory=list, alias="rewards")

    def active_campaigns(self, now: datetime) -> list[DropCampaign]:
        return [c for c in self.campaigns if c.is_active(now)]


class ImageRef(_ApiModel):
    url: str | None = Field(default=None, alias="image1xURL")


class RewardItem(_ApiModel):
    id: str
    name: str
    earnable_until: datetime | None = Field(default=None, alias="earnableUntil")
    redemption_url: str | None = Field(default=None, alias="redemptionURL")
    redemption_instructions: str | None = Field(default=None, alias="redemptionInstructions")
    thumbnail_image: ImageRef | None = Field(default=None, alias="thumbnailImage")


class UnlockRequirements(_ApiModel):
    minute_watched_goal: int = Field(default=0, alias="minuteWatchedGoal")
    subs_goal: int = Field(default=0, alias="subsGoal")


class RewardCampaign(_ApiModel):
    id: str
    name: str
    brand: str | None = None
    status: str | None = None
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime = Field(alias="endsAt")
    summary: str | None = None
    instructions: str | None = None
    about_url: str | None = Field(default=None, alias="aboutURL")
    external_url: str | None = Field(default=None, alias="externalURL")
    image: ImageRef | None = None
    is_sitewide: bool = Field(default=False, alias="isSitewide")
    unlock_requirements: UnlockRequirements = Field(
        default_factory=UnlockRequirements, alias="unlockRequirements"
    )
    rewards: list[RewardItem] = Field(default_factory=list)

    @property
    def url(self) -> str:
        return self.about_url or self.external_url or DROPS_CAMPAIGNS_URL

    def is_active(self, now: datetime) -> bool:
        # Sunkwi reports status UNKNOWN for reward campaigns, so only dates are reliable.
        return self.starts_at <= now < self.ends_at


class DropsSnapshot(_ApiModel):
    fetched_at: datetime
    games: list[GameDrops]

    def active_games(self, now: datetime) -> list[GameDrops]:
        return [g for g in self.games if g.active_campaigns(now)]

    def find_game(self, game_id: str) -> GameDrops | None:
        return next((g for g in self.games if g.game_id == game_id), None)


class RewardsSnapshot(_ApiModel):
    fetched_at: datetime
    campaigns: list[RewardCampaign]

    def active_campaigns(self, now: datetime) -> list[RewardCampaign]:
        return [c for c in self.campaigns if c.is_active(now)]


@dataclass(frozen=True, slots=True)
class BadgeOffer:
    """A badge (or reward-campaign item) that can be earned right now."""

    name: str
    source: Literal["drop", "reward"]
    campaign_name: str
    campaign_url: str
    ends_at: datetime
    minutes: int
    game_name: str | None = None
    image_url: str | None = None
    only_channels: tuple[str, ...] = ()


def badge_offers(
    drops: DropsSnapshot | None, rewards: RewardsSnapshot | None, now: datetime
) -> list[BadgeOffer]:
    """Chat badges from active Drops plus items of active reward campaigns."""
    offers: list[BadgeOffer] = []
    for game in drops.games if drops else []:
        for campaign in game.active_campaigns(now):
            channels = tuple(c.display_name or c.name for c in campaign.restricted_channels)
            offers += [
                BadgeOffer(
                    name=b.name,
                    source="drop",
                    campaign_name=campaign.name,
                    campaign_url=campaign.url,
                    ends_at=campaign.end_at,
                    minutes=campaign.minutes_for(b.id),
                    game_name=game.game_display_name,
                    image_url=b.image_asset_url,
                    only_channels=channels,
                )
                for b in campaign.badge_benefits
            ]
    for campaign in rewards.active_campaigns(now) if rewards else []:
        offers += [
            BadgeOffer(
                name=item.name,
                source="reward",
                campaign_name=campaign.name,
                campaign_url=item.redemption_url or campaign.url,
                ends_at=item.earnable_until or campaign.ends_at,
                minutes=campaign.unlock_requirements.minute_watched_goal,
                image_url=item.thumbnail_image.url if item.thumbnail_image else None,
            )
            for item in campaign.rewards
        ]
    return sorted(offers, key=lambda o: o.ends_at)


# --- client ---


class Route(StrEnum):
    DROPS = "drops"
    REWARDS = "rewards"


class StateStore(Protocol):
    async def get_state(self, key: str) -> Any: ...

    async def set_state(self, key: str, value: Any) -> None: ...


class DropsApiClient:
    """Sunkwi client that never exceeds the per-route limit, even across restarts.

    The time of the next allowed request and the last payload per route are persisted, so
    `/games`, `/rewards` and `/checknow` are served from the snapshot instead of the API.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        state: StateStore,
        *,
        api_key: str | None,
        min_interval: timedelta,
        base_url: str = BASE_URL,
    ) -> None:
        self._http = http
        self._state = state
        self._api_key = api_key
        self._min_interval = min_interval
        self._base_url = base_url.rstrip("/")
        self._next_allowed: dict[Route, datetime] = {}
        self.drops: DropsSnapshot | None = None
        self.rewards: RewardsSnapshot | None = None

    async def load(self) -> None:
        for route in Route:
            if (stamp := await self._state.get_state(_next_key(route))) is not None:
                self._next_allowed[route] = datetime.fromisoformat(stamp)
            if (cached := await self._state.get_state(_snapshot_key(route))) is not None:
                self._apply(route, cached["data"], datetime.fromisoformat(cached["fetched_at"]))

    def next_allowed_at(self, route: Route) -> datetime:
        return self._next_allowed.get(route, datetime.min.replace(tzinfo=UTC))

    async def refresh(self, route: Route, now: datetime | None = None) -> bool:
        """Fetches the route if the rate limit allows; True if a new snapshot arrived."""
        now = now or datetime.now(UTC)
        if now < self.next_allowed_at(route):
            return False
        # Every call counts against the limit, successful or not: book the slot first.
        await self._set_next_allowed(route, now + self._min_interval + SAFETY_MARGIN)
        try:
            response = await self._http.get(
                f"{self._base_url}/v2/{route}", headers=self._headers(), timeout=30
            )
        except httpx.HTTPError as exc:
            log.warning("Sunkwi %s request failed: %s", route, exc)
            return False

        if response.headers.get("x-api-key-status") == "invalid":
            log.error("DROPS_API_KEY is invalid; Sunkwi treats requests as anonymous")
        if response.status_code == 429:
            wait = _seconds(response.headers.get("retry-after")) or int(
                self._min_interval.total_seconds()
            )
            await self._set_next_allowed(route, now + timedelta(seconds=wait) + SAFETY_MARGIN)
            log.warning("Sunkwi %s rate limited, next try in %ss", route, wait)
            return False
        if response.status_code != 200:
            log.warning("Sunkwi %s returned HTTP %s", route, response.status_code)
            return False
        if (reset := _seconds(response.headers.get("ratelimit-reset"))) is not None:
            later = now + timedelta(seconds=reset) + SAFETY_MARGIN
            if later > self.next_allowed_at(route):
                await self._set_next_allowed(route, later)

        try:
            data = response.json()["data"]
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("Sunkwi %s returned an unexpected body: %s", route, exc)
            return False
        self._apply(route, data, now)
        await self._state.set_state(
            _snapshot_key(route), {"fetched_at": now.isoformat(), "data": data}
        )
        return True

    def _apply(self, route: Route, data: list[Any], fetched_at: datetime) -> None:
        if route is Route.DROPS:
            games = _parse_items(_GAMES, data, "game")
            self.drops = DropsSnapshot(fetched_at=fetched_at, games=games)
        else:
            campaigns = _parse_items(_REWARDS, data, "reward campaign")
            self.rewards = RewardsSnapshot(fetched_at=fetched_at, campaigns=campaigns)

    async def _set_next_allowed(self, route: Route, when: datetime) -> None:
        self._next_allowed[route] = when
        await self._state.set_state(_next_key(route), when.isoformat())

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers


_GAMES = TypeAdapter(GameDrops)
_REWARDS = TypeAdapter(RewardCampaign)


def _parse_items[T](adapter: TypeAdapter[T], items: Iterable[Any], what: str) -> list[T]:
    """One malformed entry must not hide every other campaign."""
    parsed: list[T] = []
    for item in items:
        try:
            parsed.append(adapter.validate_python(item))
        except ValidationError as exc:
            log.warning("skipping malformed %s from Sunkwi: %s", what, exc.errors()[:3])
    return parsed


def _seconds(value: str | None) -> int | None:
    try:
        return max(0, int(float(value))) if value is not None else None
    except ValueError:
        return None


def _next_key(route: Route) -> str:
    return f"sunkwi:{route}:next_allowed_at"


def _snapshot_key(route: Route) -> str:
    return f"sunkwi:{route}:snapshot"

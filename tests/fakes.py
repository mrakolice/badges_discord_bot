import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import discord

from src.checker import SendStatus
from src.drops_api import (
    DropsSnapshot,
    GameDrops,
    RewardCampaign,
    RewardsSnapshot,
    Route,
)
from src.twitch import TwitchStream

FIXTURES = Path(__file__).parent / "fixtures"
PLAIN_GAME_ID = "618479630"  # The Other Side, any channel gives drops
RESTRICTED_GAME_ID = "32502"  # World of Warships, only allow-listed channels
ALLOWED_LOGIN = "worldofwarships"
# Both fixture drop campaigns end 2026-11-04T23:00Z; the reward one ends 2026-11-23.
BEFORE_ENDING = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
ENDING_SOON = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def fixture_payload(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"sunkwi_{name}.json").read_text())


def drops_snapshot(fetched_at: datetime = BEFORE_ENDING) -> DropsSnapshot:
    games = [GameDrops.model_validate(g) for g in fixture_payload("drops")["data"]]
    return DropsSnapshot(fetched_at=fetched_at, games=games)


def rewards_snapshot(fetched_at: datetime = BEFORE_ENDING) -> RewardsSnapshot:
    campaigns = [RewardCampaign.model_validate(c) for c in fixture_payload("rewards")["data"]]
    return RewardsSnapshot(fetched_at=fetched_at, campaigns=campaigns)


def stream(login: str, game_id: str, viewers: int = 100) -> TwitchStream:
    return TwitchStream(
        user_login=login, user_name=login.title(), game_id=game_id, viewer_count=viewers
    )


class FakeDropsApi:
    def __init__(self, *, refreshed: bool = True) -> None:
        self.drops: DropsSnapshot | None = drops_snapshot()
        self.rewards: RewardsSnapshot | None = rewards_snapshot()
        self.refreshed = refreshed
        self.refresh_calls: list[Route] = []

    async def refresh(self, route: Route, now: datetime | None = None) -> bool:
        self.refresh_calls.append(route)
        return self.refreshed

    def next_allowed_at(self, route: Route) -> datetime:
        return BEFORE_ENDING


class FakeTwitch:
    def __init__(self) -> None:
        self.by_game: dict[str, list[TwitchStream]] = defaultdict(list)
        self.by_login: dict[str, TwitchStream] = {}
        self.game_calls: list[tuple[str, int]] = []
        self.login_calls: list[list[str]] = []

    async def get_streams_for_game(self, game_id: str, first: int) -> list[TwitchStream]:
        self.game_calls.append((game_id, first))
        return self.by_game[game_id][:first]

    async def get_streams_for_logins(self, logins: list[str]) -> list[TwitchStream]:
        self.login_calls.append(list(logins))
        return [self.by_login[login] for login in logins if login in self.by_login]


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[int, discord.Embed]] = []
        self.status: dict[int, SendStatus] = {}

    async def send(self, channel_id: int, embed: discord.Embed) -> SendStatus:
        status = self.status.get(channel_id, SendStatus.SENT)
        if status is SendStatus.SENT:
            self.sent.append((channel_id, embed))
        return status

    def titles(self, channel_id: int | None = None) -> list[str]:
        return [e.title or "" for c, e in self.sent if channel_id is None or c == channel_id]

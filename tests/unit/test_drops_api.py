from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx

from src.drops_api import BASE_URL, SAFETY_MARGIN, DropsApiClient, Route, badge_offers
from tests.fakes import (
    ALLOWED_LOGIN,
    BADGE_GAME_ID,
    BADGE_NAME,
    BEFORE_ENDING,
    PLAIN_GAME_ID,
    RESTRICTED_GAME_ID,
    drops_snapshot,
    fixture_payload,
    rewards_snapshot,
)

DROPS_URL = f"{BASE_URL}/v2/drops"
REWARDS_URL = f"{BASE_URL}/v2/rewards"
FIVE_MIN = timedelta(minutes=5)


class MemoryState:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_state(self, key: str) -> Any:
        return self.values.get(key)

    async def set_state(self, key: str, value: Any) -> None:
        self.values[key] = value


@pytest.fixture
def state() -> MemoryState:
    return MemoryState()


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def client(http: httpx.AsyncClient, state: MemoryState, key: str | None = None) -> DropsApiClient:
    return DropsApiClient(http, state, api_key=key, min_interval=FIVE_MIN)


@respx.mock
async def test_parses_drops_and_rewards(http, state) -> None:
    respx.get(DROPS_URL).respond(json=fixture_payload("drops"))
    respx.get(REWARDS_URL).respond(json=fixture_payload("rewards"))
    api = client(http, state)
    assert await api.refresh(Route.DROPS, BEFORE_ENDING)
    assert await api.refresh(Route.REWARDS, BEFORE_ENDING)

    assert api.drops is not None and api.rewards is not None
    assert {g.game_id for g in api.drops.active_games(BEFORE_ENDING)} == {
        PLAIN_GAME_ID,
        RESTRICTED_GAME_ID,
        BADGE_GAME_ID,
    }
    restricted = api.drops.find_game(RESTRICTED_GAME_ID)
    assert restricted is not None
    assert [c.name for c in restricted.campaigns[0].restricted_channels] == [ALLOWED_LOGIN]
    plain = api.drops.find_game(PLAIN_GAME_ID)
    assert plain is not None and plain.campaigns[0].restricted_channels == []
    assert plain.campaigns[0].benefits
    assert len(api.rewards.active_campaigns(BEFORE_ENDING)) == 1


@respx.mock
async def test_second_request_within_window_is_not_sent(http, state) -> None:
    route = respx.get(DROPS_URL).respond(json=fixture_payload("drops"))
    api = client(http, state)
    assert await api.refresh(Route.DROPS, BEFORE_ENDING)
    assert not await api.refresh(Route.DROPS, BEFORE_ENDING + FIVE_MIN)
    assert route.call_count == 1
    assert await api.refresh(Route.DROPS, BEFORE_ENDING + FIVE_MIN + SAFETY_MARGIN)
    assert route.call_count == 2


@respx.mock
async def test_routes_are_limited_independently(http, state) -> None:
    respx.get(DROPS_URL).respond(json=fixture_payload("drops"))
    respx.get(REWARDS_URL).respond(json=fixture_payload("rewards"))
    api = client(http, state)
    assert await api.refresh(Route.DROPS, BEFORE_ENDING)
    assert await api.refresh(Route.REWARDS, BEFORE_ENDING)


@respx.mock
async def test_rate_limit_survives_restart_with_snapshot(http, state) -> None:
    route = respx.get(DROPS_URL).respond(json=fixture_payload("drops"))
    await client(http, state).refresh(Route.DROPS, BEFORE_ENDING)

    restarted = client(http, state)
    await restarted.load()
    assert restarted.drops is not None and restarted.drops.fetched_at == BEFORE_ENDING
    assert not await restarted.refresh(Route.DROPS, BEFORE_ENDING + timedelta(minutes=1))
    assert route.call_count == 1


@respx.mock
async def test_429_respects_retry_after(http, state) -> None:
    route = respx.get(DROPS_URL).respond(429, headers={"Retry-After": "900"}, json={})
    api = client(http, state)
    assert not await api.refresh(Route.DROPS, BEFORE_ENDING)
    assert api.next_allowed_at(Route.DROPS) == BEFORE_ENDING + timedelta(seconds=900) + (
        SAFETY_MARGIN
    )
    assert not await api.refresh(Route.DROPS, BEFORE_ENDING + timedelta(minutes=10))
    assert route.call_count == 1


@respx.mock
async def test_ratelimit_reset_header_extends_wait(http, state) -> None:
    respx.get(DROPS_URL).respond(json=fixture_payload("drops"), headers={"ratelimit-reset": "600"})
    api = client(http, state)
    await api.refresh(Route.DROPS, BEFORE_ENDING)
    assert api.next_allowed_at(Route.DROPS) == BEFORE_ENDING + timedelta(seconds=600) + (
        SAFETY_MARGIN
    )


@respx.mock
async def test_network_error_books_slot_and_keeps_old_snapshot(http, state) -> None:
    respx.get(DROPS_URL).mock(
        side_effect=[
            httpx.Response(200, json=fixture_payload("drops")),
            httpx.ConnectError("boom"),
        ]
    )
    api = client(http, state)
    await api.refresh(Route.DROPS, BEFORE_ENDING)
    later = BEFORE_ENDING + FIVE_MIN + SAFETY_MARGIN
    assert not await api.refresh(Route.DROPS, later)
    assert api.drops is not None and api.drops.fetched_at == BEFORE_ENDING
    assert api.next_allowed_at(Route.DROPS) > later


@respx.mock
async def test_malformed_game_is_skipped(http, state) -> None:
    payload = fixture_payload("drops")
    payload["data"].append({"gameId": "broken"})
    respx.get(DROPS_URL).respond(json=payload)
    api = client(http, state)
    assert await api.refresh(Route.DROPS, BEFORE_ENDING)
    assert api.drops is not None and len(api.drops.games) == 3


@respx.mock
async def test_api_key_header(http, state) -> None:
    route = respx.get(DROPS_URL).respond(json=fixture_payload("drops"))
    await client(http, state, key="jdk_secret").refresh(Route.DROPS, BEFORE_ENDING)
    assert route.calls.last.request.headers["X-API-Key"] == "jdk_secret"
    await client(http, MemoryState()).refresh(Route.DROPS, BEFORE_ENDING)
    assert "X-API-Key" not in route.calls.last.request.headers


def test_badge_benefits_are_detected() -> None:
    snapshot = drops_snapshot()
    game = snapshot.find_game(BADGE_GAME_ID)
    assert game is not None
    campaign = game.campaigns[0]
    assert [b.name for b in campaign.badge_benefits] == [BADGE_NAME]
    plain = snapshot.find_game(PLAIN_GAME_ID)
    assert plain is not None and plain.campaigns[0].badge_benefits == []


def test_badge_offers_merge_drops_and_rewards() -> None:
    offers = badge_offers(drops_snapshot(), rewards_snapshot(), BEFORE_ENDING)
    by_source = {o.source: o for o in offers}
    drop = by_source["drop"]
    assert drop.name == BADGE_NAME and drop.game_name == "League of Legends"
    assert drop.only_channels and drop.campaign_url.endswith(drop.campaign_url.split("=")[-1])
    reward = by_source["reward"]
    assert reward.minutes == 20 and reward.game_name is None
    assert [o.ends_at for o in offers] == sorted(o.ends_at for o in offers)


def test_no_offers_without_data() -> None:
    assert badge_offers(None, None, BEFORE_ENDING) == []

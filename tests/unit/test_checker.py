from datetime import datetime
from typing import Any

import pytest

from src.checker import Checker, CheckerSettings, SendStatus
from src.db.records import ALL_REWARDS
from tests.fakes import (
    ALLOWED_LOGIN,
    BEFORE_ENDING,
    ENDING_SOON,
    PLAIN_GAME_ID,
    RESTRICTED_GAME_ID,
    FakeDropsApi,
    FakeNotifier,
    FakeTwitch,
    stream,
)


class Sub:
    def __init__(self, channel_id: int, type: str, target_id: str) -> None:
        self.channel_id, self.type, self.target_id = channel_id, type, target_id


class MemoryStorage:
    """In-memory stand-in for Storage with the same claim semantics."""

    def __init__(self) -> None:
        self.subs: list[Sub] = []
        self.campaigns: dict[tuple[str, str], dict[str, Any]] = {}
        self.deliveries: set[tuple[int, str, str, str]] = set()
        self.streams: set[tuple[int, str, str]] = set()
        self.deleted: list[int] = []

    def follow(self, channel_id: int, type: str, target_id: str) -> None:
        self.subs.append(Sub(channel_id, type, target_id))

    async def get_channels_for_target(self, type: str, target_id: str) -> list[int]:
        return sorted(
            {s.channel_id for s in self.subs if (s.type, s.target_id) == (type, target_id)}
        )

    async def get_all_subscriptions(self, type: str | None = None) -> list[Sub]:
        return [s for s in self.subs if type is None or s.type == type]

    async def upsert_campaign(self, campaign_id: str, campaign_type: str, data: dict) -> tuple:
        new = (campaign_id, campaign_type) not in self.campaigns
        self.campaigns[(campaign_id, campaign_type)] = data
        return None, new

    async def claim_delivery(self, channel_id: int, cid: str, ctype: str, kind: str) -> bool:
        assert (cid, ctype) in self.campaigns, "campaign must be upserted before claiming"
        key = (channel_id, cid, ctype, kind)
        if key in self.deliveries:
            return False
        self.deliveries.add(key)
        return True

    async def release_delivery(self, channel_id: int, cid: str, ctype: str, kind: str) -> None:
        self.deliveries.discard((channel_id, cid, ctype, kind))

    async def claim_stream_notification(self, channel_id, game_id, login, cooldown) -> bool:
        key = (channel_id, game_id, login)
        if key in self.streams:
            return False
        self.streams.add(key)
        return True

    async def release_stream_notification(self, channel_id, game_id, login) -> None:
        self.streams.discard((channel_id, game_id, login))

    async def delete_channel(self, channel_id: int) -> bool:
        self.deleted.append(channel_id)
        self.subs = [s for s in self.subs if s.channel_id != channel_id]
        return True

    async def prune_campaigns(self, days: int) -> int:
        return 0


class Env:
    def __init__(self, now: datetime = BEFORE_ENDING, *, top_n: int = 3) -> None:
        self.now = now
        self.storage = MemoryStorage()
        self.api = FakeDropsApi()
        self.twitch = FakeTwitch()
        self.notifier = FakeNotifier()
        self.checker = Checker(
            self.storage,  # type: ignore[arg-type]
            self.api,  # type: ignore[arg-type]
            self.twitch,  # type: ignore[arg-type]
            self.notifier,
            CheckerSettings(stream_top_n=top_n),
            clock=lambda: self.now,
        )


@pytest.fixture
def env() -> Env:
    return Env()


async def test_new_campaign_delivered_once_per_subscribed_channel(env: Env) -> None:
    env.storage.follow(1, "game", PLAIN_GAME_ID)
    env.storage.follow(2, "game", PLAIN_GAME_ID)
    env.storage.follow(3, "game", "unrelated")
    report = await env.checker.run()
    assert report.sent == 2
    assert [c for c, _ in env.notifier.sent] == [1, 2]
    await env.checker.run()
    assert len(env.notifier.sent) == 2


async def test_unsubscribed_campaigns_are_still_tracked(env: Env) -> None:
    await env.checker.run()
    assert env.notifier.sent == []
    assert len(env.storage.campaigns) == 3


async def test_rewards_go_to_badge_subscribers(env: Env) -> None:
    env.storage.follow(5, "reward", ALL_REWARDS)
    await env.checker.run()
    assert len(env.notifier.titles(5)) == 1 and env.notifier.titles(5)[0].startswith("🏅")


async def test_campaign_first_seen_ending_soon_gets_one_combined_message() -> None:
    env = Env(ENDING_SOON)
    env.storage.follow(1, "game", PLAIN_GAME_ID)
    await env.checker.run()
    await env.checker.run()
    assert env.notifier.titles(1) == [env.notifier.titles(1)[0]]
    assert env.notifier.titles(1)[0].startswith("⏰")


async def test_ending_reminder_after_new(env: Env) -> None:
    env.storage.follow(1, "game", PLAIN_GAME_ID)
    await env.checker.run()
    env.now = ENDING_SOON
    await env.checker.run()
    await env.checker.run()
    titles = env.notifier.titles(1)
    assert len(titles) == 2 and titles[0].startswith("🎁") and titles[1].startswith("⏰")


async def test_failed_send_is_retried(env: Env) -> None:
    env.storage.follow(1, "game", PLAIN_GAME_ID)
    env.notifier.status[1] = SendStatus.FAILED
    report = await env.checker.run()
    assert report.failed == 1 and env.notifier.sent == []
    del env.notifier.status[1]
    await env.checker.run()
    assert len(env.notifier.titles(1)) == 1


async def test_gone_channel_is_forgotten(env: Env) -> None:
    env.storage.follow(1, "game", PLAIN_GAME_ID)
    env.storage.follow(1, "reward", ALL_REWARDS)
    env.notifier.status[1] = SendStatus.GONE
    await env.checker.run()
    assert env.storage.deleted == [1]
    assert env.storage.subs == []


async def test_follow_then_check_does_not_duplicate(env: Env) -> None:
    env.storage.follow(1, "game", PLAIN_GAME_ID)
    assert await env.checker.deliver_game_to_channel(1, PLAIN_GAME_ID) == 1
    await env.checker.run()
    assert len(env.notifier.titles(1)) == 1


async def test_rate_limited_refresh_still_delivers_from_snapshot() -> None:
    env = Env()
    env.api.refreshed = False
    env.storage.follow(1, "game", PLAIN_GAME_ID)
    report = await env.checker.run()
    assert not report.drops_refreshed and report.sent == 1


async def test_live_streams_for_unrestricted_campaign_use_top_n() -> None:
    env = Env(top_n=2)
    env.storage.follow(1, "game", PLAIN_GAME_ID)
    env.twitch.by_game[PLAIN_GAME_ID] = [
        stream("big", PLAIN_GAME_ID, 900),
        stream("mid", PLAIN_GAME_ID, 500),
        stream("small", PLAIN_GAME_ID, 10),
    ]
    await env.checker.run()
    live = [t for t in env.notifier.titles(1) if t.startswith("🔴")]
    assert len(live) == 2 and "Big" in live[0]
    assert env.twitch.game_calls == [(PLAIN_GAME_ID, 2)]
    await env.checker.run()
    assert len([t for t in env.notifier.titles(1) if t.startswith("🔴")]) == 2


async def test_live_streams_for_restricted_campaign_only_allowed_channels(env: Env) -> None:
    env.storage.follow(1, "game", RESTRICTED_GAME_ID)
    env.twitch.by_game[RESTRICTED_GAME_ID] = [stream("random", RESTRICTED_GAME_ID, 9999)]
    env.twitch.by_login[ALLOWED_LOGIN] = stream(ALLOWED_LOGIN, RESTRICTED_GAME_ID)
    await env.checker.run()
    live = [t for t in env.notifier.titles(1) if t.startswith("🔴")]
    assert live == [f"🔴 Сейчас в эфире: {ALLOWED_LOGIN.title()}"]
    assert env.twitch.game_calls == []


async def test_allowed_channel_streaming_another_game_is_ignored(env: Env) -> None:
    env.storage.follow(1, "game", RESTRICTED_GAME_ID)
    env.twitch.by_login[ALLOWED_LOGIN] = stream(ALLOWED_LOGIN, "other-game")
    await env.checker.run()
    assert not [t for t in env.notifier.titles(1) if t.startswith("🔴")]


async def test_no_live_lookup_without_active_campaign(env: Env) -> None:
    env.storage.follow(1, "game", "no-campaign-game")
    await env.checker.run()
    assert env.twitch.game_calls == [] and env.twitch.login_calls == []

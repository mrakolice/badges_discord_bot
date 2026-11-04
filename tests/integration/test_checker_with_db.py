import pytest

from src.checker import Checker, CheckerSettings, SendStatus
from src.db import AsyncpgDatabase
from src.db.records import ALL_REWARDS
from src.storage import Storage
from tests.fakes import (
    BEFORE_ENDING,
    ENDING_SOON,
    PLAIN_GAME_ID,
    FakeDropsApi,
    FakeNotifier,
    FakeTwitch,
    stream,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


class Clock:
    def __init__(self) -> None:
        self.now = BEFORE_ENDING

    def __call__(self):
        return self.now


@pytest.fixture
async def setup(clean_db: AsyncpgDatabase):
    storage = Storage(clean_db)
    clock = Clock()
    notifier = FakeNotifier()
    twitch = FakeTwitch()
    checker = Checker(
        storage,
        FakeDropsApi(),  # type: ignore[arg-type]
        twitch,  # type: ignore[arg-type]
        notifier,
        CheckerSettings(stream_top_n=1),
        clock=clock,
    )
    await storage.ensure_channel(1, 10, "drops")
    await storage.ensure_channel(2, 10, "badges")
    await storage.add_subscription(1, "game", PLAIN_GAME_ID, "The Other Side")
    await storage.add_subscription(2, "reward", ALL_REWARDS, "Rewards")
    return storage, checker, notifier, twitch, clock


async def test_full_cycle_against_postgres(setup) -> None:
    storage, checker, notifier, twitch, clock = setup
    twitch.by_game[PLAIN_GAME_ID] = [stream("streamer", PLAIN_GAME_ID)]

    first = await checker.run()
    assert first.sent == 3  # drop campaign + live stream to #1, reward campaign to #2
    assert len(notifier.titles(1)) == 2 and len(notifier.titles(2)) == 1
    assert await storage.get_campaign(notifier_campaign_id(checker), "drop") is not None

    assert (await checker.run()).sent == 0

    clock.now = ENDING_SOON
    reminder = await checker.run()
    assert reminder.sent == 1 and notifier.titles(1)[-1].startswith("⏰")


async def test_failed_delivery_is_retried_from_db(setup) -> None:
    _, checker, notifier, _, _ = setup
    notifier.status[2] = SendStatus.FAILED
    await checker.run()
    assert notifier.titles(2) == []
    del notifier.status[2]
    await checker.run()
    assert len(notifier.titles(2)) == 1


async def test_gone_channel_is_removed_from_db(setup) -> None:
    storage, checker, notifier, _, _ = setup
    notifier.status[1] = SendStatus.GONE
    await checker.run()
    assert await storage.get_channel(1) is None
    assert await storage.get_channel(2) is not None


def notifier_campaign_id(checker: Checker) -> str:
    snapshot = checker._api.drops
    assert snapshot is not None
    game = snapshot.find_game(PLAIN_GAME_ID)
    assert game is not None
    return game.campaigns[0].id

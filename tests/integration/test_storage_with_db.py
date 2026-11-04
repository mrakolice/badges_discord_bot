from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.db import AsyncpgDatabase
from src.db.records import ALL_REWARDS
from src.storage import Storage

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def storage(clean_db: AsyncpgDatabase) -> Storage:
    return Storage(clean_db)


async def test_channel_upsert_updates_name(storage: Storage) -> None:
    await storage.ensure_channel(1, 10, "old")
    channel = await storage.ensure_channel(1, 10, "new")
    assert channel.name == "new"
    assert (await storage.get_channel(1)) == channel
    assert await storage.get_channel(2) is None


async def test_subscriptions_lifecycle(storage: Storage) -> None:
    await storage.ensure_channel(1, 10, "a")
    await storage.ensure_channel(2, 10, "b")
    game = await storage.add_subscription(1, "game", "33214", "Fortnite")
    assert game is not None
    assert await storage.add_subscription(1, "game", "33214", "Fortnite") is None
    await storage.add_subscription(1, "reward", ALL_REWARDS, "Rewards")
    await storage.add_subscription(2, "game", "33214", "Fortnite")

    assert [s.type for s in await storage.list_subscriptions(1)] == ["game", "reward"]
    assert len(await storage.list_subscriptions(1, "reward")) == 1
    assert len(await storage.get_all_subscriptions("game")) == 2
    assert await storage.get_channels_for_target("game", "33214") == [1, 2]

    assert await storage.remove_subscription(1, game.id)
    assert not await storage.remove_subscription(2, game.id)
    assert await storage.remove_subscription_by_target(1, "reward", ALL_REWARDS)
    assert await storage.list_subscriptions(1) == []


async def test_subscription_requires_known_channel(storage: Storage) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await storage.add_subscription(999, "game", "1", "X")


async def test_campaign_upsert_and_marks(storage: Storage) -> None:
    campaign, is_new = await storage.upsert_campaign("c1", "drop", {"name": "A", "n": [1, 2]})
    assert is_new and campaign.data_json == {"name": "A", "n": [1, 2]}
    campaign, is_new = await storage.upsert_campaign("c1", "drop", {"name": "B"})
    assert not is_new and campaign.data_json == {"name": "B"}

    await storage.mark_campaign_notified("c1", "drop")
    await storage.mark_campaign_notified("c1", "drop", "ending_notified_at")
    stored = await storage.get_campaign("c1", "drop")
    assert stored is not None
    assert stored.last_notified_at is not None and stored.ending_notified_at is not None
    assert await storage.get_campaign("c1", "reward") is None


async def test_state_roundtrip(storage: Storage) -> None:
    assert await storage.get_state("k") is None
    await storage.set_state("k", {"etag": "abc", "count": 3})
    await storage.set_state("k", {"etag": "def"})
    assert await storage.get_state("k") == {"etag": "def"}


async def test_stream_notification_once_per_day(storage: Storage) -> None:
    await storage.ensure_channel(1, 10, "a")
    assert not await storage.was_stream_notified_today(1, "g", "streamer")
    assert await storage.mark_stream_notified(1, "g", "streamer")
    assert not await storage.mark_stream_notified(1, "g", "streamer")
    assert await storage.was_stream_notified_today(1, "g", "streamer")
    future = datetime.now(UTC) + timedelta(hours=1)
    assert not await storage.was_stream_notified_since(1, "g", "streamer", future)
    assert await storage.mark_stream_notified(1, "g", "other")


async def test_transaction_rolls_back(clean_db: AsyncpgDatabase) -> None:
    with pytest.raises(RuntimeError):
        async with clean_db.transaction() as tx:
            await Storage(tx).ensure_channel(5, 10, "tmp")
            raise RuntimeError
    assert await Storage(clean_db).get_channel(5) is None

import asyncpg
import pytest

from src.db import AsyncpgDatabase
from src.db.records import ALL_REWARDS
from src.storage import Storage
from tests.integration.queries import TestSql

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


async def test_campaign_upsert(storage: Storage) -> None:
    campaign, is_new = await storage.upsert_campaign("c1", "drop", {"name": "A", "n": [1, 2]})
    assert is_new and campaign.data_json == {"name": "A", "n": [1, 2]}
    campaign, is_new = await storage.upsert_campaign("c1", "drop", {"name": "B"})
    assert not is_new and campaign.data_json == {"name": "B"}
    assert campaign.last_seen_at >= campaign.first_seen_at
    assert await storage.get_campaign("c1", "reward") is None


async def test_state_roundtrip(storage: Storage) -> None:
    assert await storage.get_state("k") is None
    await storage.set_state("k", {"etag": "abc", "count": 3})
    await storage.set_state("k", {"etag": "def"})
    assert await storage.get_state("k") == {"etag": "def"}


async def test_delivery_claimed_once_per_channel_and_kind(storage: Storage) -> None:
    await storage.ensure_channel(1, 10, "a")
    await storage.ensure_channel(2, 10, "b")
    await storage.upsert_campaign("c1", "drop", {})
    assert await storage.claim_delivery(1, "c1", "drop", "new")
    assert not await storage.claim_delivery(1, "c1", "drop", "new")
    assert await storage.claim_delivery(1, "c1", "drop", "ending")
    assert await storage.claim_delivery(2, "c1", "drop", "new")
    await storage.release_delivery(1, "c1", "drop", "new")
    assert await storage.claim_delivery(1, "c1", "drop", "new")


async def test_delivery_requires_known_campaign(storage: Storage) -> None:
    await storage.ensure_channel(1, 10, "a")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await storage.claim_delivery(1, "missing", "drop", "new")


async def test_prune_removes_stale_campaigns_with_deliveries(
    storage: Storage, admin: AsyncpgDatabase
) -> None:
    await storage.ensure_channel(1, 10, "a")
    await storage.upsert_campaign("old", "drop", {})
    await storage.upsert_campaign("fresh", "drop", {})
    await storage.claim_delivery(1, "old", "drop", "new")
    await admin.execute(TestSql.AGE_CAMPAIGN, campaign_id="old", days=40)
    assert await storage.prune_campaigns(30) == 1
    assert await storage.get_campaign("old", "drop") is None
    assert await storage.get_campaign("fresh", "drop") is not None
    assert await storage.claim_delivery(1, "fresh", "drop", "new")


async def test_stream_notification_cooldown(storage: Storage, admin: AsyncpgDatabase) -> None:
    await storage.ensure_channel(1, 10, "a")
    assert await storage.claim_stream_notification(1, "g", "streamer", 6)
    assert not await storage.claim_stream_notification(1, "g", "streamer", 6)
    assert await storage.claim_stream_notification(1, "g", "other", 6)
    await admin.execute(TestSql.AGE_STREAM_NOTIFICATIONS, hours=7)
    assert await storage.claim_stream_notification(1, "g", "streamer", 6)
    assert not await storage.claim_stream_notification(1, "g", "streamer", 6)
    await storage.release_stream_notification(1, "g", "streamer")
    assert await storage.claim_stream_notification(1, "g", "streamer", 6)


async def test_deleting_channels_cascades(storage: Storage) -> None:
    await storage.ensure_channel(1, 10, "a")
    await storage.ensure_channel(2, 10, "b")
    await storage.ensure_channel(3, 20, "c")
    await storage.add_subscription(1, "game", "g", "G")
    assert await storage.delete_channel(1)
    assert not await storage.delete_channel(1)
    assert await storage.list_subscriptions(1) == []
    assert await storage.delete_guild_channels(10) == 1
    assert await storage.get_channel(3) is not None


async def test_transaction_rolls_back(clean_db: AsyncpgDatabase) -> None:
    with pytest.raises(RuntimeError):
        async with clean_db.transaction() as tx:
            await Storage(tx).ensure_channel(5, 10, "tmp")
            raise RuntimeError
    assert await Storage(clean_db).get_channel(5) is None

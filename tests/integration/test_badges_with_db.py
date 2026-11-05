import pytest

from src.badge_watcher import BadgeWatcher, BadgeWatcherSettings
from src.checker import SendStatus
from src.db import AsyncpgDatabase
from src.db.records import ALL_REWARDS
from src.storage import Storage
from tests.fakes import BADGE_NAME, FakeDropsApi, FakeNotifier, FakeTwitch, badge

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def env(clean_db: AsyncpgDatabase):
    storage = Storage(clean_db)
    twitch = FakeTwitch()
    notifier = FakeNotifier()
    watcher = BadgeWatcher(
        storage,
        twitch,  # type: ignore[arg-type]
        FakeDropsApi(),  # type: ignore[arg-type]
        notifier,
        BadgeWatcherSettings(),
    )
    await storage.ensure_channel(1, 10, "badges")
    await storage.add_subscription(1, "reward", ALL_REWARDS, "Badges")
    return storage, twitch, notifier, watcher


async def test_upsert_and_search(clean_db: AsyncpgDatabase) -> None:
    storage = Storage(clean_db)
    assert (
        await storage.upsert_badges(
            [badge("a", "100% Fan"), badge("b", "Fan_Club")], announce=False
        )
        == 2
    )
    assert await storage.upsert_badges([badge("a", "100% Fan v2")], announce=True) == 0
    assert await storage.count_badges() == 2
    assert [b.title for b in await storage.search_badges("100%", 10)] == ["100% Fan v2"]
    assert [b.set_id for b in await storage.search_badges("n_c", 10)] == ["b"]
    assert await storage.search_badges("zzz", 10) == []
    assert len(await storage.search_badges(None, 10)) == 2
    found = await storage.get_badge("a", "1")
    assert found is not None and found.title == "100% Fan v2"
    assert await storage.upsert_badges([], announce=True) == 0


async def test_first_sync_is_a_silent_baseline(env) -> None:
    storage, twitch, notifier, watcher = env
    twitch.badges = [badge("old-1", "Old One"), badge("old-2", "Old Two")]
    report = await watcher.sync()
    assert report.baseline and report.new == 2 and notifier.sent == []
    assert await storage.announceable_badges(14) == []


async def test_new_badge_announced_once_with_campaign_link(env) -> None:
    storage, twitch, notifier, watcher = env
    twitch.badges = [badge("old", "Old")]
    await watcher.sync()
    twitch.badges.append(badge("worlds-2026", BADGE_NAME))
    report = await watcher.sync()
    assert report.new == 1 and report.sent == 1
    ((channel_id, embed),) = notifier.sent
    assert channel_id == 1 and embed.title == f"🆕 Новый бейдж: {BADGE_NAME}"
    assert any(f.name == "Как получить" for f in embed.fields)
    assert (await watcher.sync()).sent == 0


async def test_failed_announcement_is_retried(env) -> None:
    _, twitch, notifier, watcher = env
    twitch.badges = [badge("old", "Old")]
    await watcher.sync()
    twitch.badges.append(badge("new", "Brand New"))
    notifier.status[1] = SendStatus.FAILED
    assert (await watcher.sync()).failed == 1
    del notifier.status[1]
    assert (await watcher.sync()).sent == 1


async def test_late_subscriber_gets_recent_badges(env) -> None:
    storage, twitch, notifier, watcher = env
    twitch.badges = [badge("old", "Old")]
    await watcher.sync()
    twitch.badges.append(badge("new", "Brand New"))
    await watcher.sync()
    await storage.ensure_channel(2, 10, "late")
    await storage.add_subscription(2, "reward", ALL_REWARDS, "Badges")
    assert await watcher.announce_to_channel(2) == 1
    assert await watcher.announce_to_channel(2) == 0


async def test_empty_catalog_is_ignored(env) -> None:
    storage, twitch, _, watcher = env
    twitch.badges = []
    report = await watcher.sync()
    assert report.fetched == 0 and await storage.count_badges() == 0

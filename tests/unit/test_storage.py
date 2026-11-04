from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from src.db.base import QueryExecutor, Row
from src.db.queries import ChannelRegistry, Follows, Query
from src.db.templates import SqlTemplates
from src.storage import Storage

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class FakeExecutor(QueryExecutor):
    """Renders the real templates and records what would be sent to the driver."""

    def __init__(self, templates: SqlTemplates) -> None:
        super().__init__(templates)
        self.calls: list[tuple[Query, dict[str, Any]]] = []
        self.sent: list[tuple[str, tuple[Any, ...]]] = []
        self.result: Any = None

    def _record(self, query: Query, params: dict[str, Any]) -> None:
        self.calls.append((query, params))
        rendered = self.templates.render(query, **params)
        self.sent.append((rendered.sql, rendered.args))

    async def fetch(self, query: Query, /, **params: Any) -> list[Row]:
        self._record(query, params)
        return self.result or []

    async def fetchrow(self, query: Query, /, **params: Any) -> Row | None:
        self._record(query, params)
        return self.result

    async def fetchval(self, query: Query, /, **params: Any) -> Any:
        self._record(query, params)
        return self.result

    async def execute(self, query: Query, /, **params: Any) -> None:
        self._record(query, params)

    async def _fetch(self, sql: str, args: Sequence[Any]) -> list[Row]:
        raise NotImplementedError

    async def _fetchrow(self, sql: str, args: Sequence[Any]) -> Row | None:
        raise NotImplementedError

    async def _fetchval(self, sql: str, args: Sequence[Any]) -> Any:
        raise NotImplementedError

    async def _execute(self, sql: str, args: Sequence[Any]) -> None:
        raise NotImplementedError

    async def _execute_script(self, sql: str) -> None:
        raise NotImplementedError


@pytest.fixture
def fake(templates: SqlTemplates) -> FakeExecutor:
    return FakeExecutor(templates)


async def test_ensure_channel(fake: FakeExecutor) -> None:
    fake.result = {"id": 1, "guild_id": 2, "name": "general", "created_at": NOW}
    channel = await Storage(fake).ensure_channel(1, 2, "general")
    assert channel.name == "general"
    assert fake.calls == [
        (ChannelRegistry.UPSERT, {"channel_id": 1, "guild_id": 2, "name": "general"})
    ]
    assert fake.sent[0][1] == (1, 2, "general")


async def test_add_subscription_duplicate_returns_none(fake: FakeExecutor) -> None:
    assert await Storage(fake).add_subscription(1, "game", "123", "Rust") is None
    assert fake.calls[0][0] == Follows.ADD


async def test_remove_by_id_and_by_target_use_different_branches(fake: FakeExecutor) -> None:
    storage = Storage(fake)
    fake.result = 7
    assert await storage.remove_subscription(1, 7)
    fake.result = None
    assert not await storage.remove_subscription_by_target(1, "reward", "ALL_REWARDS")
    (by_id_sql, by_id_args), (by_target_sql, by_target_args) = fake.sent
    assert "id = $2" in by_id_sql and by_id_args == (1, 7)
    assert "target_id = $3" in by_target_sql and by_target_args == (1, "reward", "ALL_REWARDS")


async def test_list_subscriptions_type_filter_is_optional(fake: FakeExecutor) -> None:
    storage = Storage(fake)
    await storage.list_subscriptions(1)
    await storage.list_subscriptions(1, "game")
    assert fake.sent[0][1] == (1,)
    assert fake.sent[1][1] == (1, "game")


async def test_upsert_campaign_reports_new(fake: FakeExecutor) -> None:
    fake.result = {
        "campaign_id": "c1",
        "campaign_type": "drop",
        "first_seen_at": NOW,
        "last_notified_at": None,
        "ending_notified_at": None,
        "data_json": {"name": "X"},
        "inserted": True,
    }
    campaign, is_new = await Storage(fake).upsert_campaign("c1", "drop", {"name": "X"})
    assert is_new and campaign.data_json == {"name": "X"}


async def test_mark_campaign_notified_quotes_column(fake: FakeExecutor) -> None:
    await Storage(fake).mark_campaign_notified("c1", "drop", "ending_notified_at")
    assert 'SET "ending_notified_at" = now()' in fake.sent[0][0]


async def test_mark_campaign_notified_rejects_other_columns(fake: FakeExecutor) -> None:
    with pytest.raises(ValueError):
        await Storage(fake).mark_campaign_notified("c1", "drop", "data_json")  # type: ignore[arg-type]
    assert fake.calls == []


async def test_was_stream_notified_today_uses_utc_midnight(fake: FakeExecutor) -> None:
    fake.result = False
    assert not await Storage(fake).was_stream_notified_today(1, "g", "streamer")
    since = fake.calls[0][1]["since"]
    assert since.tzinfo is UTC and (since.hour, since.minute) == (0, 0)

import pytest
from pydantic import ValidationError

from src.config import Settings

REQUIRED = {
    "DISCORD_BOT_TOKEN": "discord-s3cr3t",
    "TWITCH_CLIENT_ID": "id",
    "TWITCH_CLIENT_SECRET": "secret",
    "DATABASE_URL": "postgresql://u:p@h/db",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_required_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        settings()


def test_empty_optional_values_from_compose_mean_unset(env: pytest.MonkeyPatch) -> None:
    env.setenv("DISCORD_GUILD_ID", "")
    env.setenv("DROPS_API_KEY", "")
    env.setenv("CHECK_INTERVAL_MINUTES", "")
    s = settings()
    assert s.discord_guild_id is None and s.drops_api_key is None
    assert s.check_interval_minutes == 5


def test_secrets_are_hidden_in_repr(env: pytest.MonkeyPatch) -> None:
    assert "discord-s3cr3t" not in repr(settings())


@pytest.mark.parametrize(
    ("key", "interval", "expected_min", "expected_effective"),
    [(None, 1, 5, 5), ("k", 1, 1, 1), (None, 15, 5, 15), ("k", 3, 1, 3)],
)
def test_check_interval_respects_sunkwi_limit(
    env: pytest.MonkeyPatch,
    key: str | None,
    interval: int,
    expected_min: int,
    expected_effective: int,
) -> None:
    if key:
        env.setenv("DROPS_API_KEY", key)
    env.setenv("CHECK_INTERVAL_MINUTES", str(interval))
    s = settings()
    assert s.min_check_interval_minutes == expected_min
    assert s.effective_check_interval_minutes == expected_effective


def test_invalid_values_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("STREAM_NOTIFY_TOP_N", "50")
    with pytest.raises(ValidationError):
        settings()

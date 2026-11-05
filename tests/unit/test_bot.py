from datetime import timedelta

import pytest

from src.bot import BadgeBot, BadgeBotCog, Services
from src.config import Settings
from src.drops_api import SAFETY_MARGIN
from src.main import check_interval
from tests.unit.test_config import REQUIRED

EXPECTED_COMMANDS = {
    "games",
    "rewards",
    "follow",
    "unfollow",
    "followbadges",
    "unfollowbadges",
    "list",
    "checknow",
    "help",
    "badges",
    "badge",
}
MANAGE_ONLY = {"follow", "unfollow", "followbadges", "unfollowbadges", "checknow"}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


async def test_all_spec_commands_registered_with_permissions(settings: Settings) -> None:
    bot = BadgeBot(settings)
    services = Services(None, None, None, None, None)  # type: ignore[arg-type]
    await bot.add_cog(BadgeBotCog(bot, services))
    commands = {c.name: c for c in bot.tree.get_commands()}
    assert set(commands) == EXPECTED_COMMANDS
    for name in MANAGE_ONLY:
        perms = commands[name].default_permissions
        assert perms is not None and perms.manage_channels, name
    assert commands["games"].default_permissions is None
    await bot.close()


def test_check_interval_never_hits_the_rate_limit_edge(settings: Settings) -> None:
    gate = timedelta(minutes=settings.min_check_interval_minutes) + SAFETY_MARGIN
    assert check_interval(settings) > gate

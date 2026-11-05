from typing import Annotated

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sunkwi allows 1 request / 5 min per route anonymously and 1 / min with a key.
ANON_MIN_INTERVAL_MINUTES = 5
KEYED_MIN_INTERVAL_MINUTES = 1

PositiveInt = Annotated[int, Field(ge=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        # Compose passes unset optional variables as "", which must mean "not set".
        env_ignore_empty=True,
    )

    discord_bot_token: SecretStr
    twitch_client_id: str
    twitch_client_secret: SecretStr
    database_url: SecretStr

    drops_api_key: SecretStr | None = None
    discord_guild_id: int | None = None
    default_notification_channel_id: int | None = None

    check_interval_minutes: PositiveInt = 5
    stream_notify_cooldown_hours: PositiveInt = 6
    stream_notify_top_n: Annotated[int, Field(ge=0, le=20)] = 3
    ending_soon_hours: PositiveInt = 24
    campaign_retention_days: PositiveInt = 30
    # Helix is cheap, but the badge catalog rarely changes more often than this.
    badge_sync_interval_minutes: Annotated[int, Field(ge=15)] = 60
    badge_announce_window_days: PositiveInt = 14
    bot_http_port: Annotated[int, Field(ge=0, le=65535)] = 8080
    log_level: str = "INFO"

    @property
    def min_check_interval_minutes(self) -> int:
        return KEYED_MIN_INTERVAL_MINUTES if self.drops_api_key else ANON_MIN_INTERVAL_MINUTES

    @property
    def effective_check_interval_minutes(self) -> int:
        return max(self.check_interval_minutes, self.min_check_interval_minutes)

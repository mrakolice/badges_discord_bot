import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta

import discord
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.bot import BadgeBot, DiscordNotifier, Services
from src.checker import Checker, CheckerSettings
from src.config import Settings
from src.db import create_asyncpg_database
from src.drops_api import SAFETY_MARGIN, DropsApiClient
from src.health import HealthServer
from src.storage import Storage
from src.twitch import TwitchClient

log = logging.getLogger("main")

DB_WAIT_SECONDS = 60
# Before the first successful check (fresh start) the bot still counts as healthy.
STARTUP_GRACE = timedelta(minutes=10)


def check_interval(settings: Settings) -> timedelta:
    # The Sunkwi gate opens min_interval + margin after the previous request; a job firing
    # exactly at min_interval would be refused and silently halve the refresh rate.
    return timedelta(minutes=settings.effective_check_interval_minutes) + 2 * SAFETY_MARGIN


async def run(settings: Settings) -> None:
    db = create_asyncpg_database(settings.database_url.get_secret_value())
    await db.connect(wait_seconds=DB_WAIT_SECONDS)
    storage = Storage(db)
    started_at = datetime.now(UTC)
    health: HealthServer | None = None
    scheduler = AsyncIOScheduler(timezone=UTC)

    try:
        async with httpx.AsyncClient() as http:
            drops_api = DropsApiClient(
                http,
                storage,
                api_key=settings.drops_api_key.get_secret_value()
                if settings.drops_api_key
                else None,
                min_interval=timedelta(minutes=settings.min_check_interval_minutes),
            )
            await drops_api.load()
            twitch = TwitchClient(
                http, settings.twitch_client_id, settings.twitch_client_secret.get_secret_value()
            )
            bot = BadgeBot(settings)
            notifier = DiscordNotifier(bot)
            checker = Checker(
                storage,
                drops_api,
                twitch,
                notifier,
                CheckerSettings(
                    ending_soon=timedelta(hours=settings.ending_soon_hours),
                    stream_cooldown_hours=settings.stream_notify_cooldown_hours,
                    stream_top_n=settings.stream_notify_top_n,
                    campaign_retention_days=settings.campaign_retention_days,
                ),
            )
            interval = check_interval(settings)

            async def scheduled_check() -> None:
                try:
                    await checker.run()
                except Exception:
                    log.exception("scheduled check failed")

            async def on_first_ready() -> None:
                scheduler.add_job(
                    scheduled_check,
                    "interval",
                    seconds=interval.total_seconds(),
                    id="check",
                    next_run_time=datetime.now(UTC),
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=int(interval.total_seconds()),
                )
                scheduler.start()
                log.info("checks scheduled every %s", interval)
                if settings.default_notification_channel_id:
                    await notifier.send(
                        settings.default_notification_channel_id,
                        discord.Embed(
                            title="✅ Бот запущен",
                            description="Слежу за Twitch Drops и badge-кампаниями. Справка: /help",
                            color=discord.Color.green(),
                        ),
                    )

            bot.attach(Services(storage, drops_api, twitch, checker), on_first_ready=on_first_ready)

            def is_healthy() -> dict[str, bool]:
                last = checker.last_success_at
                fresh = datetime.now(UTC) - (last or started_at) < 3 * interval + STARTUP_GRACE
                return {"discord": bot.is_ready() and not bot.is_closed(), "checks": fresh}

            if settings.bot_http_port:
                health = HealthServer(settings.bot_http_port, is_healthy)
                await health.start()

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))

            async with bot:
                await bot.start(settings.discord_bot_token.get_secret_value())
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        if health is not None:
            await health.stop()
        await db.close()
        log.info("stopped")


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from the environment
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # The bot never joins voice; skip discord.py's warnings about missing voice libraries.
    discord.VoiceClient.warn_nacl = False
    discord.VoiceClient.warn_dave = False
    try:
        asyncio.run(run(settings))
    except discord.LoginFailure:
        log.error("Discord rejected DISCORD_BOT_TOKEN; check the token in .env")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

# TODO: Discord-бот для Twitch Drops и бейджей

## 1. Инфраструктура и зависимости
- [ ] Удалить старый Telegram-код.
- [ ] Обновить `requirements.txt`:
  - `discord.py`
  - [x] `asyncpg`, `jinja2`, `pydantic` (в `pyproject.toml`, через `uv`)
  - `pydantic-settings`
  - `httpx`
  - `APScheduler`
  - `python-dotenv`
  - `pytest`, `pytest-asyncio`, `respx`
- [x] Создать `pyproject.toml` (pytest, ruff настройки).
- [x] Создать `docker-compose.test.yml` для интеграционных тестов.
- [x] Обновить `.env.example` под Discord + Postgres + Twitch + Sunkwi API.
- [x] `Dockerfile` (multi-stage uv, Python 3.14, uid 10001) + `.dockerignore`.
- [x] `docker-compose.yml`: `db-bootstrap` (роли), `migrate` (one-shot, ждёт Postgres),
  `bot` (после миграций); внешние сети `backend`/`proxy` инфра-стека, Traefik-метки выключены.
- [ ] Убрать profile `bot` и включить Traefik-метки, когда появятся `src/main.py` и `/health`.

## 2. Слой доступа к данным (asyncpg + Jinja SQL)
- [x] `src/db/templates.py`: `SqlTemplates`, `Dialect`, фильтры `bind` / `ident`.
- [x] `src/db/base.py`: ABC `QueryExecutor`, `Database`.
- [x] `src/db/asyncpg_backend.py`: `AsyncpgDatabase` (пул, JSONB-кодек, транзакции).
- [x] `src/db/migrations.py` + `src/migrate.py`: свой мигратор, `schema_migrations`, checksum.
- [x] `sql/migrations/0001_initial.sql.j2`: `channels`, `subscriptions`, `campaigns_seen`,
  `state`, `stream_notifications`.
- [x] Unit-тесты шаблонов и запрета inline-SQL, интеграционный `tests/integration/test_migrations.py`.

## 3. Конфигурация
- [ ] Создать `src/config.py` на `pydantic-settings`.
- [ ] Обязательные поля: `DISCORD_BOT_TOKEN`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `DATABASE_URL`.
- [ ] Вычисляемое поле `min_check_interval_minutes` (1 с `DROPS_API_KEY`, иначе 5).
- [ ] Unit-тест `tests/unit/test_config.py`.

## 4. Внешние API-клиенты
- [ ] Создать `src/drops_api.py`:
  - Pydantic-модели: `DropCampaign`, `RewardCampaign`, `TimeBasedDrop`, `Benefit`, `GameRef`.
  - `DropsApiClient.get_drops()` / `get_rewards()`.
  - Обработка 429 / Retry-After.
- [ ] Unit-тест `tests/unit/test_drops_api.py` с `respx`.
- [ ] Создать `src/twitch.py`:
  - Client Credentials flow.
  - `get_game(name)`.
  - `get_streams(game_id, first=10)`.
  - Кэширование App Access Token.
- [ ] Unit-тест `tests/unit/test_twitch.py` с `respx`.

## 5. Хранилище
- [x] Создать `src/storage.py`:
  - `ensure_channel(id, guild_id, name)`, `get_channel`.
  - `add_subscription`, `remove_subscription`, `remove_subscription_by_target`,
    `list_subscriptions`.
  - `get_channels_for_target`, `get_all_subscriptions`.
  - `upsert_campaign` (→ `(campaign, is_new)`), `get_campaign`, `mark_campaign_notified`.
  - `get_state`, `set_state`.
  - `was_stream_notified_today` / `_since`, `mark_stream_notified`.
- [x] Unit-тест `tests/unit/test_storage.py`.
- [x] Интеграционный тест `tests/integration/test_storage_with_db.py`.

## 6. Логика уведомлений
- [ ] Создать `src/notifications.py`:
  - `build_drop_embed(campaign, drop, stream)`.
  - `build_reward_embed(campaign)`.
  - `build_games_list_embed(games)`.
  - `build_subscriptions_list_embed(subs)`.
- [ ] Создать `src/checker.py`:
  - `run()` polling.
  - Дедупликация кампаний.
  - Matching по подпискам.
  - Поиск live-стримов через Twitch Helix.
  - Напоминания о кампаниях, заканчивающихся через < 24ч.
- [ ] Unit-тест `tests/unit/test_checker.py` с моками.
- [ ] Интеграционный тест `tests/integration/test_checker_with_db.py`.

## 7. Discord-бот
- [ ] Создать `src/bot.py`:
  - `BadgeBotCog`.
  - `on_ready` + приветствие в `DEFAULT_NOTIFICATION_CHANNEL_ID`.
  - Slash-команды: `/games`, `/rewards`, `/follow`, `/unfollow`, `/followbadges`, `/unfollowbadges`, `/list`, `/checknow`, `/help`.
  - Проверка прав `manage_channels` для управления подписками.
  - При `/follow` сразу показывать активные кампании игры.
- [ ] Создать `src/main.py`:
  - `create_asyncpg_database(settings.database_url)` + `connect()`.
  - Миграции уже применяет сервис `migrate` до старта бота.
  - Создание `discord.ext.commands.Bot`.
  - Подключение Cog.
  - APScheduler с `checker.run()`.
  - Graceful shutdown.

## 8. Тестирование и деплой
- [ ] Создать `tests/e2e/test_smoke.py`:
  - `docker build`.
  - `docker compose -f docker-compose.test.yml up`.
  - Проверка лога "Bot is ready".
- [ ] Проверить `docker compose up --build` локально.
- [ ] Ручной тест на тестовом Discord-сервере:
  - [ ] Пригласить бота.
  - [ ] `/follow Rust` → уведомление о текущих кампаниях.
  - [ ] `/list` → подписка отображается.
  - [ ] `/checknow` → без ошибок.
  - [ ] `/followbadges` → подписка на reward-кампании.
  - [ ] `/games` / `/rewards` → списки.
- [ ] Написать `README.md`:
  - Описание.
  - Инструкция по созданию Discord-приложения и получению токена.
  - Инструкция по Twitch Client ID.
  - Инструкция по Sunkwi API key (опц.).
  - Запуск через Docker Compose.
  - Команды бота.

## 9. Доработки (по желанию)
- [ ] Healthcheck endpoint или команда `/ping`.
- [ ] Фильтр reward-кампаний по ключевым словам.
- [ ] DND-часы (не беспокоить ночью).
- [ ] Логирование в stdout с ротацией (опц.).
- [ ] Мониторинг / метрики (опц.).

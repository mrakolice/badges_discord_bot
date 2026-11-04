# Подробный план реализации (Discord + Postgres + asyncpg + Jinja SQL)

> **План выполнен.** Актуальное поведение — в `SPEC.md`, статус задач — в `TODO.md`, правила
> слоя данных и инварианты — в `CLAUDE.md`. SQLAlchemy/Alembic исключены: слой БД — `src/db/` +
> `sql/<домен>/*.sql.j2` + свой мигратор.

## 0. Цель

Сделать Discord-бота, который:
- живёт в Docker-контейнере в одной сети с PostgreSQL;
- забирает активные Twitch Drops / reward-кампании из публичного Sunkwi API;
- пишет уведомления в Discord-каналы, в которых созданы подписки;
- не автоматизирует просмотр, только уведомляет.

---

## 1. Финальный стек

| Компонент | Библиотека |
|---|---|
| Discord-бот | `discord.py 2.x` |
| HTTP | `httpx` |
| API-модели | `pydantic` |
| БД | PostgreSQL 18 |
| Драйвер БД | `asyncpg` |
| SQL-уровень | `jinja2`-шаблоны в `sql/`, ABC `Database` + `AsyncpgDatabase` |
| Миграции | свой `MigrationRunner` (`python -m src.migrate`) |
| Планировщик | `APScheduler` (asyncio) |
| Конфиг | `python-dotenv` |
| Тесты | `pytest`, `pytest-asyncio`, `respx` |

---

## 2. Структура проекта

См. CLAUDE.md, раздел «Структура проекта».

---

## 3. Инфраструктура

Postgres 18 и Traefik живут в инфра-стеке (`docker-compose.infra.yml`); стек бота
(`docker-compose.yml`) подключается к его сетям `backend` и `proxy` как к внешним.

| Сервис | Что делает |
|---|---|
| `db-bootstrap` (profile `bootstrap`) | Разово: БД, роли `owner` (DDL) и `app` (DML), default privileges |
| `migrate` | При каждом `up`: ждёт Postgres, применяет недостающие миграции, выходит |
| `bot` (profile `bot`) | Стартует после `migrate` (`service_completed_successfully`) |

`Dockerfile`: multi-stage (uv → `python:3.14-slim`), пользователь uid 10001, код read-only.
Контейнеры: `read_only`, `cap_drop: ALL`, `no-new-privileges`, лимиты логов/памяти/pids.

## 4. Слой данных

См. CLAUDE.md «Слой доступа к данным». Миграции — `sql/migrations/NNNN_*.sql.j2`, применяет
сервис `migrate` при каждом `docker compose up`.

## 5. Модули

### 5.1 `src/config.py`

- `pydantic-settings` для валидации env.
- Обязательные: `DISCORD_BOT_TOKEN`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `DATABASE_URL`.
- `min_check_interval_minutes`: 1 если `DROPS_API_KEY`, иначе 5.

### 5.2 `src/drops_api.py`

`DropsApiClient`:
- `get_drops()` → `list[DropCampaign]`.
- `get_rewards()` → `list[RewardCampaign]`.
- Обработка `429`, `Retry-After`, логирование.

Pydantic-модели:
- `DropCampaign`, `RewardCampaign`, `TimeBasedDrop`, `Benefit`, `GameRef`.

### 5.3 `src/twitch.py`

`TwitchClient`:
- Client Credentials flow.
- `get_game(name)`.
- `get_streams(game_id, first=10)`.
- Кэш токена.

### 5.4 `src/storage.py`

`Storage`:
- `ensure_channel(id, guild_id, name)`.
- `add_subscription(...)`, `remove_subscription(...)`, `list_subscriptions(channel_id)`.
- `get_channels_for_target(type, target_id)`.
- `get_all_subscriptions()`.
- `upsert_campaign(...)`, `get_campaign(...)`.
- `get_state`, `set_state`.
- `was_stream_notified_today(...)`, `mark_stream_notified(...)`.

### 5.5 `src/checker.py`

`Checker`:
- `run()`:
  1. `drops = await drops_api.get_drops()`.
  2. `rewards = await drops_api.get_rewards()`.
  3. Для каждой кампании: `upsert_campaign`; если новая → уведомить подписчиков.
  4. Для каждой подписки `game`: найти live-стримы, уведомить о новых.
  5. Для кампаний, заканчивающихся < 24ч — `ending` уведомление.

### 5.6 `src/notifications.py`

Discord embed:
- `build_drop_embed(campaign, drop, stream)`.
- `build_reward_embed(campaign)`.
- `build_games_list_embed(games)`.
- `build_subscriptions_list_embed(subs)`.

### 5.7 `src/bot.py`

`BadgeBotCog`:
- `on_ready` — лог, приветствие в `DEFAULT_NOTIFICATION_CHANNEL_ID`.
- Slash-команды с проверкой `manage_channels`.
- При `/follow` сразу показывать текущие кампании.

### 5.8 `src/main.py`

- Инициализация engine.
- Миграции применяет сервис `migrate` до старта бота.
- Создание Discord Bot.
- `APScheduler` с `checker.run()`.
- Graceful shutdown.

---

## 6. Тесты

### 6.1 Unit

| Тест | Что проверяет |
|---|---|
| `test_config.py` | Валидация env, min_check_interval |
| `test_drops_api.py` | Парсинг Sunkwi API, 429, API-key |
| `test_twitch.py` | Token refresh, streams |
| `test_storage.py` | CRUD с моком engine/connection |
| `test_checker.py` | Дедупликация, matching, live-stream уведомления |

### 6.2 Интеграционные

| Тест | Что проверяет |
|---|---|
| `test_migrations.py` | Миграции применяются, повтор — no-op, checksum |
| `test_checker_with_db.py` | Checker с реальной БД и замоканными API |

### 6.3 E2E / smoke

| Тест | Что проверяет |
|---|---|
| `test_smoke.py` | `docker build`, `docker compose up`, лог "Bot is ready" |

### 6.4 Ручной чек-лист

1. Пригласить бота на тестовый сервер.
2. `/follow Rust` → уведомление о текущих кампаниях Rust.
3. `/list` → подписка отображается.
4. `/checknow` → без ошибок.
5. `/followbadges` → подписка на reward-кампании.
6. `/games` / `/rewards` → списки.

---

## 7. Порядок реализации

| Шаг | Что делаем |
|---|---|
| 1 | Удалить старый код, обновить `requirements.txt`, `Dockerfile`, `pyproject.toml` |
| 2 | Создать `docker-compose.yml` и `docker-compose.test.yml` |
| 3 | Слой данных (asyncpg + Jinja SQL), миграции `0001`/`0002` |
| 4 | Написать `src/config.py` |
| 5 | Написать `src/drops_api.py` + pydantic-модели |
| 6 | Написать `src/twitch.py` |
| 7 | Написать `src/storage.py` |
| 8 | Написать `src/notifications.py` |
| 9 | Написать `src/checker.py` |
| 10 | Написать `src/bot.py` |
| 11 | Написать `src/main.py` |
| 12 | Написать unit-тесты |
| 13 | Написать интеграционные тесты + `docker-compose.test.yml` |
| 14 | Написать smoke-тест |
| 15 | Написать `README.md` |
| 16 | Ручной тест на Discord-сервере |

---

## 8. Команды для локальной работы

См. раздел «Запуск и тесты» в CLAUDE.md и README.md.

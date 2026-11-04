# Подробный план реализации (Discord + Postgres + asyncpg + Jinja SQL)

> SQLAlchemy/Alembic исключены. Слой БД: `src/db/` + `sql/<домен>/*.sql.j2` + свой мигратор
> (правила — в CLAUDE.md, раздел «Слой доступа к данным»). Разделы ниже про SQLAlchemy/Alembic
> заменены этим слоем.

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
| БД | PostgreSQL 16 |
| Драйвер БД | `asyncpg` |
| SQL-уровень | `jinja2`-шаблоны в `sql/`, ABC `Database` + `AsyncpgDatabase` |
| Миграции | свой `MigrationRunner` (`python -m src.migrate`) |
| Планировщик | `APScheduler` (asyncio) |
| Конфиг | `python-dotenv` |
| Тесты | `pytest`, `pytest-asyncio`, `respx` |

---

## 2. Структура проекта

```
twitch_badge_bot/
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_initial.py
├── docker/
│   └── entrypoint.sh         # wait-for-postgres + alembic upgrade + run
├── src/
│   ├── __init__.py
│   ├── main.py               # точка входа
│   ├── config.py             # env + валидация
│   ├── db.py                 # SQLAlchemy engine + async session helper
│   ├── models.py             # SQLAlchemy Core tables
│   ├── storage.py            # репозиторий
│   ├── drops_api.py          # клиент Sunkwi API
│   ├── twitch.py             # клиент Twitch Helix
│   ├── checker.py            # логика polling
│   ├── notifications.py      # Discord embed
│   ├── bot.py                # discord.py Cog
│   └── utils.py              # хелперы
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_drops_api.py
│   │   ├── test_twitch.py
│   │   ├── test_storage.py
│   │   └── test_checker.py
│   ├── integration/
│   │   ├── test_postgres.py
│   │   └── test_checker_with_db.py
│   └── e2e/
│       └── test_smoke.py
├── docker-compose.yml
├── docker-compose.test.yml
├── Dockerfile
├── pyproject.toml
├── requirements.txt
├── .env.example
└── README.md
```

---

## 3. Инфраструктура

### 3.1 `docker-compose.yml`

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: bot
      POSTGRES_PASSWORD: bot
      POSTGRES_DB: twitch_badge_bot
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks:
      - botnet
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bot -d twitch_badge_bot"]
      interval: 5s
      timeout: 5s
      retries: 5

  bot:
    build: .
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://bot:bot@postgres:5432/twitch_badge_bot
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - botnet
    restart: unless-stopped

networks:
  botnet:

volumes:
  pgdata:
```

### 3.2 `Dockerfile`

```dockerfile
FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends netcat-openbsd && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "src.main"]
```

### 3.3 `docker/entrypoint.sh` (опционально)

Ждёт Postgres, запускает `alembic upgrade head`, стартует бота.

---

## 4. Alembic + SQLAlchemy Core

### 4.1 `src/models.py`

SQLAlchemy Core tables:
- `channels`
- `subscriptions`
- `campaigns_seen`
- `state`
- `stream_notifications`

### 4.2 `src/db.py`

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine

engine: AsyncEngine = create_async_engine(settings.database_url)
```

### 4.3 Alembic

- `alembic init alembic`.
- Настроить `alembic/env.py` на async-движок.
- Первая миграция создаёт таблицы из `src/models.py`.

---

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
- `alembic upgrade head` при старте (или через entrypoint).
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
| `test_postgres.py` | Alembic миграции, подключение |
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
| 3 | Инициализировать Alembic, написать `src/models.py`, первую миграцию |
| 4 | Написать `src/db.py`, `src/config.py` |
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

```bash
# Копировать env
cp .env.example .env
# отредактировать

# Поднять Postgres + bot
docker compose up --build

# Применить миграции вручную (если не через entrypoint)
docker compose exec bot alembic upgrade head

# Запуск тестов
docker compose -f docker-compose.test.yml up -d
pytest

# Создать новую миграцию после изменения models.py
alembic revision --autogenerate -m "add something"
```

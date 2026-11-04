# CLAUDE.md — Twitch Badge / Drops Discord Bot

## Что это за проект

Discord-бот, который живёт в Docker-контейнере и уведомляет пользователей о Twitch Drops и reward/badge-кампаниях. Бот **не смотрит стримы и не эмулирует поведение зрителя** — он только находит активные кампании и присылает ссылки в Discord-каналы.

## Текущий статус

Проект находится в фазе планирования. Реализованы:
- `SPEC.md` — спецификация.
- `PLAN.md` — пошаговый план с тестами.
- `TODO.md` — чек-лист задач.

- Слой доступа к данным (`src/db/`, `src/storage.py`, `sql/`) и стартовая миграция.

## Слой доступа к данным (правила)

Стек: `asyncpg` + SQL-шаблоны Jinja2 + свой мигратор. **SQLAlchemy/Alembic не используем.**

1. **Весь SQL — только в файлах `sql/<домен>/<запрос>.sql.j2`.** В `.py` SQL-литералов нет
   (это проверяет `tests/unit/test_sql_tree.py`). Тестовый SQL — в `tests/sql/`.
2. Домены: `channel_registry`, `follows`, `campaign_tracking`, `live_alerts`, `bot_state`,
   `schema_versioning`, `migrations`.
3. Значения подставляются **только** через `{{ x | bind }}` (→ `$1`, значение уходит в args),
   имена таблиц/колонок — через `{{ x | ident }}` (whitelist + кавычки). Голый `{{ x }}`
   бросает `SqlTemplateError`. Логика `{% if %}` — для опциональных частей запроса.
4. Код зависит от абстракций `QueryExecutor` / `Database` (`src/db/base.py`) и вызывает запросы
   через enum-ы из `src/db/queries.py` (по `StrEnum` на домен):
   `await db.fetch(Follows.LIST_FOR_CHANNEL, channel_id=..., type=None)`.
   Строковые имена шаблонов в коде запрещены (тест `test_template_names_only_via_query_enums`);
   новый шаблон = файл в `sql/` + член enum-а (тест сверяет enum-ы с файлами).
   Единственная реализация — `AsyncpgDatabase`. Новый драйвер = новый `Database` + свой `Dialect`.
5. Миграции: `sql/migrations/NNNN_name.sql.j2`, применяются `python -m src.migrate`
   (или `MigrationRunner(db).apply()`), учёт в `schema_migrations` с sha256.
   **Применённую миграцию не редактировать** — только новая.

## Стабильные решения

| Компонент | Выбор |
|---|---|
| Мессенджер | Discord (`discord.py 2.x`) |
| Язык | Python 3.14 |
| Доступ к БД | `asyncpg` + SQL-шаблоны `jinja2`, свой мигратор |
| API-клиент | `httpx` |
| Модели данных | `pydantic` |
| База данных | PostgreSQL 16 |
| Планировщик | `APScheduler` (asyncio) |
| Конфиг | переменные окружения + `.env` |
| Источник кампаний | Sunkwi Twitch Drops API (`https://twitch-drops-api.sunkwi.com/v2/drops`, `/v2/rewards`) |
| Контейнеризация | Docker + docker-compose |
| Тесты | `pytest`, `pytest-asyncio`, `respx` |

## Что категорически нельзя

- **Не автоматизировать просмотр Twitch.** Никакого Playwright, Selenium, headless-браузеров, имитации watch-time, отправки heartbeat-ов. Это нарушает ToS Twitch и чревато баном аккаунта.
- **Не хардкодить секреты.** Все токены, пароли, ключи — только через env.
- **Не злоупотреблять Sunkwi API.** Без ключа — не чаще 1 запроса в 5 минут на маршрут. С ключом — не чаще 1 запроса в минуту. Бот должен уважать `Retry-After` и `refreshIntervalSeconds`.
- **Не коммитить `.env` и локальные БД/venv.** В `.gitignore` уже должны быть: `.env`, `data/`, `.venv/`, `__pycache__/`, `*.pyc`, `logs/`.

## Структура проекта (ожидаемая)

```
twitch_badge_bot/
├── sql/                        # ВСЕ SQL-запросы, по доменам, *.sql.j2
│   └── migrations/             # NNNN_name.sql.j2
├── docker/
│   └── entrypoint.sh           # wait-for-postgres + миграции + запуск
├── src/
│   ├── __init__.py
│   ├── main.py                 # точка входа
│   ├── config.py               # env + валидация
│   ├── db/                     # templates, base (ABC), asyncpg_backend, migrations, records
│   ├── migrate.py              # python -m src.migrate
│   ├── storage.py              # репозиторий поверх QueryExecutor
│   ├── drops_api.py            # клиент Sunkwi API
│   ├── twitch.py               # клиент Twitch Helix
│   ├── checker.py              # логика polling
│   ├── notifications.py        # Discord embed
│   ├── bot.py                  # discord.py Cog
│   └── utils.py                # хелперы
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docker-compose.yml
├── docker-compose.test.yml
├── Dockerfile
├── pyproject.toml
├── requirements.txt
├── .env.example
├── SPEC.md
├── PLAN.md
├── TODO.md
└── README.md
```

## Конвенции кода

- Python 3.14, type hints обязательны. Зависимости — через `uv` (`uv sync`).
- Асинхронный код везде, где есть I/O.
- `ruff` для форматирования и линтинга.
- Максимальная длина строки 100 символов.
- Коммитим только рабочий код; тесты должны проходить (`pytest`).
- Модели API — `pydantic`. Строки БД — pydantic-модели в `src/db/records.py`.

## Запуск и тесты

```bash
# Копировать env
cp .env.example .env
# отредактировать

# Поднять Postgres + bot
docker compose up --build

# Unit-тесты
uv run pytest tests/unit

# Интеграционные (Postgres на порту ${TEST_POSTGRES_PORT:-55433}, без TEST_DATABASE_URL — skip)
docker compose -f docker-compose.test.yml up -d --wait
TEST_DATABASE_URL=postgresql://bot:bot@localhost:55433/twitch_badge_bot_test uv run pytest

# Миграции
DATABASE_URL=... uv run python -m src.migrate
```

## Работа с env

Обязательные переменные:
- `DISCORD_BOT_TOKEN`
- `TWITCH_CLIENT_ID`
- `TWITCH_CLIENT_SECRET`
- `DATABASE_URL`

Опциональные:
- `DROPS_API_KEY` — для повышения лимита Sunkwi API.
- `DISCORD_GUILD_ID` — для быстрой регистрации slash-команд.
- `DEFAULT_NOTIFICATION_CHANNEL_ID` — канал для стартового сообщения.
- `CHECK_INTERVAL_MINUTES`
- `STREAM_NOTIFY_COOLDOWN_HOURS`

## Перед изменениями

1. Прочитать `SPEC.md`, `PLAN.md`, `TODO.md`.
2. Изменение схемы БД — только новой миграцией в `sql/migrations/` по правилам выше.
3. Если изменение касается взаимодействия с Twitch — убедиться, что оно не нарушает ToS.
4. Обновлять `TODO.md` и `README.md` при значимых изменениях.

## Связь с пользователем

- Пользователь говорит по-русски — отвечай на русском.
- Пользователь любит сначала обсуждать план и стек, а потом писать код.
- Перед началом нового этапа кратко summarизируй, что будешь делать.

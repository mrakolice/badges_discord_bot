# TODO: Discord-бот для Twitch Drops и бейджей

## 1. Инфраструктура и зависимости
- [x] Удалить старый код (заглушка `main.py` в корне).
- [x] Зависимости в `pyproject.toml` через `uv`: `discord.py`, `asyncpg`, `jinja2`, `pydantic`,
  `pydantic-settings`, `httpx`, `APScheduler 3.11`; dev — `pytest`, `pytest-asyncio`,
  `respx`, `ruff`.
- [x] `docker-compose.test.yml` (Postgres 18) для интеграционных тестов.
- [x] `.env.example` под Discord + Postgres + Twitch + Sunkwi API.
- [x] `Dockerfile` (multi-stage uv, Python 3.14, uid 10001) + `.dockerignore`.
- [x] `docker-compose.yml`: `db-bootstrap` (роли), `migrate` (one-shot, ждёт Postgres),
  `bot` (после миграций, healthcheck); внешние сети `backend`/`proxy` инфра-стека.
- [x] Traefik публикует только `/twitch-bot/health`.

## 2. Слой доступа к данным (asyncpg + Jinja SQL)
- [x] `src/db/templates.py`: `SqlTemplates`, `Dialect`, фильтры `bind` / `ident`.
- [x] `src/db/base.py`: ABC `QueryExecutor`, `Database`; enum-ы запросов в `src/db/queries.py`.
- [x] `src/db/asyncpg_backend.py`: пул, JSONB-кодек, транзакции, ожидание БД.
- [x] `src/db/migrations.py` + `src/migrate.py`: свой мигратор, checksum, advisory lock.
- [x] `0001_initial`, `0002_per_channel_deliveries` (журнал доставок, кулдаун стримов).
- [x] Тесты шаблонов, запрета inline-SQL, миграций.

## 3. Конфигурация
- [x] `src/config.py` на `pydantic-settings`; пустые переменные из compose = «не задано».
- [x] Минимальный интервал: 1 мин с `DROPS_API_KEY`, иначе 5.
- [x] `tests/unit/test_config.py`.

## 4. Внешние API-клиенты
- [x] `src/drops_api.py`: модели по реальному формату Sunkwi (игры → кампании, `allow.channels`),
  лимит с учётом рестартов, 429/`Retry-After`/`ratelimit-reset`, снимок в `state`.
- [x] `src/twitch.py`: Client Credentials, кэш токена, 401 → обновление, стримы по игре и логинам.
- [x] `tests/unit/test_drops_api.py`, `tests/unit/test_twitch.py` с `respx`.

## 5. Хранилище
- [x] `src/storage.py`: каналы, подписки, кампании, `claim/release_delivery`,
  `claim/release_stream_notification`, `prune_campaigns`, `state`.
- [x] `tests/unit/test_storage.py`, `tests/integration/test_storage_with_db.py`.

## 6. Логика уведомлений
- [x] `src/notifications.py`: embed-ы кампаний, наград, live, списки с учётом лимитов Discord.
- [x] `src/checker.py`: polling под блокировкой, доставка «ровно один раз» на канал,
  напоминания, live-стримы только с дропами, удаление пропавших каналов.
- [x] `tests/unit/test_checker.py`, `tests/integration/test_checker_with_db.py`.

## 7. Discord-бот
- [x] `src/bot.py`: `BadgeBotCog`, все 9 команд, `manage_channels`, автодополнение `/follow`,
  обработка удаления каналов/серверов, приветствие в `DEFAULT_NOTIFICATION_CHANNEL_ID`.
- [x] `src/main.py`: БД, APScheduler, `/health`, graceful shutdown по SIGTERM.
- [x] `src/health.py` + healthcheck в compose.

## 8. Тестирование и деплой
- [x] Smoke в Docker: сборка, bootstrap, миграции (повторный запуск — no-op), старт бота до логина.
- [ ] E2E «Bot is ready» — нужен настоящий токен тестового бота.
- [ ] Ручной тест на тестовом Discord-сервере:
  - [ ] Пригласить бота.
  - [ ] `/follow Rust` → уведомление о текущих кампаниях.
  - [ ] `/list` → подписка отображается.
  - [ ] `/checknow` → без ошибок.
  - [ ] `/followbadges` → подписка на reward-кампании.
  - [ ] `/games` / `/rewards` → списки.
- [x] `README.md`.

## 9. Доработки (по желанию)
- [ ] Фильтр reward-кампаний по ключевым словам.
- [ ] DND-часы (не беспокоить ночью).
- [ ] Мониторинг / метрики (опц.).
- [ ] Повтор отправки при Forbidden шумит в логах каждую проверку — ограничить.

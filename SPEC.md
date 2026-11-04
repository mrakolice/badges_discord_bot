# Спецификация: Discord-бот для Twitch Drops и бейджей

## 1. Проблема и ограничения

Пользователь хочет получать уведомления в Discord: «когда и на какой стрим зайти, чтобы получить бейдж / Drops-награду». Бот живёт в Docker-контейнере в одной сети с PostgreSQL, многопользовательский (канальный), общение через slash-команды в каналах.

**Ключевое ограничение Twitch:** нет официального публичного API для списка активных Drops-кампаний и reward-кампаний (сайт-вайд бейджи).

**Что нельзя делать:** автоматизировать просмотр (Playwright, headless, имитация watch-time). Это нарушает ToS и чревато баном.

---

## 2. Источник данных

Используем **публичную обёртку Sunkwi Twitch Drops API**:

- `https://twitch-drops-api.sunkwi.com/v2/drops` — активные Drops-кампании по играм.
- `https://twitch-drops-api.sunkwi.com/v2/rewards` — сайт-вайд reward-кампании (часто чат-бейджи и партнёрские награды).

### Почему этот источник

- Не требует авторизации Twitch (1 запрос в 5 минут анонимно).
- Бесплатный API-ключ даёт 1 запрос в минуту.
- Обновляется раз в 60 секунд.
- Содержит `gameId`, `gameDisplayName`, `startAt`, `endAt`, `requiredMinutesWatched`, названия наград.
- Не требует скрапинга Twitch GraphQL.

---

## 3. Финальный стек

| Компонент | Выбор | Почему |
|---|---|---|
| Discord-бот | `discord.py 2.x` | Зрелая, официальная, async, поддержка slash-команд |
| HTTP-клиент | `httpx` | Современный async API, удобно мокать |
| Модели API | `pydantic` | Валидация и ясная структура данных из Sunkwi API |
| База данных | PostgreSQL 18 (общий, из инфра-стека) | Надёжность, JSONB, многопользовательская нагрузка |
| Драйвер БД | `asyncpg` | Самый быстрый async-драйвер для Postgres |
| Работа с БД | SQL-файлы `sql/<домен>/*.sql.j2` (Jinja2, фильтры `bind`/`ident`) | Весь SQL на виду, параметры всегда биндятся |
| Абстракция БД | ABC `QueryExecutor`/`Database` + `AsyncpgDatabase` | Можно подменить драйвер |
| Миграции | свой `MigrationRunner` (`sql/migrations/`, `migrations_meta.schema_migrations`), сервис `migrate` при каждом `up` | Те же правила шаблонов, advisory lock |
| Планировщик | `APScheduler` (asyncio) | Гибкие интервалы, надёжен |
| Логирование | стандартный `logging` → stdout | Для Docker достаточно |
| Конфиг | переменные окружения + `.env` | Просто и безопасно |

---

## 4. Что делает бот

1. Раз в несколько минут забирает кампании из Sunkwi API.
2. Запоминает увиденные кампании в PostgreSQL.
3. Сравнивает с предыдущим состоянием и находит новые кампании / награды.
4. Для каждого Discord-канала смотрит его подписки (игры / reward-кампании).
5. Присылает в канал уведомление с названием кампании, наградой, временем просмотра, датой окончания и ссылкой.
6. Для Drops дополнительно ищет live-стрим через Twitch Helix и шлёт «сейчас в эфире: @streamer».
7. Напоминает, если кампания скоро заканчивается.

**Бот не открывает стримы и не эмулирует просмотр.**

---

## 5. Архитектура

```
Инфра-стек (docker-compose.infra.yml)        Стек бота (docker-compose.yml)
  ├── traefik ── сеть proxy (external) ───────┐
  └── postgres:18-alpine ── сеть backend ─────┼── db-bootstrap (разово, profile bootstrap)
                                              ├── migrate (one-shot, роль owner)
                                              │       │ service_completed_successfully
                                              │       ▼
                                              └── bot (роль app, только DML)
  bot (twitch-badge-bot)
          │
          ├── APScheduler (раз в N минут)
          │       │
          │       ▼
          ├── DropsApiClient ──► Sunkwi API
          │       │
          │       ▼
          ├── TwitchClient ──► Helix streams
          │       │
          │       ▼
          ├── Checker
          │       │
          │       ▼
          ├── Storage → QueryExecutor (sql/*.sql.j2) → AsyncpgDatabase
          │       │
          │       ▼
          └── discord.py Bot ──► Discord channels
```

---

## 6. Модель данных (`sql/migrations/0001_initial.sql.j2`)

### `channels`

| Поле | Тип |
|---|---|
| `id` | BIGINT PK |
| `guild_id` | BIGINT |
| `name` | TEXT |
| `created_at` | TIMESTAMPTZ |

### `subscriptions`

| Поле | Тип | Описание |
|---|---|---|
| `id` | SERIAL PK | |
| `channel_id` | BIGINT FK → channels | |
| `type` | TEXT (`game` / `reward`) | |
| `target_id` | TEXT | `gameId` или `ALL_REWARDS` |
| `target_name` | TEXT | человекочитаемое имя |
| `created_at` | TIMESTAMPTZ | |

Уникальность: `(channel_id, type, target_id)`.

### `campaigns_seen`

| Поле | Тип | Описание |
|---|---|---|
| `campaign_id` | TEXT | |
| `campaign_type` | TEXT (`drop` / `reward`) | |
| `first_seen_at` | TIMESTAMPTZ | |
| `last_notified_at` | TIMESTAMPTZ | |
| `ending_notified_at` | TIMESTAMPTZ | |
| `data_json` | JSONB | сырые данные кампании |

PK: `(campaign_id, campaign_type)`.

### `state`

| Поле | Тип |
|---|---|
| `key` | TEXT PK |
| `value` | JSONB |

### `stream_notifications`

| Поле | Тип |
|---|---|
| `id` | SERIAL PK |
| `channel_id` | BIGINT |
| `game_id` | TEXT |
| `streamer_login` | TEXT |
| `notified_at` | TIMESTAMPTZ |

Уникальность: `(channel_id, game_id, streamer_login, (notified_at AT TIME ZONE 'UTC')::date)` — сутки по UTC.

---

## 7. Команды Discord

Команды — slash (`/`), работают в канале.

| Команда | Описание |
|---|---|
| `/games` | Список активных игр с Drops. |
| `/rewards` | Список активных reward/badge-кампаний. |
| `/follow <игра>` | Подписать канал на Drops игры. |
| `/unfollow <id>` | Отписать канал по ID подписки. |
| `/followbadges` | Подписать канал на badge/reward-кампании. |
| `/unfollowbadges` | Отписать канал. |
| `/list` | Подписки этого канала. |
| `/checknow` | Принудительная проверка. |
| `/help` | Справка. |

`/follow`, `/unfollow`, `/followbadges`, `/unfollowbadges` требуют права `manage_channels`.

---

## 8. Поведение уведомлений

1. **При подписке на игру** (`/follow`) — сразу сообщаем обо всех текущих активных кампаниях для этой игры.
2. **Новая кампания** — уведомление во все подписанные каналы.
3. **Live-стример** — если появился стрим по подписанной игре, бот дополнительно шлёт «сейчас в эфире: @streamer» (не чаще одного раза в сутки на канал+игра+стример).
4. **Reward/badge-кампании** — уведомляем о каждой новой кампании в каналах, подписанных на `/followbadges`.
5. **Кампания скоро заканчивается** — напоминание за 24 часа.

---

## 9. Конфигурация

```env
# Discord
DISCORD_BOT_TOKEN=...
DISCORD_GUILD_ID=...              # опционально, для быстрой регистрации slash-команд
DEFAULT_NOTIFICATION_CHANNEL_ID=  # куда слать стартовое сообщение

# Sunkwi Drops API
DROPS_API_KEY=...                 # опционально

# Twitch Helix
TWITCH_CLIENT_ID=...
TWITCH_CLIENT_SECRET=...

# Postgres
DATABASE_URL=postgresql+asyncpg://bot:bot@postgres:5432/twitch_badge_bot

# Логика
CHECK_INTERVAL_MINUTES=5
STREAM_NOTIFY_COOLDOWN_HOURS=6
```

---

## 10. План действий

Подробный пошаговый план с тестами — в файле `PLAN.md`.

Кратко:

1. Очистить старый код, обновить зависимости.
2. Настроить `docker-compose.yml` с PostgreSQL.
3. Слой доступа к данным (asyncpg + Jinja SQL) и первая миграция — **сделано**.
4. Реализовать конфигурацию.
5. Реализовать клиенты Sunkwi API и Twitch Helix.
6. Реализовать `Storage` поверх `QueryExecutor` — **сделано**.
7. Реализовать `Checker`.
8. Реализовать Discord Cog.
9. Написать тесты (unit, интеграция, smoke).
10. Написать `README.md` и провести ручной тест.

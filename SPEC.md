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

1. Раз в `CHECK_INTERVAL_MINUTES` забирает кампании из Sunkwi API, не нарушая лимит: время
   следующего разрешённого запроса и последний снимок данных хранятся в БД (`state`), поэтому
   перезапуски и `/checknow` не порождают лишних запросов (отказ 429 тоже засчитывается).
2. Запоминает увиденные кампании в PostgreSQL (`campaigns_seen`, чистка через 30 дней).
3. Для каждого канала и каждой активной кампании по его подпискам отправляет уведомление
   **ровно один раз**: доставка отмечается в `campaign_deliveries` до отправки и снимается,
   если отправить не удалось (повтор при следующей проверке).
4. Уведомление: название, награды, сколько смотреть, дата окончания (в часовом поясе читателя),
   ссылка, каналы-исключения (`allow.channels`).
5. Для Drops ищет live-стримы через Twitch Helix — только те, что реально дают дропы: каналы из
   `allow.channels`, если кампания ограничена, иначе топ-`STREAM_NOTIFY_TOP_N` по зрителям.
6. Напоминает, если кампания закончится в течение `ENDING_SOON_HOURS` (24 ч).

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

## 6. Модель данных (`sql/migrations/0001…0002`)

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
| `last_seen_at` | TIMESTAMPTZ | для чистки ушедших из API кампаний |
| `data_json` | JSONB | данные кампании |

PK: `(campaign_id, campaign_type)`.

### `campaign_deliveries`

| Поле | Тип | Описание |
|---|---|---|
| `channel_id` | BIGINT FK → channels | |
| `campaign_id`, `campaign_type` | FK → campaigns_seen | |
| `kind` | TEXT (`new` / `ending`) | новое / «скоро закончится» |
| `delivered_at` | TIMESTAMPTZ | |

PK: `(channel_id, campaign_id, campaign_type, kind)`. Вставка `ON CONFLICT DO NOTHING` —
атомарная отметка «отправлено», поэтому поздние подписчики, `/follow` и сбои отправки
обрабатываются без дублей и пропусков.

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

Уникальность: `(channel_id, game_id, streamer_login)`; повторное уведомление — атомарным upsert,
только если прошло `STREAM_NOTIFY_COOLDOWN_HOURS`.

Учёт миграций — `migrations_meta.schema_migrations` (отдельная схема, недоступная роли бота).

---

## 7. Команды Discord

Команды — slash (`/`), работают в канале.

| Команда | Описание |
|---|---|
| `/games [поиск]` | Список активных игр с Drops (из снимка, без запроса к API). |
| `/rewards` | Список активных reward/badge-кампаний. |
| `/follow <игра>` | Подписать канал на Drops игры (автодополнение; игры без кампаний — через Helix). |
| `/unfollow <id>` | Отписать канал по ID подписки. |
| `/followbadges` | Подписать канал на badge/reward-кампании. |
| `/unfollowbadges` | Отписать канал. |
| `/list` | Подписки этого канала. |
| `/checknow` | Проверка сейчас; к API — только если позволяет лимит, иначе по снимку. |
| `/help` | Справка. |

`/follow`, `/unfollow`, `/followbadges`, `/unfollowbadges`, `/checknow` требуют права
`manage_channels`. Команды работают только на серверах. При удалении канала или выходе бота
с сервера подписки удаляются.

---

## 8. Поведение уведомлений

1. **При подписке на игру** (`/follow`) — сразу сообщаем обо всех текущих активных кампаниях игры;
   следующая проверка их не повторит. То же для `/followbadges`.
2. **Новая кампания** — уведомление во все подписанные каналы (один раз на канал).
3. **Live-стример** — «сейчас в эфире» для стримов, дающих дропы (см. §4.5), не чаще раза в
   `STREAM_NOTIFY_COOLDOWN_HOURS` на канал+игра+стример.
4. **Reward/badge-кампании** — каналам, подписанным через `/followbadges`.
5. **Кампания скоро заканчивается** — напоминание за `ENDING_SOON_HOURS`; если кампания впервые
   видна каналу уже в этом окне — одно объединённое сообщение.
6. Упоминания (`@everyone`, роли) в сообщениях бота отключены.

---

## 9. Конфигурация

Полный список — `.env.example`. Основное:

```env
DISCORD_BOT_TOKEN=...
DISCORD_GUILD_ID=                 # опционально: мгновенная регистрация команд на одном сервере
DEFAULT_NOTIFICATION_CHANNEL_ID=  # опционально: сообщение «бот запущен»
DROPS_API_KEY=                    # опционально: лимит 1/мин вместо 1/5 мин
TWITCH_CLIENT_ID=...
TWITCH_CLIENT_SECRET=...
DATABASE_URL=...                  # в Docker собирается из BOT_DB_* в compose

CHECK_INTERVAL_MINUTES=5          # не меньше 5 без ключа и 1 с ключом
STREAM_NOTIFY_COOLDOWN_HOURS=6
STREAM_NOTIFY_TOP_N=3             # 0 — без live-уведомлений
ENDING_SOON_HOURS=24
CAMPAIGN_RETENTION_DAYS=30
BOT_HTTP_PORT=8080                # /health; 0 — выключить
```

---

## 10. Статус

Реализовано всё из §4–§8: слой данных и миграции, клиенты Sunkwi и Helix, `Checker`,
Discord Cog, `/health`, Docker-стек. Осталось: ручной тест на Discord-сервере (TODO.md §8).

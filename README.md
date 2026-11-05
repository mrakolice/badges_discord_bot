# Twitch Drops / Badge Discord Bot

Discord-бот, который сообщает в каналы о Twitch Drops и reward/badge-кампаниях: что за
награда, сколько смотреть, когда заканчивается, кто из подходящих стримеров сейчас в эфире.

Бот **не смотрит стримы и не эмулирует зрителя** — только присылает ссылки. Данные о
кампаниях берутся из публичного [Sunkwi Twitch Drops API](https://drops.sunkwi.com/docs),
live-стримы — из Twitch Helix.

## Что нужно

- Docker + Docker Compose.
- Запущенный инфра-стек (`docker-compose.infra.yml`): общий PostgreSQL 18 и Traefik, сети
  `backend` и `proxy`.
- Discord-приложение с ботом, Twitch-приложение. Ключ Sunkwi — по желанию.

## 1. Discord-бот

1. <https://discord.com/developers/applications> → **New Application**.
2. **Bot** → **Reset Token** → скопировать в `DISCORD_BOT_TOKEN`. Привилегированные
   intents не нужны.
3. **OAuth2 → URL Generator**: scopes `bot` и `applications.commands`; права бота
   `View Channels`, `Send Messages`, `Embed Links`. Открыть ссылку и добавить бота на сервер.
4. Для мгновенной регистрации команд на одном сервере укажите его ID в `DISCORD_GUILD_ID`
   (без него глобальные команды могут появляться до часа).

## 2. Twitch

<https://dev.twitch.tv/console/apps> → **Register Your Application** (OAuth Redirect URL —
`http://localhost`, категория любая) → `TWITCH_CLIENT_ID` и **New Secret** →
`TWITCH_CLIENT_SECRET`. Используется только чтение публичных данных (поиск игр и стримов).

## 3. Sunkwi API key (необязательно)

Без ключа — 1 запрос в 5 минут на маршрут, с ключом — 1 в минуту. Ключ бесплатный:
<https://drops.sunkwi.com/dashboard> → `DROPS_API_KEY`. Бот сам соблюдает лимит, в том числе
между перезапусками.

## 4. Запуск

```bash
cp .env.example .env   # заполнить токены и пароли (URL-safe, например: openssl rand -hex 24)
```

Один раз — создать БД и роли в общем Postgres (нужен `POSTGRES_PASSWORD` суперпользователя):

```bash
docker compose --profile bootstrap run --rm db-bootstrap
```

Сборка, миграции и запуск бота:

```bash
docker compose up -d --build
```

При каждом `up` сначала выполняется сервис `migrate`: он применяет только недостающие
миграции и завершается, бот стартует после него. Бот работает под ролью, которая может только
читать и менять данные, но не схему.

Проверка: `docker compose logs -f bot` → `Bot is ready`, а `https://<BOTS_HOSTNAME>/twitch-bot/health`
отвечает `{"status": "ok"}`.

## Команды

| Команда | Что делает | Права |
|---|---|---|
| `/games [поиск]` | Игры с активными Drops | все |
| `/rewards` | Бейджи и награды, которые можно получить сейчас | все |
| `/badges [поиск]` | Каталог глобальных бейджей Twitch | все |
| `/badge <бейдж>` | Карточка бейджа: описание, как получить | все |
| `/list` | Подписки канала | все |
| `/help` | Справка | все |
| `/follow <игра>` | Подписать канал на Drops игры и сразу прислать текущие кампании | Управлять каналами |
| `/unfollow <id>` | Отписать по ID из `/list` | Управлять каналами |
| `/followbadges` / `/unfollowbadges` | Подписка на бейджи: новые, из Drops и reward-кампаний | Управлять каналами |
| `/checknow` | Проверить сейчас (к API — только если позволяет лимит) | Управлять каналами |

Подписка на бейджи включает новые глобальные бейджи Twitch (каталог Helix сверяется раз в
час; бейджи, существовавшие до первого запуска, не анонсируются), Drops-кампании с бейджем в
чат и reward-кампании.

Каждая кампания приходит в канал один раз, плюс напоминание за `ENDING_SOON_HOURS` до конца.
О стримах бот сообщает только если они реально дают дропы, не чаще раза в
`STREAM_NOTIFY_COOLDOWN_HOURS` на стримера.

## Разработка

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest tests/unit
```

Интеграционные тесты с настоящим Postgres:

```bash
docker compose -f docker-compose.test.yml up -d --wait
TEST_DATABASE_URL=postgresql://bot:bot@localhost:55433/twitch_badge_bot_test uv run pytest
```

Устройство и правила (SQL только в `sql/`, enum-ы запросов, миграции) — в `CLAUDE.md`,
поведение — в `SPEC.md`.

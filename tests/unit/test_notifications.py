from datetime import UTC, datetime

import discord

from src.db.records import Subscription
from src.notifications import (
    EMBED_TOTAL_LIMIT,
    EMBEDS_PER_MESSAGE,
    batch_embeds,
    build_drop_embed,
    build_games_list_embeds,
    build_live_embed,
    build_reward_embed,
    build_subscriptions_list_embed,
)
from tests.fakes import (
    ALLOWED_LOGIN,
    BEFORE_ENDING,
    PLAIN_GAME_ID,
    RESTRICTED_GAME_ID,
    drops_snapshot,
    rewards_snapshot,
    stream,
)


def test_drop_embed() -> None:
    game = drops_snapshot().find_game(PLAIN_GAME_ID)
    assert game is not None
    campaign = game.campaigns[0]
    embed = build_drop_embed(game, campaign)
    assert embed.title and campaign.name in embed.title
    assert embed.url and campaign.id in embed.url
    names = [f.name for f in embed.fields]
    assert "Награды" in names and "Заканчивается" in names
    assert "Только на каналах" not in names
    ending = build_drop_embed(game, campaign, ending_soon=True)
    assert ending.title and ending.title.startswith("⏰")


def test_drop_embed_lists_allowed_channels() -> None:
    game = drops_snapshot().find_game(RESTRICTED_GAME_ID)
    assert game is not None
    embed = build_drop_embed(game, game.campaigns[0])
    allowed = next(f for f in embed.fields if f.name == "Только на каналах")
    assert allowed.value and "WorldofWarships".lower() in allowed.value.lower()
    assert ALLOWED_LOGIN


def test_reward_and_live_embeds() -> None:
    reward = rewards_snapshot().campaigns[0]
    embed = build_reward_embed(reward)
    assert embed.title and reward.name in embed.title
    game = drops_snapshot().find_game(PLAIN_GAME_ID)
    assert game is not None
    live = build_live_embed(game, stream("some_streamer", PLAIN_GAME_ID))
    assert live.url == "https://www.twitch.tv/some_streamer"


def test_long_lists_are_split_within_limits() -> None:
    game = drops_snapshot().find_game(PLAIN_GAME_ID)
    assert game is not None
    games = [game.model_copy(update={"game_display_name": f"Game {i:03d}"}) for i in range(300)]
    embeds = build_games_list_embeds(games, BEFORE_ENDING)
    assert len(embeds) > 1
    assert all(len(e.description or "") <= 4096 for e in embeds)
    for batch in batch_embeds(embeds):
        assert len(batch) <= EMBEDS_PER_MESSAGE
        assert sum(len(e) for e in batch) <= EMBED_TOTAL_LIMIT


def test_batch_embeds_respects_count_limit() -> None:
    embeds = [discord.Embed(title=str(i)) for i in range(23)]
    assert [len(b) for b in batch_embeds(embeds)] == [10, 10, 3]


def test_empty_subscriptions() -> None:
    assert "Подписок нет" in (build_subscriptions_list_embed([]).description or "")
    sub = Subscription(
        id=7,
        channel_id=1,
        type="game",
        target_id="1",
        target_name="Rust",
        created_at=datetime.now(UTC),
    )
    assert "#7" in (build_subscriptions_list_embed([sub]).description or "")

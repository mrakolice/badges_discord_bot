from datetime import UTC, datetime

from src.badge_watcher import matching_offers
from src.db.records import ChatBadge
from src.drops_api import badge_offers
from src.notifications import build_badge_embed, build_drop_embed, build_offers_list_embeds
from tests.fakes import (
    BADGE_GAME_ID,
    BADGE_NAME,
    BEFORE_ENDING,
    PLAIN_GAME_ID,
    drops_snapshot,
    rewards_snapshot,
)


def catalog_badge(title: str = BADGE_NAME) -> ChatBadge:
    return ChatBadge(
        set_id="worlds-2026",
        version_id="1",
        title=title,
        description="Earned by watching Worlds",
        image_url_1x="https://x/1",
        image_url_2x="https://x/2",
        image_url_4x="https://x/3",
        click_url=None,
        first_seen_at=datetime(2026, 10, 10, tzinfo=UTC),
        last_seen_at=datetime(2026, 11, 2, tzinfo=UTC),
    )


def offers():
    return badge_offers(drops_snapshot(), rewards_snapshot(), BEFORE_ENDING)


def test_matching_ignores_case_spacing_and_badge_word() -> None:
    assert [o.name for o in matching_offers("worlds  2026 supporter", offers())] == [BADGE_NAME]
    assert matching_offers("Worlds 2026 Supporter Badge", offers())
    assert matching_offers("Worlds 2026", offers()) == []
    assert matching_offers("Badge", offers()) == []


def test_badge_drop_embed_highlights_chat_badge() -> None:
    game = drops_snapshot().find_game(BADGE_GAME_ID)
    assert game is not None
    embed = build_drop_embed(game, game.campaigns[0])
    assert embed.title and embed.title.startswith("🏅")
    field = next(f for f in embed.fields if f.name == "🏅 Бейдж в чат")
    assert field.value and BADGE_NAME in field.value
    assert not any(f.name == "Награды" for f in embed.fields)
    plain = drops_snapshot().find_game(PLAIN_GAME_ID)
    assert plain is not None
    assert (build_drop_embed(plain, plain.campaigns[0]).title or "").startswith("🎁")


def test_catalog_badge_embed_links_campaign() -> None:
    embed = build_badge_embed(catalog_badge(), matching_offers(BADGE_NAME, offers()), new=True)
    assert embed.title == f"🆕 Новый бейдж: {BADGE_NAME}"
    how = next(f for f in embed.fields if f.name == "Как получить")
    assert how.value and "League of Legends" in how.value
    assert embed.url and "dropID=" in embed.url
    unknown = build_badge_embed(catalog_badge("Mystery"), [])
    assert not any(f.name == "Как получить" for f in unknown.fields)


def test_offers_list_mentions_both_sources() -> None:
    text = "\n".join(e.description or "" for e in build_offers_list_embeds(offers()))
    assert BADGE_NAME in text and "Pokémon" in text
    assert "только у" in text

import httpx
import pytest
import respx

from src.twitch import HELIX_URL, TOKEN_URL, TwitchClient, TwitchError


def token(value: str = "t1") -> httpx.Response:
    return httpx.Response(200, json={"access_token": value, "expires_in": 3600})


def streams(*logins: str, game_id: str = "1") -> dict:
    return {
        "data": [
            {"user_login": login, "user_name": login, "game_id": game_id, "viewer_count": 5}
            for login in logins
        ]
    }


@pytest.fixture
async def twitch():
    async with httpx.AsyncClient() as http:
        yield TwitchClient(http, "cid", "secret")


@respx.mock
async def test_token_is_cached(twitch: TwitchClient) -> None:
    token_route = respx.post(TOKEN_URL).mock(return_value=token())
    respx.get(f"{HELIX_URL}/streams").respond(json=streams("a"))
    await twitch.get_streams_for_game("1", 3)
    await twitch.get_streams_for_game("1", 3)
    assert token_route.call_count == 1
    request = respx.calls.last.request
    assert request.headers["Client-Id"] == "cid"
    assert request.headers["Authorization"] == "Bearer t1"


@respx.mock
async def test_401_refreshes_token_once(twitch: TwitchClient) -> None:
    respx.post(TOKEN_URL).mock(side_effect=[token("old"), token("new")])
    respx.get(f"{HELIX_URL}/streams").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json=streams("a"))]
    )
    result = await twitch.get_streams_for_game("1", 3)
    assert [s.user_login for s in result] == ["a"]
    assert respx.calls.last.request.headers["Authorization"] == "Bearer new"


@respx.mock
async def test_errors_raise_twitch_error(twitch: TwitchClient) -> None:
    respx.post(TOKEN_URL).mock(return_value=token())
    respx.get(f"{HELIX_URL}/streams").respond(500)
    with pytest.raises(TwitchError):
        await twitch.get_streams_for_game("1", 3)


@respx.mock
async def test_streams_for_logins_are_chunked_and_deduplicated(twitch: TwitchClient) -> None:
    respx.post(TOKEN_URL).mock(return_value=token())
    route = respx.get(f"{HELIX_URL}/streams").respond(json=streams())
    logins = [f"user{i}" for i in range(150)] + ["USER1"]
    await twitch.get_streams_for_logins(logins)
    assert route.call_count == 2
    first = route.calls[0].request.url.params.get_list("user_login")
    assert len(first) == 100 and len(route.calls[1].request.url.params.get_list("user_login")) == 50


@respx.mock
async def test_get_game(twitch: TwitchClient) -> None:
    respx.post(TOKEN_URL).mock(return_value=token())
    respx.get(f"{HELIX_URL}/games").respond(json={"data": [{"id": "33214", "name": "Fortnite"}]})
    game = await twitch.get_game("fortnite")
    assert game is not None and game.id == "33214"


async def test_zero_streams_requested_makes_no_call(twitch: TwitchClient) -> None:
    assert await twitch.get_streams_for_game("1", 0) == []

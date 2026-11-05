import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from src.db.records import BadgeData

log = logging.getLogger(__name__)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX_URL = "https://api.twitch.tv/helix"
MAX_LOGINS_PER_REQUEST = 100
# Refresh a bit before expiry so a request never goes out with a dying token.
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)


class TwitchError(RuntimeError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class TwitchGame(_Model):
    id: str
    name: str
    box_art_url: str | None = None


class TwitchStream(_Model):
    user_login: str
    user_name: str
    game_id: str
    game_name: str = ""
    title: str = ""
    viewer_count: int = 0
    started_at: datetime | None = None

    @property
    def url(self) -> str:
        return f"https://www.twitch.tv/{self.user_login}"


class TwitchClient:
    """Read-only Helix client with an app access token (Client Credentials flow)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        *,
        token_url: str = TOKEN_URL,
        helix_url: str = HELIX_URL,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._helix_url = helix_url.rstrip("/")
        self._token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=UTC)
        self._lock = asyncio.Lock()

    async def get_game(self, name: str) -> TwitchGame | None:
        data = await self._get("/games", [("name", name)])
        return TwitchGame.model_validate(data[0]) if data else None

    async def get_global_badges(self) -> list[BadgeData]:
        """Every global chat badge version (Helix GET /chat/badges/global)."""
        badges: list[BadgeData] = []
        for badge_set in await self._get("/chat/badges/global", []):
            for version in badge_set.get("versions", []):
                try:
                    badges.append(
                        BadgeData(
                            set_id=badge_set["set_id"],
                            version_id=version["id"],
                            title=version.get("title") or badge_set["set_id"],
                            description=version.get("description") or "",
                            image_url_1x=version["image_url_1x"],
                            image_url_2x=version["image_url_2x"],
                            image_url_4x=version["image_url_4x"],
                            click_url=version.get("click_url") or None,
                        )
                    )
                except (KeyError, ValidationError) as exc:
                    log.warning("skipping malformed badge %s: %s", badge_set.get("set_id"), exc)
        return badges

    async def get_streams_for_game(self, game_id: str, first: int) -> list[TwitchStream]:
        """Live streams of the game, most viewers first (Helix default order)."""
        if first <= 0:
            return []
        params = [("game_id", game_id), ("type", "live"), ("first", str(min(first, 100)))]
        return [TwitchStream.model_validate(s) for s in await self._get("/streams", params)]

    async def get_streams_for_logins(self, logins: Sequence[str]) -> list[TwitchStream]:
        streams: list[TwitchStream] = []
        unique = list(dict.fromkeys(login.lower() for login in logins))
        for start in range(0, len(unique), MAX_LOGINS_PER_REQUEST):
            chunk = unique[start : start + MAX_LOGINS_PER_REQUEST]
            params = [("user_login", login) for login in chunk]
            params += [("type", "live"), ("first", str(len(chunk)))]
            streams += [TwitchStream.model_validate(s) for s in await self._get("/streams", params)]
        return streams

    async def _get(self, path: str, params: list[tuple[str, str]]) -> list[dict]:
        for attempt in range(2):
            token = await self._ensure_token(force=attempt > 0)
            response = await self._http.get(
                f"{self._helix_url}{path}",
                params=params,
                headers={"Client-Id": self._client_id, "Authorization": f"Bearer {token}"},
                timeout=20,
            )
            if response.status_code == 401 and attempt == 0:
                log.info("Twitch token rejected, refreshing")
                continue
            if response.status_code != 200:
                raise TwitchError(f"Helix {path} returned HTTP {response.status_code}")
            return response.json().get("data", [])
        raise TwitchError(f"Helix {path} rejected a fresh token")

    async def _ensure_token(self, *, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token and datetime.now(UTC) < self._expires_at:
                return self._token
            response = await self._http.post(
                self._token_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=20,
            )
            if response.status_code != 200:
                raise TwitchError(f"Twitch token request returned HTTP {response.status_code}")
            body = response.json()
            self._token = body["access_token"]
            lifetime = timedelta(seconds=int(body.get("expires_in", 3600)))
            self._expires_at = datetime.now(UTC) + lifetime - TOKEN_REFRESH_MARGIN
            return self._token

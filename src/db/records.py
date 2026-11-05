from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SubscriptionType = Literal["game", "reward"]
CampaignType = Literal["drop", "reward"]
DeliveryKind = Literal["new", "ending"]
ALL_REWARDS = "ALL_REWARDS"


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class Channel(_Record):
    id: int
    guild_id: int
    name: str
    created_at: datetime


class Subscription(_Record):
    id: int
    channel_id: int
    type: SubscriptionType
    target_id: str
    target_name: str
    created_at: datetime


class CampaignSeen(_Record):
    campaign_id: str
    campaign_type: CampaignType
    first_seen_at: datetime
    last_seen_at: datetime
    data_json: dict[str, Any]


class BadgeData(_Record):
    """One version of a global chat badge as Helix reports it."""

    set_id: str
    version_id: str
    title: str
    description: str = ""
    image_url_1x: str
    image_url_2x: str
    image_url_4x: str
    click_url: str | None = None


class ChatBadge(_Record):
    set_id: str
    version_id: str
    title: str
    description: str
    image_url_1x: str
    image_url_2x: str
    image_url_4x: str
    click_url: str | None
    first_seen_at: datetime
    last_seen_at: datetime

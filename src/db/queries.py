from enum import StrEnum


class Query(StrEnum):
    """Base for template names; a member's value is its path under `sql/` without suffix."""


class ChannelRegistry(Query):
    UPSERT = "channel_registry/upsert"
    GET = "channel_registry/get"
    DELETE = "channel_registry/delete"
    DELETE_GUILD = "channel_registry/delete_guild"


class Follows(Query):
    ADD = "follows/add"
    REMOVE = "follows/remove"
    LIST_FOR_CHANNEL = "follows/list_for_channel"
    LIST_ALL = "follows/list_all"
    CHANNELS_FOR_TARGET = "follows/channels_for_target"


class CampaignTracking(Query):
    UPSERT_SEEN = "campaign_tracking/upsert_seen"
    GET = "campaign_tracking/get"
    PRUNE_UNSEEN = "campaign_tracking/prune_unseen"


class Announcements(Query):
    CLAIM = "announcements/claim"
    RELEASE = "announcements/release"


class LiveAlerts(Query):
    CLAIM = "live_alerts/claim"
    RELEASE = "live_alerts/release"


class BotState(Query):
    GET = "bot_state/get"
    SET = "bot_state/set"


class SchemaVersioning(Query):
    ACQUIRE_LOCK = "schema_versioning/acquire_lock"
    ENSURE_TABLE = "schema_versioning/ensure_table"
    LIST_APPLIED = "schema_versioning/list_applied"
    RECORD_APPLIED = "schema_versioning/record_applied"


ALL_QUERY_GROUPS: tuple[type[Query], ...] = (
    ChannelRegistry,
    Follows,
    CampaignTracking,
    Announcements,
    LiveAlerts,
    BotState,
    SchemaVersioning,
)

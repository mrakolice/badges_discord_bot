from enum import StrEnum


class Query(StrEnum):
    """Base for template names; a member's value is its path under `sql/` without suffix."""


class ChannelRegistry(Query):
    UPSERT = "channel_registry/upsert"
    GET = "channel_registry/get"


class Follows(Query):
    ADD = "follows/add"
    REMOVE = "follows/remove"
    LIST_FOR_CHANNEL = "follows/list_for_channel"
    LIST_ALL = "follows/list_all"
    CHANNELS_FOR_TARGET = "follows/channels_for_target"


class CampaignTracking(Query):
    UPSERT_SEEN = "campaign_tracking/upsert_seen"
    GET = "campaign_tracking/get"
    MARK_NOTIFIED = "campaign_tracking/mark_notified"


class LiveAlerts(Query):
    WAS_NOTIFIED_SINCE = "live_alerts/was_notified_since"
    RECORD = "live_alerts/record"


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
    LiveAlerts,
    BotState,
    SchemaVersioning,
)

from src.db.queries import Query


class TestSql(Query):
    """Test-only templates from tests/sql."""

    __test__ = False

    RESET_SCHEMA = "reset_schema"
    TRUNCATE_ALL = "truncate_all"
    LIST_TABLES = "list_tables"
    AGE_CAMPAIGN = "age_campaign"
    AGE_STREAM_NOTIFICATIONS = "age_stream_notifications"

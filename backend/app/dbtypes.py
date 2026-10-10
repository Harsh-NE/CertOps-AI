from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator

from app.timeutil import ensure_utc


class UtcDateTime(TypeDecorator):
    """timestamptz on Postgres; on SQLite (which has no tz support) values are stored as UTC and returned aware."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return ensure_utc(value)

    def process_result_value(self, value, dialect):
        return ensure_utc(value)

"""All datetimes in the DB are naive UTC. Use these helpers instead of datetime.utcnow()."""
from datetime import UTC, datetime
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def days_until(dt: Optional[datetime]) -> Optional[int]:
    """Whole days from now until dt (negative if in the past); None if dt is None."""
    if dt is None:
        return None
    return (to_naive_utc(dt) - utcnow()).days

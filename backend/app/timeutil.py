"""All datetimes in code are timezone-aware UTC. Use these helpers, never naive datetime.utcnow()."""
from datetime import UTC, datetime
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Aware UTC from any datetime. Naive values are assumed to already be UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def days_until(dt: Optional[datetime]) -> Optional[int]:
    """Whole days from now until dt (negative if in the past); None if dt is None."""
    if dt is None:
        return None
    return (ensure_utc(dt) - utcnow()).days

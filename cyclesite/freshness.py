"""Data-age check: say "my data is too old" instead of answering confidently."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Freshness:
    level: str  # "fresh" | "stale" | "expired" | "unknown"
    age_days: int | None
    newest_data: str | None
    message: str
    requires_acknowledgement: bool

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "age_days": self.age_days,
            "newest_data": self.newest_data,
            "message": self.message,
            "requires_acknowledgement": self.requires_acknowledgement,
        }


def assess_freshness(
    newest: datetime | None,
    now: datetime | None = None,
    warn_days: int = 365,
    expired_days: int = 3 * 365,
) -> Freshness:
    if newest is None:
        return Freshness(
            "unknown",
            None,
            None,
            "No timestamp is recorded for this data; its age is unknown.",
            True,
        )
    now = now or datetime.now(UTC)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    age = (now - newest).days
    stamp = newest.date().isoformat()
    if age < 0:
        return Freshness(
            "unknown", age, stamp, "Newest record is in the future; check the data.", True
        )
    if age <= warn_days:
        return Freshness("fresh", age, stamp, f"Newest data is {age} days old.", False)
    years = age / 365.25
    if age <= expired_days:
        return Freshness(
            "stale",
            age,
            stamp,
            f"Newest data is {years:.1f} years old; patterns may have shifted.",
            False,
        )
    return Freshness(
        "expired",
        age,
        stamp,
        f"Newest data is {years:.1f} years old ({stamp}). The city has changed since; results "
        "only illustrate the method and must not be used for a real lease decision.",
        True,
    )

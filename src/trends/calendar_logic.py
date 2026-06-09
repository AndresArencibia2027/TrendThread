"""Production calendar logic.

Given a saved event/opportunity, compute the key working-back milestones the
shop needs to hit so products are live before demand peaks.

Two profiles:

Major / predictable events (anchored on a known ``event_date``):
    - research   : 12 weeks before the event
    - design     :  8 weeks before
    - listing    :  6 weeks before
    - marketing  :  4 weeks before
    - last useful upload : 1 week before (after this, listings rarely rank in time)

Viral trends (anchored on "today", because they have no fixed date):
    - research   : today
    - design     : within 1 day
    - listing    : within 1-2 days
    - last useful upload / monitor window : 7-14 days out
"""

from datetime import date, datetime, timedelta
from typing import Any

WEEK = timedelta(weeks=1)

# Weeks-before-event offsets for major predictable events.
MAJOR_EVENT_OFFSETS_WEEKS = {
    "research_start_date": 12,
    "design_start_date": 8,
    "listing_publish_date": 6,
    "marketing_push_date": 4,
    "last_useful_upload_date": 1,
}

# Day offsets from "today" for fast-moving viral trends.
VIRAL_OFFSETS_DAYS = {
    "research_start_date": 0,
    "design_start_date": 1,
    "listing_publish_date": 2,
    "marketing_push_date": 3,
    # Viral demand decays fast; treat ~14 days as the last useful upload.
    "last_useful_upload_date": 14,
}

MILESTONE_TO_TASK = {
    "research_start_date": "research",
    "design_start_date": "design",
    "listing_publish_date": "listing",
    "marketing_push_date": "marketing",
    "last_useful_upload_date": "last_upload",
}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # ISO datetime fallback (e.g. "2026-12-25T00:00:00")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def is_major_event(event: dict[str, Any]) -> bool:
    """Heuristic: a major event has a known date and is not a viral/evergreen trend."""
    if event.get("is_major_event") is not None:
        # Respect an explicit flag if the model provided one.
        flag = event.get("is_major_event")
        if isinstance(flag, str):
            return flag.strip().lower() in {"1", "true", "yes"}
        return bool(flag)
    trend_type = (event.get("trend_type") or "").strip().lower()
    has_date = _parse_date(event.get("event_date")) is not None
    return trend_type == "event" and has_date


def compute_calendar(event: dict[str, Any], today: date | None = None) -> dict[str, str | None]:
    """Return the five milestone dates as ISO strings (or None when unknown)."""
    today = today or date.today()
    trend_type = (event.get("trend_type") or "").strip().lower()

    # Viral / fast-moving: anchor on today.
    if trend_type == "viral" or (not is_major_event(event) and trend_type != "evergreen"):
        if trend_type == "viral":
            return {
                key: _iso(today + timedelta(days=days))
                for key, days in VIRAL_OFFSETS_DAYS.items()
            }

    event_date = _parse_date(event.get("event_date"))

    # Major predictable event with a real date: work backwards.
    if event_date and is_major_event(event):
        return {
            key: _iso(event_date - weeks * WEEK)
            for key, weeks in MAJOR_EVENT_OFFSETS_WEEKS.items()
        }

    # Evergreen or undated: research can start now, no hard deadline.
    if trend_type == "evergreen":
        return {
            "research_start_date": _iso(today),
            "design_start_date": _iso(today + WEEK),
            "listing_publish_date": _iso(today + 2 * WEEK),
            "marketing_push_date": _iso(today + 3 * WEEK),
            "last_useful_upload_date": None,
        }

    # Fallback for anything dated but not flagged major: treat date as deadline
    # with the viral-day cushion if it is very near, else major offsets.
    if event_date:
        return {
            key: _iso(event_date - weeks * WEEK)
            for key, weeks in MAJOR_EVENT_OFFSETS_WEEKS.items()
        }

    # Truly undated, non-evergreen -> behave like a viral trend.
    return {
        key: _iso(today + timedelta(days=days))
        for key, days in VIRAL_OFFSETS_DAYS.items()
    }


def build_production_tasks(calendar: dict[str, str | None]) -> list[dict[str, Any]]:
    """Turn computed milestone dates into production_task rows."""
    tasks: list[dict[str, Any]] = []
    for milestone, task_type in MILESTONE_TO_TASK.items():
        due = calendar.get(milestone)
        if due:
            tasks.append({"task_type": task_type, "due_date": due, "status": "pending"})
    return tasks

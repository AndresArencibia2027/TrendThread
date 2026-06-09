"""Trend scan orchestration.

Coordinates the Gemini engine, calendar logic and database. This is the single
entrypoint the API routes / cron / CLI call. It:

    1. records a trend_scan_run,
    2. asks Gemini for structured opportunities,
    3. persists events + design ideas + SEO keywords + production tasks +
       legal risk checks,
    4. computes the production calendar for each saved event,
    5. enforces the "High / Very High risk must be manually reviewed and never
       auto-approved" rule.
"""

import json
from typing import Any

from . import calendar_logic, config, database, gemini_engine

# Risk levels that block auto-approval.
_BLOCKING_RISK = {"high", "very high"}

# Which sections of the Gemini output become saved ``events``.
_EVENT_SECTIONS = (
    "merch_opportunities",
    "upcoming_events",
    "viral_trends_to_monitor",
    "evergreen_ideas",
)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [s.strip() for s in str(value).split(",") if s.strip()]


def _coerce_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _requires_review(legal_risk: str | None) -> bool:
    return (legal_risk or "").strip().lower() in _BLOCKING_RISK


def _opportunity_to_event_row(opp: dict[str, Any]) -> dict[str, Any]:
    legal_risk = opp.get("legal_risk")
    requires_review = _requires_review(legal_risk)
    return {
        "name": opp.get("trend_event_name") or opp.get("name") or "Untitled",
        "category": opp.get("category"),
        "trend_type": (opp.get("trend_type") or "").strip().lower() or None,
        "event_date": opp.get("event_date") if opp.get("event_date") not in ("", "null") else None,
        "target_buyer": opp.get("target_buyer"),
        "safe_design_angle": opp.get("safe_design_angle"),
        "legal_risk": legal_risk,
        "saturation_risk": opp.get("saturation_risk"),
        "trend_score": _coerce_int(opp.get("trend_score")),
        "recommended_action": opp.get("recommended_action"),
        "is_major_event": 1 if calendar_logic.is_major_event(opp) else 0,
        "requires_manual_review": 1 if requires_review else 0,
        # Never auto-approve risky items. Safe items stay unapproved until a human
        # acts; we only guarantee risky ones are explicitly held.
        "approved": 0,
    }


def _persist_opportunity(run_id: int, opp: dict[str, Any]) -> int:
    event_row = _opportunity_to_event_row(opp)

    # Production calendar (work-back dates) derived from event type + date.
    calendar = calendar_logic.compute_calendar(opp)
    event_row.update(calendar)

    event_id = database.insert_event(run_id, event_row)

    database.insert_design_ideas(run_id, event_id, _as_list(opp.get("design_concepts")))
    database.insert_seo_keywords(run_id, event_id, _as_list(opp.get("etsy_seo_keywords")))
    database.insert_production_tasks(event_id, calendar_logic.build_production_tasks(calendar))

    database.insert_legal_risk_check(
        run_id,
        event_id,
        {
            "risk_level": opp.get("legal_risk"),
            "unsafe_terms": _as_list(opp.get("unsafe_terms_to_avoid")),
            "safe_alternative": opp.get("safe_design_angle") or opp.get("safe_alternative"),
            "requires_manual_review": _requires_review(opp.get("legal_risk")),
            "notes": opp.get("why_risky"),
        },
    )
    return event_id


def run_and_store(scan_type: str, extra_context: str | None = None) -> dict[str, Any]:
    """Run a Gemini scan and persist everything. Returns a result summary dict."""
    if not config.is_gemini_engine():
        raise RuntimeError(
            f"TREND_ENGINE_PROVIDER is '{config.TREND_ENGINE_PROVIDER}', not 'gemini'. "
            "Set TREND_ENGINE_PROVIDER=gemini to use the trend engine."
        )

    database.init_db()
    run_id = database.create_run(scan_type, provider="gemini", model=config.GEMINI_TREND_MODEL)

    try:
        result = gemini_engine.run_scan(scan_type, extra_context=extra_context)
    except Exception as exc:  # noqa: BLE001 - we want to record any failure
        database.finish_run(run_id, status="error", error=str(exc))
        raise

    saved_event_ids: list[int] = []
    seen_names: set[str] = set()
    for section in _EVENT_SECTIONS:
        for opp in result.get(section, []) or []:
            if not isinstance(opp, dict):
                continue
            name_key = (opp.get("trend_event_name") or opp.get("name") or "").strip().lower()
            if name_key and name_key in seen_names:
                continue
            seen_names.add(name_key)
            saved_event_ids.append(_persist_opportunity(run_id, opp))

    # High-risk "avoid" items are recorded as legal checks with no sellable event,
    # so the operator still sees the safe transformation.
    action_plan = result.get("immediate_action_plan", [])
    summary = json.dumps(action_plan) if action_plan else None
    database.finish_run(
        run_id,
        status="success",
        summary=summary,
        raw_response=result.get("_raw"),
    )

    return {
        "run_id": run_id,
        "scan_type": scan_type,
        "model": config.GEMINI_TREND_MODEL,
        "saved_events": len(saved_event_ids),
        "merch_opportunities": result.get("merch_opportunities", []),
        "upcoming_events": result.get("upcoming_events", []),
        "viral_trends_to_monitor": result.get("viral_trends_to_monitor", []),
        "evergreen_ideas": result.get("evergreen_ideas", []),
        "high_risk_trends_to_avoid": result.get("high_risk_trends_to_avoid", []),
        "immediate_action_plan": action_plan,
    }


def get_saved_events(limit: int = 100, upcoming_only: bool = False) -> list[dict[str, Any]]:
    database.init_db()
    return database.list_events(limit=limit, upcoming_only=upcoming_only)


# --- Listing generation + publishing ---------------------------------------

def generate_listing_for_event(event_id: int, with_image: bool = True) -> dict[str, Any]:
    """Generate an IP-safe listing (copy + image) for a saved event and store it.

    High / Very High legal-risk events are still generated (so the operator can
    see the safe angle), but the listing is marked ``blocked`` so it cannot be
    auto-published until a human approves it.
    """
    from . import listing_generator

    database.init_db()
    event = database.get_event(event_id)
    if not event:
        raise ValueError(f"Event {event_id} not found")

    draft = listing_generator.generate_listing(event, with_image=with_image)

    requires_review = bool(event.get("requires_manual_review"))
    draft["status"] = "blocked" if requires_review else "generated"
    if requires_review:
        draft["blocked_reason"] = (
            f"Legal risk '{event.get('legal_risk')}' requires manual review before publishing."
        )

    listing_id = database.create_listing(draft)
    saved = database.get_listing(listing_id)
    saved["requires_manual_review"] = requires_review
    return saved


def publish_listing(listing_id: int, force: bool = False) -> dict[str, Any]:
    """Publish a generated listing to Printify (as a draft by default).

    Refuses to publish listings flagged ``blocked`` (High/Very High legal risk)
    unless ``force=True`` is passed (an explicit manual approval).
    """
    database.init_db()
    listing = database.get_listing(listing_id)
    if not listing:
        raise ValueError(f"Listing {listing_id} not found")

    if listing.get("status") == "blocked" and not force:
        raise PermissionError(
            "This listing is blocked for manual review (High/Very High legal risk) "
            "and cannot be auto-published. Approve it explicitly to override."
        )
    if not listing.get("image_path"):
        raise ValueError("Listing has no generated image to publish.")

    from printify_upload_tshirt import publish_image_as_product

    try:
        result = publish_image_as_product(
            image_path=listing["image_path"],
            title=listing.get("title") or "Original Graphic Tee",
            description=listing.get("description"),
            tags=listing.get("tags") or [],
        )
    except Exception as exc:  # noqa: BLE001
        database.mark_listing_error(listing_id, str(exc))
        raise

    database.mark_listing_published(
        listing_id,
        target="printify",
        external_id=result.get("product_id"),
        external_url=result.get("url"),
    )
    return {"listing_id": listing_id, **result}


def get_listings(limit: int = 100) -> list[dict[str, Any]]:
    database.init_db()
    return database.list_listings(limit=limit)

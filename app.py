"""TrendThread web layer (server-side only).

Serves the operator dashboard and the trend API. The dashboard "Run Trend Scan"
button calls the backend API (POST /api/trends/scan); the browser never talks to
Gemini directly. Daily/weekly cron routes require the CRON_SECRET.

Run locally:
    pip install -r requirements.txt
    python -m src.trends.database          # one-time DB init (also auto-runs)
    flask --app app run                    # or: python app.py

Routes:
    GET  /                      dashboard UI
    POST /api/trends/scan       manual scan (used by the dashboard button)
    POST /api/trends/daily      cron: current/viral trends   (requires CRON_SECRET)
    POST /api/trends/weekly     cron: event calendar planning (requires CRON_SECRET)
    GET  /api/trends/events     saved events (calendar-ordered)
"""

import functools
import os

from flask import Flask, abort, jsonify, render_template, request, send_file

from src.trends import config, database, service

app = Flask(__name__)


def _cron_authorized(req) -> bool:
    """Validate CRON_SECRET from Authorization: Bearer <secret> or ?secret=."""
    secret = config.get_cron_secret()
    if not secret:
        # No secret configured -> cron routes are locked down by default.
        return False
    auth_header = req.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        if auth_header[len("Bearer "):].strip() == secret:
            return True
    if req.headers.get("X-Cron-Secret") == secret:
        return True
    if req.args.get("secret") == secret:
        return True
    return False


def require_cron_secret(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not _cron_authorized(request):
            return jsonify({"error": "Unauthorized: valid CRON_SECRET required"}), 401
        return view(*args, **kwargs)

    return wrapper


@app.get("/")
def dashboard():
    database.init_db()
    events = service.get_saved_events(limit=100)
    runs = database.list_runs(limit=10)
    listings = service.get_listings(limit=100)
    return render_template(
        "dashboard.html",
        events=events,
        runs=runs,
        listings=listings,
        provider=config.TREND_ENGINE_PROVIDER,
        model=config.GEMINI_TREND_MODEL,
        legacy_enabled=config.legacy_scraping_enabled(),
        printify_ready=bool(os.getenv("PRINTIFY_API_TOKEN")),
    )


@app.post("/api/trends/scan")
def scan():
    """Manual scan triggered by the dashboard button."""
    payload = request.get_json(silent=True) or {}
    scan_type = payload.get("scan_type", "manual")
    extra_context = payload.get("context")
    try:
        result = service.run_and_store(scan_type=scan_type, extra_context=extra_context)
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/trends/daily")
@require_cron_secret
def daily():
    """Cron: scan current / viral trends."""
    try:
        result = service.run_and_store(scan_type="daily")
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/trends/weekly")
@require_cron_secret
def weekly():
    """Cron: upcoming-event calendar + production planning."""
    try:
        result = service.run_and_store(scan_type="weekly")
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/trends/events")
def events():
    upcoming_only = request.args.get("upcoming", "").lower() in {"1", "true", "yes"}
    limit = request.args.get("limit", default=100, type=int)
    data = service.get_saved_events(limit=limit, upcoming_only=upcoming_only)
    return jsonify({"ok": True, "count": len(data), "events": data})


# --- Listings: generate, publish, list, image -------------------------------

@app.post("/api/listings/generate")
def generate_listing():
    """Generate an IP-safe listing (copy + image) for a saved event."""
    payload = request.get_json(silent=True) or {}
    event_id = payload.get("event_id")
    if not event_id:
        return jsonify({"ok": False, "error": "event_id is required"}), 400
    with_image = payload.get("with_image", True)
    try:
        listing = service.generate_listing_for_event(int(event_id), with_image=with_image)
        return jsonify({"ok": True, "listing": listing})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/listings/<int:listing_id>/publish")
def publish_listing(listing_id: int):
    """Publish a generated listing to the Printify store."""
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force", False))
    try:
        result = service.publish_listing(listing_id, force=force)
        return jsonify({"ok": True, **result})
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc), "blocked": True}), 409
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/listings")
def listings():
    data = service.get_listings(limit=request.args.get("limit", default=100, type=int))
    return jsonify({"ok": True, "count": len(data), "listings": data})


@app.get("/api/listings/<int:listing_id>/image")
def listing_image(listing_id: int):
    """Serve a generated listing image for preview in the dashboard."""
    listing = database.get_listing(listing_id)
    if not listing or not listing.get("image_path"):
        abort(404)
    path = os.path.abspath(listing["image_path"])
    # Confine served files to the project tree.
    if not path.startswith(os.path.abspath(os.getcwd())) or not os.path.isfile(path):
        abort(404)
    return send_file(path)


if __name__ == "__main__":
    database.init_db()
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)

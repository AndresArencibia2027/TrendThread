#!/usr/bin/env python3
"""CLI runner for trend scans — used by system cron (or run manually).

This calls the trend service directly (no HTTP), so it does not need the web
server running. Use it from crontab for the daily/weekly schedules.

Usage:
    python scripts/run_scan.py daily
    python scripts/run_scan.py weekly
    python scripts/run_scan.py manual --context "summer pet vibes"

Example crontab (run `crontab -e`):
    # Daily current/viral scan at 08:00
    0 8 * * *  cd /path/to/TrendThread && /path/to/venv/bin/python scripts/run_scan.py daily >> data/cron.log 2>&1
    # Weekly event-calendar planning, Mondays at 07:00
    0 7 * * 1  cd /path/to/TrendThread && /path/to/venv/bin/python scripts/run_scan.py weekly >> data/cron.log 2>&1
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.trends import service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Gemini trend scan.")
    parser.add_argument("scan_type", choices=["manual", "daily", "weekly"])
    parser.add_argument("--context", default=None, help="Optional focus/niche steering.")
    parser.add_argument("--json", action="store_true", help="Print full JSON result.")
    args = parser.parse_args()

    result = service.run_and_store(scan_type=args.scan_type, extra_context=args.context)

    if args.json:
        # Drop large raw text for readability.
        print(json.dumps({k: v for k, v in result.items()}, indent=2, default=str))
    else:
        print(
            f"[{args.scan_type}] run #{result['run_id']} saved "
            f"{result['saved_events']} events (model {result['model']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

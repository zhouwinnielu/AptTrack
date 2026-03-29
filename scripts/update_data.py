from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history.json"
TARGET_URL = "https://www.avaloncommunities.com/new-jersey/princeton-apartments/avalon-princeton-circle/"
TIMEZONE = ZoneInfo("America/New_York")
TARGET_HOURS = {9, 17}


@dataclass
class UpdateDecision:
    should_run: bool
    reason: str


def fetch_live_units() -> dict:
    request = urllib.request.Request(
        TARGET_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8")
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach Avalon website: {error}") from error

    match = re.search(
        r"Fusion\.globalContent=(\{.*?\});Fusion\.globalContentConfig=",
        html,
        flags=re.DOTALL,
    )
    if not match:
        raise RuntimeError("Could not locate embedded unit data in the Avalon page.")

    global_content = json.loads(match.group(1))
    units = global_content.get("units", [])
    if not isinstance(units, list):
        raise RuntimeError("Unexpected Avalon unit data format.")

    normalized_units = []
    for unit in units:
        if unit.get("bedroomNumber") != 1 or unit.get("bathroomNumber") != 1:
            continue

        pricing = unit.get("startingAtPricesUnfurnished") or {}
        price_details = pricing.get("prices") or {}
        move_in_date = pricing.get("moveInDate") or ""
        available_date = unit.get("availableDateUnfurnished") or ""
        start_date = move_in_date or available_date

        normalized_units.append(
            {
                "unit_id": unit.get("unitId"),
                "unit_name": unit.get("unitName"),
                "address_line1": (unit.get("address") or {}).get("addressLine1"),
                "floor_plan": (unit.get("floorPlan") or {}).get("name"),
                "collection": (unit.get("finishPackage") or {}).get("name"),
                "square_feet": unit.get("squareFeet"),
                "price": price_details.get("price") or price_details.get("totalPrice") or 0,
                "lease_term_months": pricing.get("leaseTerm"),
                "start_date": start_date[:10] if start_date else "",
                "move_in_date": move_in_date[:10] if move_in_date else "",
                "available_date": available_date[:10] if available_date else "",
                "unit_status": unit.get("unitStatus"),
                "unit_url": unit.get("url"),
            }
        )

    normalized_units.sort(key=lambda item: (item["start_date"], item["price"], item["unit_name"]))

    now = datetime.now(timezone.utc)
    return {
        "generated_at": now.isoformat(),
        "generated_at_local": now.astimezone(TIMEZONE).isoformat(),
        "source_url": TARGET_URL,
        "schedule_timezone": "America/New_York",
        "scheduled_hours_local": ["09:00", "17:00"],
        "units": normalized_units,
    }


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def should_run_scheduled(existing_latest: dict) -> UpdateDecision:
    now_local = datetime.now(TIMEZONE)
    if now_local.hour not in TARGET_HOURS:
        return UpdateDecision(False, f"Skipping at local hour {now_local.hour:02d}:00 ET")

    generated_at = existing_latest.get("generated_at")
    if generated_at:
        previous_local = datetime.fromisoformat(generated_at).astimezone(TIMEZONE)
        if previous_local.date() == now_local.date() and previous_local.hour == now_local.hour:
            return UpdateDecision(False, "A scrape already exists for this local time slot")

    return UpdateDecision(True, f"Running scheduled scrape for {now_local.strftime('%Y-%m-%d %H:%M %Z')}")


def build_history(existing_history: dict, latest_payload: dict) -> dict:
    history_items = existing_history.get("history", [])
    history_by_unit = {item["unit_id"]: item for item in history_items}

    for unit in latest_payload["units"]:
        item = history_by_unit.setdefault(
            unit["unit_id"],
            {
                "unit_id": unit["unit_id"],
                "unit_name": unit["unit_name"],
                "floor_plan": unit["floor_plan"],
                "collection": unit["collection"],
                "unit_url": unit["unit_url"],
                "observations": [],
            },
        )
        item["unit_name"] = unit["unit_name"]
        item["floor_plan"] = unit["floor_plan"]
        item["collection"] = unit["collection"]
        item["unit_url"] = unit["unit_url"]
        item["observations"].append(
            {
                "generated_at": latest_payload["generated_at"],
                "price": unit["price"],
                "start_date": unit["start_date"],
                "lease_term_months": unit["lease_term_months"],
                "unit_status": unit["unit_status"],
            }
        )

    for item in history_by_unit.values():
        observations = item["observations"]
        observations.sort(key=lambda row: row["generated_at"])
        latest = observations[-1]
        previous = observations[-2] if len(observations) > 1 else None
        prices = [row["price"] for row in observations if row["price"] is not None]
        item["observation_count"] = len(observations)
        item["first_seen"] = observations[0]["generated_at"]
        item["last_seen"] = latest["generated_at"]
        item["latest_price"] = latest["price"]
        item["previous_price"] = previous["price"] if previous else None
        item["price_change"] = latest["price"] - previous["price"] if previous else None
        item["min_price"] = min(prices) if prices else None
        item["max_price"] = max(prices) if prices else None

    return {
        "generated_at": latest_payload["generated_at"],
        "source_url": latest_payload["source_url"],
        "schedule_timezone": "America/New_York",
        "scheduled_hours_local": ["09:00", "17:00"],
        "history": sorted(
            history_by_unit.values(),
            key=lambda item: (
                item["latest_price"] if item["latest_price"] is not None else 10**12,
                item["unit_name"],
            ),
        ),
    }


def build_latest(latest_payload: dict, history_payload: dict) -> dict:
    history_by_unit = {item["unit_id"]: item for item in history_payload["history"]}
    enriched_units = []
    for unit in latest_payload["units"]:
        history_item = history_by_unit.get(unit["unit_id"], {})
        enriched_units.append(
            {
                **unit,
                "observation_count": history_item.get("observation_count", 1),
                "first_seen": history_item.get("first_seen", latest_payload["generated_at"]),
                "last_seen": history_item.get("last_seen", latest_payload["generated_at"]),
                "previous_price": history_item.get("previous_price"),
                "price_change": history_item.get("price_change"),
                "min_price": history_item.get("min_price", unit["price"]),
                "max_price": history_item.get("max_price", unit["price"]),
            }
        )

    return {**latest_payload, "units": enriched_units}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing_latest = load_json(LATEST_PATH, default={})
    existing_history = load_json(HISTORY_PATH, default={"history": []})

    if args.scheduled:
        decision = should_run_scheduled(existing_latest)
        print(decision.reason)
        if not decision.should_run:
            return 0

    latest_payload = fetch_live_units()
    history_payload = build_history(existing_history, latest_payload)
    latest_with_history = build_latest(latest_payload, history_payload)

    write_json(HISTORY_PATH, history_payload)
    write_json(LATEST_PATH, latest_with_history)

    print(json.dumps({"generated_at": latest_with_history["generated_at"], "units": len(latest_with_history["units"]), "latest_path": str(LATEST_PATH.relative_to(REPO_ROOT)), "history_path": str(HISTORY_PATH.relative_to(REPO_ROOT))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

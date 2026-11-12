#!/usr/bin/env python3
"""CLI home-gateway simulator: POST /events like the real gateway would.

Examples:
  python simulator/send_event.py glass
  python simulator/send_event.py custom "Window pane exploding into shards" --db 95 --device D-210
  python simulator/send_event.py glass --event-id E-1001   # run twice to see dedupe
  python simulator/send_event.py coughs --count 10
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone

import httpx

SCENARIOS = {
    "glass": ("D-210", 92, "Sharp crash of shattering glass followed by two seconds of silence"),
    "fall": ("D-212", 88, "Heavy thud then a groan as if someone fell in the bathroom"),
    "scream": ("D-212", 90, "Someone screaming loudly in the bathroom"),
    "cough": ("D-211", 60, "A dry cough repeated three times"),
    "door": ("D-210", 65, "Front door latch clicking open"),
    "ambiguous": ("D-210", 55, "someone is dragging furniture and laughing"),
}


def send(api: str, token: str, house: str, device: str, db: float, text: str, event_id: str | None) -> dict:
    body = {
        "eventId": event_id or f"E-{uuid.uuid4().hex[:8]}",
        "houseId": house,
        "deviceId": device,
        "ts": datetime.now(timezone.utc).isoformat(),
        "description": text,
        "levelDb": db,
        "durationSec": 3,
    }
    r = httpx.post(f"{api}/events", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    out = r.json()
    if r.status_code >= 400:
        print(f"HTTP {r.status_code}: {out}", file=sys.stderr)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", choices=[*SCENARIOS, "custom", "coughs"])
    p.add_argument("text", nargs="?", help="sound description (custom only)")
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--token", default="staff-token")
    p.add_argument("--house", default="H-1042")
    p.add_argument("--device", default=None)
    p.add_argument("--db", type=float, default=None)
    p.add_argument("--event-id", default=None)
    p.add_argument("--count", type=int, default=10)
    a = p.parse_args()

    if a.scenario == "custom":
        if not a.text:
            p.error("custom needs a description")
        device, db, text = a.device or "D-210", a.db or 70, a.text
    elif a.scenario == "coughs":
        for _ in range(a.count):
            out = send(a.api, a.token, a.house, a.device or "D-211", a.db or 62, SCENARIOS["cough"][2], None)
        print(json.dumps(out, indent=2))
        return 0
    else:
        device, db, text = SCENARIOS[a.scenario]
        device, db = a.device or device, a.db or db
    print(json.dumps(send(a.api, a.token, a.house, device, db, text, a.event_id), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

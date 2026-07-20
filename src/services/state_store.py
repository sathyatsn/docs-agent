import json
import os

STATE_FILE = "config/processed_meetings.json"


def load_processed() -> set[str]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def mark_processed(event_id: str) -> None:
    processed = load_processed()
    processed.add(event_id)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(list(processed), f, indent=2)

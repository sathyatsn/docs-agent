"""Pending release plans awaiting approval, persisted as JSON.

A plan stores the verified, structured ReleaseRequests (never free text to be
re-parsed) and the fingerprint of the plan shown to the approver.
"""

import json
import os
import secrets
import tempfile
import threading
from dataclasses import asdict, dataclass, field

PLAN_STORE_FILE = os.getenv("RELEASE_PLAN_STORE", "config/release_plans.json")

PENDING, APPLYING, APPLIED, CANCELLED, EXPIRED, STALE, FAILED, BLOCKED = (
    "pending", "applying", "applied", "cancelled", "expired", "stale", "failed", "blocked")


@dataclass
class StoredPlan:
    id: str
    status: str
    created_at: str
    requested_by: str
    source: str  # "cli" | "slack"
    releases: list[dict]  # [{"request": {...}, "evidence": str|None, "ga_evidence": str|None, "notes": [...]}]
    fingerprint: str
    source_text: str = ""
    approved_by: str | None = None
    finished_at: str | None = None
    epic_keys: list[str] = field(default_factory=list)
    created_keys: list[str] = field(default_factory=list)
    error: str | None = None
    replaced_by: str | None = None
    context: dict = field(default_factory=dict)  # e.g. Slack channel/thread


class PlanStore:
    def __init__(self, path: str = PLAN_STORE_FILE):
        self.path = path
        self.lock = threading.RLock()

    def _read(self) -> dict[str, dict]:
        if not os.path.exists(self.path):
            return {}
        with open(self.path) as f:
            return json.load(f)

    def _write(self, data: dict[str, dict]) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".release_plans.")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def new_id(self) -> str:
        return f"rp-{secrets.token_hex(4)}"

    def get(self, plan_id: str) -> StoredPlan | None:
        with self.lock:
            raw = self._read().get(plan_id)
        return StoredPlan(**raw) if raw else None

    def save(self, plan: StoredPlan) -> None:
        with self.lock:
            data = self._read()
            data[plan.id] = asdict(plan)
            self._write(data)

    def transition(self, plan_id: str, expected: str, new_status: str) -> StoredPlan | None:
        """Atomically move a plan from `expected` to `new_status`; None if it was not in `expected`."""
        with self.lock:
            data = self._read()
            raw = data.get(plan_id)
            if not raw or raw["status"] != expected:
                return None
            raw["status"] = new_status
            self._write(data)
            return StoredPlan(**raw)

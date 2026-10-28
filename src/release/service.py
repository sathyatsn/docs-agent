"""Prepare -> review -> approve flow shared by the CLI and the Slack bot.

prepare(): build plans from verified requests, store them with a fingerprint.
approve(): rebuild the plans from the stored requests (no LLM involved), and
create tickets only if the result is identical to what the approver reviewed.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.release.catalog import Catalog
from src.release.grounding import GroundedRelease
from src.release.models import ReleasePlan, ReleaseRequest
from src.release.plan_store import (
    APPLIED, APPLYING, BLOCKED, CANCELLED, EXPIRED, FAILED, PENDING, STALE, PlanStore, StoredPlan)
from src.release.planner import apply_plan, plan_fingerprint, plan_release

logger = logging.getLogger(__name__)


@dataclass
class Prepared:
    stored: StoredPlan
    plans: list[ReleasePlan]


@dataclass
class ApprovalResult:
    status: str  # applied | stale | blocked | failed | expired | not_found | already_<status>
    message: str
    plans: list[ReleasePlan] = field(default_factory=list)
    created_keys: list[str] = field(default_factory=list)
    epic_keys: list[str] = field(default_factory=list)
    replacement: Prepared | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReleaseService:
    def __init__(self, catalog: Catalog, jira, store: PlanStore, ttl_hours: int | None = None):
        self.catalog = catalog
        self.jira = jira
        self.store = store
        self.ttl = timedelta(hours=ttl_hours or catalog.slack.get("plan_ttl_hours", 24))

    def _build(self, requests: list[ReleaseRequest]) -> list[ReleasePlan]:
        return [plan_release(self.catalog, r, self.jira) for r in requests]

    def prepare(self, releases: list[GroundedRelease | ReleaseRequest], requested_by: str,
                source: str, source_text: str = "", context: dict | None = None) -> Prepared:
        entries = []
        for item in releases:
            if isinstance(item, GroundedRelease):
                entries.append({"request": item.request.to_dict(), "evidence": item.evidence,
                                "ga_evidence": item.ga_evidence, "notes": item.notes})
            else:
                entries.append({"request": item.to_dict(), "evidence": None, "ga_evidence": None, "notes": []})
        requests = [ReleaseRequest.from_dict(e["request"]) for e in entries]
        plans = self._build(requests)
        stored = StoredPlan(
            id=self.store.new_id(),
            status=PENDING,
            created_at=_now().isoformat(),
            requested_by=requested_by,
            source=source,
            releases=entries,
            fingerprint=plan_fingerprint(plans),
            source_text=source_text,
            context=context or {},
        )
        self.store.save(stored)
        return Prepared(stored, plans)

    def cancel(self, plan_id: str, user: str) -> ApprovalResult:
        stored = self.store.transition(plan_id, PENDING, CANCELLED)
        if not stored:
            return self._not_pending(plan_id)
        stored.approved_by, stored.finished_at = user, _now().isoformat()
        self.store.save(stored)
        return ApprovalResult(CANCELLED, f"Plan {plan_id} cancelled by {user}. Nothing was created.")

    def _not_pending(self, plan_id: str) -> ApprovalResult:
        stored = self.store.get(plan_id)
        if not stored:
            return ApprovalResult("not_found", f"Plan {plan_id} not found.")
        return ApprovalResult(f"already_{stored.status}",
                              f"Plan {plan_id} is already {stored.status}; nothing was done.",
                              created_keys=stored.created_keys, epic_keys=stored.epic_keys)

    def approve(self, plan_id: str, approver: str) -> ApprovalResult:
        # Claim the plan first so a double click or two approvers cannot apply it twice.
        stored = self.store.transition(plan_id, PENDING, APPLYING)
        if not stored:
            return self._not_pending(plan_id)

        def finish(status: str, **changes) -> StoredPlan:
            stored.status, stored.finished_at, stored.approved_by = status, _now().isoformat(), approver
            for k, v in changes.items():
                setattr(stored, k, v)
            self.store.save(stored)
            return stored

        if _now() - datetime.fromisoformat(stored.created_at) > self.ttl:
            finish(EXPIRED)
            return ApprovalResult(EXPIRED, f"Plan {plan_id} expired; please request it again.")

        requests = [ReleaseRequest.from_dict(e["request"]) for e in stored.releases]
        try:
            plans = self._build(requests)
        except Exception as e:  # catalog or Jira error: nothing written yet
            finish(FAILED, error=str(e))
            return ApprovalResult(FAILED, f"Could not rebuild plan {plan_id}: {e}. Nothing was created.")

        if plan_fingerprint(plans) != stored.fingerprint:
            replacement = self.prepare(
                [ReleaseRequest.from_dict(e["request"]) for e in stored.releases],
                stored.requested_by, stored.source, stored.source_text, stored.context)
            for old, new in zip(stored.releases, replacement.stored.releases):
                new.update({k: old[k] for k in ("evidence", "ga_evidence", "notes")})
            self.store.save(replacement.stored)
            finish(STALE, replaced_by=replacement.stored.id)
            return ApprovalResult(
                STALE, f"Jira or the release catalog changed since plan {plan_id} was reviewed. "
                       f"Nothing was created; please review the updated plan {replacement.stored.id}.",
                plans=replacement.plans, replacement=replacement)

        if any(p.blocked for p in plans):
            finish(BLOCKED)
            return ApprovalResult(BLOCKED, "Plan is blocked (see warnings). Nothing was created.", plans=plans)

        created, epics = [], []
        try:
            for plan in plans:
                new_keys = apply_plan(plan, self.catalog, self.jira, created)
                epics.append(plan.epic.existing_key)
                if new_keys:
                    self._comment_provenance(plan, new_keys, stored, approver)
        except Exception as e:
            logger.exception("Applying plan %s failed", plan_id)
            epics = [p.epic.existing_key for p in plans if p.epic.existing_key]
            finish(FAILED, error=str(e), created_keys=created, epic_keys=epics)
            return ApprovalResult(
                FAILED, f"Failed after creating {len(created)} issue(s): {e}. Re-requesting the same "
                        f"release is safe; only the missing tickets will be created.",
                plans=plans, created_keys=created, epic_keys=epics)

        finish(APPLIED, created_keys=created, epic_keys=epics)
        return ApprovalResult(APPLIED, f"Created {len(created)} issue(s).", plans=plans,
                              created_keys=created, epic_keys=epics)

    def _comment_provenance(self, plan: ReleasePlan, new_keys: list[str], stored: StoredPlan,
                            approver: str) -> None:
        lines = [
            f"Release tickets created by release-agent (plan {stored.id}): {', '.join(new_keys)}",
            f"* Requested by: {stored.requested_by} via {stored.source}",
            f"* Approved by: {approver}",
        ]
        if stored.source_text:
            lines += ["* Original request:", "{noformat}", stored.source_text, "{noformat}"]
        try:
            self.jira.add_comment(plan.epic.existing_key, "\n".join(lines))
        except Exception:  # the tickets exist; a missing audit comment must not fail the run
            logger.exception("Could not add provenance comment to %s", plan.epic.existing_key)

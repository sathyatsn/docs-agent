import copy
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

from src.release.catalog import Catalog
from src.release.models import ReleaseRequest
from src.release.plan_store import PlanStore
from src.release.planner import plan_release
from src.release.service import ReleaseService
from tests.fakes import FakeJira


@pytest.fixture(scope="module")
def catalog_data():
    with open("config/release_catalog.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def catalog(catalog_data):
    return Catalog(copy.deepcopy(catalog_data))


@pytest.fixture
def store(tmp_path):
    return PlanStore(str(tmp_path / "plans.json"))


def sgw(version="4.1.4"):
    return ReleaseRequest(product="sgw", version=version, ga_date=date(2026, 12, 10))


def test_approve_creates_exactly_the_reviewed_plan(catalog, store):
    jira = FakeJira()
    service = ReleaseService(catalog, jira, store)
    prepared = service.prepare([sgw()], "tpm", "cli", "SGW 4.1.4 GA Dec 10")
    assert jira.created == []  # preparing never writes

    result = service.approve(prepared.stored.id, "approver")
    assert result.status == "applied"
    assert len(result.created_keys) == 6
    assert [i["summary"] for i in jira.issues] == [i.summary for i in [prepared.plans[0].epic, *prepared.plans[0].tasks]]
    comment = jira.comments[result.epic_keys[0]][0]
    assert "Approved by: approver" in comment and "SGW 4.1.4 GA Dec 10" in comment
    assert store.get(prepared.stored.id).status == "applied"


def test_double_approve_creates_nothing_twice(catalog, store):
    jira = FakeJira()
    service = ReleaseService(catalog, jira, store)
    plan_id = service.prepare([sgw()], "tpm", "slack").stored.id
    service.approve(plan_id, "a")
    second = service.approve(plan_id, "b")
    assert second.status == "already_applied"
    assert len(jira.created) == 6


def test_jira_changed_since_review_requires_new_approval(catalog, store):
    jira = FakeJira()
    service = ReleaseService(catalog, jira, store)
    prepared = service.prepare([sgw()], "tpm", "slack")
    # Someone creates the epic by hand while the plan waits for approval.
    jira.issues.append({"key": "CM-5000", "summary": "SGW Maintenance Release Ticket - 4.1.4",
                        "type": "Epic", "project": "CM"})
    result = service.approve(prepared.stored.id, "approver")
    assert result.status == "stale" and jira.created == []
    new_id = result.replacement.stored.id
    assert result.replacement.plans[0].epic.existing_key == "CM-5000"
    applied = service.approve(new_id, "approver")
    assert applied.status == "applied" and len(applied.created_keys) == 5  # tasks only


def test_catalog_changed_since_review_requires_new_approval(catalog, store):
    jira = FakeJira()
    service = ReleaseService(catalog, jira, store)
    plan_id = service.prepare([sgw()], "tpm", "slack").stored.id
    catalog.tasks["qe_signoff"]["summary"] = "QE Signoff (renamed)"
    assert service.approve(plan_id, "approver").status == "stale"
    assert jira.created == []


def test_expired_plan_is_not_applied(catalog, store):
    jira = FakeJira()
    service = ReleaseService(catalog, jira, store, ttl_hours=1)
    stored = service.prepare([sgw()], "tpm", "slack").stored
    stored.created_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.save(stored)
    assert service.approve(stored.id, "approver").status == "expired"
    assert jira.created == []


def test_cancel_then_approve_does_nothing(catalog, store):
    jira = FakeJira()
    service = ReleaseService(catalog, jira, store)
    plan_id = service.prepare([sgw()], "tpm", "slack").stored.id
    assert service.cancel(plan_id, "tpm").status == "cancelled"
    assert service.approve(plan_id, "approver").status == "already_cancelled"
    assert jira.created == []


def test_blocked_plan_is_not_applied(catalog, store):
    jira = FakeJira([
        {"key": "CM-1", "summary": "SGW Maintenance Release Ticket - 4.1.4", "type": "Epic"},
        {"key": "CM-2", "summary": "SGW Maintenance Release Ticket - 4.1.4 - GA Dec 10", "type": "Epic"},
    ])
    service = ReleaseService(catalog, jira, store)
    plan_id = service.prepare([sgw()], "tpm", "slack").stored.id
    assert service.approve(plan_id, "approver").status == "blocked"
    assert jira.created == []


def test_partial_failure_is_reported_and_retry_completes(catalog, store):
    jira = FakeJira(fail_on_create=3)
    service = ReleaseService(catalog, jira, store)
    result = service.approve(service.prepare([sgw()], "tpm", "slack").stored.id, "approver")
    assert result.status == "failed" and len(result.created_keys) == 2
    assert result.epic_keys == [result.created_keys[0]]
    jira.fail_on_create = None
    retry = service.approve(service.prepare([sgw()], "tpm", "slack").stored.id, "approver")
    assert retry.status == "applied" and len(retry.created_keys) == 4
    assert len([i for i in jira.issues if i["type"] == "Epic"]) == 1


def test_per_product_jira_project(catalog_data, store):
    data = copy.deepcopy(catalog_data)
    data["products"]["edge-server"]["jira"] = {"project": "EDGE"}
    catalog = Catalog(data)
    jira = FakeJira()
    plan = plan_release(catalog, ReleaseRequest(product="edge-server", version="1.0.3"), jira)
    assert {i.project for i in [plan.epic, *plan.tasks]} == {"EDGE"}
    service = ReleaseService(catalog, jira, store)
    result = service.approve(service.prepare([ReleaseRequest(product="edge-server", version="1.0.3")],
                                             "tpm", "cli").stored.id, "approver")
    assert all(k.startswith("EDGE-") for k in result.created_keys)
    assert catalog.projects() == {"CM": ["cbl", "cbl-vs", "cbl-js", "cbl-rn", "sgw"], "EDGE": ["edge-server"]}


def test_invalid_project_key_rejected(catalog_data):
    data = copy.deepcopy(catalog_data)
    data["products"]["sgw"]["jira"] = {"project": "not a key"}
    with pytest.raises(ValueError, match="invalid Jira project key"):
        Catalog(data)

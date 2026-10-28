from datetime import date

import pytest

from src.release.catalog import Catalog
from src.release.models import ReleaseRequest, release_type_for
from src.release.planner import apply_plan, plan_release
from tests.fakes import FakeJira


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load("config/release_catalog.yaml")


def test_release_type_inference():
    assert release_type_for("4.1") == "major"
    assert release_type_for("4.1.0") == "major"
    assert release_type_for("4.1.3") == "maintenance"
    assert release_type_for("3.2.4.1") == "maintenance"


def test_invalid_version_rejected():
    with pytest.raises(ValueError):
        ReleaseRequest(product="sgw", version="4.1.x")


def test_resolve_product_aliases(catalog):
    assert catalog.resolve_product("Sync Gateway") == "sgw"
    assert catalog.resolve_product("CBL JS") == "cbl-js"
    assert catalog.resolve_product("react native") == "cbl-rn"


def test_product_for_legacy_summaries(catalog):
    assert catalog.product_for_summary("CBL Maintenance Release Ticket - 4.0.5") == "cbl"
    assert catalog.product_for_summary("CBL JS Maintenance Release Ticket CBL JS 1.0.3") == "cbl-js"
    assert catalog.product_for_summary("CBL-React Native Maintenance Release Ticket -1.1.2") == "cbl-rn"
    assert catalog.product_for_summary("CBL Maintenance Release Ticket CBL Vector Search 1.0.1") == "cbl-vs"
    assert catalog.product_for_summary("SGW Maintenance Release Ticket - 4.1.3") == "sgw"


def test_sgw_maintenance_plan_matches_template(catalog):
    req = ReleaseRequest(product="sgw", version="4.1.4", ga_date=date(2026, 12, 10))
    plan = plan_release(catalog, req)
    assert plan.epic.summary == "SGW Maintenance Release Ticket - 4.1.4 - GA Dec 10, 2026"
    assert [t.summary for t in plan.tasks] == [
        "QE Sign-off - SGW 4.1.4",
        "Perf Sign-off - SGW 4.1.4",
        "Documentation Tasks - SGW 4.1.4",
        "Publish Builds - SGW 4.1.4",
        "Web Updates - SGW 4.1.4",
    ]
    publish = next(t for t in plan.tasks if t.task_id == "publish_builds")
    assert "DockerHub" in publish.description and "NuGet" not in publish.description
    qe = next(t for t in plan.tasks if t.task_id == "qe_signoff")
    assert qe.due_date == date(2026, 12, 5)
    assert "release-sgw-4.1.4" in plan.epic.labels


def test_platform_scoped_release_marks_other_platforms_na(catalog):
    req = ReleaseRequest(product="cbl", version="4.1.4", platforms=["dotnet"])
    plan = plan_release(catalog, req)
    assert plan.epic.summary.startswith("CBL .NET Maintenance Release Ticket - 4.1.4")
    publish = next(t for t in plan.tasks if t.task_id == "publish_builds").description
    assert "|NuGet for .NET|(x)|" in publish
    assert "|Carthage, Cocoapods for iOS|NA (platform not in this release)|" in publish
    assert "|S3|(x)|" in publish
    qe = next(t for t in plan.tasks if t.task_id == "qe_signoff").description
    assert "|.NET|TBD|" in qe and "|iOS|" not in qe


def test_major_release_uses_full_task_list(catalog):
    plan = plan_release(catalog, ReleaseRequest(product="cbl", version="4.2", ga_date=date(2027, 3, 1)))
    assert plan.epic.summary == "CBL Release Ticket - 4.2 - GA Mar 1, 2027"
    assert len(plan.tasks) == 16
    assert any(t.summary == "Cloud Marketplace updates - CBL 4.2 - N/A" for t in plan.tasks)
    assert any("No owner configured" in w for w in plan.warnings)


def test_new_release_creates_epic_then_children(catalog):
    jira = FakeJira()
    plan = plan_release(catalog, ReleaseRequest(product="edge-server", version="1.0.3"), jira)
    created = apply_plan(plan, catalog, jira)
    assert len(created) == 6
    epic_key = created[0]
    assert all(i["parent"] == epic_key for i in jira.issues[1:])


def test_rerun_is_idempotent(catalog):
    jira = FakeJira()
    req = ReleaseRequest(product="sgw", version="4.1.4")
    apply_plan(plan_release(catalog, req, jira), catalog, jira)
    second = plan_release(catalog, ReleaseRequest(product="sgw", version="4.1.4"), jira)
    assert second.to_create == []


def test_existing_manual_epic_only_gets_missing_tasks(catalog):
    jira = FakeJira([
        {"key": "CM-1827", "summary": "SGW Maintenance Release Ticket - 4.1.3", "type": "Epic"},
        {"key": "CM-1828", "summary": "QE Sign-off - 4.1.3", "type": "Task", "parent": "CM-1827"},
        {"key": "CM-1832", "summary": "Web updates - 4.1.3", "type": "Task", "parent": "CM-1827"},
        # Same version, different product: must not be matched.
        {"key": "CM-1769", "summary": "CBL Maintenance Release Ticket - 4.1.3", "type": "Epic"},
    ])
    plan = plan_release(catalog, ReleaseRequest(product="sgw", version="4.1.3"), jira)
    assert plan.epic.existing_key == "CM-1827"
    assert {t.task_id for t in plan.to_create} == {"perf_signoff", "documentation", "publish_builds"}
    apply_plan(plan, catalog, jira)
    assert {i["parent"] for i in jira.issues if i["key"] in jira.created} == {"CM-1827"}


def test_duplicate_epics_block_the_plan(catalog):
    jira = FakeJira([
        {"key": "CM-1800", "summary": "CBL-React Native Maintenance Release Ticket -1.1.2 - GA Target TBD", "type": "Epic"},
        {"key": "CM-1804", "summary": "CBL-React Native Maintenance Release Ticket -1.1.2 - GA Target TBD", "type": "Epic"},
    ])
    plan = plan_release(catalog, ReleaseRequest(product="cbl-rn", version="1.1.2"), jira)
    assert plan.blocked
    with pytest.raises(RuntimeError):
        apply_plan(plan, catalog, jira)
    assert jira.created == []


def test_unknown_platform_rejected(catalog):
    with pytest.raises(ValueError):
        plan_release(catalog, ReleaseRequest(product="sgw", version="4.1.4", platforms=["ios"]))


def test_platform_scoped_release_is_distinct_from_full_release(catalog):
    jira = FakeJira([
        {"key": "CM-1303", "summary": "CBL Release Ticket 4.0 - GA Oct 27, 2025", "type": "Epic"},
        {"key": "CM-1322", "summary": "CBL Release Ticket 4.0 .NET- Nov 25, 2025", "type": "Epic"},
    ])
    full = plan_release(catalog, ReleaseRequest(product="cbl", version="4.0"), jira)
    dotnet = plan_release(catalog, ReleaseRequest(product="cbl", version="4.0", platforms=["dotnet"]), jira)
    assert not full.blocked and full.epic.existing_key == "CM-1303"
    assert not dotnet.blocked and dotnet.epic.existing_key == "CM-1322"
    assert "release-cbl-4.0-dotnet" in dotnet.epic.labels


def test_audit_flags_duplicates_and_missing_tasks(catalog):
    from src.release.auditor import audit_open_releases

    class AuditJira(FakeJira):
        def search(self, jql, fields):
            if jql.startswith("parent = "):
                return super().search(jql, fields)
            return [{"key": i["key"], "fields": {"summary": i["summary"], "duedate": None}}
                    for i in self.issues if i["type"] == "Epic"]

    jira = AuditJira([
        {"key": "CM-1354", "summary": "SGW Maintenance Release Ticket 3.2.7 - Nov 12", "type": "Epic"},
        {"key": "CM-1355", "summary": "SGW Maintenance Release Ticket 3.2.7 - Nov 12", "type": "Epic"},
        {"key": "CM-1348", "summary": "SGW Maintenance Release Ticket x.x.x - <GA Date>", "type": "Epic"},
    ])
    findings = {f.key: f.issues for f in audit_open_releases(catalog, jira)}
    assert any("Possible duplicate" in i for i in findings["CM-1354"])
    assert "Missing task: QE Sign-off" in findings["CM-1355"]
    assert any("placeholder" in i for i in findings["CM-1348"])

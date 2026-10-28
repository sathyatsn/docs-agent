"""End-to-end test of .claude/skills/release-tickets/scripts/release_plan.py.

Plays Claude's part exactly as SKILL.md describes it, with FakeJira standing in
for the Atlassian connector, and checks that mistakes are caught.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fakes import FakeJira

SCRIPT = Path(__file__).resolve().parent.parent / ".claude/skills/release-tickets/scripts/release_plan.py"
TODAY = "2026-10-28"


def run(*args, cwd):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def verify(tmp: Path, request: str, candidates: list) -> tuple[int, dict]:
    (tmp / "request.txt").write_text(request)
    (tmp / "candidates.json").write_text(json.dumps(candidates))
    code, out, _ = run("verify", "--text-file", "request.txt", "--candidates", "candidates.json",
                       "--out", "verified.json", "--today", TODAY, cwd=tmp)
    return code, json.loads(out)


def plan(tmp: Path, jira: FakeJira, expect: str | None = None) -> tuple[int, str]:
    """Step 4: run `plan`, answering its searches from FakeJira until it is satisfied."""
    searches_file = tmp / "searches.json"
    searches_file.unlink(missing_ok=True)
    for _ in range(5):
        extra = ["--expect-fingerprint", expect] if expect else []
        code, out, err = run("plan", "--verified", "verified.json", "--searches", "searches.json",
                             "--out", "plan.json", *extra, cwd=tmp)
        if code != 3:
            return code, out
        searches = json.loads(searches_file.read_text()) if searches_file.exists() else {}
        for q in json.loads(out)["queries"]:
            searches[q["id"]] = [
                {"key": i["key"], "summary": i["fields"]["summary"], "duedate": None, "assignee": None}
                for i in jira.search(q["jql"], ["summary"])
            ]
        searches_file.write_text(json.dumps(searches))
    raise AssertionError("plan never stopped asking for searches")


def create_as_claude(tmp: Path, jira: FakeJira, tamper=None) -> list[dict]:
    """Step 6/7: create issues from plan.json and read them back into created.json."""
    data = json.loads((tmp / "plan.json").read_text())
    read_back = []
    for release in data["releases"]:
        if release["blocked"]:
            continue
        epic_key = None
        for issue in release["issues"]:
            if issue["action"] == "exists":
                epic_key = epic_key or issue["existing_key"]
                continue
            fields = dict(issue)
            if tamper:
                tamper(fields)
            parent = epic_key if fields["parent"] else None
            key = jira.create_issue(fields["project"], fields["issue_type"], fields["summary"],
                                    fields["description"], fields["labels"],
                                    fields["assignee_account_id"], fields["due_date"], parent_key=parent)
            if issue["ref"] == "epic":
                epic_key = key
            read_back.append({"key": key, "summary": fields["summary"], "project": fields["project"],
                              "issue_type": fields["issue_type"], "parent": parent,
                              "assignee_account_id": fields["assignee_account_id"],
                              "due_date": fields["due_date"], "labels": fields["labels"],
                              "description": fields["description"]})
    (tmp / "created.json").write_text(json.dumps(read_back))
    return read_back


def check(tmp: Path) -> tuple[int, dict]:
    code, out, _ = run("check", "--plan", "plan.json", "--created", "created.json", cwd=tmp)
    return code, json.loads(out)


SGW_REQUEST = "Please create the release ticket for SGW 4.1.4, GA Dec 10"
SGW_CANDIDATE = [{"product": "sgw", "version": "4.1.4", "platforms": None, "ga_date": "2026-12-10",
                  "evidence": "SGW 4.1.4", "ga_date_evidence": "GA Dec 10"}]


def test_full_flow_creates_exactly_the_plan(tmp_path):
    jira = FakeJira()
    code, result = verify(tmp_path, SGW_REQUEST, SGW_CANDIDATE)
    assert code == 0 and result["ok"]

    code, shown = plan(tmp_path, jira)
    assert code == 0
    assert "SGW Maintenance Release Ticket - 4.1.4 - GA Dec 10, 2026" in shown
    assert "Read from: “SGW 4.1.4” · GA from: “GA Dec 10”" in shown
    assert "| **create** | QE Sign-off - SGW 4.1.4 | Adam Fraser | 2026-12-05 |" in shown
    assert "**6 issue(s) to create.**" in shown
    fingerprint = shown.rsplit("fingerprint: ", 1)[1].strip()

    # User says yes: refresh and confirm nothing changed.
    code, _ = plan(tmp_path, jira, expect=fingerprint)
    assert code == 0
    created = create_as_claude(tmp_path, jira)
    assert len(created) == 6 and jira.issues[1]["parent"] == jira.issues[0]["key"]

    code, report = check(tmp_path)
    assert code == 0 and report == {"ok": True, "verified_issues": 6, "problems": []}

    # Asking again finds everything and creates nothing.
    code, shown = plan(tmp_path, jira)
    assert "**Nothing to create.**" in shown


def test_rejected_reading_stops_the_flow(tmp_path):
    bad = [dict(SGW_CANDIDATE[0], version="4.1.5")]
    code, result = verify(tmp_path, SGW_REQUEST, bad)
    assert code == 1 and not result["ok"]
    assert any("4.1.5" in r for r in result["rejected"])
    code, _, err = run("plan", "--verified", "verified.json", "--searches", "s.json", "--out", "p.json", cwd=tmp_path)
    assert code == 1 and "not ok" in err


def test_jira_change_after_approval_is_detected(tmp_path):
    jira = FakeJira()
    verify(tmp_path, SGW_REQUEST, SGW_CANDIDATE)
    _, shown = plan(tmp_path, jira)
    fingerprint = shown.rsplit("fingerprint: ", 1)[1].strip()
    jira.issues.append({"key": "CM-4242", "summary": "SGW Maintenance Release Ticket - 4.1.4", "type": "Epic"})
    code, shown = plan(tmp_path, jira, expect=fingerprint)
    assert code == 4 and "exists CM-4242" in shown and "PLAN CHANGED" in shown


def test_existing_manual_epic_gets_only_missing_tasks(tmp_path):
    jira = FakeJira([
        {"key": "CM-1827", "summary": "SGW Maintenance Release Ticket - 4.1.3", "type": "Epic"},
        {"key": "CM-1828", "summary": "QE Sign-off - 4.1.3", "type": "Task", "parent": "CM-1827"},
    ])
    verify(tmp_path, "SGW 4.1.3", [{"product": "sgw", "version": "4.1.3", "platforms": None,
                                    "ga_date": None, "evidence": "SGW 4.1.3", "ga_date_evidence": None}])
    _, shown = plan(tmp_path, jira)
    assert "exists CM-1827" in shown and "exists CM-1828" in shown and "**4 issue(s) to create.**" in shown
    create_as_claude(tmp_path, jira)
    assert {i["parent"] for i in jira.issues if i["key"] in jira.created} == {"CM-1827"}
    assert check(tmp_path)[0] == 0


@pytest.mark.parametrize("tamper, expected", [
    (lambda f: f.update(assignee_account_id=None) if f["ref"] == "qe_signoff" else None, "assignee"),
    (lambda f: f.update(summary=f["summary"].replace("Sign-off", "Signoff")), "expected 1 created issue"),
    (lambda f: f.update(due_date=None) if f["ref"] == "web_updates" else None, "due date"),
    (lambda f: f.update(description=f["description"].split("|")[0]) if f["ref"] == "documentation" else None,
     "description is missing"),
    (lambda f: f.update(labels=[]) if f["ref"] == "epic" else None, "missing labels"),
])
def test_check_catches_fields_not_copied_exactly(tmp_path, tamper, expected):
    jira = FakeJira()
    verify(tmp_path, SGW_REQUEST, SGW_CANDIDATE)
    plan(tmp_path, jira)
    create_as_claude(tmp_path, jira, tamper=tamper)
    code, report = check(tmp_path)
    assert code == 1 and any(expected in p for p in report["problems"]), report


def test_duplicate_epics_block(tmp_path):
    jira = FakeJira([
        {"key": "CM-1800", "summary": "CBL-React Native Maintenance Release Ticket -1.1.2", "type": "Epic"},
        {"key": "CM-1804", "summary": "CBL-React Native Maintenance Release Ticket -1.1.2", "type": "Epic"},
    ])
    verify(tmp_path, "CBL React Native 1.1.2", [{"product": "cbl-rn", "version": "1.1.2", "platforms": None,
                                                 "ga_date": None, "evidence": "CBL React Native 1.1.2",
                                                 "ga_date_evidence": None}])
    _, shown = plan(tmp_path, jira)
    assert "⛔ **Blocked**" in shown and "CM-1800, CM-1804" in shown and "**Nothing to create.**" in shown


def test_audit_reports_gaps(tmp_path):
    jira = FakeJira([
        {"key": "CM-1815", "summary": "SGW Maintenance Release Ticket - 3.3.9", "type": "Epic"},
        {"key": "CM-1816", "summary": "QE Sign-off - 3.3.9", "type": "Task", "parent": "CM-1815"},
    ])
    searches = {}
    for _ in range(4):
        (tmp_path / "a.json").write_text(json.dumps(searches))
        code, out, _ = run("audit", "--searches", "a.json", cwd=tmp_path)
        if code != 3:
            break
        for q in json.loads(out)["queries"]:
            searches[q["id"]] = [{"key": i["key"], "summary": i["fields"]["summary"], "duedate": None,
                                  "assignee": None} for i in jira.search(q["jql"], ["summary"])]
    assert code == 0
    assert "Missing task: Perf Sign-off" in out and "No due date" in out

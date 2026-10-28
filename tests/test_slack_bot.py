import json
from datetime import date
from urllib.parse import urlencode

import pytest

from src.release import slack_app
from src.release.catalog import Catalog
from src.release.grounding import GroundedRelease
from src.release.intake import IntakeResult
from src.release.models import ReleaseRequest
from src.release.plan_store import PlanStore
from src.release.service import ReleaseService
from src.release.slack_app import Deps, handle_action, handle_request
from src.release.slack_format import APPROVE_ACTION, CANCEL_ACTION
from tests.fakes import FakeJira

APPROVER, TPM, OTHER = "UAPPROVER", "UTPM", "UOTHER"


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)

    @property
    def last(self):
        return self.calls[-1]


@pytest.fixture
def deps(tmp_path):
    catalog = Catalog.load("config/release_catalog.yaml")
    catalog.slack["approvers"] = [APPROVER]
    jira = FakeJira()

    def parse(text):
        if "4.1.4" in text:
            req = ReleaseRequest(product="sgw", version="4.1.4", ga_date=date(2026, 12, 10))
            return IntakeResult(releases=[GroundedRelease(req, "SGW 4.1.4", "GA Dec 10")])
        return IntakeResult(rejected=["4.1.x: not a valid version"])

    d = Deps(catalog=catalog, service=ReleaseService(catalog, jira, PlanStore(str(tmp_path / "p.json"))),
             parse=parse, browse=jira.browse_url, owner_name=jira.display_name,
             user_name=lambda u: f"name-{u}")
    d.jira = jira
    return d


def post_plan(deps) -> tuple[str, list]:
    reply = Recorder()
    handle_request("SGW 4.1.4 GA Dec 10", TPM, "C1", reply, deps)
    blocks = reply.last["blocks"]
    actions = next(b for b in blocks if b["type"] == "actions")
    return actions["elements"][0]["value"], blocks


def test_help(deps):
    reply = Recorder()
    handle_request("help", TPM, "C1", reply, deps)
    assert "One release per line" in reply.last["text"]


def test_plan_is_posted_with_evidence_and_nothing_created(deps):
    plan_id, blocks = post_plan(deps)
    text = json.dumps(blocks, ensure_ascii=False)
    assert "SGW Maintenance Release Ticket - 4.1.4 - GA Dec 10, 2026" in text
    assert "read from: “SGW 4.1.4”" in text and "GA from: “GA Dec 10”" in text
    assert "Approve: create 6 issue(s)" in text
    assert deps.jira.created == []


def test_unverifiable_request_plans_nothing(deps):
    reply = Recorder()
    handle_request("SGW 4.1.x soon", TPM, "C1", reply, deps)
    assert "could not verify" in reply.last["text"] and "blocks" not in reply.last
    assert deps.jira.created == []


def test_channel_allow_list(deps):
    deps.catalog.slack["allowed_channels"] = ["CRELEASE"]
    reply = Recorder()
    handle_request("SGW 4.1.4", TPM, "CRANDOM", reply, deps)
    assert "can't be requested from this channel" in reply.last["text"]


def test_non_approver_cannot_create(deps):
    plan_id, blocks = post_plan(deps)
    respond = Recorder()
    handle_action(APPROVE_ACTION, plan_id, TPM, blocks, respond, deps)
    assert respond.last["response_type"] == "ephemeral"
    assert deps.jira.created == []
    assert deps.service.store.get(plan_id).status == "pending"


def test_approver_creates_and_buttons_are_removed(deps):
    plan_id, blocks = post_plan(deps)
    respond = Recorder()
    handle_action(APPROVE_ACTION, plan_id, APPROVER, blocks, respond, deps)
    assert len(deps.jira.created) == 6
    first, final = respond.calls[0], respond.calls[-1]
    assert not any(b["type"] == "actions" for b in first["blocks"])  # buttons gone before Jira writes
    assert "applied" in json.dumps(final["blocks"]) and "CM-9000" in json.dumps(final["blocks"])
    assert "Approved by: name-UAPPROVER (Slack UAPPROVER)" in deps.jira.comments["CM-9000"][0]


def test_second_click_is_harmless(deps):
    plan_id, blocks = post_plan(deps)
    handle_action(APPROVE_ACTION, plan_id, APPROVER, blocks, Recorder(), deps)
    respond = Recorder()
    handle_action(APPROVE_ACTION, plan_id, APPROVER, blocks, respond, deps)
    assert "already_applied" in respond.last["text"]
    assert len(deps.jira.created) == 6


def test_requester_can_cancel_but_others_cannot(deps):
    plan_id, blocks = post_plan(deps)
    respond = Recorder()
    handle_action(CANCEL_ACTION, plan_id, OTHER, blocks, respond, deps)
    assert respond.last["response_type"] == "ephemeral"
    handle_action(CANCEL_ACTION, plan_id, TPM, blocks, respond, deps)
    assert deps.service.store.get(plan_id).status == "cancelled"


def test_stale_plan_reposts_for_approval(deps):
    plan_id, blocks = post_plan(deps)
    deps.jira.issues.append({"key": "CM-77", "summary": "SGW Maintenance Release Ticket - 4.1.4",
                             "type": "Epic", "project": "CM"})
    respond = Recorder()
    handle_action(APPROVE_ACTION, plan_id, APPROVER, blocks, respond, deps)
    assert deps.jira.created == []
    repost = respond.last
    assert repost["replace_original"] is False and "CM-77" in json.dumps(repost["blocks"])
    assert any(b["type"] == "actions" for b in repost["blocks"])


def test_no_approve_button_when_nothing_to_create(deps):
    post_plan_id, blocks = post_plan(deps)
    handle_action(APPROVE_ACTION, post_plan_id, APPROVER, blocks, Recorder(), deps)
    reply = Recorder()
    handle_request("SGW 4.1.4 GA Dec 10", TPM, "C1", reply, deps)
    assert not any(b["type"] == "actions" for b in reply.last["blocks"])
    assert "Nothing to create" in json.dumps(reply.last["blocks"])


def test_bolt_wiring_routes_command_and_buttons(deps, monkeypatch):
    from slack_bolt.request import BoltRequest
    from slack_sdk.web import SlackResponse, WebClient

    # Bolt authorises each request with auth.test; answer it locally instead of calling Slack.
    monkeypatch.setattr(WebClient, "auth_test", lambda self, **kw: SlackResponse(
        client=self, http_verb="POST", api_url="auth.test", req_args={}, headers={}, status_code=200,
        data={"ok": True, "user_id": "UBOT", "bot_id": "BBOT", "team_id": "T1", "url": "https://t.slack.com/"}))
    seen = []
    monkeypatch.setattr(slack_app, "handle_request", lambda *a: seen.append(("request", a[0], a[1])))
    monkeypatch.setattr(slack_app, "handle_action", lambda *a: seen.append(("action", a[0], a[1], a[2])))
    app = slack_app.build_app(deps, "xoxb-test", token_verification_enabled=False,
                              request_verification_enabled=False)

    command = urlencode({"command": "/release", "text": "SGW 4.1.4", "user_id": TPM, "channel_id": "C1",
                         "team_id": "T1", "response_url": "https://hooks.slack.test/x", "trigger_id": "t"})
    resp = app.dispatch(BoltRequest(body=command, headers={"content-type": ["application/x-www-form-urlencoded"]}))
    assert resp.status == 200

    payload = {"type": "block_actions", "user": {"id": APPROVER}, "team": {"id": "T1"},
               "container": {"type": "message"}, "response_url": "https://hooks.slack.test/y",
               "message": {"blocks": []}, "trigger_id": "t", "channel": {"id": "C1"},
               "actions": [{"type": "button", "action_id": APPROVE_ACTION, "value": "rp-1", "block_id": "b"}]}
    resp = app.dispatch(BoltRequest(body=urlencode({"payload": json.dumps(payload)}),
                                    headers={"content-type": ["application/x-www-form-urlencoded"]}))
    assert resp.status == 200
    assert seen == [("request", "SGW 4.1.4", TPM), ("action", APPROVE_ACTION, "rp-1", APPROVER)]

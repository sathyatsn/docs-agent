"""Block Kit messages for release plans. Pure functions: no Slack or Jira calls."""

from typing import Callable

from src.release.models import PlannedIssue, ReleasePlan
from src.release.service import ApprovalResult, Prepared

MAX_SECTION = 2900  # Slack limit is 3000 chars per section text
APPROVE_ACTION, CANCEL_ACTION = "release_approve", "release_cancel"


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:MAX_SECTION]}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:MAX_SECTION]}]}


def _issue_line(issue: PlannedIssue, browse: Callable[[str], str], owner: Callable[[str], str]) -> str:
    if issue.existing_key:
        state = f":white_check_mark: <{browse(issue.existing_key)}|{issue.existing_key}> exists"
    else:
        state = ":new: create"
    who = owner(issue.assignee_id) if issue.assignee_id else ":warning: unassigned"
    due = f" · due {issue.due_date:%b %-d}" if issue.due_date else ""
    return f"{state} · {esc(issue.summary)} · {esc(who)}{due}"


def _chunks(lines: list[str]) -> list[str]:
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > MAX_SECTION:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    return chunks + ([current] if current else [])


def plan_blocks(prepared: Prepared, requester: str, browse: Callable[[str], str],
                owner: Callable[[str], str], ttl_hours: int) -> list[dict]:
    stored, plans = prepared.stored, prepared.plans
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Release ticket plan {stored.id}"}},
        _context(f"Requested by {requester} · approvers only · expires in {ttl_hours}h · "
                 "*Nothing is created in Jira until an approver clicks Approve.*"),
    ]
    for entry, plan in zip(stored.releases, plans):
        blocks.append({"type": "divider"})
        head = f"*{esc(plan.epic.summary)}* · project `{plan.epic.project}`"
        source = []
        if entry.get("evidence"):
            source.append(f"read from: “{esc(entry['evidence'])}”")
        if entry.get("ga_evidence"):
            source.append(f"GA from: “{esc(entry['ga_evidence'])}”")
        if source:
            head += "\n_" + " · ".join(source) + "_"
        blocks.append(_section(head))
        lines = [_issue_line(i, browse, owner) for i in [plan.epic, *plan.tasks]]
        blocks += [_section(chunk) for chunk in _chunks(lines)]
        flags = [f":warning: {esc(w)}" for w in plan.warnings]
        flags += [f":mag: {esc(n)}" for n in entry.get("notes", [])]
        if plan.blocked:
            flags.insert(0, ":no_entry: *Blocked:* nothing will be created for this release")
        if flags:
            blocks.append(_context("\n".join(flags)))

    to_create = sum(len(p.to_create) for p in plans if not p.blocked)
    blocks.append({"type": "divider"})
    if to_create == 0:
        blocks.append(_section("Nothing to create: every ticket already exists or the plan is blocked."))
        return blocks
    blocks.append({
        "type": "actions",
        "block_id": f"release_plan_{stored.id}",
        "elements": [
            {
                "type": "button", "style": "primary", "action_id": APPROVE_ACTION, "value": stored.id,
                "text": {"type": "plain_text", "text": f"Approve: create {to_create} issue(s)"},
                "confirm": {
                    "title": {"type": "plain_text", "text": "Create Jira tickets?"},
                    "text": {"type": "mrkdwn", "text": f"This creates {to_create} issue(s) in Jira exactly as listed."},
                    "confirm": {"type": "plain_text", "text": "Create"},
                    "deny": {"type": "plain_text", "text": "Back"},
                },
            },
            {"type": "button", "style": "danger", "action_id": CANCEL_ACTION, "value": stored.id,
             "text": {"type": "plain_text", "text": "Cancel"}},
        ],
    })
    return blocks


def result_blocks(result: ApprovalResult, plan_id: str, actor: str, browse: Callable[[str], str]) -> list[dict]:
    icon = {"applied": ":white_check_mark:", "cancelled": ":x:", "stale": ":arrows_counterclockwise:",
            "failed": ":rotating_light:", "blocked": ":no_entry:", "expired": ":hourglass:"}.get(result.status, ":information_source:")
    blocks = [_section(f"{icon} *Plan {plan_id}: {result.status}* by {actor}\n{esc(result.message)}")]
    if result.epic_keys:
        epics = "\n".join(f"• <{browse(k)}|{k}>" for k in result.epic_keys if k)
        blocks.append(_section(f"*Release epics*\n{epics}"))
    if result.created_keys:
        blocks.append(_context("Created: " + ", ".join(f"<{browse(k)}|{k}>" for k in result.created_keys)))
    return blocks


def rejection_text(rejected: list[str], examples: str) -> str:
    lines = ["I could not verify every part of that request, so I have not planned those releases:"]
    lines += [f"• {esc(r)}" for r in rejected]
    lines += ["", f"Write one release per line, for example:\n{examples}"]
    return "\n".join(lines)

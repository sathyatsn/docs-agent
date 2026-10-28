import hashlib
import json
import re
from datetime import timedelta

from src.release.catalog import Catalog
from src.release.jira_reader import JiraReader
from src.release.models import PlannedIssue, ReleasePlan, ReleaseRequest
from src.release.render import render_epic, render_task



def release_scope(catalog: Catalog, request: ReleaseRequest) -> tuple[str, ...]:
    """Platform subset that makes this a separate release (e.g. a .NET-only CBL drop)."""
    all_platforms = catalog.products[request.product].get("platforms", [])
    if not request.platforms or set(request.platforms) == set(all_platforms):
        return ()
    return tuple(sorted(request.platforms))


def release_label(request: ReleaseRequest, scope: tuple[str, ...] = ()) -> str:
    return "-".join(["release", request.product, request.version, *scope])


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _version_in(summary: str, version: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(version)}(?![\d.])", summary) is not None


def _display_title(catalog: Catalog, request: ReleaseRequest, platforms: list[str]) -> str:
    product = catalog.products[request.product]
    title = product["title"]
    all_platforms = product.get("platforms", [])
    if len(all_platforms) > 1 and set(platforms) != set(all_platforms):
        title += " " + "/".join(catalog.platforms[p] for p in platforms)
    return title


def find_existing_epics(jira: JiraReader, catalog: Catalog, request: ReleaseRequest) -> list[dict]:
    """Find release epics for this product+version, whether the agent or a human created them."""
    project = catalog.jira_for(request.product)["project"]
    scope = release_scope(catalog, request)
    by_label = jira.search(
        f'project = {project} AND issuetype = Epic AND labels = "{release_label(request, scope)}"',
        ["summary"],
    )
    legacy = jira.search(
        f'project = {project} AND issuetype = Epic AND summary ~ "Release Ticket" '
        f'AND summary ~ "\\"{request.version}\\""',
        ["summary"],
    )
    found = {i["key"]: i for i in by_label}
    for issue in legacy:
        summary = issue["fields"]["summary"]
        if re.search(r"duplicate", summary, re.IGNORECASE):
            continue
        if (catalog.product_for_summary(summary) == request.product
                and _version_in(summary, request.version)
                and catalog.scope_in_summary(request.product, summary) == scope):
            found.setdefault(issue["key"], issue)
    return list(found.values())


def plan_release(catalog: Catalog, request: ReleaseRequest, jira: JiraReader | None = None) -> ReleasePlan:
    """Build the full ticket set for a release, then mark whatever already exists in Jira."""
    if request.product not in catalog.products:
        raise KeyError(f"Unknown product '{request.product}'")
    product = catalog.products[request.product]
    platforms = request.platforms or list(product.get("platforms", []))
    unknown = set(platforms) - set(product.get("platforms", []))
    if unknown:
        raise ValueError(f"{product['title']} does not ship on: {', '.join(sorted(unknown))}")

    jira_cfg = catalog.jira_for(request.product)
    project = jira_cfg["project"]
    title = _display_title(catalog, request, platforms)
    labels = [jira_cfg["agent_label"], release_label(request, release_scope(catalog, request)),
              f"release-type-{request.release_type}"]
    kind = "Maintenance Release Ticket" if request.release_type == "maintenance" else "Release Ticket"
    epic_summary = f"{title} {kind} - {request.version}"
    if request.ga_date:
        epic_summary += f" - GA {request.ga_date:%b %-d, %Y}"

    manager = catalog.owner_for(request.product, "release_manager")
    epic = PlannedIssue(
        task_id=None,
        project=project,
        summary=epic_summary,
        description=render_epic(catalog, request, platforms),
        labels=labels,
        assignee_id=(manager or {}).get("account_id"),
        assignee_name=(manager or {}).get("name"),
        owner_function="release_manager",
        due_date=request.ga_date,
    )
    plan = ReleasePlan(request=request, epic=epic)

    for task_id in catalog.task_ids_for(request.product, request.release_type):
        task = catalog.tasks[task_id]
        summary = f"{task['summary']} - {title} {request.version}"
        if task_id in product.get("not_applicable", []):
            summary += " - N/A"
        owner = catalog.owner_for(request.product, task["owner"])
        if not owner:
            plan.warnings.append(f"No owner configured for '{task['owner']}' ({task['summary']}); left unassigned")
        due = request.ga_date + timedelta(days=task.get("due_offset_days", 0)) if request.ga_date else None
        plan.tasks.append(PlannedIssue(
            task_id=task_id,
            project=project,
            summary=summary,
            description=render_task(catalog, task_id, request, platforms),
            labels=labels,
            assignee_id=(owner or {}).get("account_id"),
            assignee_name=(owner or {}).get("name"),
            owner_function=task["owner"],
            due_date=due,
        ))

    if not request.ga_date:
        plan.warnings.append("No GA date given: new tickets will have no GA in the title and no due dates")
    if jira:
        _reconcile(plan, catalog, jira)
    return plan


def _reconcile(plan: ReleasePlan, catalog: Catalog, jira: JiraReader) -> None:
    request = plan.request
    if request.epic_key:
        epic_key = request.epic_key
    else:
        existing = find_existing_epics(jira, catalog, request)
        if len(existing) > 1:
            keys = ", ".join(sorted(i["key"] for i in existing))
            plan.warnings.append(f"Multiple release epics already exist ({keys}); "
                                 "close the duplicates or pass --epic to choose one")
            plan.blocked = True
            return
        if not existing:
            return
        epic_key = existing[0]["key"]
        plan.warnings.append(f"Release epic already exists: {epic_key} "
                             f"('{existing[0]['fields']['summary']}'); only missing tasks will be added")

    plan.epic.existing_key = epic_key
    if not request.epic_key:
        plan.epic.existing_summary = existing[0]["fields"]["summary"]
    children = jira.search(f"parent = {epic_key}", ["summary"])
    for task in plan.tasks:
        prefix = _normalize(catalog.tasks[task.task_id]["summary"])
        for child in children:
            if _normalize(child["fields"]["summary"]).startswith(prefix):
                task.existing_key = child["key"]
                task.existing_summary = child["fields"]["summary"]
                break


def plan_fingerprint(plans: list[ReleasePlan]) -> str:
    """Hash of everything a reviewer approves: which issues, their content, owners, dates and
    what already exists in Jira. If any of it changes before approval, the hash changes."""
    def issue(i: PlannedIssue) -> dict:
        return {
            "project": i.project, "task_id": i.task_id, "summary": i.summary,
            "description": i.description, "labels": i.labels, "assignee": i.assignee_id,
            "due": i.due_date.isoformat() if i.due_date else None, "existing": i.existing_key,
        }

    payload = [
        {"request": p.request.to_dict(), "blocked": p.blocked,
         "issues": [issue(i) for i in [p.epic, *p.tasks]]}
        for p in plans
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

import logging
import re
from datetime import timedelta

from src.release.catalog import Catalog
from src.release.jira_client import JiraClient
from src.release.models import PlannedIssue, ReleasePlan, ReleaseRequest
from src.release.render import render_epic, render_task

logger = logging.getLogger(__name__)


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


def find_existing_epics(jira: JiraClient, catalog: Catalog, request: ReleaseRequest) -> list[dict]:
    """Find release epics for this product+version, whether the agent or a human created them."""
    project = catalog.jira["project"]
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


def plan_release(catalog: Catalog, request: ReleaseRequest, jira: JiraClient | None = None) -> ReleasePlan:
    """Build the full ticket set for a release, then mark whatever already exists in Jira."""
    if request.product not in catalog.products:
        raise KeyError(f"Unknown product '{request.product}'")
    product = catalog.products[request.product]
    platforms = request.platforms or list(product.get("platforms", []))
    unknown = set(platforms) - set(product.get("platforms", []))
    if unknown:
        raise ValueError(f"{product['title']} does not ship on: {', '.join(sorted(unknown))}")

    title = _display_title(catalog, request, platforms)
    labels = [catalog.jira["agent_label"], release_label(request, release_scope(catalog, request)),
              f"release-type-{request.release_type}"]
    kind = "Maintenance Release Ticket" if request.release_type == "maintenance" else "Release Ticket"
    epic_summary = f"{title} {kind} - {request.version}"
    if request.ga_date:
        epic_summary += f" - GA {request.ga_date:%b %-d, %Y}"

    epic = PlannedIssue(
        task_id=None,
        summary=epic_summary,
        description=render_epic(catalog, request, platforms),
        labels=labels,
        assignee_id=catalog.owner_for(request.product, "release_manager"),
        owner_function="release_manager",
        due_date=request.ga_date,
    )
    plan = ReleasePlan(request=request, epic=epic)

    for task_id in catalog.task_ids_for(request.product, request.release_type):
        task = catalog.tasks[task_id]
        summary = f"{task['summary']} - {title} {request.version}"
        if task_id in product.get("not_applicable", []):
            summary += " - N/A"
        assignee = catalog.owner_for(request.product, task["owner"])
        if not assignee:
            plan.warnings.append(f"No owner configured for '{task['owner']}' ({task['summary']}); left unassigned")
        due = request.ga_date + timedelta(days=task.get("due_offset_days", 0)) if request.ga_date else None
        plan.tasks.append(PlannedIssue(
            task_id=task_id,
            summary=summary,
            description=render_task(catalog, task_id, request, platforms),
            labels=labels,
            assignee_id=assignee,
            owner_function=task["owner"],
            due_date=due,
        ))

    if not request.ga_date:
        plan.warnings.append("No GA date given: epic title and due dates will be blank")
    if jira:
        _reconcile(plan, catalog, jira)
    return plan


def _reconcile(plan: ReleasePlan, catalog: Catalog, jira: JiraClient) -> None:
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
    children = jira.search(f"parent = {epic_key}", ["summary"])
    for task in plan.tasks:
        prefix = _normalize(catalog.tasks[task.task_id]["summary"])
        for child in children:
            if _normalize(child["fields"]["summary"]).startswith(prefix):
                task.existing_key = child["key"]
                break


def apply_plan(plan: ReleasePlan, catalog: Catalog, jira: JiraClient) -> list[str]:
    """Create every planned issue that does not exist yet. Returns the new keys."""
    if plan.blocked:
        raise RuntimeError("Plan is blocked: " + "; ".join(plan.warnings))
    project = catalog.jira["project"]
    created = []

    epic = plan.epic
    if epic.is_new:
        epic.existing_key = jira.create_issue(
            project, catalog.jira["epic_issue_type"], epic.summary, epic.description,
            epic.labels, epic.assignee_id, epic.due_date,
        )
        created.append(epic.existing_key)
        logger.info("Created epic %s: %s", epic.existing_key, epic.summary)

    for task in plan.tasks:
        if not task.is_new:
            continue
        task.existing_key = jira.create_issue(
            project, catalog.jira["task_issue_type"], task.summary, task.description,
            task.labels, task.assignee_id, task.due_date, parent_key=epic.existing_key,
        )
        created.append(task.existing_key)
        logger.info("Created task %s: %s", task.existing_key, task.summary)
    return created

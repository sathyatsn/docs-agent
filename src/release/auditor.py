"""Audit open release epics against the catalog: duplicates, missing tasks, unassigned work."""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from src.release.catalog import Catalog
from src.release.jira_client import JiraClient
from src.release.models import release_type_for

VERSION_IN_SUMMARY = re.compile(r"(?<![\d.])(\d+\.\d+(?:\.\d+){0,2})(?![\d.])")


@dataclass
class EpicFinding:
    key: str
    summary: str
    product: str | None
    version: str | None
    issues: list[str] = field(default_factory=list)


def audit_open_releases(catalog: Catalog, jira: JiraClient) -> list[EpicFinding]:
    epics = []
    for project, products in catalog.projects().items():
        for epic in jira.search(
            f'project = {project} AND issuetype = Epic AND summary ~ "Release Ticket" '
            f"AND statusCategory != Done ORDER BY created DESC",
            ["summary", "duedate", "assignee"],
        ):
            epics.append((epic, products))
    findings: list[EpicFinding] = []
    by_release = defaultdict(list)

    for epic, project_products in epics:
        summary = epic["fields"]["summary"]
        match = VERSION_IN_SUMMARY.search(summary)
        product = catalog.product_for_summary(summary)
        finding = EpicFinding(
            key=epic["key"],
            summary=summary,
            product=product if product in project_products else None,
            version=match.group(1) if match else None,
        )
        findings.append(finding)
        if not finding.version:
            finding.issues.append("Template or placeholder epic (no version in title)")
            continue
        if not finding.product:
            finding.issues.append("Title does not match any catalog product")
            continue
        scope = catalog.scope_in_summary(finding.product, summary)
        by_release[(finding.product, finding.version, scope)].append(finding)
        if not epic["fields"].get("duedate"):
            finding.issues.append("No due date (GA) set on the epic")

        release_type = release_type_for(finding.version)
        expected = catalog.task_ids_for(finding.product, release_type)
        children = jira.search(f"parent = {epic['key']}", ["summary", "assignee"])
        child_names = [re.sub(r"[^a-z0-9]", "", c["fields"]["summary"].lower()) for c in children]
        for task_id in expected:
            prefix = re.sub(r"[^a-z0-9]", "", catalog.tasks[task_id]["summary"].lower())
            if not any(name.startswith(prefix) for name in child_names):
                finding.issues.append(f"Missing task: {catalog.tasks[task_id]['summary']}")
        for child in children:
            if not child["fields"].get("assignee"):
                finding.issues.append(f"Unassigned: {child['key']} {child['fields']['summary']}")

    for (product, version, _scope), group in by_release.items():
        if len(group) > 1:
            keys = ", ".join(f.key for f in group)
            for f in group:
                f.issues.insert(0, f"Possible duplicate of {product} {version}: {keys}")
    return findings


def format_audit(findings: list[EpicFinding]) -> str:
    lines = ["| Epic | Summary | Findings |", "| --- | --- | --- |"]
    for f in findings:
        if f.issues:
            lines.append(f"| {f.key} | {f.summary} | {'<br>'.join(f.issues)} |")
    clean = sum(1 for f in findings if not f.issues)
    lines.append("")
    lines.append(f"{len(findings)} open release epics, {clean} with no findings.")
    return "\n".join(lines)

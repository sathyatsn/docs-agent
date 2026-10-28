"""Render catalog checklists as Markdown descriptions for the Atlassian connector."""

from src.release.catalog import Catalog
from src.release.models import ReleaseRequest

NOT_DONE = "❌"
LEGEND = "❌ Not Done / ✅ Done / NA"
NOT_IN_RELEASE = "NA (platform not in this release)"
FOOTER = "*Created with the release-tickets skill from release_catalog.yaml.*"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _row(row) -> dict:
    return {"text": row} if isinstance(row, str) else row


def _status(row: dict, release_platforms: list[str]) -> str:
    tagged = row.get("platforms")
    if tagged and not set(tagged) & set(release_platforms):
        return NOT_IN_RELEASE
    return _cell(row.get("status", NOT_DONE))


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(r) + " |" for r in rows]


def _checklist(section: dict, release_platforms: list[str]) -> list[str]:
    columns = section["columns"]
    header = [columns[0], f"{columns[1]} ({LEGEND})", *columns[2:]]
    rows = []
    for raw in section.get("rows", []):
        row = _row(raw)
        rows.append([_cell(row["text"]), _status(row, release_platforms)] + [" "] * (len(columns) - 2))
    return _table(header, rows)


def _platform_matrix(section: dict, catalog: Catalog, request: ReleaseRequest,
                     release_platforms: list[str]) -> list[str]:
    rows = [[catalog.platforms[p], _cell(request.qe_matrix.get(p, "TBD"))] for p in release_platforms]
    return _table(section["columns"], rows)


def render_task(catalog: Catalog, task_id: str, request: ReleaseRequest,
                release_platforms: list[str]) -> str:
    out: list[str] = []
    for section in catalog.sections_for(task_id, request.product, request.release_type):
        if section.get("type") == "platform_matrix" and not release_platforms:
            continue
        if section.get("heading"):
            out.append(f"**{section['heading']}**")
            out.append("")
        if section.get("notes"):
            out += [f"- {n}" for n in section["notes"]]
            out.append("")
        if section.get("type") == "platform_matrix":
            out += _platform_matrix(section, catalog, request, release_platforms)
        elif section.get("columns"):
            out += _checklist(section, release_platforms)
        if section.get("notes_after"):
            out.append("")
            out += [f"- {n}" for n in section["notes_after"]]
        out.append("")
    out.append(FOOTER)
    return "\n".join(out)


def render_epic(catalog: Catalog, request: ReleaseRequest, release_platforms: list[str]) -> str:
    product = catalog.products[request.product]
    kind = "maintenance release" if request.release_type == "maintenance" else "release"
    lines = [
        f"Use this template for every {kind} of {product['name']}.",
        f"**Please do not modify without consultation with {product['process_owners']}.**",
        "This process is fixed and any changes should be understood by stakeholders.",
        "",
        "Release Tasks are outlined in child tickets of this Release epic.",
    ]
    details = []
    if release_platforms:
        details.append(f"- **Platforms in scope:** {', '.join(catalog.platforms[p] for p in release_platforms)}")
        skipped = [catalog.platforms[p] for p in product["platforms"] if p not in release_platforms]
        if skipped:
            details.append(f"- **Not in this release:** {', '.join(skipped)}")
    if request.ga_date:
        details.append(f"- **Target GA:** {request.ga_date:%b %-d, %Y}")
    if details:
        lines += [""] + details
    lines += ["", FOOTER]
    return "\n".join(lines)

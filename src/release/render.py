"""Render catalog checklists as Jira wiki markup (REST API v2 descriptions)."""

from src.release.catalog import Catalog
from src.release.models import ReleaseRequest

NOT_DONE = "(x)"
LEGEND = "(x) Not Done / (/) Done / NA"
AGENT_FOOTER = "_Created by release-agent from config/release_catalog.yaml._"


def _escape(text: str) -> str:
    for ch in "|[]{}":
        text = text.replace(ch, "\\" + ch)
    return text


def _row(row) -> dict:
    return {"text": row} if isinstance(row, str) else row


def _status(row: dict, release_platforms: list[str]) -> str:
    tagged = row.get("platforms")
    if tagged and not set(tagged) & set(release_platforms):
        return "NA (platform not in this release)"
    return _escape(row.get("status", NOT_DONE))


def _table(section: dict, release_platforms: list[str]) -> list[str]:
    columns = section["columns"]
    header = [columns[0], f"{columns[1]} ({LEGEND})", *columns[2:]]
    lines = ["||" + "||".join(header) + "||"]
    for raw in section.get("rows", []):
        row = _row(raw)
        cells = [_escape(row["text"]), _status(row, release_platforms)]
        cells += [" "] * (len(columns) - 2)
        lines.append("|" + "|".join(cells) + "|")
    return lines


def _platform_matrix(section: dict, catalog: Catalog, request: ReleaseRequest,
                     release_platforms: list[str]) -> list[str]:
    lines = ["||" + "||".join(section["columns"]) + "||"]
    for platform in release_platforms:
        value = request.qe_matrix.get(platform, "TBD")
        lines.append(f"|{catalog.platforms[platform]}|{_escape(value)}|")
    return lines


def render_task(catalog: Catalog, task_id: str, request: ReleaseRequest,
                release_platforms: list[str]) -> str:
    out: list[str] = []
    for section in catalog.sections_for(task_id, request.product, request.release_type):
        if section.get("type") == "platform_matrix" and not release_platforms:
            continue
        if section.get("heading"):
            out.append(f"*{section['heading']}*")
        out += [f"* {_escape(n)}" for n in section.get("notes", [])]
        if section.get("type") == "platform_matrix":
            out += _platform_matrix(section, catalog, request, release_platforms)
        elif section.get("columns"):
            out += _table(section, release_platforms)
        out += [f"* {_escape(n)}" for n in section.get("notes_after", [])]
        out.append("")
    out.append(AGENT_FOOTER)
    return "\n".join(out)


def render_epic(catalog: Catalog, request: ReleaseRequest, release_platforms: list[str]) -> str:
    product = catalog.products[request.product]
    kind = "maintenance release" if request.release_type == "maintenance" else "release"
    lines = [
        f"Use this template for every {kind} of {product['name']}.",
        f"*Please do not modify without consultation with {product['process_owners']}.*",
        "This process is fixed and any changes should be understood by stakeholders.",
        "",
        "Release Tasks are outlined in child tickets of this Release epic.",
    ]
    if release_platforms:
        names = ", ".join(catalog.platforms[p] for p in release_platforms)
        lines += ["", f"*Platforms in scope:* {names}"]
        skipped = [catalog.platforms[p] for p in product["platforms"] if p not in release_platforms]
        if skipped:
            lines.append(f"*Not in this release:* {', '.join(skipped)}")
    if request.ga_date:
        lines.append(f"*Target GA:* {request.ga_date:%b %-d, %Y}")
    lines += ["", AGENT_FOOTER]
    return "\n".join(lines)

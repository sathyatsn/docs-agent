"""Deterministic helper for the release-tickets skill. No network, no credentials.

Claude handles conversation and the Atlassian connector; this script does all
checking and produces every ticket field, so nothing in a ticket is model-written.

  verify    check Claude's reading of the request against the user's exact words
  plan      build the exact tickets; asks for Jira searches it still needs
  check     compare the issues actually created with the plan
  audit     report gaps in open release epics
  products  list products and platforms

Exit codes: 0 ok, 1 rejected/mismatch, 3 more Jira searches needed, 4 plan changed.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
BUNDLED = SKILL_DIR / "lib"                  # packaged skill (build_skill.py)
REPO_ROOT = SKILL_DIR.parent.parent.parent   # .claude/skills/release-tickets -> repo root
CODE_ROOT = BUNDLED if (BUNDLED / "src" / "release").is_dir() else REPO_ROOT
sys.path.insert(0, str(CODE_ROOT))

from src.release.auditor import audit_open_releases, format_audit  # noqa: E402
from src.release.catalog import Catalog  # noqa: E402
from src.release.grounding import ground_all  # noqa: E402
from src.release.jira_reader import RecordedJira  # noqa: E402
from src.release.models import ReleaseRequest  # noqa: E402
from src.release.planner import plan_fingerprint, plan_release  # noqa: E402

CATALOG = CODE_ROOT / "config" / "release_catalog.yaml"


def _load(path: str | None, default):
    if not path or not Path(path).exists():
        return default
    with open(path) as f:
        return json.load(f)


def _dump(obj, path: str | None = None) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if path:
        Path(path).write_text(text)
    else:
        print(text)


def _need_searches(missing: list[dict]) -> int:
    _dump({"status": "need_searches",
           "instructions": "Run each JQL with the Atlassian connector. Record the results in the searches "
                           "file as {id: [{key, summary, duedate, assignee}...]} (an empty list if nothing "
                           "matched), then re-run the same command.",
           "queries": missing})
    return 3


def cmd_verify(args, catalog: Catalog) -> int:
    text = Path(args.text_file).read_text()
    candidates = _load(args.candidates, [])
    if isinstance(candidates, dict):
        candidates = candidates.get("releases", [])
    today = date.fromisoformat(args.today) if args.today else date.today()
    accepted, rejected = ground_all(catalog, text, candidates, today)
    if not candidates and not rejected:
        rejected = ["No release found in the request."]
    result = {
        "ok": not rejected and bool(accepted),
        "rejected": rejected,
        "releases": [{"request": g.request.to_dict(), "evidence": g.evidence,
                      "ga_evidence": g.ga_evidence, "notes": g.notes} for g in accepted],
    }
    _dump(result, args.out)
    _dump({"ok": result["ok"], "rejected": rejected, "verified": len(accepted)})
    return 0 if result["ok"] else 1


def _issue_record(issue, catalog: Catalog, product: str) -> dict:
    jira_cfg = catalog.jira_for(product)
    return {
        "ref": "epic" if issue.task_id is None else issue.task_id,
        "action": "exists" if issue.existing_key else "create",
        "existing_key": issue.existing_key,
        "existing_summary": issue.existing_summary,
        "project": issue.project,
        "issue_type": jira_cfg["epic_issue_type"] if issue.task_id is None else jira_cfg["task_issue_type"],
        "summary": issue.summary,
        "description": issue.description,
        "assignee_account_id": issue.assignee_id,
        "assignee_name": issue.assignee_name,
        "due_date": issue.due_date.isoformat() if issue.due_date else None,
        "labels": issue.labels,
        "parent": None if issue.task_id is None else "epic",
    }


def _summary_markdown(plans, entries) -> str:
    lines = []
    for plan, entry in zip(plans, entries):
        lines.append(f"### {plan.epic.summary}  (project {plan.epic.project})")
        if entry.get("evidence"):
            src = f"Read from: “{entry['evidence']}”"
            if entry.get("ga_evidence"):
                src += f" · GA from: “{entry['ga_evidence']}”"
            lines.append(src)
        lines += ["", "| | Ticket | Owner | Due |", "|---|---|---|---|"]
        for issue in [plan.epic, *plan.tasks]:
            if issue.existing_key:
                # Show only what Jira says; the agent doesn't touch existing tickets.
                title = issue.existing_summary or issue.summary
                lines.append(f"| exists {issue.existing_key} | {title} | (unchanged) | (unchanged) |")
                continue
            owner = issue.assignee_name or "⚠ unassigned"
            due = issue.due_date.isoformat() if issue.due_date else "-"
            lines.append(f"| **create** | {issue.summary} | {owner} | {due} |")
        if plan.blocked:
            lines.append("\n⛔ **Blocked**: nothing will be created for this release.")
        lines += [f"- ⚠ {w}" for w in plan.warnings] + [f"- 🔎 {n}" for n in entry.get("notes", [])]
        lines.append("")
    total = sum(len(p.to_create) for p in plans if not p.blocked)
    lines.append(f"**{total} issue(s) to create.**" if total else "**Nothing to create.**")
    return "\n".join(lines)


def cmd_plan(args, catalog: Catalog) -> int:
    verified = _load(args.verified, None)
    if not verified or not verified.get("ok"):
        print("verified file missing or not ok; run `verify` first", file=sys.stderr)
        return 1
    jira = RecordedJira(_load(args.searches, {}))
    entries = verified["releases"]
    plans = [plan_release(catalog, ReleaseRequest.from_dict(e["request"]), jira) for e in entries]
    if jira.missing:
        return _need_searches(jira.missing)

    fingerprint = plan_fingerprint(plans)
    out = {
        "fingerprint": fingerprint,
        "to_create": sum(len(p.to_create) for p in plans if not p.blocked),
        "releases": [{
            "title": p.epic.summary,
            "blocked": p.blocked,
            "warnings": p.warnings,
            "notes": e.get("notes", []),
            "issues": [_issue_record(i, catalog, p.request.product) for i in [p.epic, *p.tasks]],
        } for p, e in zip(plans, entries)],
    }
    _dump(out, args.out)
    print(_summary_markdown(plans, entries))
    print(f"\nfingerprint: {fingerprint}")
    if args.expect_fingerprint and args.expect_fingerprint != fingerprint:
        print("\nPLAN CHANGED since it was shown to the user: show the new plan and ask again.")
        return 4
    return 0


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _missing_description_parts(planned: str, actual: str | None) -> list[str]:
    """Headings, bullet notes and checklist rows of the planned description absent from the actual one."""
    if actual is None:
        return ["(description was not read back)"]
    have = _norm(actual)
    parts = []
    for line in planned.splitlines():
        line = line.strip()
        if line.startswith("|") and not line.startswith("|---"):
            parts.append(line.strip("|").split("|")[0].replace("\\", ""))
        elif line.startswith("- ") or line.startswith("**"):
            parts.append(line.lstrip("- ").strip("*"))
    return [p.strip() for p in parts if _norm(p) and _norm(p) not in have]


def cmd_check(args, catalog: Catalog) -> int:
    plan = _load(args.plan, None)
    created = _load(args.created, [])
    by_summary = {}
    for issue in created:
        by_summary.setdefault(issue["summary"].strip(), []).append(issue)

    problems, ok = [], 0
    for release in plan["releases"]:
        if release["blocked"]:
            continue
        epic = release["issues"][0]
        epic_key = epic["existing_key"]
        for planned in release["issues"]:
            if planned["action"] != "create":
                continue
            matches = by_summary.get(planned["summary"], [])
            if len(matches) != 1:
                problems.append(f"{planned['summary']}: expected 1 created issue, found {len(matches)}")
                continue
            actual = matches[0]
            if planned["ref"] == "epic":
                epic_key = actual["key"]
            checks = {
                "project": (planned["project"], actual.get("project")),
                "issue type": (planned["issue_type"], actual.get("issue_type")),
                "assignee": (planned["assignee_account_id"], actual.get("assignee_account_id")),
                "due date": (planned["due_date"], actual.get("due_date")),
            }
            if planned["parent"]:
                checks["parent"] = (epic_key, actual.get("parent"))
            for name, (want, got) in checks.items():
                if want != got:
                    problems.append(f"{actual['key']} {planned['summary']}: {name} is {got!r}, expected {want!r}")
            missing_labels = set(planned["labels"]) - set(actual.get("labels") or [])
            if missing_labels:
                problems.append(f"{actual['key']}: missing labels {sorted(missing_labels)}")
            missing_text = _missing_description_parts(planned["description"], actual.get("description"))
            if missing_text:
                problems.append(f"{actual['key']}: description is missing {missing_text[:3]}")
            ok += 1

    _dump({"ok": not problems, "verified_issues": ok, "problems": problems})
    return 0 if not problems else 1


def cmd_audit(args, catalog: Catalog) -> int:
    jira = RecordedJira(_load(args.searches, {}))
    findings = audit_open_releases(catalog, jira)
    if jira.missing:
        return _need_searches(jira.missing)
    print(format_audit(findings))
    return 0


def cmd_products(args, catalog: Catalog) -> int:
    for key, p in catalog.products.items():
        platforms = ", ".join(catalog.platforms[x] for x in p.get("platforms", [])) or "-"
        print(f"- {p['title']} ({p['name']}): key `{key}`, project {catalog.jira_for(key)['project']}, "
              f"platforms: {platforms}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("verify")
    v.add_argument("--text-file", required=True, help="The user's request, verbatim")
    v.add_argument("--candidates", required=True, help="JSON list of candidate releases")
    v.add_argument("--out", required=True, help="Where to write verified releases")
    v.add_argument("--today", help="YYYY-MM-DD (defaults to today)")

    p = sub.add_parser("plan")
    p.add_argument("--verified", required=True)
    p.add_argument("--searches", required=True, help="JSON {query_id: [issues]} (created if absent)")
    p.add_argument("--out", required=True)
    p.add_argument("--expect-fingerprint", help="Fail with exit 4 if the plan differs from this")

    c = sub.add_parser("check")
    c.add_argument("--plan", required=True)
    c.add_argument("--created", required=True, help="JSON list of the created issues as read back from Jira")

    a = sub.add_parser("audit")
    a.add_argument("--searches", required=True)

    sub.add_parser("products")

    args = parser.parse_args()
    catalog = Catalog.load(str(CATALOG))
    return {"verify": cmd_verify, "plan": cmd_plan, "check": cmd_check,
            "audit": cmd_audit, "products": cmd_products}[args.command](args, catalog)


if __name__ == "__main__":
    sys.exit(main())

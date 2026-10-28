import argparse
import getpass
import logging
import sys
from datetime import date

import yaml
from dotenv import load_dotenv

from src.release.catalog import Catalog
from src.release.models import ReleasePlan, ReleaseRequest

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _jira(offline: bool):
    if offline:
        return None
    from src.release.jira_client import JiraClient
    return JiraClient()


def _request_from_dict(catalog: Catalog, item: dict, defaults: dict | None = None) -> ReleaseRequest:
    merged = {**(defaults or {}), **item}
    ga = merged.get("ga_date")
    return ReleaseRequest(
        product=catalog.resolve_product(merged["product"]),
        version=str(merged["version"]),
        ga_date=ga if isinstance(ga, date) or ga is None else date.fromisoformat(str(ga)),
        platforms=merged.get("platforms"),
        release_type=merged.get("release_type"),
        epic_key=merged.get("epic"),
        qe_matrix=merged.get("qe_matrix", {}),
    )


def print_plan(plan: ReleasePlan, show_descriptions: bool) -> None:
    r = plan.request
    print(f"\n=== {r.product} {r.version} ({r.release_type}) ===")
    rows = [plan.epic, *plan.tasks]
    for issue in rows:
        status = f"exists {issue.existing_key}" if issue.existing_key else "CREATE"
        due = issue.due_date.isoformat() if issue.due_date else "-"
        owner = issue.owner_function or "-"
        indent = "" if issue.task_id is None else "  └ "
        print(f"{indent}[{status:>14}] {issue.summary}  (owner: {owner}, due: {due})")
        if show_descriptions and issue.is_new:
            print("      " + issue.description.replace("\n", "\n      "))
    for warning in plan.warnings:
        print(f"  ! {warning}")
    if plan.blocked:
        print("  ✗ BLOCKED: nothing will be created for this release")


def _service(offline: bool):
    from src.release.plan_store import PlanStore
    from src.release.service import ReleaseService
    return ReleaseService(Catalog.load(), _jira(offline), PlanStore())


def run(releases: list, apply: bool, offline: bool, show_descriptions: bool, source: str,
        source_text: str = "") -> int:
    """Plan releases, store the plan for approval, and optionally apply it immediately."""
    service = _service(offline)
    prepared = service.prepare(releases, requested_by=getpass.getuser(), source=source,
                               source_text=source_text)
    for plan in prepared.plans:
        print_plan(plan, show_descriptions)
    for entry in prepared.stored.releases:
        for note in entry.get("notes", []):
            print(f"  ? {note}")

    total = sum(len(p.to_create) for p in prepared.plans if not p.blocked)
    if not apply:
        print(f"\nPlan {prepared.stored.id}: {total} issue(s) would be created. Nothing written yet.")
        if not offline and total:
            print(f"Create exactly this plan with: python release_agent.py approve {prepared.stored.id}")
        return 0
    if offline:
        print("--apply needs Jira access; drop --offline.")
        return 2
    return report(service.approve(prepared.stored.id, getpass.getuser()))


def report(result) -> int:
    print(result.message)
    for key in result.epic_keys:
        print(f"  epic: {key}")
    if result.created_keys:
        print(f"  created: {', '.join(result.created_keys)}")
    if result.replacement:
        for plan in result.replacement.plans:
            print_plan(plan, False)
        print(f"Review, then: python release_agent.py approve {result.replacement.stored.id}")
    return 0 if result.status in ("applied", "cancelled") else 1


def main():
    parser = argparse.ArgumentParser(description="Release Ticket Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--apply", action="store_true", help="Create issues (default: dry run)")
        p.add_argument("--offline", action="store_true", help="Plan without querying Jira")
        p.add_argument("--show-descriptions", action="store_true", help="Print rendered descriptions")

    create = sub.add_parser("create", help="Plan/create tickets for one release")
    create.add_argument("--product", required=True, help="Product key or name, e.g. sgw, 'Sync Gateway'")
    create.add_argument("--version", required=True)
    create.add_argument("--ga", help="GA date, YYYY-MM-DD")
    create.add_argument("--platforms", help="Comma-separated subset, e.g. dotnet or ios,android")
    create.add_argument("--release-type", choices=["major", "maintenance"])
    create.add_argument("--epic", help="Attach to this existing epic key")
    add_common(create)

    train = sub.add_parser("train", help="Plan/create a batch of releases from a YAML file")
    train.add_argument("file")
    add_common(train)

    intake = sub.add_parser("intake", help="Parse a free-text request with Claude into a plan for approval")
    intake.add_argument("text", help="Free text, or '-' to read stdin")
    intake.add_argument("--show-descriptions", action="store_true", help="Print rendered descriptions")

    approve = sub.add_parser("approve", help="Create the tickets of a reviewed plan, exactly as shown")
    approve.add_argument("plan_id")
    cancel = sub.add_parser("cancel", help="Cancel a pending plan")
    cancel.add_argument("plan_id")

    sub.add_parser("audit", help="Report duplicates, missing tasks and gaps on open release epics")
    sub.add_parser("products", help="List catalog products and platforms")

    args = parser.parse_args()
    catalog = Catalog.load()

    if args.command == "products":
        for key, p in catalog.products.items():
            print(f"{key:12} {p['name']:32} platforms: {', '.join(p.get('platforms', [])) or '-'}")
        return

    if args.command == "audit":
        from src.release.auditor import audit_open_releases, format_audit
        print(format_audit(audit_open_releases(catalog, _jira(False))))
        return

    if args.command in ("approve", "cancel"):
        service = _service(False)
        user = getpass.getuser()
        result = service.approve(args.plan_id, user) if args.command == "approve" else service.cancel(args.plan_id, user)
        sys.exit(report(result))

    if args.command == "intake":
        from src.release.intake import parse_release_text
        text = sys.stdin.read() if args.text == "-" else args.text
        result = parse_release_text(text, catalog)
        if result.rejected or not result.releases:
            print("Could not verify the whole request; nothing was planned:")
            for line in result.rejected or ["No release found."]:
                print(f"  ! {line}")
            sys.exit(1)
        sys.exit(run(result.releases, False, False, args.show_descriptions, "cli-intake", text))

    if args.command == "create":
        item = {"product": args.product, "version": args.version, "ga_date": args.ga,
                "release_type": args.release_type, "epic": args.epic,
                "platforms": args.platforms.split(",") if args.platforms else None}
        requests_ = [_request_from_dict(catalog, item)]
    else:
        with open(args.file) as f:
            data = yaml.safe_load(f)
        defaults = {k: v for k, v in data.items() if k != "releases"}
        requests_ = [_request_from_dict(catalog, item, defaults) for item in data["releases"]]

    sys.exit(run(requests_, args.apply, args.offline, args.show_descriptions, "cli"))


if __name__ == "__main__":
    main()

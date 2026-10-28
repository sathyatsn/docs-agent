import argparse
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


def run(requests_: list[ReleaseRequest], apply: bool, offline: bool, show_descriptions: bool) -> int:
    from src.release.planner import apply_plan, plan_release

    catalog = Catalog.load()
    jira = _jira(offline)
    plans = [plan_release(catalog, r, jira) for r in requests_]
    for plan in plans:
        print_plan(plan, show_descriptions)

    total = sum(len(p.to_create) for p in plans if not p.blocked)
    if not apply:
        print(f"\nDry run: {total} issue(s) would be created. Re-run with --apply to create them.")
        return 0
    if offline:
        print("--apply needs Jira access; drop --offline.")
        return 2

    failed = False
    for plan in plans:
        if plan.blocked:
            failed = True
            continue
        created = apply_plan(plan, catalog, jira)
        print(f"{plan.request.product} {plan.request.version}: created {len(created)} -> "
              f"{jira.browse_url(plan.epic.existing_key)}")
    return 1 if failed else 0


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

    intake = sub.add_parser("intake", help="Parse a free-text request with Claude, then plan/create")
    intake.add_argument("text", help="Free text, or '-' to read stdin")
    add_common(intake)

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

    if args.command == "create":
        item = {"product": args.product, "version": args.version, "ga_date": args.ga,
                "release_type": args.release_type, "epic": args.epic,
                "platforms": args.platforms.split(",") if args.platforms else None}
        requests_ = [_request_from_dict(catalog, item)]
    elif args.command == "train":
        with open(args.file) as f:
            data = yaml.safe_load(f)
        defaults = {k: v for k, v in data.items() if k != "releases"}
        requests_ = [_request_from_dict(catalog, item, defaults) for item in data["releases"]]
    else:
        from src.release.intake import parse_release_text
        text = sys.stdin.read() if args.text == "-" else args.text
        requests_, unparsed = parse_release_text(text, catalog)
        for line in unparsed:
            print(f"! Could not parse: {line}")
        if not requests_:
            sys.exit(1)

    sys.exit(run(requests_, args.apply, args.offline, args.show_descriptions))


if __name__ == "__main__":
    main()

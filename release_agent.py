"""Offline preview of release tickets from config/release_catalog.yaml.

Tickets are created by asking Claude (release-tickets skill + Atlassian
connector). This CLI needs no credentials: use it to preview what a release
will get, for example after editing the catalog.

  python release_agent.py products
  python release_agent.py preview --product sgw --version 4.1.4 --ga 2026-12-10
  python release_agent.py preview --product cbl --version 3.4.4 --platforms dotnet --show-descriptions
  python release_agent.py train releases/example-train.yaml
"""

import argparse
import sys
from datetime import date

import yaml

from src.release.catalog import Catalog
from src.release.models import ReleasePlan, ReleaseRequest
from src.release.planner import plan_release


def _request_from_dict(catalog: Catalog, item: dict, defaults: dict | None = None) -> ReleaseRequest:
    merged = {**(defaults or {}), **item}
    ga = merged.get("ga_date")
    return ReleaseRequest(
        product=catalog.resolve_product(merged["product"]),
        version=str(merged["version"]),
        ga_date=ga if isinstance(ga, date) or ga is None else date.fromisoformat(str(ga)),
        platforms=merged.get("platforms"),
        release_type=merged.get("release_type"),
        qe_matrix=merged.get("qe_matrix", {}),
    )


def print_plan(plan: ReleasePlan, show_descriptions: bool) -> None:
    r = plan.request
    print(f"\n=== {r.product} {r.version} ({r.release_type}) -> project {plan.epic.project} ===")
    for issue in [plan.epic, *plan.tasks]:
        due = issue.due_date.isoformat() if issue.due_date else "-"
        owner = issue.assignee_name or "UNASSIGNED"
        indent = "" if issue.task_id is None else "  └ "
        print(f"{indent}{issue.summary}  (owner: {owner}, due: {due})")
        if show_descriptions:
            print("      " + issue.description.replace("\n", "\n      "))
    for warning in plan.warnings:
        print(f"  ! {warning}")


def main():
    parser = argparse.ArgumentParser(description="Offline preview of release tickets")
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview", help="Preview the tickets for one release")
    preview.add_argument("--product", required=True, help="Product key or name, e.g. sgw, 'Sync Gateway'")
    preview.add_argument("--version", required=True)
    preview.add_argument("--ga", help="GA date, YYYY-MM-DD")
    preview.add_argument("--platforms", help="Comma-separated subset, e.g. dotnet or ios,android")
    preview.add_argument("--release-type", choices=["major", "maintenance"])
    preview.add_argument("--show-descriptions", action="store_true")

    train = sub.add_parser("train", help="Preview a batch of releases from a YAML file")
    train.add_argument("file")
    train.add_argument("--show-descriptions", action="store_true")

    sub.add_parser("products", help="List catalog products and platforms")

    args = parser.parse_args()
    catalog = Catalog.load()

    if args.command == "products":
        for key, p in catalog.products.items():
            print(f"{key:12} {p['name']:32} project: {catalog.jira_for(key)['project']:6} "
                  f"platforms: {', '.join(p.get('platforms', [])) or '-'}")
        return

    if args.command == "preview":
        item = {"product": args.product, "version": args.version, "ga_date": args.ga,
                "release_type": args.release_type,
                "platforms": args.platforms.split(",") if args.platforms else None}
        requests_ = [_request_from_dict(catalog, item)]
    else:
        with open(args.file) as f:
            data = yaml.safe_load(f)
        defaults = {k: v for k, v in data.items() if k != "releases"}
        requests_ = [_request_from_dict(catalog, item, defaults) for item in data["releases"]]

    plans = [plan_release(catalog, r) for r in requests_]
    for plan in plans:
        print_plan(plan, args.show_descriptions)
    print(f"\n{sum(len(p.to_create) for p in plans)} issue(s). Preview only: ask Claude to create them.")


if __name__ == "__main__":
    sys.exit(main())

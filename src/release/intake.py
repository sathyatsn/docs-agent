"""Turn free text (a Slack ask, release-planning notes) into ReleaseRequests with Claude.

Claude only parses intent. Ticket content always comes from the catalog, and
nothing is written to Jira until a human reviews the plan and passes --apply.
"""

import json
import os
from datetime import date

import anthropic

from src.release.catalog import Catalog
from src.release.models import ReleaseRequest

SYSTEM_PROMPT = """\
You extract software release requests for the Couchbase Mobile release team.
Each request names one product, one version and optionally a GA date and a
subset of platforms (for example "CBL .NET 4.1.4" means product cbl with
platforms [dotnet]). One sentence can contain several releases; return one
entry per product+version. Resolve relative or partial dates against today's
date and return them as YYYY-MM-DD. Leave a field null rather than guessing.
Anything you cannot map to a known product goes in `unparsed`.\
"""


def _schema(catalog: Catalog) -> dict:
    nullable = lambda schema: {"anyOf": [schema, {"type": "null"}]}  # noqa: E731
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["releases", "unparsed"],
        "properties": {
            "releases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["product", "version", "ga_date", "platforms"],
                    "properties": {
                        "product": {"type": "string", "enum": list(catalog.products)},
                        "version": {"type": "string"},
                        "ga_date": nullable({"type": "string"}),
                        "platforms": nullable({
                            "type": "array",
                            "items": {"type": "string", "enum": list(catalog.platforms)},
                        }),
                    },
                },
            },
            "unparsed": {"type": "array", "items": {"type": "string"}},
        },
    }


def parse_release_text(text: str, catalog: Catalog) -> tuple[list[ReleaseRequest], list[str]]:
    model = os.getenv("RELEASE_AGENT_MODEL", "claude-opus-5-5")
    client = anthropic.Anthropic()

    products = "\n".join(
        f"- {key}: {p['name']} (aka {', '.join(p.get('aliases', []))}); "
        f"platforms: {', '.join(p.get('platforms', [])) or 'n/a'}"
        for key, p in catalog.products.items()
    )
    user_prompt = f"Today is {date.today().isoformat()}.\n\nKnown products:\n{products}\n\nRequest:\n{text}"

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={
            "fallbacks": "default",
            "output_config": {"format": {"type": "json_schema", "schema": _schema(catalog)}},
        },
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to parse the release request")

    raw = next(b.text for b in response.content if b.type == "text")
    data = json.loads(raw)

    requests_, unparsed = [], list(data.get("unparsed", []))
    for item in data["releases"]:
        try:
            requests_.append(ReleaseRequest(
                product=item["product"],
                version=item["version"],
                ga_date=date.fromisoformat(item["ga_date"]) if item.get("ga_date") else None,
                platforms=item.get("platforms") or None,
            ))
        except ValueError as e:
            unparsed.append(f"{item}: {e}")
    return requests_, unparsed

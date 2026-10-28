"""Turn free text (a Slack ask, release-planning notes) into verified ReleaseRequests.

Claude only proposes how to split the text into releases. Every field it returns
must be backed by a verbatim quote and is re-checked by src/release/grounding.py;
anything unverifiable is rejected and sent back to the requester. Ticket content
always comes from the catalog, never from the model.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import date

import anthropic

from src.release.catalog import Catalog
from src.release.grounding import VERSION_TOKEN, GroundedRelease, ground_all

SYSTEM_PROMPT = """\
You extract software release requests for the Couchbase release team. The output
is checked word-for-word against the request, so copy, never paraphrase.

For each release the request asks for, return one entry with:
- product: the catalog key of the product named for that version.
- version: the version exactly as written.
- platforms: only if the request limits that release to specific platforms
  (e.g. "CBL .NET 4.1.4" -> ["dotnet"]); otherwise null.
- ga_date: YYYY-MM-DD only if a GA/release date is stated for it; resolve
  partial or relative dates against today's date. Otherwise null.
- evidence: the shortest verbatim span of the request that contains the
  product name and the version.
- ga_date_evidence: the verbatim span that states the date, or null.

Do not invent releases, versions, dates or platforms. If something is unclear,
leave it out and describe it in `unclear`.\
"""


@dataclass
class IntakeResult:
    releases: list[GroundedRelease] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def _schema(catalog: Catalog) -> dict:
    def nullable(schema: dict) -> dict:
        return {"anyOf": [schema, {"type": "null"}]}

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["releases", "unclear"],
        "properties": {
            "releases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["product", "version", "platforms", "ga_date", "evidence", "ga_date_evidence"],
                    "properties": {
                        "product": {"type": "string", "enum": list(catalog.products)},
                        "version": {"type": "string"},
                        "platforms": nullable({
                            "type": "array",
                            "items": {"type": "string", "enum": list(catalog.platforms)},
                        }),
                        "ga_date": nullable({"type": "string"}),
                        "evidence": {"type": "string"},
                        "ga_date_evidence": nullable({"type": "string"}),
                    },
                },
            },
            "unclear": {"type": "array", "items": {"type": "string"}},
        },
    }


def _ask_claude(text: str, catalog: Catalog, today: date) -> dict:
    model = os.getenv("RELEASE_AGENT_MODEL", "claude-opus-5-5")
    client = anthropic.Anthropic()
    products = "\n".join(
        f"- {key}: {p['name']} (also called: {', '.join(sorted(catalog.product_names(key)))}); "
        f"platforms: {', '.join(p.get('platforms', [])) or 'n/a'}"
        for key, p in catalog.products.items()
    )
    user_prompt = (f"Today is {today:%A} {today.isoformat()}.\n\nKnown products:\n{products}\n\n"
                   f"<request>\n{text}\n</request>")

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
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Release request too long to parse; split it into smaller messages")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def parse_release_text(text: str, catalog: Catalog, today: date | None = None) -> IntakeResult:
    today = today or date.today()
    if not VERSION_TOKEN.search(text):
        return IntakeResult(rejected=["No version number found. Example: 'SGW 4.1.4 GA Dec 10'"])

    data = _ask_claude(text, catalog, today)
    releases, rejected = ground_all(catalog, text, data.get("releases", []), today)
    rejected += [f"Unclear: {u}" for u in data.get("unclear", [])]
    return IntakeResult(releases=releases, rejected=rejected)

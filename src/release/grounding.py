"""Deterministic checks that every field Claude extracted is backed by the user's own words.

Claude proposes; this module verifies. A candidate release is accepted only if:
  * its evidence quote appears verbatim in the request text,
  * the version appears in that quote,
  * the product named closest before that version in the text is the claimed product,
  * the platforms named around that version are exactly the claimed platforms,
  * the GA date agrees with the day/month/year written in the text.
Anything else is rejected with a reason the requester can act on. Nothing is guessed.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from src.release.catalog import Catalog
from src.release.models import VERSION_RE, ReleaseRequest

VERSION_TOKEN = re.compile(r"(?<![\d.])\d+\.\d+(?:\.\d+){0,2}(?!\w)(?!\.\d)")
SEPARATORS = re.compile(r"[\n;]")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
SLASH_DATE = re.compile(r"\b\d{1,2}/\d{1,2}\b")
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_TOKEN = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?", re.IGNORECASE)
MAX_DAYS_AHEAD = 400
MAX_DAYS_PAST = 30


@dataclass
class GroundedRelease:
    request: ReleaseRequest
    evidence: str
    ga_evidence: str | None = None
    notes: list[str] = field(default_factory=list)


def _locate(text: str, quote: str) -> int:
    """Index of `quote` in `text`, ignoring case and runs of whitespace; -1 if absent."""
    if not quote or not quote.strip():
        return -1
    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    m = re.search(pattern, text, re.IGNORECASE)
    return m.start() if m else -1


def _segment(text: str, version_start: int, version_end: int) -> str:
    """Text that belongs to one release: bounded by neighbouring versions and line/; breaks."""
    start, end = 0, len(text)
    for m in VERSION_TOKEN.finditer(text):
        if m.end() <= version_start:
            start = max(start, m.end())
        elif m.start() >= version_end:
            end = min(end, m.start())
            break
    for m in SEPARATORS.finditer(text):
        if m.end() <= version_start:
            start = max(start, m.end())
        elif m.start() >= version_end:
            end = min(end, m.start())
            break
    return text[start:end]


def _check_date(ga: date, quote: str, today: date) -> str | None:
    """Return an error if the date contradicts what is written, else None."""
    if ga > today + timedelta(days=MAX_DAYS_AHEAD):
        return f"GA date {ga} is more than {MAX_DAYS_AHEAD} days away"
    if ga < today - timedelta(days=MAX_DAYS_PAST):
        return f"GA date {ga} is in the past"
    if SLASH_DATE.search(quote):
        return f"'{quote}' is ambiguous (day/month order); write it as e.g. 'Dec 10' or 2026-12-10"
    iso = ISO_DATE.search(quote)
    if iso:
        if date(*map(int, iso.groups())) != ga:
            return f"GA date {ga} does not match '{iso.group(0)}'"
        return None
    months = {MONTHS[m.group(1).lower()] for m in MONTH_TOKEN.finditer(quote)}
    if months and ga.month not in months:
        return f"GA date {ga} does not match the month in '{quote}'"
    without_versions = VERSION_TOKEN.sub(" ", quote)
    years = {int(y) for y in re.findall(r"\b(20\d{2})\b", without_versions)}
    if years and ga.year not in years:
        return f"GA date {ga} does not match the year in '{quote}'"
    days = {int(d) for d in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", without_versions) if 1 <= int(d) <= 31}
    if days and ga.day not in days:
        return f"GA date {ga} does not match the day in '{quote}'"
    return None


def ground(catalog: Catalog, text: str, candidate: dict, today: date | None = None) -> GroundedRelease | str:
    """Verify one candidate from Claude. Returns a GroundedRelease, or a rejection reason."""
    today = today or date.today()
    label = f"{candidate.get('product')} {candidate.get('version')}"
    evidence = candidate.get("evidence") or ""
    version = (candidate.get("version") or "").strip()
    product = candidate.get("product")

    ev_start = _locate(text, evidence)
    if ev_start < 0:
        return f"{label}: the quoted text '{evidence}' is not in the request"
    if product not in catalog.products:
        return f"{label}: unknown product"
    if not VERSION_RE.match(version):
        return f"{label}: '{version}' is not a valid version"

    version_match = None
    for m in VERSION_TOKEN.finditer(text, ev_start):
        if m.group(0) == version:
            version_match = m
            break
    if not version_match or version_match.start() > ev_start + len(evidence) + 1:
        return f"{label}: version {version} is not written in the request"

    named = catalog.product_mentioned_before(text, version_match.start())
    if named is None:
        return f"{label}: no product is named before {version}; write it as '<product> {version}'"
    if named != product:
        return (f"{label}: the request names {catalog.products[named]['title']} before {version}, "
                f"not {catalog.products[product]['title']}")

    segment = _segment(text, version_match.start(), version_match.end())
    claimed = set(candidate.get("platforms") or [])
    written = catalog.platforms_mentioned(product, segment)
    all_platforms = set(catalog.products[product].get("platforms", []))
    if claimed - all_platforms:
        return f"{label}: {catalog.products[product]['title']} does not ship on {', '.join(sorted(claimed - all_platforms))}"
    if len(all_platforms) > 1 and claimed != written and not (not claimed and written == all_platforms):
        said = ", ".join(catalog.platforms[p] for p in sorted(written)) or "no platforms"
        return (f"{label}: unclear platform scope (the request mentions {said} near {version}); "
                f"put each release on its own line, e.g. '{catalog.products[product]['title']} .NET {version}' "
                f"for a single platform or '{catalog.products[product]['title']} {version}' for all")

    ga = None
    ga_evidence = candidate.get("ga_date_evidence")
    notes = []
    if candidate.get("ga_date"):
        try:
            ga = date.fromisoformat(candidate["ga_date"])
        except ValueError:
            return f"{label}: '{candidate['ga_date']}' is not a date"
        if _locate(text, ga_evidence or "") < 0:
            return f"{label}: the GA date is not backed by text in the request"
        error = _check_date(ga, ga_evidence, today)
        if error:
            return f"{label}: {error}"
        has_concrete = ISO_DATE.search(ga_evidence) or MONTH_TOKEN.search(ga_evidence)
        if not has_concrete:
            notes.append(f"GA date {ga:%a %b %-d, %Y} was worked out from '{ga_evidence}'; please check it")

    try:
        request = ReleaseRequest(product=product, version=version, ga_date=ga,
                                 platforms=sorted(claimed) or None)
    except ValueError as e:
        return f"{label}: {e}"
    return GroundedRelease(request=request, evidence=evidence, ga_evidence=ga_evidence, notes=notes)


def ground_all(catalog: Catalog, text: str, candidates: list[dict],
               today: date | None = None) -> tuple[list[GroundedRelease], list[str]]:
    accepted, rejected, seen = [], [], set()
    for candidate in candidates:
        result = ground(catalog, text, candidate, today)
        if isinstance(result, str):
            rejected.append(result)
            continue
        r = result.request
        key = (r.product, r.version, tuple(r.platforms or ()))
        if key in seen:
            continue
        seen.add(key)
        accepted.append(result)

    # Every version written in the request must be accounted for.
    accounted = {g.request.version for g in accepted} | {c.get("version") for c in candidates}
    for m in VERSION_TOKEN.finditer(text):
        if m.group(0) not in accounted:
            rejected.append(f"{m.group(0)}: mentioned in the request but not understood as a release")
    return accepted, rejected

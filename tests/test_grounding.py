"""Grounding: every field Claude returns must be backed by the request text.

The candidate dicts below stand in for Claude output, including deliberately
wrong ("hallucinated") outputs that must be rejected.
"""

from datetime import date

import pytest

from src.release.catalog import Catalog
from src.release.grounding import ground, ground_all

TODAY = date(2026, 10, 28)


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load("config/release_catalog.yaml")


def cand(product, version, evidence, platforms=None, ga=None, ga_ev=None):
    return {"product": product, "version": version, "evidence": evidence, "platforms": platforms,
            "ga_date": ga, "ga_date_evidence": ga_ev}


def test_simple_request(catalog):
    text = "Please cut SGW 4.1.4 GA Dec 10"
    g = ground(catalog, text, cand("sgw", "4.1.4", "SGW 4.1.4", ga="2026-12-10", ga_ev="GA Dec 10"), TODAY)
    assert g.request.product == "sgw" and g.request.ga_date == date(2026, 12, 10)
    assert g.notes == []


def test_shared_date_and_version_list(catalog):
    text = "SGW 4.1.4 and 4.0.10 GA Dec 10"
    accepted, rejected = ground_all(catalog, text, [
        cand("sgw", "4.1.4", "SGW 4.1.4", ga="2026-12-10", ga_ev="GA Dec 10"),
        cand("sgw", "4.0.10", "4.0.10", ga="2026-12-10", ga_ev="GA Dec 10"),
    ], TODAY)
    assert rejected == []
    assert [g.request.version for g in accepted] == ["4.1.4", "4.0.10"]


def test_platform_scoped_line(catalog):
    text = "SGW 4.1.4 GA Dec 10\nCBL .NET 3.4.4 GA Dec 12"
    accepted, rejected = ground_all(catalog, text, [
        cand("sgw", "4.1.4", "SGW 4.1.4", ga="2026-12-10", ga_ev="GA Dec 10"),
        cand("cbl", "3.4.4", "CBL .NET 3.4.4", platforms=["dotnet"], ga="2026-12-12", ga_ev="GA Dec 12"),
    ], TODAY)
    assert rejected == []
    assert accepted[1].request.platforms == ["dotnet"]


def test_cbl_js_is_not_cbl(catalog):
    text = "CBL 4.1.4 and CBL JS 1.0.4"
    accepted, rejected = ground_all(catalog, text, [
        cand("cbl", "4.1.4", "CBL 4.1.4"), cand("cbl-js", "1.0.4", "CBL JS 1.0.4")], TODAY)
    assert rejected == [] and [g.request.product for g in accepted] == ["cbl", "cbl-js"]


def test_full_product_name(catalog):
    text = "Couchbase Lite for JavaScript 1.0.4"
    g = ground(catalog, text, cand("cbl-js", "1.0.4", text), TODAY)
    assert not isinstance(g, str)


# --- hallucinations that must be rejected --------------------------------------------------

def test_rejects_invented_version(catalog):
    r = ground(catalog, "SGW 4.1.4 GA Dec 10", cand("sgw", "4.1.5", "SGW 4.1.4"), TODAY)
    assert "not written in the request" in r


def test_rejects_quote_not_in_text(catalog):
    r = ground(catalog, "SGW 4.1.4", cand("sgw", "4.1.4", "Sync Gateway 4.1.4"), TODAY)
    assert "is not in the request" in r


def test_rejects_wrong_product(catalog):
    r = ground(catalog, "CBL JS 1.0.4", cand("cbl", "1.0.4", "CBL JS 1.0.4"), TODAY)
    assert "names CBL JS before 1.0.4" in r


def test_rejects_invented_platform(catalog):
    r = ground(catalog, "CBL 4.1.4", cand("cbl", "4.1.4", "CBL 4.1.4", platforms=["ios"]), TODAY)
    assert "unclear platform scope" in r


def test_rejects_dropped_platform_scope(catalog):
    # The text limits the release to .NET; silently planning a full release would be wrong.
    r = ground(catalog, "CBL .NET 4.1.4", cand("cbl", "4.1.4", "CBL .NET 4.1.4"), TODAY)
    assert "unclear platform scope" in r


def test_rejects_platform_product_does_not_ship(catalog):
    r = ground(catalog, "SGW iOS 4.1.4", cand("sgw", "4.1.4", "SGW iOS 4.1.4", platforms=["ios"]), TODAY)
    assert "does not ship on ios" in r


def test_rejects_wrong_day(catalog):
    r = ground(catalog, "SGW 4.1.4 GA Dec 10",
               cand("sgw", "4.1.4", "SGW 4.1.4", ga="2026-12-01", ga_ev="GA Dec 10"), TODAY)
    assert "does not match the day" in r


def test_rejects_wrong_month(catalog):
    r = ground(catalog, "SGW 4.1.4 GA Dec 10",
               cand("sgw", "4.1.4", "SGW 4.1.4", ga="2026-11-10", ga_ev="GA Dec 10"), TODAY)
    assert "does not match the month" in r


def test_rejects_date_without_text(catalog):
    r = ground(catalog, "SGW 4.1.4", cand("sgw", "4.1.4", "SGW 4.1.4", ga="2026-12-10", ga_ev="Dec 10"), TODAY)
    assert "not backed by text" in r


def test_rejects_ambiguous_numeric_date(catalog):
    r = ground(catalog, "SGW 4.1.4 GA 12/10",
               cand("sgw", "4.1.4", "SGW 4.1.4", ga="2026-12-10", ga_ev="GA 12/10"), TODAY)
    assert "ambiguous" in r


def test_rejects_past_date(catalog):
    r = ground(catalog, "SGW 4.1.4 GA 2025-12-10",
               cand("sgw", "4.1.4", "SGW 4.1.4", ga="2025-12-10", ga_ev="GA 2025-12-10"), TODAY)
    assert "in the past" in r


def test_relative_date_is_flagged_for_review(catalog):
    g = ground(catalog, "SGW 4.1.4 GA next Friday",
               cand("sgw", "4.1.4", "SGW 4.1.4", ga="2026-11-06", ga_ev="next Friday"), TODAY)
    assert "please check it" in g.notes[0]


def test_rejects_version_without_product(catalog):
    r = ground(catalog, "cut 4.1.4 please", cand("sgw", "4.1.4", "4.1.4"), TODAY)
    assert "no product is named" in r


def test_flags_versions_claude_skipped(catalog):
    _, rejected = ground_all(catalog, "SGW 4.1.4 and SGW 4.0.10",
                             [cand("sgw", "4.1.4", "SGW 4.1.4")], TODAY)
    assert rejected == ["4.0.10: mentioned in the request but not understood as a release"]


def test_duplicates_are_collapsed(catalog):
    accepted, rejected = ground_all(catalog, "SGW 4.1.4", [
        cand("sgw", "4.1.4", "SGW 4.1.4"), cand("sgw", "4.1.4", "SGW 4.1.4")], TODAY)
    assert len(accepted) == 1 and rejected == []

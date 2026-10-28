import json
from datetime import date
from types import SimpleNamespace

import pytest

from src.release import intake
from src.release.catalog import Catalog

TODAY = date(2026, 10, 28)


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load("config/release_catalog.yaml")


def fake_claude(monkeypatch, payload: dict, calls: list):
    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            block = SimpleNamespace(type="text", text=json.dumps(payload))
            return SimpleNamespace(stop_reason="end_turn", content=[block])

    monkeypatch.setattr(intake.anthropic, "Anthropic", lambda: SimpleNamespace(messages=Messages()))


def test_no_version_skips_claude(catalog, monkeypatch):
    calls = []
    fake_claude(monkeypatch, {"releases": [], "unclear": []}, calls)
    result = intake.parse_release_text("can we do a release soon?", catalog, TODAY)
    assert calls == [] and result.releases == [] and result.rejected


def test_request_shape_and_grounded_result(catalog, monkeypatch):
    calls = []
    fake_claude(monkeypatch, {"releases": [{
        "product": "sgw", "version": "4.1.4", "platforms": None, "ga_date": "2026-12-10",
        "evidence": "SGW 4.1.4", "ga_date_evidence": "GA Dec 10"}], "unclear": []}, calls)
    result = intake.parse_release_text("SGW 4.1.4 GA Dec 10", catalog, TODAY)
    assert result.rejected == [] and result.releases[0].request.ga_date == date(2026, 12, 10)

    sent = calls[0]
    assert sent["model"] == "claude-opus-5-5"
    fmt = sent["extra_body"]["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert "tool_choice" not in sent  # forced tool use is rejected by current models
    assert "<request>\nSGW 4.1.4 GA Dec 10\n</request>" in sent["messages"][0]["content"]


def test_hallucinated_reply_is_rejected(catalog, monkeypatch):
    fake_claude(monkeypatch, {"releases": [{
        "product": "sgw", "version": "4.1.5", "platforms": None, "ga_date": "2026-12-11",
        "evidence": "SGW 4.1.4", "ga_date_evidence": "GA Dec 10"}], "unclear": []}, [])
    result = intake.parse_release_text("SGW 4.1.4 GA Dec 10", catalog, TODAY)
    assert result.releases == []
    assert any("4.1.5" in r for r in result.rejected)
    assert any("4.1.4: mentioned in the request but not understood" in r for r in result.rejected)


def test_unclear_items_block_the_request(catalog, monkeypatch):
    fake_claude(monkeypatch, {"releases": [], "unclear": ["'the usual SDK bump' is not a product"]}, [])
    result = intake.parse_release_text("the usual SDK bump 3.2", catalog, TODAY)
    assert result.rejected[0].startswith("Unclear:") or any("Unclear" in r for r in result.rejected)

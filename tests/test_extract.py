"""Offline unit tests for src/extract.py.

No network calls are made: every function that would contact Etimad is
replaced with a stub. Run from the project root with:

    python -m pytest tests -v
"""
import json

import pytest

import extract


# ── Reading the tender list from a response ──────────────────────────────
def test_extract_items_reads_the_data_key():
    payload = {"data": [{"tenderId": 1}, {"tenderId": 2}], "totalCount": 2}
    assert extract.extract_items(payload) == [{"tenderId": 1}, {"tenderId": 2}]


def test_extract_items_accepts_a_plain_list():
    assert extract.extract_items([{"tenderId": 1}]) == [{"tenderId": 1}]


def test_extract_items_returns_empty_list_for_unexpected_payloads():
    assert extract.extract_items({"other": []}) == []
    assert extract.extract_items(None) == []


def test_get_total_count():
    assert extract.get_total_count({"totalCount": 7109}) == 7109
    assert extract.get_total_count([]) is None


# ── Parsing the detail-section HTML ──────────────────────────────────────
DETAIL_HTML = """
<ul>
  <li class="list-group-item">
    <div class="etd-item-title">Place of execution</div>
    <div class="etd-item-info">Riyadh</div>
  </li>
  <li class="list-group-item">
    <div class="etd-item-title">Last date for offers</div>
    <div class="etd-item-info"><span>2026-09-20</span><span>1448-04-09</span></div>
  </li>
  <li class="list-group-item">
    <div class="etd-item-title">Row without a value</div>
  </li>
</ul>
"""


def test_parse_view_component_html_reads_label_value_pairs():
    data = extract.parse_view_component_html(DETAIL_HTML)
    assert data["Place of execution"] == "Riyadh"


def test_parse_view_component_html_joins_multiple_spans():
    data = extract.parse_view_component_html(DETAIL_HTML)
    assert data["Last date for offers"] == "2026-09-20 | 1448-04-09"


def test_parse_view_component_html_skips_incomplete_rows():
    data = extract.parse_view_component_html(DETAIL_HTML)
    assert "Row without a value" not in data


def test_parse_view_component_html_empty_fragment():
    assert extract.parse_view_component_html("") == {}


# ── Paging and de-duplication ────────────────────────────────────────────
@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda seconds: None)


def test_run_extraction_deduplicates_and_stops_on_empty_page(monkeypatch, no_sleep):
    pages = {
        1: {"data": [{"tenderId": 1}, {"tenderId": 2}], "totalCount": 3},
        2: {"data": [{"tenderId": 2}, {"tenderId": 3}]},  # tender 2 appears again
        3: {"data": []},                                   # empty page: stop here
        4: {"data": [{"tenderId": 99}]},                   # must never be read
    }
    requested = []

    def fake_fetch_page(page_number, *args, **kwargs):
        requested.append(page_number)
        return pages[page_number]

    monkeypatch.setattr(extract, "fetch_page", fake_fetch_page)
    records = extract.run_extraction(pages_to_scrape=10)

    assert [r["tenderId"] for r in records] == [1, 2, 3]
    assert requested == [1, 2, 3]


def test_run_extraction_respects_the_page_limit(monkeypatch, no_sleep):
    monkeypatch.setattr(
        extract, "fetch_page",
        lambda page_number, *a, **k: {"data": [{"tenderId": page_number}]},
    )
    records = extract.run_extraction(pages_to_scrape=2)
    assert [r["tenderId"] for r in records] == [1, 2]


# ── Incremental tender details ───────────────────────────────────────────
def test_fetch_missing_tender_details_only_fetches_new_tenders(tmp_path, monkeypatch, no_sleep):
    store_path = tmp_path / "tender_details_by_id.json"
    store_path.write_text(json.dumps({"1": {"dates": {"x": "already fetched"}}}), encoding="utf-8")
    monkeypatch.setattr(extract, "DETAILS_STORE_PATH", store_path)

    fetched = []

    def fake_fetch_detail(url, tender_id_str):
        fetched.append(tender_id_str)
        return {"ok": "yes"}

    monkeypatch.setattr(extract, "fetch_detail", fake_fetch_detail)
    records = [
        {"tenderId": 1, "tenderIdString": "A"},  # already in the store
        {"tenderId": 2, "tenderIdString": "B"},  # new
    ]
    store = extract.fetch_missing_tender_details(records)

    assert set(fetched) == {"B"}                                  # only the new tender
    assert len(fetched) == len(extract.DETAIL_ENDPOINTS)          # all four sections
    assert store["1"] == {"dates": {"x": "already fetched"}}      # existing entry untouched
    assert set(store["2"]) == set(extract.DETAIL_ENDPOINTS)
    assert json.loads(store_path.read_text(encoding="utf-8")) == store  # saved to disk


def test_fetch_missing_tender_details_respects_max_new(tmp_path, monkeypatch, no_sleep):
    monkeypatch.setattr(extract, "DETAILS_STORE_PATH", tmp_path / "store.json")
    monkeypatch.setattr(extract, "fetch_detail", lambda url, tid: {})
    records = [{"tenderId": i, "tenderIdString": str(i)} for i in range(5)]

    store = extract.fetch_missing_tender_details(records, max_new=2)
    assert set(store) == {"0", "1"}

"""
Task 1 — Extract
=================
Retrieves tender records from the Etimad public tenders portal and saves them,
unmodified, to `data/raw/`. Cleaning, translation, and agency classification are
handled in Task 2 (`02_profile_clean.py`).

Data source: internal JSON endpoint used by the Etimad public listing page
(see README.md -> Source Definition for endpoint details, auth, rate limits,
and licence notes).
"""

# ── 1. Imports ────────────────────────────────────────────────────────────
import requests
import json
import time
from datetime import date
from pathlib import Path
from bs4 import BeautifulSoup


# ── 2. Configuration ─────────────────────────────────────────────────────
BASE_URL = "https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync"

PAGE_SIZE = 100
PAGES_TO_SCRAPE = 20
PUBLISH_DATE_ID = 5  # matches the default filter applied on the public listing page

RAW_DIR = Path("../data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)
TODAY = date.today().isoformat()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=1",
}


# ── 3. Fetch a single page ───────────────────────────────────────────────
# Requests can return 429 Too Many Requests under rapid successive calls.
# Retries with exponential backoff on 429, and a short backoff on generic
# connection errors.
#
# NOTE (2026-09-19): after an accidental burst of ~9 concurrent runs hit this
# endpoint at once, Etimad's rate limiter stayed rejecting requests far longer
# than the original 5s/10s/15s/20s backoff could ride out — even a single,
# well-behaved run kept failing after 4 attempts. Backoff and retry count were
# both increased so a single clean run has a much better chance of riding out
# a lingering throttle on its own, without needing another manual retry cycle.
def fetch_page(page_number, page_size=PAGE_SIZE, max_retries=6):
    params = {
        "PageSize": page_size,
        "PublishDateId": PUBLISH_DATE_ID,
        "pageNumber": page_number,
    }

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError:
            if response.status_code in (429, 400):
                wait_time = 20 * attempt
                print(f"Page {page_number}: HTTP {response.status_code} — retrying in {wait_time}s "
                      f"(attempt {attempt}/{max_retries})")
                time.sleep(wait_time)
            else:
                print(f"Page {page_number}: HTTP {response.status_code} — aborting")
                print("Response body:", response.text[:500])
                raise
        except requests.exceptions.RequestException as e:
            print(f"Page {page_number}: connection error ({e}) — retrying in 5s")
            time.sleep(5)

    raise RuntimeError(f"Failed to fetch page {page_number} after {max_retries} attempts")


# ── 4. Inspect response shape ────────────────────────────────────────────
# Run once to confirm the response structure before building the extraction loop.
# Prints the top-level type, the key holding the tender list, and one sample record.
def inspect_response_shape():
    sample = fetch_page(page_number=1, page_size=5)

    items_preview = []

    if isinstance(sample, list):
        items_preview = sample
    elif isinstance(sample, dict):
        print("Top-level keys:", list(sample.keys()))
        for key in ["data", "Data", "result", "Result", "items", "Items", "tenders"]:
            if key in sample and isinstance(sample[key], list):
                items_preview = sample[key]
                print(f"Tender list found under key: '{key}' ({len(items_preview)} items)")
                break

    if items_preview:
        print("\nFields on a sample record:")
        print(list(items_preview[0].keys()))


# ── 5. Extract the tender list from a response ───────────────────────────
# RESPONSE_LIST_KEY is confirmed from the inspection above: the Etimad endpoint
# wraps the tender list under "data", alongside "totalCount", "pageSize",
# and "currentPage".
RESPONSE_LIST_KEY = "data"


def extract_items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and RESPONSE_LIST_KEY:
        return payload.get(RESPONSE_LIST_KEY, [])
    return []


def get_total_count(payload):
    if isinstance(payload, dict):
        return payload.get("totalCount")
    return None


# ── 6. Run the extraction ────────────────────────────────────────────────
# Iterates through pages and deduplicates on tenderId, which is a stable
# unique identifier returned by the API. Stops early if a page returns no items.
def run_extraction(pages_to_scrape):
    all_items = []
    seen_ids = set()

    for page_number in range(1, pages_to_scrape + 1):
        payload = fetch_page(page_number)
        items = extract_items(payload)

        if page_number == 1:
            total_available = get_total_count(payload)
            if total_available is not None:
                print(f"Total tenders available on the portal: {total_available}\n")

        if not items:
            print(f"Page {page_number}: no items returned — stopping")
            break

        new_count = 0
        for item in items:
            tender_id = item.get("tenderId")
            if tender_id is not None and tender_id not in seen_ids:
                seen_ids.add(tender_id)
                all_items.append(item)
                new_count += 1

        print(f"Page {page_number}: {len(items)} items, {new_count} new")
        time.sleep(3)

    return all_items


# ── 7. Save raw output ───────────────────────────────────────────────────
# Records are saved exactly as returned by the API — no field selection,
# renaming, or cleaning at this stage.
def save_raw_output(records):
    output_path = RAW_DIR / f"etimad_all_tenders_{TODAY}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"Saved to: {output_path}")
    return output_path


# ── 8. Source diversity summary ──────────────────────────────────────────
# Informational only. Final agency classification (mapping raw agency names
# to canonical sources) happens in Task 2.
def print_source_diversity_summary(records):
    AGENCY_FIELD_NAME = "agencyName"

    distinct_agencies = sorted(set(
        r.get(AGENCY_FIELD_NAME) for r in records if r.get(AGENCY_FIELD_NAME)
    ))
    distinct_activities = sorted(set(
        r.get("tenderActivityName") for r in records if r.get("tenderActivityName")
    ))

    print(f"Total tenders: {len(records)}")
    print(f"Distinct raw agency names: {len(distinct_agencies)}")
    print(f"Distinct activity/category names: {len(distinct_activities)}")


# ── 9. Tender detail extraction (incremental) ────────────────────────────
# Each tender listing above is a summary. Full detail — dates, classification and
# execution location, awarding results, local-content requirements — lives behind
# four additional endpoints, keyed by tenderIdString (already present on every
# record above).
#
# These four endpoints return rendered HTML fragments, not JSON (their names —
# ...ViewComponenet — are ASP.NET view components). Each fragment follows a
# consistent structure: a list of <li class="list-group-item"> rows, each with
# a label (.etd-item-title) and a value (.etd-item-info, sometimes containing
# multiple <span> values — e.g. a Gregorian date alongside its Hijri equivalent).
# parse_view_component_html extracts every label/value pair generically,
# regardless of the exact wording of the labels.
#
# Incremental by design, for daily runs: results are stored in a persistent
# file (tender_details_by_id.json) keyed by tenderId. Every run loads whatever
# was already fetched and only requests details for tenders not yet in that file.
# The first run processes all tenders (4 endpoints each, ~1-1.5 hours at the
# 3-second courtesy delay); every run after that only processes tenders that are
# new since the previous run — typically a handful, finishing in minutes. An
# awarding result of {} is a genuine value (not yet awarded), not a fetch
# failure, so it is never retried on the next run.

DETAIL_ENDPOINTS = {
    "dates": "https://tenders.etimad.sa/Tender/GetTenderDatesViewComponenet",
    "classification_location": "https://tenders.etimad.sa/Tender/GetRelationsDetailsViewComponenet",
    "awarding": "https://tenders.etimad.sa/Tender/GetAwardingResultsForVisitorViewComponenet",
    "local_content": "https://tenders.etimad.sa/Tender/GetLocalContentDetailsViewComponenet",
}

DETAILS_STORE_PATH = RAW_DIR / "tender_details_by_id.json"


def parse_view_component_html(html_text):
    """Extracts label/value pairs from an Etimad view-component HTML fragment."""
    soup = BeautifulSoup(html_text, "html.parser")
    data = {}
    for item in soup.select("li.list-group-item"):
        title_el = item.select_one(".etd-item-title")
        info_el = item.select_one(".etd-item-info")
        if not title_el or not info_el:
            continue
        label = title_el.get_text(strip=True)
        spans = info_el.find_all("span")
        if spans:
            value = " | ".join(s.get_text(strip=True) for s in spans if s.get_text(strip=True))
        else:
            value = info_el.get_text(strip=True)
        if label:
            data[label] = value
    return data


def fetch_detail(endpoint_url, tender_id_str, max_retries=3):
    params = {"tenderIdStr": tender_id_str}
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(endpoint_url, params=params, headers=HEADERS, timeout=15)
            response.raise_for_status()
            return parse_view_component_html(response.text)
        except requests.exceptions.HTTPError:
            if response.status_code == 429:
                time.sleep(5 * attempt)
            else:
                return {"error": f"HTTP {response.status_code}"}
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}
    return {"error": "max retries exceeded"}


def load_details_store():
    if DETAILS_STORE_PATH.exists():
        with open(DETAILS_STORE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_details_store(store):
    with open(DETAILS_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def fetch_missing_tender_details(records, max_new=None):
    """max_new caps how many *new* tenders are processed this run — useful
    for a quick validation pass before committing to the full run."""
    store = load_details_store()
    pending = [r for r in records if str(r.get("tenderId")) not in store]

    if max_new is not None:
        pending = pending[:max_new]

    print(f"Already have details for {len(store)} tenders")
    print(f"Fetching details for {len(pending)} new tenders this run")

    for i, record in enumerate(pending, start=1):
        tender_id = record.get("tenderId")
        tender_id_str = record.get("tenderIdString")
        if not tender_id_str:
            continue

        detail = {}
        for name, url in DETAIL_ENDPOINTS.items():
            detail[name] = fetch_detail(url, tender_id_str)
            time.sleep(3)

        store[str(tender_id)] = detail
        print(f"[{i}/{len(pending)}] Done: tender {tender_id}")

        # Save every 20 tenders so a long first run isn't lost to an interruption
        if i % 20 == 0:
            save_details_store(store)

    save_details_store(store)
    return store


def main():
    inspect_response_shape()

    records = run_extraction(PAGES_TO_SCRAPE)
    print(f"\nTotal unique records extracted: {len(records)}")

    save_raw_output(records)
    print_source_diversity_summary(records)

    tender_details = fetch_missing_tender_details(records)
    print(f"\nTotal tenders with details on file: {len(tender_details)}")


if __name__ == "__main__":
    main()

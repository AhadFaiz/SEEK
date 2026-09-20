"""
Task 4 — Join & Transform
===========================
Loads `data/processed/validated.csv` (Task 3's output), attaches whatever
tender-detail data has been extracted so far (`data/raw/tender_details_by_id.json`
from Task 1), and upserts the result into the permanent archive at
`data/processed/tenders_archive.csv`.

Upsert = insert new tenders, update existing ones if they changed, and keep
everything else untouched. This is what makes daily automated runs safe —
running this script again tomorrow only adds/updates what's different; it
never duplicates or loses a tender that was archived before.
"""

# ── 1. Imports ────────────────────────────────────────────────────────────
import json
import os
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv


# ── 2. Load validated data ────────────────────────────────────────────────
RAW_DIR = Path("../data/raw")
INTERIM_DIR = Path("../data/interim")
PROCESSED_DIR = Path("../data/processed")
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def load_validated():
    validated = pd.read_csv(PROCESSED_DIR / "validated.csv")
    print(f"Loaded {len(validated)} validated rows")
    return validated


# ── 3. Attach tender detail data (if available) ──────────────────────────
# Task 1's incremental detail extraction (tender_details_by_id.json) may
# only cover a subset of tenders so far — that's expected, not an error. Each
# tender's four detail sections (dates, classification/location, awarding,
# local content) are attached as a single JSON string column rather than
# expanded into fixed columns, since the label wording inside them can vary
# slightly between tenders.
def load_tender_details():
    details_path = RAW_DIR / "tender_details_by_id.json"

    if details_path.exists():
        with open(details_path, encoding="utf-8") as f:
            tender_details = json.load(f)
    else:
        tender_details = {}

    return tender_details


# ── 3.5 Translate tender detail dictionaries to English ──────────────────
# tender_details_by_id.json was scraped straight from Etimad's HTML detail
# tabs (Task 1), so every label (e.g. تاريخ التقديم) and most values inside
# it are still Arabic — Task 2's translation step never touches this file, it
# only translates the main listing fields. Left as-is, this Arabic text would
# end up embedded inside the tender_details_json column of every archive
# and final CSV.
#
# This step finds every Arabic string (label or value) anywhere in the
# nested dict, translates the unique ones via the same Azure Translator setup
# Task 2 uses, and reuses/extends the same translation_cache.json — so a
# label already translated in a previous run (or by Task 2) is never re-sent.

load_dotenv()
AZURE_KEY = os.getenv("AZURE_TRANSLATOR_KEY")
AZURE_ENDPOINT = os.getenv("AZURE_TRANSLATOR_ENDPOINT")
AZURE_REGION = os.getenv("AZURE_TRANSLATOR_REGION")

CACHE_PATH = INTERIM_DIR / "translation_cache.json"

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

BATCH_SIZE = 100


def has_arabic(text):
    return isinstance(text, str) and bool(ARABIC_RE.search(text))


def translate_batch_azure(texts, source="ar", target="en", max_retries=3):
    """Translates up to 100 texts in a single Azure Translator call."""
    if not texts:
        return []

    url = f"{AZURE_ENDPOINT}/translate"
    params = {"api-version": "3.0", "from": source, "to": target}
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Ocp-Apim-Subscription-Region": AZURE_REGION,
        "Content-Type": "application/json",
    }
    body = [{"text": t} for t in texts]

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, params=params, headers=headers, json=body, timeout=30)
            response.raise_for_status()
            result = response.json()
            return [item["translations"][0]["text"] for item in result]
        except Exception:
            if attempt == max_retries:
                return [f"Unknown - translation failed: {t[:30]}" for t in texts]
            time.sleep(3 * attempt)


def collect_arabic_strings(details_store):
    """Every distinct Arabic label or value anywhere in the nested structure."""
    found = set()
    for sections in details_store.values():
        for fields in sections.values():
            if not isinstance(fields, dict):
                continue
            for label, value in fields.items():
                if has_arabic(label):
                    found.add(label)
                if has_arabic(value):
                    found.add(value)
    return found


def translate_value(value, translation_cache):
    if has_arabic(value):
        return translation_cache.get(value, value)
    return value


def translate_details_store(details_store, translation_cache):
    translated_store = {}
    for tender_id, sections in details_store.items():
        translated_sections = {}
        for section_name, fields in sections.items():
            if not isinstance(fields, dict):
                translated_sections[section_name] = fields
                continue
            translated_sections[section_name] = {
                translate_value(label, translation_cache): translate_value(value, translation_cache)
                for label, value in fields.items()
            }
        translated_store[tender_id] = translated_sections
    return translated_store


def translate_tender_details(tender_details):
    if CACHE_PATH.exists():
        with open(CACHE_PATH, encoding="utf-8") as f:
            translation_cache = json.load(f)
    else:
        translation_cache = {}

    arabic_strings = [
        s for s in collect_arabic_strings(tender_details)
        if s not in translation_cache or translation_cache[s].startswith("Unknown - translation failed")
    ]
    print(f"tender_details_by_id.json: {len(arabic_strings)} new/failed unique Arabic strings to translate")

    for i in range(0, len(arabic_strings), BATCH_SIZE):
        batch = arabic_strings[i:i + BATCH_SIZE]
        translations = translate_batch_azure(batch)
        for original, translated in zip(batch, translations):
            translation_cache[original] = translated
        print(f"  ... {min(i + BATCH_SIZE, len(arabic_strings))}/{len(arabic_strings)}")
        time.sleep(0.5)

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(translation_cache, f, ensure_ascii=False, indent=2)

    tender_details = translate_details_store(tender_details, translation_cache)
    print("Done: tender_details_by_id.json content translated to English")
    return tender_details


def attach_detail_columns(validated, tender_details):
    def get_detail_json(tender_id):
        return json.dumps(tender_details.get(str(tender_id), {}), ensure_ascii=False)

    validated["tender_details_json"] = validated["tender_id"].apply(get_detail_json)
    validated["has_full_details"] = validated["tender_id"].astype(str).isin(tender_details.keys())

    print(f"Rows with full detail data attached: {validated['has_full_details'].sum()} / {len(validated)}")
    return validated


# ── 4. Upsert into the permanent archive ─────────────────────────────────
# - First run ever: the archive doesn't exist yet, so it's simply created.
# - Every run after that: new tender_ids are appended, and any tender_id
#   that already exists gets its row replaced with the freshest version
#   (in case its status, awarding result, etc. changed since last time).
ARCHIVE_PATH = PROCESSED_DIR / "tenders_archive.csv"


def upsert_archive(validated):
    if ARCHIVE_PATH.exists():
        archive = pd.read_csv(ARCHIVE_PATH)
        before_count = len(archive)
        combined = pd.concat([archive, validated], ignore_index=True)
        # keep="last" means today's row wins over yesterday's for the same tender_id
        combined = combined.drop_duplicates(subset="tender_id", keep="last")
    else:
        before_count = 0
        combined = validated

    combined = combined.sort_values("tender_id").reset_index(drop=True)
    combined.to_csv(ARCHIVE_PATH, index=False, encoding="utf-8-sig")

    new_count = len(combined) - before_count
    print(f"Archive before this run: {before_count} tenders")
    print(f"Archive after this run: {len(combined)} tenders")
    print(f"Net new tenders added: {new_count}")

    return combined


# ── 5. Save today's snapshot (optional, for audit trail) ────────────────
def save_snapshot(combined):
    today = date.today().isoformat()
    snapshot_path = PROCESSED_DIR / f"final_{today}.csv"
    combined.to_csv(snapshot_path, index=False, encoding="utf-8-sig")
    print(f"Snapshot saved to: {snapshot_path}")
    return snapshot_path


def main():
    validated = load_validated()

    tender_details = load_tender_details()
    tender_details = translate_tender_details(tender_details)

    validated = attach_detail_columns(validated, tender_details)

    combined = upsert_archive(validated)
    save_snapshot(combined)


if __name__ == "__main__":
    main()

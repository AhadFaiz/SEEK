"""
SEEK — join and archive (reference implementation).

The production version of this logic runs in Azure Data Factory. This module
is kept as readable reference code for the same rules.

Loads data/processed/validated.csv, attaches each tender's detail sections
from data/raw/tender_details_by_id.json (translated to English), and upserts
the result into data/processed/tenders_archive.csv.

Upsert: new tenders are added, tenders already in the archive are replaced by
their newest version, and everything else is left untouched — so running it
again never duplicates or loses a tender.
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
    """Load data/processed/validated.csv."""
    validated = pd.read_csv(PROCESSED_DIR / "validated.csv")
    print(f"Loaded {len(validated)} validated rows")
    return validated


# ── 3. Tender details ────────────────────────────────────────────────────
# The details file may cover only some tenders; that is expected. Each tender's
# four sections (dates, classification/location, awarding, local content) are
# attached as one JSON string column, because label wording varies slightly
# between tenders.
def load_tender_details():
    """Load tender_details_by_id.json, or return an empty dict if it does not exist."""
    details_path = RAW_DIR / "tender_details_by_id.json"

    if details_path.exists():
        with open(details_path, encoding="utf-8") as f:
            tender_details = json.load(f)
    else:
        tender_details = {}

    return tender_details


# ── 4. Translate the detail sections ─────────────────────────────────────
# Labels and most values in the detail sections are Arabic. Every distinct
# Arabic string is translated once with Azure AI Translator, reusing the same
# translation cache as the listing fields.

load_dotenv()
AZURE_KEY = os.getenv("AZURE_TRANSLATOR_KEY")
AZURE_ENDPOINT = os.getenv("AZURE_TRANSLATOR_ENDPOINT")
AZURE_REGION = os.getenv("AZURE_TRANSLATOR_REGION")

CACHE_PATH = INTERIM_DIR / "translation_cache.json"

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

BATCH_SIZE = 100


def has_arabic(text):
    """Return True if text is a string containing Arabic characters."""
    return isinstance(text, str) and bool(ARABIC_RE.search(text))


def translate_batch_azure(texts, source="ar", target="en", max_retries=3):
    """Translate up to 100 texts in one Azure Translator call.

    Retries with a 3 s × attempt wait; texts that still fail are returned as
    "Unknown - translation failed: <text>" so they are retried on the next run.
    """
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
    """Return every distinct Arabic label or value in the nested details store."""
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
    """Return the cached translation of an Arabic value, or the value unchanged."""
    if has_arabic(value):
        return translation_cache.get(value, value)
    return value


def translate_details_store(details_store, translation_cache):
    """Return a copy of the details store with every label and value translated."""
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
    """Translate new Arabic strings in the details store, update the cache, and return the translated store."""
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
    """Add tender_details_json (the detail sections as JSON) and has_full_details columns."""
    def get_detail_json(tender_id):
        return json.dumps(tender_details.get(str(tender_id), {}), ensure_ascii=False)

    validated["tender_details_json"] = validated["tender_id"].apply(get_detail_json)
    validated["has_full_details"] = validated["tender_id"].astype(str).isin(tender_details.keys())

    print(f"Rows with full detail data attached: {validated['has_full_details'].sum()} / {len(validated)}")
    return validated


# ── 5. Upsert into the archive ───────────────────────────────────────────
ARCHIVE_PATH = PROCESSED_DIR / "tenders_archive.csv"


def upsert_archive(validated):
    """Merge today's rows into tenders_archive.csv, keeping one row per tender_id.

    On the first run the archive is created. Afterwards new tender_ids are
    added and existing ones are replaced by today's version.
    """
    if ARCHIVE_PATH.exists():
        archive = pd.read_csv(ARCHIVE_PATH)
        before_count = len(archive)
        combined = pd.concat([archive, validated], ignore_index=True)
        # keep="last": today's row wins over the archived row for the same tender_id
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


# ── 6. Daily snapshot ────────────────────────────────────────────────────
def save_snapshot(combined):
    """Save a dated copy of the archive (final_<date>.csv) as an audit trail."""
    today = date.today().isoformat()
    snapshot_path = PROCESSED_DIR / f"final_{today}.csv"
    combined.to_csv(snapshot_path, index=False, encoding="utf-8-sig")
    print(f"Snapshot saved to: {snapshot_path}")
    return snapshot_path


def main():
    """Attach translated details, upsert the archive and save today's snapshot."""
    validated = load_validated()

    tender_details = load_tender_details()
    tender_details = translate_tender_details(tender_details)

    validated = attach_detail_columns(validated, tender_details)

    combined = upsert_archive(validated)
    save_snapshot(combined)


if __name__ == "__main__":
    main()

"""
Shared Configuration
=====================
Central place for non-secret settings, paths, and constants used across
Task 1-4 (extract.py, profile.py, clean.py, schema.py, transform.py).

Secrets (Azure Translator key, ADLS SAS token) are NOT stored here — they
live in `.env`, loaded via python-dotenv, and kept out of git via
.gitignore. This file only holds non-secret settings: URLs, file paths,
column lists, and batch sizes.
"""

# ── 1. Imports ────────────────────────────────────────────────────────────
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ── 2. Data directories ──────────────────────────────────────────────────
# Paths are relative to src/, matching how the scripts already resolve them
# when run as `python3 <script>.py` from inside src/.
RAW_DIR = Path("../data/raw")
INTERIM_DIR = Path("../data/interim")
PROCESSED_DIR = Path("../data/processed")

RAW_DIR.mkdir(parents=True, exist_ok=True)
INTERIM_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

DETAILS_STORE_PATH = RAW_DIR / "tender_details_by_id.json"
CACHE_PATH = INTERIM_DIR / "translation_cache.json"
CLEANED_PATH = INTERIM_DIR / "cleaned.csv"
VALIDATED_PATH = PROCESSED_DIR / "validated.csv"
REJECTED_PATH = PROCESSED_DIR / "rejected.csv"
ARCHIVE_PATH = PROCESSED_DIR / "tenders_archive.csv"


# ── 3. Task 1: Etimad extraction ─────────────────────────────────────────
BASE_URL = "https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync"
PAGE_SIZE = 100
PAGES_TO_SCRAPE = 20
PUBLISH_DATE_ID = 5  # matches the default filter applied on the public listing page

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=1",
}

DETAIL_ENDPOINTS = {
    "dates": "https://tenders.etimad.sa/Tender/GetTenderDatesViewComponenet",
    "classification_location": "https://tenders.etimad.sa/Tender/GetRelationsDetailsViewComponenet",
    "awarding": "https://tenders.etimad.sa/Tender/GetAwardingResultsForVisitorViewComponenet",
    "local_content": "https://tenders.etimad.sa/Tender/GetLocalContentDetailsViewComponenet",
}


# ── 4. Task 2: Profile, clean & translate ────────────────────────────────
COLUMNS_TO_TRANSLATE = [
    "tenderName", "tenderTypeName", "tenderActivityName",
    "branchName", "agencyName", "tenderStatusName", "tenderNumber",
]
NEEDS_REVIEW_PREFIX = "Other - needs review: "
TRANSLATE_BATCH_SIZE = 100


# ── 5. Task 3: Schema validation ─────────────────────────────────────────
REQUIRED_COLUMNS = ["tender_id", "reference_number", "tender_name", "source_entity", "sector"]
NUMERIC_COLUMNS = ["buying_cost", "financial_fees", "invitation_cost"]


# ── 6. Azure Translator (used by Task 2 and Task 4) ──────────────────────
# Values come from .env — never hardcoded, never committed to git.
AZURE_TRANSLATOR_KEY = os.getenv("AZURE_TRANSLATOR_KEY")
AZURE_TRANSLATOR_ENDPOINT = os.getenv("AZURE_TRANSLATOR_ENDPOINT")
AZURE_TRANSLATOR_REGION = os.getenv("AZURE_TRANSLATOR_REGION")


# ── 7. ADLS Gen2 landing zone ─────────────────────────────────────────────
ADLS_ACCOUNT_NAME = os.getenv("ADLS_ACCOUNT_NAME")
ADLS_SAS_TOKEN = os.getenv("ADLS_SAS_TOKEN")

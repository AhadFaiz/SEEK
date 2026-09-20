"""
Task 2 — Profile & Clean
========================
Loads the raw tender records from `data/raw/`, profiles the data, translates
Arabic text fields to English, classifies each agency into a canonical source
and a broader sector, and saves the cleaned result to `data/interim/cleaned.csv`.
"""

# ── 1. Imports ────────────────────────────────────────────────────────────
import json
import time
import os
import re
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv


# ── 2. Load the latest raw extract ───────────────────────────────────────
RAW_DIR = Path("../data/raw")
INTERIM_DIR = Path("../data/interim")
INTERIM_DIR.mkdir(parents=True, exist_ok=True)


def load_latest_raw():
    latest_file = sorted(RAW_DIR.glob("etimad_all_tenders_*.json"))[-1]

    with open(latest_file, encoding="utf-8") as f:
        records = json.load(f)

    df = pd.DataFrame(records)
    print(f"Loaded {len(df)} rows from {latest_file.name}")
    return df


# ── 3. Profile ────────────────────────────────────────────────────────────
# For each column: dtype, null count, null percentage, unique count, and a
# sample value. Use this to spot columns that need cleaning before moving on.
def profile_dataframe(df):
    profile = pd.DataFrame({
        "dtype": df.dtypes,
        "null_count": df.isnull().sum(),
        "null_pct": (df.isnull().mean() * 100).round(1),
        "unique_count": df.nunique(),
        "sample_value": df.iloc[0],
    })
    print(profile)
    return profile


# ── 4. Translate Arabic text fields to English (Azure AI Translator) ────
# Uses the official Azure Translator REST API — reliable, no scraping-based
# blocks. Requests are sent in batches of up to 100 texts per call, which is
# dramatically faster than one call per value.
#
# Credentials are read from a local .env file (never committed to git):
#   AZURE_TRANSLATOR_KEY=...
#   AZURE_TRANSLATOR_ENDPOINT=https://api.cognitive.microsofttranslator.com/
#   AZURE_TRANSLATOR_REGION=uaenorth
#
# A persistent cache (translation_cache.json) is loaded and updated so that
# values translated in a previous run are never re-sent to the API — only
# genuinely new values incur a request on subsequent daily runs.

load_dotenv()

AZURE_KEY = os.getenv("AZURE_TRANSLATOR_KEY")
AZURE_ENDPOINT = os.getenv("AZURE_TRANSLATOR_ENDPOINT")
AZURE_REGION = os.getenv("AZURE_TRANSLATOR_REGION")

CACHE_PATH = INTERIM_DIR / "translation_cache.json"

COLUMNS_TO_TRANSLATE = [
    "tenderName", "tenderTypeName", "tenderActivityName", "branchName",
    "agencyName", "tenderStatusName", "tenderNumber",
]
BATCH_SIZE = 100

NEEDS_REVIEW_PREFIX = "Other - needs review: "

SECTOR_KEYWORDS = {
    "Security & Defense": ["أمن", "دفاع", "حرس", "شرطة", "قوات", "عسكري", "مباحث", "حدود", "بحرية", "برية", "جوي", "جوية", "أركان"],
    "Health": ["صحة", "صحية", "صحي", "مستشف", "طبي", "طبية"],
    "Education": ["تعليم", "جامعة", "مدرسة", "تدريب", "كلية", "مكتبة"],
    "Municipal & Housing": ["بلدية", "أمانة", "إسكان", "أمارة", "إمارة"],
    "Finance & Economy": ["مالية", "اقتصاد", "تجارة", "استثمار", "زكاة", "ضريبة", "بنك", "صندوق", "جمارك", "إفلاس", "تأمين", "تصفية"],
    "Justice & Legal": ["عدل", "قضاء", "نيابة", "محكمة"],
    "Transport & Logistics": ["نقل", "طرق", "طيران", "موانئ", "بحري"],
    "Energy, Water & Environment": ["طاقة", "كهرباء", "مياه", "بيئة", "زراعة", "نفط", "تحلية", "محمية", "نبات", "حيوان", "نخيل", "تمور"],
    "Technology & Data": ["بيانات", "ذكاء اصطناعي", "اتصالات", "تقنية", "معلومات", "إحصاء", "إنترنت", "SPACE"],
    "Religious & Cultural Affairs": ["أوقاف", "حج", "عمرة", "ثقافة", "إسلامية", "اسلامية", "دعوة", "إرشاد", "ارشاد"],
    "Strategic Development & Partnerships": ["تنمية", "استراتيجي", "شراكات", "تطوير", "مشاريع", "الهيئة الملكية"],
    "Public Safety & Anti-Crime": ["مكافحة", "ضبط", "المخدرات"],
    "Media & Communication": ["إعلام", "صحافة", "بث", "نشر"],
    "Sports & Youth": ["رياضة", "رياضي", "شباب", "نادي", "سباق"],
    "Social & Labor Affairs": ["عمل", "اجتماعي", "تنمية اجتماعية", "ضمان"],
    "Tourism & Heritage": ["سياحة", "تراث"],
}


def load_translation_cache():
    if CACHE_PATH.exists():
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_translation_cache(translation_cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(translation_cache, f, ensure_ascii=False, indent=2)


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


def translate_columns(df, translation_cache):
    for col in COLUMNS_TO_TRANSLATE:
        if col not in df.columns:
            continue

        unique_values = [
            v for v in df[col].dropna().unique()
            if v not in translation_cache or translation_cache[v].startswith("Unknown - translation failed")
        ]
        print(f"{col}: {len(unique_values)} new/failed unique values to translate")

        for i in range(0, len(unique_values), BATCH_SIZE):
            batch = unique_values[i:i + BATCH_SIZE]
            translations = translate_batch_azure(batch)
            for original, translated in zip(batch, translations):
                translation_cache[original] = translated
            print(f"  ... {min(i + BATCH_SIZE, len(unique_values))}/{len(unique_values)}")
            time.sleep(0.5)

        df[f"{col}_en"] = df[col].map(translation_cache)
        print(f"Done: {col}")

    save_translation_cache(translation_cache)

    failed = [k for k, v in translation_cache.items() if v.startswith("Unknown - translation failed")]
    print(f"\nTranslation complete. Failed: {len(failed)}")
    if failed:
        print(failed)

    return df


# ── 5. Classify agency into a canonical source ───────────────────────────
# Raw agency names include branch-level variation (e.g. different regional
# offices of the same authority, written as "Authority - Branch" or
# "Authority (Branch)"). The canonical source is derived automatically by
# splitting on the first "-", "(", or the words "فرع"/"مكتب" — no manual
# dictionary required. AGENCY_COL is resolved dynamically so this cell works
# whether or not the snake_case renaming (Section 7) has already run.
def derive_canonical_source(agency_name):
    if not agency_name or not isinstance(agency_name, str):
        return "Unknown - extraction issue"
    parts = re.split(r"\s*-\s*|\s*\(|\bفرع\b|\bمكتب\b", agency_name)
    core = parts[0].strip()
    return core if core else agency_name


def add_canonical_source(df):
    agency_col = "agencyName" if "agencyName" in df.columns else "agency_name"

    df["source_entity"] = df[agency_col].apply(derive_canonical_source)

    print(f"Distinct sources after automatic merge: {df['source_entity'].nunique()}")
    print(df["source_entity"].value_counts().head(20))
    return df


# ── 6. Classify into a broader sector ────────────────────────────────────
# A second, coarser grouping on top of source_entity — useful for reporting
# sector-level diversity (security, health, education, ...) rather than raw
# agency counts. Keyword-based, with an "Other - needs review" fallback so new
# agencies never get silently misclassified.
def classify_sector(agency_name):
    if not agency_name or not isinstance(agency_name, str):
        return "Unknown - extraction issue"
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in agency_name for kw in keywords):
            return sector
    return f"Other - needs review: {agency_name}"


def add_sector(df):
    df["sector"] = df["source_entity"].apply(classify_sector)

    unclassified = df[df["sector"].str.startswith("Other")]["sector"].unique()
    print(f"Sectors used: {df['sector'].nunique()}")
    print(f"Agencies not matched to a sector: {len(unclassified)}")
    print(unclassified)
    return df


# ── 6.5 Translate source_entity to English ───────────────────────────────
# source_entity (Section 5) is derived by splitting the original Arabic
# agencyName — its values (e.g. قوات الدفاع الجوي) are Arabic fragments, not
# full agency names, so they generally aren't already sitting in
# translation_cache as exact keys. sector (Section 6) is fine — it's
# already in English regardless of the language of source_entity, since the
# dictionary keys ("Security & Defense", etc.) were written in English from
# the start.
#
# This step batch-translates the unique source_entity values the same way
# Section 4 did, reusing the same on-disk cache, and overwrites the column
# in place.
def translate_source_entity(df, translation_cache):
    unique_entities = [
        v for v in df["source_entity"].dropna().unique()
        if v not in translation_cache or translation_cache[v].startswith("Unknown - translation failed")
    ]
    print(f"source_entity: {len(unique_entities)} new/failed unique values to translate")

    for i in range(0, len(unique_entities), BATCH_SIZE):
        batch = unique_entities[i:i + BATCH_SIZE]
        translations = translate_batch_azure(batch)
        for original, translated in zip(batch, translations):
            translation_cache[original] = translated
        print(f"  ... {min(i + BATCH_SIZE, len(unique_entities))}/{len(unique_entities)}")
        time.sleep(0.5)

    df["source_entity"] = df["source_entity"].map(translation_cache).fillna(df["source_entity"])

    save_translation_cache(translation_cache)

    print("Done: source_entity")
    print(df["source_entity"].unique()[:10])
    return df


# ── 6.6 Fix the sector fallback label ────────────────────────────────────
# Section 6 built sector before source_entity was translated (it had
# to — the keyword matching needs the original Arabic). For agencies that
# didn't match any sector keyword, the fallback value is
# "Other - needs review: <agency name>" — and at that point <agency name>
# was still Arabic, so it got baked into sector as Arabic text that survives
# even after Section 6.5 translates source_entity itself.
#
# This replaces the Arabic tail of any "Other - needs review: ..." value
# with its English translation, using the same cache Section 6.5 just
# populated — the exact Arabic string was translated one cell ago, so it's
# guaranteed to already be in translation_cache.
def fix_needs_review_label(sector_value, translation_cache):
    if isinstance(sector_value, str) and sector_value.startswith(NEEDS_REVIEW_PREFIX):
        arabic_name = sector_value[len(NEEDS_REVIEW_PREFIX):]
        translated_name = translation_cache.get(arabic_name, arabic_name)
        return NEEDS_REVIEW_PREFIX + translated_name
    return sector_value


def fix_sector_labels(df, translation_cache):
    before = df["sector"].astype(str).str.startswith(NEEDS_REVIEW_PREFIX).sum()
    df["sector"] = df["sector"].apply(lambda v: fix_needs_review_label(v, translation_cache))
    print(f"Fixed {before} 'Other - needs review' labels to use the English agency name")
    return df


# ── 6.5 (continued) Replace Arabic text with the English translation ────
# The requirement is that the final data contains no Arabic text at all,
# so the original Arabic columns are not kept side-by-side with their _en
# counterparts. Instead, each translated column's value overwrites the
# original column (same name), and the temporary _en column is dropped.
#
# This has to run after Section 5 (canonical source) and Section 6 (sector),
# because both of those still need the original Arabic agencyName to do
# their pattern matching (e.g. splitting on "فرع"/"مكتب", matching Arabic
# sector keywords). Doing this replacement any earlier would break them.
def overwrite_arabic_columns_with_english(df):
    for col in COLUMNS_TO_TRANSLATE:
        en_col = f"{col}_en"
        if col in df.columns and en_col in df.columns:
            df[col] = df[en_col]
            df = df.drop(columns=[en_col])

    print("Arabic originals replaced with English translations for:", COLUMNS_TO_TRANSLATE)
    print(df[COLUMNS_TO_TRANSLATE].head(3))
    return df


# ── 7. General cleanup ────────────────────────────────────────────────────
# - Column names standardized to snake_case
# - Missing values already labeled explicitly at extraction (Task 1) and above
# - No further deduplication needed — tenderId uniqueness was enforced in Task 1
def to_snake_case_columns(df):
    df.columns = [
        "".join(["_" + c.lower() if c.isupper() else c for c in col]).lstrip("_")
        for col in df.columns
    ]
    print(df.columns.tolist())
    return df


# ── 8. Save ────────────────────────────────────────────────────────────────
def save_cleaned(df):
    output_path = INTERIM_DIR / "cleaned.csv"
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"Saved to: {output_path}")
    return output_path


def main():
    df = load_latest_raw()
    profile_dataframe(df)

    translation_cache = load_translation_cache()
    df = translate_columns(df, translation_cache)

    df = add_canonical_source(df)
    df = add_sector(df)

    df = translate_source_entity(df, translation_cache)
    df = fix_sector_labels(df, translation_cache)

    df = overwrite_arabic_columns_with_english(df)
    df = to_snake_case_columns(df)

    save_cleaned(df)


if __name__ == "__main__":
    main()

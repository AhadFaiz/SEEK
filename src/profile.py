"""
SEEK — profiling, translation and classification (reference implementation).

The production version of this logic runs in Azure Data Factory. This module
is kept as readable reference code for the same rules:

    1. Profile the latest raw extract (types, nulls, unique values).
    2. Translate Arabic text columns with Azure AI Translator, 100 texts per
       call, reusing a translation cache so no text is translated twice.
    3. Derive source_entity — the parent organisation without branch detail.
    4. Classify each tender into one of 16 business sectors from Arabic keywords
       ("Other - needs review: <entity>" when nothing matches).
    5. Rename columns to snake_case and save data/interim/cleaned.csv.

Note: unlike the production pipeline, which keeps the Arabic original next to
an English (_en) column, this version replaces each Arabic column with its
English translation.

Settings are read from a local .env file (never committed):
    AZURE_TRANSLATOR_KEY, AZURE_TRANSLATOR_ENDPOINT, AZURE_TRANSLATOR_REGION
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
    """Load the most recent etimad_all_tenders_<date>.json into a DataFrame."""
    latest_file = sorted(RAW_DIR.glob("etimad_all_tenders_*.json"))[-1]

    with open(latest_file, encoding="utf-8") as f:
        records = json.load(f)

    df = pd.DataFrame(records)
    print(f"Loaded {len(df)} rows from {latest_file.name}")
    return df


# ── 3. Profile ────────────────────────────────────────────────────────────
def profile_dataframe(df):
    """Print and return dtype, null count, null %, unique count and a sample value per column."""
    profile = pd.DataFrame({
        "dtype": df.dtypes,
        "null_count": df.isnull().sum(),
        "null_pct": (df.isnull().mean() * 100).round(1),
        "unique_count": df.nunique(),
        "sample_value": df.iloc[0],
    })
    print(profile)
    return profile


# ── 4. Translation settings and sector keywords ──────────────────────────
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

# Sector → Arabic keywords matched against source_entity. Order matters: the
# first sector with a matching keyword wins.
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


# ── 5. Translation ───────────────────────────────────────────────────────
def load_translation_cache():
    """Load the Arabic → English translation cache, or return an empty dict."""
    if CACHE_PATH.exists():
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_translation_cache(translation_cache):
    """Write the translation cache back to disk."""
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(translation_cache, f, ensure_ascii=False, indent=2)


def translate_batch_azure(texts, source="ar", target="en", max_retries=3):
    """Translate up to 100 texts in one Azure Translator call.

    Retries with a 3 s × attempt wait. If every attempt fails, each text is
    returned as "Unknown - translation failed: <text>" so it is retried on the
    next run.
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


def translate_columns(df, translation_cache):
    """Add a <column>_en translation for each column in COLUMNS_TO_TRANSLATE.

    Only values not yet in the cache (or that failed before) are sent to the
    translator; the cache is saved at the end.
    """
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


# ── 6. Parent organisation (source_entity) ───────────────────────────────
def derive_canonical_source(agency_name):
    """Return the parent organisation from an agency name.

    Agency names often include a branch, written as "Authority - Branch",
    "Authority (Branch)" or with the words فرع / مكتب. The text before the
    first of these is kept.
    """
    if not agency_name or not isinstance(agency_name, str):
        return "Unknown - extraction issue"
    parts = re.split(r"\s*-\s*|\s*\(|\bفرع\b|\bمكتب\b", agency_name)
    core = parts[0].strip()
    return core if core else agency_name


def add_canonical_source(df):
    """Add the source_entity column (works before or after snake_case renaming)."""
    agency_col = "agencyName" if "agencyName" in df.columns else "agency_name"

    df["source_entity"] = df[agency_col].apply(derive_canonical_source)

    print(f"Distinct sources after automatic merge: {df['source_entity'].nunique()}")
    print(df["source_entity"].value_counts().head(20))
    return df


# ── 7. Sector classification ─────────────────────────────────────────────
def classify_sector(agency_name):
    """Return the first sector whose keywords appear in the name, else an "Other - needs review" label."""
    if not agency_name or not isinstance(agency_name, str):
        return "Unknown - extraction issue"
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in agency_name for kw in keywords):
            return sector
    return f"Other - needs review: {agency_name}"


def add_sector(df):
    """Add the sector column from source_entity and report unmatched entities."""
    df["sector"] = df["source_entity"].apply(classify_sector)

    unclassified = df[df["sector"].str.startswith("Other")]["sector"].unique()
    print(f"Sectors used: {df['sector'].nunique()}")
    print(f"Agencies not matched to a sector: {len(unclassified)}")
    print(unclassified)
    return df


# ── 8. Translate source_entity ───────────────────────────────────────────
def translate_source_entity(df, translation_cache):
    """Translate source_entity to English in place, reusing the translation cache.

    source_entity values are fragments of the agency name, so most of them are
    not in the cache yet and are translated here in batches.
    """
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


# ── 9. English "Other - needs review" labels ─────────────────────────────
def fix_needs_review_label(sector_value, translation_cache):
    """Replace the Arabic entity name in an "Other - needs review" label with its translation."""
    if isinstance(sector_value, str) and sector_value.startswith(NEEDS_REVIEW_PREFIX):
        arabic_name = sector_value[len(NEEDS_REVIEW_PREFIX):]
        translated_name = translation_cache.get(arabic_name, arabic_name)
        return NEEDS_REVIEW_PREFIX + translated_name
    return sector_value


def fix_sector_labels(df, translation_cache):
    """Apply fix_needs_review_label to the whole sector column.

    Sectors are classified on the Arabic text, so fallback labels contain the
    Arabic entity name until this step translates it.
    """
    before = df["sector"].astype(str).str.startswith(NEEDS_REVIEW_PREFIX).sum()
    df["sector"] = df["sector"].apply(lambda v: fix_needs_review_label(v, translation_cache))
    print(f"Fixed {before} 'Other - needs review' labels to use the English agency name")
    return df


# ── 10. Replace Arabic columns with English ──────────────────────────────
def overwrite_arabic_columns_with_english(df):
    """Replace each translated column with its _en version and drop the _en column.

    Runs after source_entity and sector, which both need the Arabic agency name.
    """
    for col in COLUMNS_TO_TRANSLATE:
        en_col = f"{col}_en"
        if col in df.columns and en_col in df.columns:
            df[col] = df[en_col]
            df = df.drop(columns=[en_col])

    print("Arabic originals replaced with English translations for:", COLUMNS_TO_TRANSLATE)
    print(df[COLUMNS_TO_TRANSLATE].head(3))
    return df


# ── 11. Column names and save ────────────────────────────────────────────
def to_snake_case_columns(df):
    """Rename every column from camelCase to snake_case."""
    df.columns = [
        "".join(["_" + c.lower() if c.isupper() else c for c in col]).lstrip("_")
        for col in df.columns
    ]
    print(df.columns.tolist())
    return df


def save_cleaned(df):
    """Save the result to data/interim/cleaned.csv (UTF-8 with BOM)."""
    output_path = INTERIM_DIR / "cleaned.csv"
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"Saved to: {output_path}")
    return output_path


def main():
    """Run profiling, translation, classification and save the cleaned file."""
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

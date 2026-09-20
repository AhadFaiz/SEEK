"""
Task 3 — Schema Validation
===========================
Loads `data/interim/cleaned.csv` (Task 2's output), checks every row against
a simple schema (required fields present, numeric fields actually numeric,
tender_id unique), and splits the result into `validated.csv` (rows that
pass every check) and `rejected.csv` (rows that fail, with the specific
reason listed) inside `data/processed/`.

Nothing is silently dropped or guessed — a rejected row keeps every original
column plus a `validation_issues` note explaining exactly why it was
rejected, so Task 4 (and anyone reviewing the data later) can see the reason.
"""

# ── 1. Imports ────────────────────────────────────────────────────────────
from pathlib import Path

import pandas as pd


# ── 2. Load cleaned data ─────────────────────────────────────────────────
INTERIM_DIR = Path("../data/interim")
PROCESSED_DIR = Path("../data/processed")
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def load_cleaned():
    df = pd.read_csv(INTERIM_DIR / "cleaned.csv")
    print(f"Loaded {len(df)} rows from cleaned.csv")
    print(df.columns.tolist())
    return df


# ── 3. Define the schema ─────────────────────────────────────────────────
# - REQUIRED_COLUMNS: must be present and non-empty on every row.
# - NUMERIC_COLUMNS: if present and non-null, must parse as a number.
# - tender_id must additionally be unique across the whole file.
REQUIRED_COLUMNS = ["tender_id", "reference_number", "tender_name", "source_entity", "sector"]
NUMERIC_COLUMNS = ["buying_cost", "financial_fees", "invitation_cost"]


# ── 4. Validate every row ────────────────────────────────────────────────
def validate_row(row):
    issues = []

    for col in REQUIRED_COLUMNS:
        if col not in row or pd.isna(row[col]) or str(row[col]).strip() == "":
            issues.append(f"missing_{col}")

    for col in NUMERIC_COLUMNS:
        if col in row and pd.notna(row[col]):
            try:
                float(row[col])
            except (ValueError, TypeError):
                issues.append(f"{col}_not_numeric")

    return issues


def add_validation_issues(df):
    df["validation_issues"] = df.apply(validate_row, axis=1)
    return df


# ── 5. Check for duplicate tender_id ─────────────────────────────────────
# A duplicate tender_id means the same tender appears twice — this would
# corrupt the upsert logic in Task 4, so it must be caught here, not later.
def flag_duplicate_tender_ids(df):
    duplicate_ids = set(df["tender_id"][df["tender_id"].duplicated(keep=False)])

    if duplicate_ids:
        df["validation_issues"] = df.apply(
            lambda r: r["validation_issues"] + ["duplicate_tender_id"] if r["tender_id"] in duplicate_ids else r["validation_issues"],
            axis=1,
        )

    print(f"Duplicate tender_id values found: {len(duplicate_ids)}")
    return df


# ── 6. Split into validated / rejected and save ──────────────────────────
def split_and_save(df):
    df["is_valid"] = df["validation_issues"].apply(lambda x: len(x) == 0)

    validated = df[df["is_valid"]].drop(columns=["validation_issues", "is_valid"])
    rejected = df[~df["is_valid"]].drop(columns=["is_valid"]).copy()
    rejected["validation_issues"] = rejected["validation_issues"].apply(lambda x: "; ".join(x))

    validated_path = PROCESSED_DIR / "validated.csv"
    rejected_path = PROCESSED_DIR / "rejected.csv"

    validated.to_csv(validated_path, index=False, encoding="utf-8-sig")
    rejected.to_csv(rejected_path, index=False, encoding="utf-8-sig")

    print(f"Validated: {len(validated)} -> {validated_path}")
    print(f"Rejected: {len(rejected)} -> {rejected_path}")

    if len(rejected):
        print("\nRejection reasons breakdown:")
        print(rejected["validation_issues"].value_counts())

    return validated, rejected


def main():
    df = load_cleaned()
    df = add_validation_issues(df)
    df = flag_duplicate_tender_ids(df)
    split_and_save(df)


if __name__ == "__main__":
    main()

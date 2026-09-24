"""
SEEK — schema validation (reference implementation).

The production version of this logic runs in Azure Data Factory. This module
is kept as readable reference code for the same rules.

Loads data/interim/cleaned.csv, checks every row, and splits the result in
data/processed/:

    validated.csv   rows that pass every check
    rejected.csv    rows that fail, with a validation_issues column giving the reason

Checks: required fields are present and non-empty, cost fields are numeric
when present, and tender_id is unique. Nothing is silently dropped.
"""

# ── 1. Imports ────────────────────────────────────────────────────────────
from pathlib import Path

import pandas as pd


# ── 2. Load cleaned data ─────────────────────────────────────────────────
INTERIM_DIR = Path("../data/interim")
PROCESSED_DIR = Path("../data/processed")
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def load_cleaned():
    """Load data/interim/cleaned.csv and print its columns."""
    df = pd.read_csv(INTERIM_DIR / "cleaned.csv")
    print(f"Loaded {len(df)} rows from cleaned.csv")
    print(df.columns.tolist())
    return df


# ── 3. Schema ────────────────────────────────────────────────────────────
# REQUIRED_COLUMNS: must be present and non-empty on every row.
# NUMERIC_COLUMNS:  if present and non-null, must parse as a number.
# tender_id must also be unique across the whole file (see section 5).
REQUIRED_COLUMNS = ["tender_id", "reference_number", "tender_name", "source_entity", "sector"]
NUMERIC_COLUMNS = ["buying_cost", "financial_fees", "invitation_cost"]


# ── 4. Validate every row ────────────────────────────────────────────────
def validate_row(row):
    """Return a list of issue codes for one row (empty list = valid)."""
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
    """Add a validation_issues column (a list of issue codes per row)."""
    df["validation_issues"] = df.apply(validate_row, axis=1)
    return df


# ── 5. Duplicate tender_id ───────────────────────────────────────────────
def flag_duplicate_tender_ids(df):
    """Add "duplicate_tender_id" to every row whose tender_id appears more than once.

    A duplicate would make the archive upsert ambiguous, so it is caught here.
    """
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
    """Write validated.csv and rejected.csv and print a breakdown of rejection reasons."""
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
    """Validate cleaned.csv and write validated.csv and rejected.csv."""
    df = load_cleaned()
    df = add_validation_issues(df)
    df = flag_duplicate_tender_ids(df)
    split_and_save(df)


if __name__ == "__main__":
    main()

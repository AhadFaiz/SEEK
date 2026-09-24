"""
SEEK output check script.

Verifies the Gold archive (tender_copy.csv) produced by the Azure Data Factory
pipeline, and reconciles it with the raw daily files from seek-data-landing.

Usage:
    python scripts/check_output.py --archive path/to/tender_copy.csv
    python scripts/check_output.py --archive path/to/tender_copy.csv --raw data/raw

Checks:
    1. CSV structure       - every row has the same number of columns as the header
    2. Keys                - reference_number is present and unique
    3. Clean output        - no helper columns, no line breaks inside values
    4. English coverage    - share of rows with Arabic left in each *_en column
    5. Sector distribution - tenders per sector
    6. Reconciliation      - (with --raw) raw tenders vs archive, and key fields
                             compared with each tender's latest raw snapshot
"""
import argparse
import csv
import glob
import json
import os
import re
from collections import Counter

ARABIC = re.compile("[\u0600-\u06FF]")
HELPER_COLUMNS = {"arabic_text", "english_text", "keyword", "match_rank", "priority"}
COMPARE_FIELDS = [
    "tender_name", "agency_name", "tender_type_name", "tender_status_name",
    "buying_cost", "financial_fees", "invitation_cost", "last_offer_presentation_date",
]


def snake(key):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def norm(value):
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def same(field, raw_value, archive_value):
    if field in ("buying_cost", "financial_fees", "invitation_cost"):
        try:
            return float(raw_value or 0) == float(archive_value or 0)
        except ValueError:
            return norm(raw_value) == norm(archive_value)
    if field.endswith("_date"):
        return norm(raw_value)[:16] == norm(archive_value)[:16]
    return norm(raw_value) == norm(archive_value)


def find_records(obj):
    if isinstance(obj, dict):
        if "referenceNumber" in obj:
            yield obj
        else:
            for value in obj.values():
                yield from find_records(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from find_records(value)


def check_archive(path):
    failures = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        broken = sum(1 for row in reader if len(row) != len(header))
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    columns = list(rows[0].keys()) if rows else header

    print(f"Archive: {path}")
    print(f"  rows: {len(rows)} | columns: {len(columns)}")

    print("\n[1] CSV structure")
    print(f"  rows with wrong column count: {broken}")
    failures += broken > 0

    print("\n[2] Keys")
    refs = [(r.get("reference_number") or "").strip() for r in rows]
    empty = sum(1 for x in refs if not x)
    dupes = len(refs) - len(set(refs))
    print(f"  empty reference_number: {empty}")
    print(f"  duplicate reference_number: {dupes}")
    failures += (empty > 0) + (dupes > 0)

    print("\n[3] Clean output")
    helpers = sorted(HELPER_COLUMNS & set(columns))
    breaks = sum(1 for r in rows for v in r.values() if v and ("\n" in v or "\r" in v))
    print(f"  helper columns left: {helpers or 'none'}")
    print(f"  line breaks inside values: {breaks}")
    failures += bool(helpers) + (breaks > 0)

    print("\n[4] English coverage (rows still containing Arabic)")
    for col in columns:
        if col.endswith("_en") or col == "sector":
            n = sum(1 for r in rows if ARABIC.search(r.get(col) or ""))
            share = n / len(rows) if rows else 0
            print(f"  {col}: {n} ({share:.1%})")

    print("\n[5] Sector distribution")
    sectors = Counter(
        "Other - needs review" if (r.get("sector") or "").startswith("Other") else r.get("sector")
        for r in rows
    )
    for sector, count in sectors.most_common():
        print(f"  {count:5}  {sector}")

    return rows, failures


def reconcile(rows, raw_dir):
    failures = 0
    archive = {r["reference_number"]: r for r in rows}
    files = sorted(glob.glob(os.path.join(raw_dir, "etimad_all_tenders_*.json")))
    latest = {}
    for path in files:  # file names sort by date, so the last snapshot wins
        with open(path, encoding="utf-8") as fh:
            for rec in find_records(json.load(fh)):
                latest[str(rec["referenceNumber"])] = {snake(k): v for k, v in rec.items()}

    print(f"\n[6] Reconciliation with raw files in {raw_dir}")
    print(f"  raw files: {[os.path.basename(f)[19:29] for f in files]}")
    missing = set(latest) - set(archive)
    extra = set(archive) - set(latest)
    print(f"  tenders in raw files: {len(latest)} | in archive: {len(archive)}")
    print(f"  missing from archive: {len(missing)}")
    print(f"  in archive but not in raw files: {len(extra)}")
    failures += bool(missing) + bool(extra)

    print("  field mismatches vs latest raw snapshot:")
    common = set(latest) & set(archive)
    for field in COMPARE_FIELDS:
        bad = [r for r in common if field in latest[r] and not same(field, latest[r][field], archive[r].get(field))]
        print(f"    {field}: {len(bad)}")
        failures += bool(bad)
    return failures


def main():
    parser = argparse.ArgumentParser(description="Check the SEEK Gold archive.")
    parser.add_argument("--archive", required=True, help="path to tender_copy.csv")
    parser.add_argument("--raw", help="folder with etimad_all_tenders_<date>.json files")
    args = parser.parse_args()

    rows, failures = check_archive(args.archive)
    if args.raw:
        failures += reconcile(rows, args.raw)

    print("\nRESULT:", "ALL CHECKS PASSED" if failures == 0 else f"{failures} check(s) need attention")


if __name__ == "__main__":
    main()

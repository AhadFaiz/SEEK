"""
SEEK — local entry point for the extraction step.

Runs the extractor in src/extract.py, which collects the day's tender list and
any new tender details from Etimad and saves them to data/raw/.

The same entry point is used by the Azure Function (azure_function/function_app.py)
on its daily schedule. Processing of the raw data runs in Azure Data Factory.

Run from the project root:
    python3 main.py
"""

import os
import sys
from pathlib import Path

# src/ modules use relative paths (e.g. ../data/raw), so run from inside src/.
SRC_DIR = Path(__file__).resolve().parent / "src"
os.chdir(SRC_DIR)
sys.path.insert(0, str(SRC_DIR))

import extract


def main():
    """Run each pipeline step in order and print a header for each."""
    steps = [
        ("Task 1 — Extract", extract.main),
    ]

    for title, step in steps:
        print("=" * 60)
        print(title)
        print("=" * 60)
        step()
        print()

    print("=" * 60)
    print("Pipeline complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()

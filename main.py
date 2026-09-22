"""
Main entry point
=================
Runs Task 1 (extraction) only.

Tasks 2-4 (clean/translate/classify, validate, archive) moved to Aseel's
Azure Data Factory pipeline as of the instructor's ADF-native-transformation
pivot — see SEEK_ADF_Handoff_for_Aseel.md for full details.

Task 5 (notifications) is temporarily removed from this file until it's
decided whether notifications move to ADF (reading tender_copy.csv from
seek-public) or stay Python-side reading from ADF's output container.

Run from the project root:
    python3 main.py
"""

import os
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
os.chdir(SRC_DIR)
sys.path.insert(0, str(SRC_DIR))

import extract


def main():
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
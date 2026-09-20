"""
Main entry point
=================
Runs the full SEEK pipeline end to end, in order:
extract -> profile/clean -> schema validation -> join & archive.

Equivalent to running each script by hand from inside src/
(`python3 extract.py`, `python3 profile.py`, `python3 schema.py`,
`python3 transform.py`) but as a single command — this is what a
scheduler (cron, GitHub Actions, an Azure Function) calls to run the
whole pipeline unattended, with no human running anything by hand.

Run from the project root:
    python3 main.py
"""

# ── 1. Make the src/ modules importable, with the right working directory ─
# extract.py, profile.py, schema.py, and transform.py all resolve their data
# paths as "../data/..." relative to the current working directory — that
# only works correctly when the working directory is src/ (matching how
# they're normally run by hand). Since main.py lives at the project root,
# switch into src/ before importing anything, and add src/ to sys.path so
# `import extract` etc. can find the modules there.
import os
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
os.chdir(SRC_DIR)
sys.path.insert(0, str(SRC_DIR))

import extract
import schema
import transform

# NOTE: "profile" is also the name of a Python standard-library module
# (the built-in performance profiler). Importing local profile.py works
# here only because SRC_DIR was placed at the front of sys.path above, so
# Python finds this project's profile.py before the stdlib one. This is a
# real naming collision, not just a style nitpick — if this module is ever
# imported from a different working directory or packaged differently (e.g.
# inside an Azure Function), the import order isn't guaranteed and it could
# silently pick up the wrong "profile". Since clean.py already exists and
# is unused, the safer long-term fix is renaming profile.py's contents into
# clean.py and dropping profile.py, rather than relying on import order.
import profile as profile_clean


# ── 2. Run every stage in order ──────────────────────────────────────────
def main():
    steps = [
        ("Task 1 — Extract", extract.main),
        ("Task 2 — Profile & Clean", profile_clean.main),
        ("Task 3 — Schema Validation", schema.main),
        ("Task 4 — Join & Transform", transform.main),
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

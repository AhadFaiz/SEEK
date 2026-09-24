"""Test setup: make src/ importable and run from inside src/.

extract.py resolves its data folder relative to src/ (../data/raw), exactly as
when it runs through main.py, so the tests import it from the same place.
"""
import os
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
os.chdir(SRC_DIR)
sys.path.insert(0, str(SRC_DIR))

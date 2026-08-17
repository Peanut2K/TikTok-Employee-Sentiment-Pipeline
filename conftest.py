"""Puts src/ on sys.path so `import sltiktok...` works from the repo root.

Imported automatically by pytest; the test files run standalone too, and
each one imports this module explicitly for that case.
"""
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

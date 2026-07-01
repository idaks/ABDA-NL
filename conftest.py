"""Pytest root configuration.

Adds the project root to `sys.path` so tests can import `app.*`
without an install step.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

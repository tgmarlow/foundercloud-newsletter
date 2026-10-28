#!/usr/bin/env python3
"""Render this week's Build Sheet into issues/ and update state/featured.json."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from newsletter.build import main

if __name__ == "__main__":
    raise SystemExit(main())

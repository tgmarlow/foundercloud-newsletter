#!/usr/bin/env python3
"""Create an unsent Zoho Campaigns draft. Never calls sendcampaign."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from newsletter.zoho import main

if __name__ == "__main__":
    raise SystemExit(main())

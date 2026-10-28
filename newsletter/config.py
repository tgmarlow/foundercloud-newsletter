"""Load newsletter.config.yml."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "newsletter.config.yml"


def load_config(root: Path | None = None) -> dict:
    path = (root or ROOT) / "newsletter.config.yml"
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must be a mapping")
    base = str(data.get("pages_base_url") or "").rstrip("/")
    data["pages_base_url"] = base
    for key in ("posts_per_issue", "founders_per_issue", "founders_minimum"):
        data[key] = int(data[key])
    return data

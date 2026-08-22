#!/usr/bin/env python3
"""Replace the repository owner placeholder before the first public push."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "YOUR_GITHUB_USERNAME"

if len(sys.argv) != 2 or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", sys.argv[1]):
    raise SystemExit("Usage: python3 tools/configure_repository.py GITHUB_USERNAME")

owner = sys.argv[1]
manifest_path = ROOT / "custom_components" / "kress_fleet" / "manifest.json"
manifest = json.loads(manifest_path.read_text())
manifest["codeowners"] = [f"@{owner}"]
manifest["documentation"] = f"https://github.com/{owner}/home-assistant-kress-fleet"
manifest["issue_tracker"] = f"https://github.com/{owner}/home-assistant-kress-fleet/issues"
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

for rel in ("README.md", "PUBLISHING.md"):
    path = ROOT / rel
    text = path.read_text()
    path.write_text(text.replace(PLACEHOLDER, owner))

print(f"Configured repository owner: {owner}")

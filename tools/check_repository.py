#!/usr/bin/env python3
"""Small dependency-free pre-publication hygiene checker."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

for path in ROOT.rglob("*"):
    if ".git" in path.parts:
        continue
    if path.is_dir() and path.name == "__pycache__":
        errors.append(f"Python cache directory must not be committed: {path.relative_to(ROOT)}")
    if path.is_file() and path.suffix == ".pyc":
        errors.append(f"Compiled Python file must not be committed: {path.relative_to(ROOT)}")

manifest_path = ROOT / "custom_components" / "kress_fleet" / "manifest.json"
manifest = json.loads(manifest_path.read_text())
if manifest.get("domain") != "kress_fleet":
    errors.append("manifest domain is not kress_fleet")
version = str(manifest.get("version", ""))
if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
    errors.append(f"manifest version is not valid semantic versioning: {version!r}")

# Catch common pasted-secret shapes without flagging documentation that merely
# names those secret types. Long opaque values after an assignment/header are
# what we care about.
secret_patterns = [
    re.compile(r"(?i)(?:access_token|x-xsrf-token|mqtt[_ -]?(?:token|signature)|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{24,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"(?i)(?:password|passwd)\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
]
text_suffixes = {".py", ".json", ".md", ".yml", ".yaml", ".txt"}
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in text_suffixes:
        continue
    if path == Path(__file__).resolve():
        continue
    text = path.read_text(errors="ignore")
    for pattern in secret_patterns:
        if pattern.search(text):
            errors.append(f"Possible secret in {path.relative_to(ROOT)} matching {pattern.pattern}")

if errors:
    print("Repository check failed:")
    for error in errors:
        print(f" - {error}")
    sys.exit(1)

print("Repository hygiene checks passed")

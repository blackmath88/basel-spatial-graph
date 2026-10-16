#!/usr/bin/env python3
"""Check local links/images in the public entry documents; no network requests.

This is a narrow structural check, not a Markdown parser or a claim that
external URLs, anchors, commands, accessibility or project behaviour work.
"""
from pathlib import Path
from urllib.parse import unquote, urlsplit
import re
import sys

root = Path(__file__).resolve().parents[1]
files = [root / "README.md", root / "CONTRIBUTING.md"]
files += sorted((root / ".github").glob("*GUIDE.md"))
errors = []
checked = 0
for file in files:
    if not file.is_file():
        errors.append(f"Missing document: {file.relative_to(root)}")
        continue
    for target in re.findall(r"\]\(([^)]+)\)", file.read_text()):
        parsed = urlsplit(target)
        if parsed.scheme or target.startswith("#"):
            continue
        path = unquote(parsed.path)
        if not path:
            continue
        checked += 1
        if not (file.parent / path).exists():
            errors.append(f"{file.relative_to(root)}: missing local target {target}")
if errors:
    print("\n".join(errors), file=sys.stderr)
    sys.exit(1)
print(f"PASS: {len(files)} entry documents, {checked} local link/image targets")
print("External URLs, anchors, runtime commands and rendered layout require separate verification.")

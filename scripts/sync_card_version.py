#!/usr/bin/env python3
"""Keep the Lovelace card's console banner version in lockstep with manifest.json.

The card prints its version to the browser console on load (a common HACS
convention). That string used to be bumped by hand, only when the card file
itself changed - which let it drift far behind the integration version
(v0.6.0 banner while manifest.json was already at v0.8.2, flagged during the
hacs/default review, see hacs/default#8672).

From now on the banner always mirrors manifest.json's version - no manual
judgement call, no drift possible.

Usage:
  python3 scripts/sync_card_version.py --check   # exit 1 if out of sync (CI gate)
  python3 scripts/sync_card_version.py --fix      # rewrite the banner to match
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "custom_components" / "recorder_throttle" / "manifest.json"
CARD_JS = REPO_ROOT / "custom_components" / "recorder_throttle" / "recorder-throttle-card.js"
# Version is captured directly (group 2) so callers never need to slice the match by hand.
BANNER_RE = re.compile(r'(console\.info\("%c recorder-throttle-card %c\s*)v([\d.]+)(\s*"\s*,)')


def manifest_version() -> str:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


def find_banner_match(text: str) -> re.Match:
    """Return the single banner match, or exit loudly if there isn't exactly one.

    A silent `search()` would only ever see the first hit and could hide drift
    if a second banner line ever creeps in; a bare `sub()` would then rewrite
    every occurrence without anyone noticing there were several.
    """
    matches = list(BANNER_RE.finditer(text))
    if not matches:
        sys.exit(f"Could not find the console.info version banner in {CARD_JS}")
    if len(matches) > 1:
        sys.exit(f"Found {len(matches)} version banners in {CARD_JS}, expected exactly 1 - fix manually.")
    return matches[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="exit 1 if the banner is out of sync (CI gate)")
    g.add_argument("--fix", action="store_true", help="rewrite the banner to match manifest.json")
    args = ap.parse_args()

    target = manifest_version()
    card_text = CARD_JS.read_text(encoding="utf-8")
    current = find_banner_match(card_text).group(2)

    if args.check:
        if current != target:
            print(f"[FAIL] Card banner says v{current}, manifest.json says v{target}.")
            print("Run: python3 scripts/sync_card_version.py --fix")
            return 1
        print(f"[OK] Card banner in sync (v{target}).")
        return 0

    # --fix
    if current == target:
        print(f"[OK] Already in sync (v{target}), nothing to do.")
        return 0
    new_content = BANNER_RE.sub(rf"\g<1>v{target}\g<3>", card_text, count=1)
    CARD_JS.write_text(new_content, encoding="utf-8")
    print(f"[FIXED] Card banner: v{current} -> v{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

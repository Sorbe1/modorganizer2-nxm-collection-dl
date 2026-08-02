#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_helpers import topLevelDownloadMetadataAudit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--downloads",
        type=Path,
        default=None,
        help=(
            "MO2 downloads directory. May also be supplied with "
            "NXM_COLLECTION_DL_DOWNLOADS."
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    downloads = args.downloads
    if downloads is None:
        env_downloads = os.environ.get("NXM_COLLECTION_DL_DOWNLOADS")
        if env_downloads:
            downloads = Path(env_downloads)
        else:
            parser.error("provide --downloads or set NXM_COLLECTION_DL_DOWNLOADS")

    audit = topLevelDownloadMetadataAudit(downloads)
    if args.json:
        print(json.dumps(audit, indent=2, sort_keys=True))
        return 0 if not audit["downloaded_only"] and not audit["missing_archive"] else 1

    print(f"Downloads: {downloads}")
    print(f"Metadata checked: {audit['checked']}")
    print(f"Installed: {len(audit['installed'])}")
    print(f"Downloaded only: {len(audit['downloaded_only'])}")
    print(f"Missing archive: {len(audit['missing_archive'])}")
    print(f"Unknown installed state: {len(audit['unknown_installed_state'])}")
    for label in ("downloaded_only", "missing_archive", "unknown_installed_state"):
        if not audit[label]:
            continue
        print(f"{label}:")
        for path in audit[label]:
            print(f"  {path}")
    return 0 if not audit["downloaded_only"] and not audit["missing_archive"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

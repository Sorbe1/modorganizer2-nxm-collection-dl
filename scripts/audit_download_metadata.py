#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_helpers import (
    topLevelDownloadMetadataAudit,
    validInstalledDownloadKeysForProfile,
)


DIRTY_AUDIT_KEYS = (
    "downloaded_only",
    "missing_archive",
    "unknown_installed_state",
    "installed_without_valid_container",
)


def resolve_download_audit_paths(downloads=None, base=None):
    """Return ``(downloads, base)`` for a Downloads metadata audit."""
    if downloads is not None:
        return Path(downloads), Path(base) if base is not None else None

    if base is None:
        env_base = os.environ.get("NXM_COLLECTION_DL_MO2_BASE")
        if env_base:
            base = Path(env_base)

    if base is not None:
        base = Path(base)
        return base / "downloads", base

    env_downloads = os.environ.get("NXM_COLLECTION_DL_DOWNLOADS")
    if env_downloads:
        return Path(env_downloads), None

    return None, None


def download_audit_is_dirty(audit):
    """Return True when any visible Downloads metadata state needs review."""
    audit = audit or {}
    return any(audit.get(key) for key in DIRTY_AUDIT_KEYS)


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
    parser.add_argument(
        "--base",
        type=Path,
        default=None,
        help=(
            "MO2 instance base directory. When supplied, the audit derives "
            "the downloads directory and validates installed sidecars against "
            "MO2 mod-container evidence. May also be supplied with "
            "NXM_COLLECTION_DL_MO2_BASE."
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    downloads, base = resolve_download_audit_paths(args.downloads, args.base)
    if downloads is None:
        parser.error(
            "provide --downloads, --base, NXM_COLLECTION_DL_DOWNLOADS, "
            "or NXM_COLLECTION_DL_MO2_BASE"
        )

    valid_installed_keys = None
    if base is not None:
        valid_installed_keys = validInstalledDownloadKeysForProfile(
            base,
            downloads_path=downloads,
        )

    audit = topLevelDownloadMetadataAudit(
        downloads,
        valid_installed_keys=valid_installed_keys,
    )
    dirty = download_audit_is_dirty(audit)
    if args.json:
        print(json.dumps(audit, indent=2, sort_keys=True))
        return 1 if dirty else 0

    print(f"Downloads: {downloads}")
    if base is not None:
        print(f"Base: {base}")
    print(f"Metadata checked: {audit['checked']}")
    print(f"Installed: {len(audit['installed'])}")
    print(f"Downloaded only: {len(audit['downloaded_only'])}")
    print(f"Missing archive: {len(audit['missing_archive'])}")
    print(f"Unknown installed state: {len(audit['unknown_installed_state'])}")
    print(
        "Installed without valid container: "
        f"{len(audit['installed_without_valid_container'])}"
    )
    for label in DIRTY_AUDIT_KEYS:
        if not audit[label]:
            continue
        print(f"{label}:")
        for path in audit[label]:
            print(f"  {path}")
    return 1 if dirty else 0


if __name__ == "__main__":
    raise SystemExit(main())

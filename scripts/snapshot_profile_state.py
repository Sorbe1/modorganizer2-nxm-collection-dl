#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_helpers import (
    MO2_PROFILE_STATE_FILES as PROFILE_FILES,
    profileStateFileStats as profile_file_stats,
    safeProfileSnapshotLabel as safe_label,
    snapshotMo2ProfileState,
)


def snapshot_profile_state(base, profile_name="Default", label="baseline"):
    return snapshotMo2ProfileState(
        base_path=Path(base), profile_name=profile_name, label=label
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        type=Path,
        default=None,
        help=(
            "MO2 instance base directory. May also be supplied with "
            "NXM_COLLECTION_DL_MO2_BASE."
        ),
    )
    parser.add_argument("--profile", default="Default")
    parser.add_argument("--label", default="baseline")
    args = parser.parse_args()

    base = args.base
    if base is None:
        env_base = os.environ.get("NXM_COLLECTION_DL_MO2_BASE")
        if env_base:
            base = Path(env_base)
        else:
            parser.error("provide --base or set NXM_COLLECTION_DL_MO2_BASE")

    snapshot_dir, manifest = snapshot_profile_state(
        base, profile_name=args.profile, label=args.label
    )
    print(f"Profile: {manifest['profile']}")
    print(f"Label: {manifest['label']}")
    for file_name in PROFILE_FILES:
        stats = manifest["files"][file_name]
        if stats["exists"]:
            print(
                f"{file_name}: {stats['lines']} lines, "
                f"{stats['enabled']} enabled, {stats['disabled']} disabled"
            )
        else:
            print(f"{file_name}: missing")
    audit = manifest.get("download_metadata_audit")
    if audit is not None:
        print(
            "downloads metadata: "
            f"{audit.get('checked', 0)} checked, "
            f"{audit.get('installed_count', 0)} installed, "
            f"{len(audit.get('downloaded_only', []))} downloaded-only, "
            f"{len(audit.get('missing_archive', []))} missing archive, "
            f"{len(audit.get('unknown_installed_state', []))} unknown state"
        )
    print(f"Snapshot: {snapshot_dir}")


if __name__ == "__main__":
    main()

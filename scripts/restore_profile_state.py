#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_helpers import restoreMo2ProfileStateSnapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "snapshot",
        type=Path,
        help="Snapshot directory created by scripts/snapshot_profile_state.py.",
    )
    parser.add_argument(
        "--profile-path",
        type=Path,
        default=None,
        help="Override the profile path recorded in the snapshot manifest.",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=None,
        help="Directory for the automatic pre-restore backup.",
    )
    parser.add_argument("--backup-label", default="pre-restore")
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help=(
            "Refuse to restore snapshots whose manifest profile audit contains "
            "disabled entries, bad base order, stale Downloads metadata, or "
            "other known profile issues."
        ),
    )
    args = parser.parse_args()

    result = restoreMo2ProfileStateSnapshot(
        args.snapshot,
        profile_path=args.profile_path,
        snapshot_root=args.backup_root,
        backup_label=args.backup_label,
        require_clean=args.require_clean,
    )
    print(f"Profile: {result['profile_path']}")
    print(f"Pre-restore backup: {result['backup_dir']}")
    print("Restored: " + ", ".join(result["restored"]))
    if result["skipped"]:
        print("Skipped missing snapshot files: " + ", ".join(result["skipped"]))


if __name__ == "__main__":
    main()

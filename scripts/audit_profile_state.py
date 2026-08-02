#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_helpers import auditMo2ProfileState


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path)
    parser.add_argument("--profile", default="Default")
    parser.add_argument("--profile-path", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when the profile audit reports issues.",
    )
    args = parser.parse_args()

    result = auditMo2ProfileState(
        base_path=args.base,
        profile_name=args.profile,
        profile_path=args.profile_path,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Profile: {result['profile']}")
        print(f"Path: {result['profile_path']}")
        print(f"Clean: {'yes' if result['clean'] else 'no'}")
        modlist = result["files"].get("modlist.txt") or {}
        plugins = result["files"].get("plugins.txt") or {}
        print(
            "Modlist: "
            f"{modlist.get('lines', 0)} lines, "
            f"{modlist.get('enabled', 0)} enabled, "
            f"{modlist.get('disabled', 0)} disabled"
        )
        print(
            "Plugins: "
            f"{plugins.get('lines', 0)} lines, "
            f"{plugins.get('enabled', 0)} enabled, "
            f"{plugins.get('disabled', 0)} disabled"
        )
        capacity = result.get("plugin_capacity_audit") or {}
        print(
            "Plugin capacity: "
            f"{capacity.get('regular_by_extension_count', 0)} regular by extension, "
            f"{capacity.get('light_by_extension_count', 0)} light by extension, "
            f"{capacity.get('regular_slots_remaining_by_extension', 0)} "
            "regular slots remaining by extension"
        )
        if capacity.get("regular_limit_exceeded_by_extension"):
            print(
                "Plugin capacity overage: "
                f"{capacity.get('regular_overage_by_extension', 0)} "
                "regular plugins by extension"
            )
        audit = result.get("download_metadata_audit") or {}
        print(
            "Downloads metadata: "
            f"{audit.get('checked', 0)} checked, "
            f"{audit.get('installed_count', 0)} installed, "
            f"{len(audit.get('downloaded_only', []))} downloaded-only, "
            f"{len(audit.get('missing_archive', []))} missing archive, "
            f"{len(audit.get('unknown_installed_state', []))} unknown state"
        )
        if result["issues"]:
            print("Issues:")
            for issue in result["issues"]:
                print(f"- {issue['type']}: {issue}")
        else:
            print("Issues: none")
        if result.get("warnings"):
            print("Warnings:")
            for warning in result["warnings"]:
                print(f"- {warning['type']}: {warning}")
        else:
            print("Warnings: none")

    if args.strict and not result["clean"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

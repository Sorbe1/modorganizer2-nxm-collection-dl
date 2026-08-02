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
            f"{capacity.get('regular_count', 0)} regular, "
            f"{capacity.get('light_count', 0)} light, "
            f"{capacity.get('regular_slots_remaining', 0)} "
            "regular slots remaining"
        )
        if capacity.get("header_capacity_supported"):
            print(
                "Plugin capacity evidence: "
                f"{capacity.get('esl_flagged_plugin_count', 0)} "
                "ESL-flagged plugin(s); "
                f"{capacity.get('unresolved_header_plugin_count', 0)} "
                "unresolved header(s)"
            )
        if capacity.get("regular_limit_exceeded"):
            print(
                "Plugin capacity overage: "
                f"{capacity.get('regular_overage', 0)} regular plugin(s)"
            )
        dependency = result.get("plugin_dependency_audit") or {}
        dependency_status = "yes" if dependency.get("supported") else "no"
        print(
            "Plugin dependency audit supported: "
            f"{dependency_status}; "
            f"{dependency.get('checked', 0)} checked, "
            f"{dependency.get('problem_count', 0)} problem(s)"
        )
        print(
            "Mod containers: "
            f"{len(result.get('transient_mod_dirs') or [])} transient, "
            f"{len(result.get('invalid_active_mod_containers') or [])} "
            "invalid active"
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
                warning_type = warning["type"]
                if warning_type == "modlist_ordering_diagnostics":
                    print(
                        "- modlist_ordering_diagnostics: "
                        f"{warning.get('count', 0)} likely ordering candidate(s)"
                    )
                    message = warning.get("message")
                    if message:
                        print(f"  {message}")
                    for example in warning.get("examples", [])[:10]:
                        if example.get("type") == "variant_before_base":
                            print(
                                "  - variant before base: "
                                f"{example.get('mod')} "
                                f"({example.get('priority')}) before "
                                f"{example.get('target')} "
                                f"({example.get('target_priority')})"
                            )
                        elif example.get("type") == "patch_before_target":
                            print(
                                "  - patch before target: "
                                f"{example.get('mod')} "
                                f"({example.get('priority')}) before "
                                f"{example.get('target')} "
                                f"({example.get('target_priority')})"
                            )
                        else:
                            print(f"  - {example}")
                elif warning_type in ("plugin_capacity", "plugin_capacity_by_extension"):
                    regular_count = warning.get(
                        "regular_count",
                        warning.get("regular_by_extension_count", 0),
                    )
                    overage = warning.get(
                        "regular_overage",
                        warning.get("regular_overage_by_extension", 0),
                    )
                    print(
                        f"- {warning_type}: "
                        f"{regular_count} regular plugin(s); "
                        f"overage {overage}"
                    )
                    message = warning.get("message")
                    if message:
                        print(f"  {message}")
                else:
                    print(f"- {warning_type}: {warning}")
        else:
            print("Warnings: none")

    if args.strict and not result["clean"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

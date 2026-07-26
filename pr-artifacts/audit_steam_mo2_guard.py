#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collection_helpers import steamMo2GuardAudit

DEFAULT_STEAM_ROOT = Path("/home/tkb/.local/share/Steam")
DEFAULT_LIBRARY_ROOT = Path("/mnt/STEAMNTFS/SteamLibrary")
DEFAULT_USER_ID = "19417571"
DEFAULT_APP_ID = "489830"
DEFAULT_MO2_BASE = (
    DEFAULT_LIBRARY_ROOT
    / "steamapps/compatdata/489830/pfx/drive_c/users/steamuser/AppData/Local/"
    "ModOrganizer/Skyrim Special Edition - Derp"
)


def read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_binary_text(path):
    try:
        return path.read_bytes().decode("latin-1", errors="replace")
    except OSError:
        return ""


def lsattr_line(path):
    try:
        completed = subprocess.run(
            ["lsattr", "-d", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return ""
    return completed.stdout.strip()


def child_count(path):
    try:
        return sum(1 for _ in path.iterdir())
    except OSError:
        return None


def audit(args):
    steam_root = args.steam_root
    library_root = args.library_root
    app_id = args.app_id
    user_config = steam_root / "userdata" / args.user_id / "config"
    mo2_base = args.mo2_base

    return steamMo2GuardAudit(
        localconfig_text=read_text(user_config / "localconfig.vdf"),
        steam_config_text=read_text(steam_root / "config" / "config.vdf"),
        compat_text=read_text(user_config / "compat.vdf"),
        appinfo_text=read_binary_text(steam_root / "appcache" / "appinfo.vdf"),
        gameprocess_log_text=read_text(steam_root / "logs" / "gameprocess_log.txt"),
        localconfig_lsattr=lsattr_line(user_config / "localconfig.vdf"),
        steam_config_lsattr=lsattr_line(steam_root / "config" / "config.vdf"),
        compat_lsattr=lsattr_line(user_config / "compat.vdf"),
        appinfo_lsattr=lsattr_line(steam_root / "appcache" / "appinfo.vdf"),
        appmanifest_lsattr=lsattr_line(
            library_root / "steamapps" / f"appmanifest_{app_id}.acf"
        ),
        shadercache_lsattr=lsattr_line(
            library_root / "steamapps" / "shadercache" / app_id
        ),
        mods_count=child_count(mo2_base / "mods"),
        downloads_count=child_count(mo2_base / "downloads"),
        app_id=app_id,
        expected_launch_options=args.expected_launch_options,
        expected_default_launch_option=args.expected_default_launch_option,
        expected_launch_executable=args.expected_launch_executable,
        require_clean_mo2=args.require_clean_mo2,
        require_latest_launch=args.require_latest_launch,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Fail-fast audit for Steam changes that break MO2 collection tests."
    )
    parser.add_argument("--steam-root", type=Path, default=DEFAULT_STEAM_ROOT)
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT)
    parser.add_argument("--mo2-base", type=Path, default=DEFAULT_MO2_BASE)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--app-id", default=DEFAULT_APP_ID)
    parser.add_argument(
        "--expected-launch-options",
        default="",
        help="Exact Steam LaunchOptions value required for the test.",
    )
    parser.add_argument(
        "--expected-default-launch-option",
        default="3",
        help="Steam DefaultLaunchOption value that selects the MO2 redirector entry.",
    )
    parser.add_argument(
        "--expected-launch-executable",
        default="mo2-redirector.exe",
        help="Executable that must appear in the latest Steam launch log when checked.",
    )
    parser.add_argument(
        "--require-clean-mo2",
        action="store_true",
        help="Also require empty managed mods and downloads directories.",
    )
    parser.add_argument(
        "--require-latest-launch",
        action="store_true",
        help="Also require the latest Steam gameprocess launch to use the redirector.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = audit(args)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = "PASS" if result["ok"] else "FAIL"
        print(f"Steam/MO2 guard: {status}")
        print(f"LaunchOptions: {result['launch_options']!r}")
        print(f"DefaultLaunchOption: {result['default_launch_option']!r}")
        if args.require_latest_launch:
            print(f"Latest launch: {result['latest_launch_command']!r}")
        print(
            "Shader queue: "
            + (", ".join(result["shader_processing_queue"]) or "(empty)")
        )
        print(f"Shader cache disabled: {result['shader_cache_disabled']}")
        print(f"Shader cache size: {result['shader_cache_size']}")
        print(
            "Appinfo has launch executable: "
            f"{result['appinfo_has_launch_executable']}"
        )
        print(f"MO2 mods/downloads: {result['mods_count']}/{result['downloads_count']}")
        for problem in result["problems"]:
            print(f"ERROR: {problem}")
        for warning in result["warnings"]:
            print(f"WARNING: {warning}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

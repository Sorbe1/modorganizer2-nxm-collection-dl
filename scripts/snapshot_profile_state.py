#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path


PROFILE_FILES = ("modlist.txt", "plugins.txt", "loadorder.txt")


def safe_label(value):
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    label = label.strip(".-")
    return label or "snapshot"


def profile_file_stats(path):
    if not path.exists():
        return {"exists": False, "lines": 0, "enabled": 0, "disabled": 0}

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    enabled = 0
    disabled = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("+", "*")):
            enabled += 1
        elif stripped.startswith("-"):
            disabled += 1
    return {
        "exists": True,
        "lines": len(lines),
        "enabled": enabled,
        "disabled": disabled,
    }


def snapshot_profile_state(base, profile_name="Default", label="baseline"):
    base = Path(base)
    profile = base / "profiles" / profile_name
    if not profile.exists():
        raise FileNotFoundError(f"MO2 profile not found: {profile}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot_dir = (
        base
        / "profile-snapshots"
        / f"{safe_label(profile_name)}-{safe_label(label)}-{timestamp}"
    )
    snapshot_dir.mkdir(parents=True, exist_ok=False)

    files = {}
    for file_name in PROFILE_FILES:
        source = profile / file_name
        destination = snapshot_dir / file_name
        if source.exists():
            shutil.copy2(source, destination)
        files[file_name] = profile_file_stats(source)

    manifest = {
        "base": str(base),
        "profile": profile_name,
        "label": label,
        "created": datetime.now().isoformat(timespec="seconds"),
        "files": files,
    }
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return snapshot_dir, manifest


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
    print(f"Snapshot: {snapshot_dir}")


if __name__ == "__main__":
    main()

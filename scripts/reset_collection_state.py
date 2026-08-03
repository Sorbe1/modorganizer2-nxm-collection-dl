#!/usr/bin/env python3
import argparse
import json
import re
import shutil
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path


def collection_identity(collection_file):
    data = json.loads(collection_file.read_text(encoding="utf-8"))
    entries = data.get("essentialMods", []) + data.get("chosenOptional", [])
    keys = set()
    mod_ids = set()
    names = set()
    for entry in entries:
        file_info = entry["file"]
        mod_info = file_info["mod"]
        mod_id = int(mod_info["modId"])
        file_id = int(file_info["fileId"])
        keys.add((mod_id, file_id))
        mod_ids.add(mod_id)
        names.add(str(mod_info["name"]).casefold())
        names.add(str(file_info["name"]).casefold())
    return data, keys, mod_ids, names


def installed_identity(meta_ini):
    mod_id = None
    file_ids = []
    installation_file = ""
    for line in meta_ini.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("modid="):
            try:
                mod_id = int(line.split("=", 1)[1])
            except ValueError:
                pass
        elif "\\fileid=" in line:
            try:
                file_ids.append(int(line.split("=", 1)[1]))
            except ValueError:
                pass
        elif line.startswith("installationFile="):
            installation_file = line.split("=", 1)[1].casefold()
    keys = set()
    if mod_id is not None:
        keys = {(mod_id, file_id) for file_id in file_ids}
    return mod_id, keys, installation_file


def name_without_mo2_suffix(name):
    base = name
    if " #" in base:
        stem, suffix = base.rsplit(" #", 1)
        if suffix.isdigit():
            base = stem
    return base.casefold()


def matching_mod_dirs(base, keys, mod_ids, names):
    matches = []
    for meta_ini in sorted((base / "mods").glob("*/meta.ini")):
        mod_id, installed_keys, installation_file = installed_identity(meta_ini)
        mod_name = name_without_mo2_suffix(meta_ini.parent.name)
        if installed_keys & keys:
            matches.append(meta_ini.parent)
        elif mod_id in mod_ids:
            matches.append(meta_ini.parent)
        elif mod_name in names:
            matches.append(meta_ini.parent)
        elif installation_file and any(
            f"-{mid}-" in installation_file for mid in mod_ids
        ):
            matches.append(meta_ini.parent)
    return matches


def download_meta_key(metadata_file):
    parser = ConfigParser()
    try:
        parser.read(metadata_file, encoding="utf-8")
        general = parser["General"]
        return int(general["modID"]), int(general["fileID"])
    except (OSError, KeyError, ValueError):
        return None


def infer_mod_id_from_download_name(name):
    clean_name = str(name)
    if clean_name.endswith(".unfinished"):
        clean_name = clean_name[: -len(".unfinished")]

    for match in re.finditer(r"-(\d+)(?=[-.])", clean_name):
        value = int(match.group(1))
        if value >= 1000:
            return value
    return None


def matching_orphan_unfinished_download_files(base, mod_ids):
    files = []
    for archive_file in sorted((base / "downloads").glob("*.unfinished")):
        metadata_file = Path(str(archive_file) + ".meta")
        if metadata_file.exists():
            continue
        if infer_mod_id_from_download_name(archive_file.name) in mod_ids:
            files.append(archive_file)
    return files


def matching_download_files(base, keys, mod_ids):
    files = []
    for metadata_file in sorted((base / "downloads").glob("*.meta")):
        key = download_meta_key(metadata_file)
        if key not in keys:
            continue
        archive_file = metadata_file.with_suffix("")
        files.append(metadata_file)
        if archive_file.exists():
            files.append(archive_file)
        unfinished_archive = Path(str(archive_file) + ".unfinished")
        unfinished_metadata = Path(str(archive_file) + ".unfinished.meta")
        if unfinished_archive.exists():
            files.append(unfinished_archive)
        if unfinished_metadata.exists():
            files.append(unfinished_metadata)
    files.extend(matching_orphan_unfinished_download_files(base, mod_ids))

    deduped = []
    seen = set()
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def update_list_file(path, removed_names, backup_dir):
    if not path.exists():
        return 0
    shutil.copy2(path, backup_dir / f"{path.name}.before-reset")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = []
    removed = 0
    for line in lines:
        prefix_stripped = line[1:] if line.startswith(("+", "-")) else line
        if prefix_stripped in removed_names or line in removed_names:
            removed += 1
            continue
        kept.append(line)
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


def delete_paths(paths):
    removed = []
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(path.name)
    return removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "collection", help="Collection metadata basename, e.g. 8vdyr1_12"
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=None,
        help=(
            "MO2 instance base directory. May also be supplied with "
            "NXM_COLLECTION_DL_MO2_BASE."
        ),
    )
    parser.add_argument("--keep-downloads", action="store_true")
    args = parser.parse_args()
    if args.base is None:
        import os

        env_base = os.environ.get("NXM_COLLECTION_DL_MO2_BASE")
        if env_base:
            args.base = Path(env_base)
        else:
            parser.error("provide --base or set NXM_COLLECTION_DL_MO2_BASE")

    collection_file = (
        args.base / "collections" / "skyrimspecialedition" / f"{args.collection}.json"
    )
    data, keys, mod_ids, names = collection_identity(collection_file)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = (
        args.base / "reset-backups" / f"{args.collection}-full-reset-{timestamp}"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)

    mod_dirs = matching_mod_dirs(args.base, keys, mod_ids, names)
    reset_mod_dirs = delete_paths(mod_dirs)
    removed_names = set(reset_mod_dirs)

    profile = args.base / "profiles" / "Default"
    modlist_removed = update_list_file(
        profile / "modlist.txt", removed_names, backup_dir
    )
    loadorder_removed = update_list_file(
        profile / "loadorder.txt", removed_names, backup_dir
    )
    plugins_removed = update_list_file(
        profile / "plugins.txt", removed_names, backup_dir
    )

    reset_downloads = []
    if not args.keep_downloads:
        reset_downloads = delete_paths(
            matching_download_files(args.base, keys, mod_ids)
        )

    manifest = {
        "collection": args.collection,
        "name": data.get("name"),
        "entries": len(keys),
        "reset_mod_dirs": reset_mod_dirs,
        "reset_download_files": reset_downloads,
        "modlist_removed": modlist_removed,
        "loadorder_removed": loadorder_removed,
        "plugins_removed": plugins_removed,
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    print(f"Collection: {args.collection} ({data.get('name')})")
    print(f"Collection entries: {len(keys)}")
    print(f"Reset mod dirs: {len(reset_mod_dirs)}")
    print(f"Reset download files: {len(reset_downloads)}")
    print(f"Removed modlist lines: {modlist_removed}")
    print(f"Removed loadorder lines: {loadorder_removed}")
    print(f"Removed plugins lines: {plugins_removed}")
    print(f"Backup: {backup_dir}")


if __name__ == "__main__":
    main()

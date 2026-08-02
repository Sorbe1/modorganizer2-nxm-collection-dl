import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import unicodedata
import zipfile
from configparser import ConfigParser
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

FOMOD_ADVANCE_EXCLUDED_TITLES = {
    "",
    "Error",
    "Install Mods",
    "Mod Exists",
    "Quick Install",
    "NXM Collection Installer - Installing Mods",
    "NXM Collection Installer - Select Collection",
}

EMPTY_INSTALLER_OUTPUT_REASON = (
    "installer completed but produced an empty mod container"
)
INVALID_INSTALLER_OUTPUT_REASON = (
    "installer completed but produced invalid MO2 game data"
)
EMPTY_OPTIONAL_FOMOD_OUTPUT_REASON = (
    "FOMOD completed with no applicable files for the active profile"
)
COLLECTION_TRANSIENT_MOD_DIR_PREFIXES = (
    ".nxm-collection-installing-",
    ".nxm-collection-extracting-",
)
MO2_PROFILE_STATE_FILES = ("modlist.txt", "plugins.txt", "loadorder.txt")

INSTALLER_SETTING_DEFAULTS = {
    "auto_accept_quick_install": True,
    "auto_dismiss_known_post_install_errors": True,
    "auto_cancel_invalid_install_content": True,
    "headless_archive_installs": True,
    "headless_zip_installs": True,
    "auto_merge_existing_mods": False,
    "auto_advance_fomod_defaults": True,
    "auto_advance_fomod_max_steps": 80,
    "install_files_as_separate_mods": True,
    "activate_mods_after_install": True,
    "activate_mods_during_install": False,
    "trace_install_diagnostics": False,
}

DIRECT_INSTALL_MARKER_DIRS = {
    "animations",
    "bodyslide",
    "calientetools",
    "grass",
    "interface",
    "meshes",
    "mcm",
    "music",
    "netscriptframework",
    "particlelights",
    "kreate",
    "scripts",
    "seq",
    "shaders",
    "skse",
    "sound",
    "strings",
    "textures",
}

DIRECT_INSTALL_MARKER_FILES = {
    "meta.ini",
}

DIRECT_INSTALL_MARKER_FILE_EXTENSIONS = {
    ".ini",
}

DIRECT_INSTALL_PLUGIN_EXTENSIONS = {
    ".esp",
    ".esm",
    ".esl",
}

IGNORABLE_ARCHIVE_ROOT_FILE_EXTENSIONS = {
    ".bmp",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".md",
    ".pdf",
    ".png",
    ".rtf",
    ".txt",
    ".url",
    ".webp",
}

KNOWN_GAME_ROOT_FILE_EVIDENCE = {
    # SSE Engine Fixes Part 2 installs beside SkyrimSE.exe rather than into an
    # MO2 mod container. A replay should treat the collection entry as complete
    # when the expected preloader DLL is already present in the game directory.
    (17230, 658442): ("d3dx9_42.dll",),
}

AUTOMATED_INSTALL_CADENCE_DEFAULTS = {
    "next_mod_delay_ms": 50,
    "dialog_poll_initial_delay_ms": 50,
    "dialog_poll_interval_ms": 50,
    "fomod_advance_interval_ms": 50,
    "restore_install_dialog_focus": False,
    "raise_mo2_for_native_install": False,
    "hide_progress_for_native_install": False,
}


def archiveInspectionSubprocessKwargs(
    timeout=30, capture_stdout=True, stderr_to_stdout=True
):
    """Return subprocess options safe for archive inspection from MO2's GUI process.

    MO2 runs plugin Python inside a GUI/Wine process where standard handles can be
    invalid. Explicitly redirect every standard stream so launching 7z/7zz does
    not inherit bad handles and fail with WinError 6.
    """
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.STDOUT if stderr_to_stdout else subprocess.DEVNULL,
        "timeout": timeout,
    }
    if capture_stdout:
        kwargs["stdout"] = subprocess.PIPE
    else:
        kwargs["stdout"] = subprocess.DEVNULL

    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    startf_use_show_window = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    if startupinfo_type and startf_use_show_window:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= startf_use_show_window
        kwargs["startupinfo"] = startupinfo

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags

    return kwargs


def safeProfileSnapshotLabel(value):
    """Return a filesystem-friendly label for MO2 profile snapshots."""
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    label = label.strip(".-")
    return label or "snapshot"


def profileStateFileStats(path):
    """Return simple order-file stats for a snapshot manifest."""
    path = Path(path)
    if not path.exists():
        return {
            "exists": False,
            "lines": 0,
            "enabled": 0,
            "disabled": 0,
            "bytes": 0,
            "sha256": None,
        }

    data = path.read_bytes()
    lines = data.decode("utf-8", errors="replace").splitlines()
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
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def snapshotMo2ProfileState(
    base_path=None,
    profile_name="Default",
    label="baseline",
    profile_path=None,
    snapshot_root=None,
    timestamp=None,
):
    """Copy MO2 profile order files and write a manifest before risky repairs."""
    if profile_path is None:
        if base_path is None:
            raise ValueError("base_path or profile_path is required")
        base_path = Path(base_path)
        profile_path = base_path / "profiles" / profile_name
    else:
        profile_path = Path(profile_path)
        if base_path is None:
            base_path = profile_path.parent.parent
        else:
            base_path = Path(base_path)
        profile_name = profile_name or profile_path.name

    if not profile_path.exists():
        raise FileNotFoundError(f"MO2 profile not found: {profile_path}")

    timestamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot_root = Path(snapshot_root) if snapshot_root is not None else (
        base_path / "profile-snapshots"
    )
    snapshot_dir = snapshot_root / (
        f"{safeProfileSnapshotLabel(profile_name)}-"
        f"{safeProfileSnapshotLabel(label)}-{timestamp}"
    )
    if snapshot_dir.exists():
        suffix = 2
        while (snapshot_dir.parent / f"{snapshot_dir.name}-{suffix}").exists():
            suffix += 1
        snapshot_dir = snapshot_dir.parent / f"{snapshot_dir.name}-{suffix}"
    snapshot_dir.mkdir(parents=True, exist_ok=False)

    files = {}
    for file_name in MO2_PROFILE_STATE_FILES:
        source = profile_path / file_name
        if source.exists():
            shutil.copy2(source, snapshot_dir / file_name)
        files[file_name] = profileStateFileStats(source)

    manifest = {
        "base": str(base_path),
        "profile_path": str(profile_path),
        "profile": profile_name,
        "label": label,
        "created": datetime.now().isoformat(timespec="seconds"),
        "files": files,
    }
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return snapshot_dir, manifest


def restoreMo2ProfileStateSnapshot(
    snapshot_dir,
    profile_path=None,
    snapshot_root=None,
    backup_label="pre-restore",
):
    """Restore MO2 profile order files from a verified snapshot manifest."""
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Snapshot manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if profile_path is None:
        profile_path = manifest.get("profile_path")
    if not profile_path:
        raise ValueError("profile_path is required when manifest has none")

    profile_path = Path(profile_path)
    if not profile_path.exists():
        raise FileNotFoundError(f"MO2 profile not found: {profile_path}")

    file_manifest = manifest.get("files") or {}
    verified = []
    restored = []
    skipped = []
    for file_name in MO2_PROFILE_STATE_FILES:
        stats = file_manifest.get(file_name) or {}
        if not stats.get("exists"):
            skipped.append(file_name)
            continue

        source = snapshot_dir / file_name
        if not source.exists():
            raise FileNotFoundError(f"Snapshot file not found: {source}")

        data = source.read_bytes()
        expected_hash = stats.get("sha256")
        if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError(f"Snapshot file hash mismatch: {file_name}")
        expected_size = stats.get("bytes")
        if expected_size is not None and len(data) != expected_size:
            raise ValueError(f"Snapshot file size mismatch: {file_name}")
        verified.append(file_name)

    backup_dir, backup_manifest = snapshotMo2ProfileState(
        profile_path=profile_path,
        profile_name=manifest.get("profile") or profile_path.name,
        label=backup_label,
        snapshot_root=snapshot_root,
    )

    for file_name in verified:
        shutil.copy2(snapshot_dir / file_name, profile_path / file_name)
        restored.append(file_name)

    return {
        "snapshot_dir": str(snapshot_dir),
        "profile_path": str(profile_path),
        "backup_dir": str(backup_dir),
        "backup_manifest": backup_manifest,
        "restored": restored,
        "skipped": skipped,
        "verified": verified,
    }


def backgroundWorkerSubprocessKwargs():
    """Return subprocess options safe for long-lived helper workers from MO2."""
    kwargs = archiveInspectionSubprocessKwargs(
        timeout=None, capture_stdout=False, stderr_to_stdout=False
    )
    kwargs.pop("timeout", None)
    if os.name != "nt":
        kwargs["start_new_session"] = True
    return kwargs


def sevenZipModuleConfigPathFromListing(listing_text):
    """Return the FOMOD ModuleConfig path from a 7z ``l -slt`` listing."""
    if isinstance(listing_text, bytes):
        listing_text = listing_text.decode("utf-8", errors="replace")
    for raw_line in str(listing_text).splitlines():
        if not raw_line.startswith("Path = "):
            continue
        candidate = raw_line[7:].replace("\\", "/")
        if candidate.lower().endswith("fomod/moduleconfig.xml"):
            return candidate
    return None


def normalizedArchiveMemberPath(member_name):
    """Return a safe normalized archive member path, or ``None`` if unsafe."""
    raw = str(member_name or "").replace("\\", "/")
    if raw.startswith("/"):
        return None
    normalized = raw
    normalized = re.sub(r"/+", "/", normalized).strip("/")
    if not normalized:
        return None
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if re.match(r"^[A-Za-z]:", parts[0]):
        return None
    return "/".join(parts)


def safeArchiveMemberTarget(base_dir, member_name):
    """Return the extraction target for an archive member, rejecting zip-slip."""
    normalized = normalizedArchiveMemberPath(member_name)
    if not normalized:
        return None
    base_path = Path(base_dir).resolve()
    target_path = (base_path / normalized).resolve()
    try:
        target_path.relative_to(base_path)
    except ValueError:
        return None
    return target_path


HEADLESS_ARCHIVE_EXTENSIONS = {
    ".7z",
    ".rar",
    ".zip",
}


def sevenZipArchiveMemberPaths(listing_text):
    """Return file/member paths from a ``7z l -slt`` listing."""
    if isinstance(listing_text, bytes):
        listing_text = listing_text.decode("utf-8", errors="replace")
    in_entries = False
    current_path = None
    current_is_dir = False
    paths = []

    def flush_current():
        if current_path and not current_is_dir:
            paths.append(current_path.replace("\\", "/"))

    for raw_line in str(listing_text).splitlines():
        line = raw_line.strip("\r")
        if line.startswith("----------"):
            in_entries = True
            flush_current()
            current_path = None
            current_is_dir = False
            continue
        if not in_entries:
            continue
        if line.startswith("Path = "):
            flush_current()
            current_path = line[7:]
            current_is_dir = False
            continue
        if line.startswith("Folder = "):
            current_is_dir = line[9:].strip() == "+"
            continue
        if line.startswith("Attributes = "):
            attributes = line[len("Attributes = ") :].casefold()
            current_is_dir = "d" in attributes
    flush_current()
    return paths


def zipArchiveMemberPaths(archive_path):
    """Return normalized member paths from a ZIP archive without shelling out."""
    with zipfile.ZipFile(archive_path) as archive:
        return [name.replace("\\", "/") for name in archive.namelist()]


def moveHeadlessArchivePayload(extract_root, target_dir, layout_plan):
    """Move a preflighted extracted archive tree into ``target_dir``."""
    if (layout_plan or {}).get("fomod_selection"):
        return moveHeadlessFomodSelectionPayload(extract_root, target_dir, layout_plan)

    strip_prefix = str((layout_plan or {}).get("strip_prefix") or "")
    extracted = 0
    extract_root = Path(extract_root)
    target_dir = Path(target_dir)
    for source_path in extract_root.rglob("*"):
        if not source_path.is_file():
            continue
        try:
            relative_name = source_path.relative_to(extract_root).as_posix()
        except ValueError:
            continue
        if strip_prefix and not relative_name.startswith(strip_prefix):
            continue
        output_name = (
            relative_name[len(strip_prefix) :] if strip_prefix else relative_name
        )
        target_path = safeArchiveMemberTarget(target_dir, output_name)
        if target_path is None:
            raise RuntimeError(f"Unsafe archive member path: {relative_name}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(target_path))
        extracted += 1
    if extracted <= 0:
        raise RuntimeError("Archive contained no installable files.")
    return extracted


def resolveHeadlessFomodSourcePath(extract_root, source):
    """Resolve a FOMOD source path inside an extracted archive tree."""
    extract_root = Path(extract_root).resolve()
    source = normalizedArchiveMemberPath(source)
    if not source:
        raise RuntimeError("Unsafe FOMOD source path.")

    direct_path = (extract_root / source).resolve()
    try:
        direct_path.relative_to(extract_root)
    except ValueError:
        raise RuntimeError(f"Unsafe FOMOD source path: {source}") from None
    if direct_path.exists():
        return direct_path

    normalized_source = source.casefold()
    source_name = Path(source).name.casefold()
    matches = []
    for candidate in extract_root.rglob("*"):
        try:
            relative = candidate.relative_to(extract_root).as_posix()
        except ValueError:
            continue
        normalized_relative = normalizedArchiveMemberPath(relative).casefold()
        if normalized_relative == normalized_source:
            matches.append(candidate)
            continue
        if normalized_relative.endswith("/" + normalized_source):
            matches.append(candidate)
            continue
        if "/" not in normalized_source and candidate.name.casefold() == source_name:
            matches.append(candidate)

    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        return unique_matches[0].resolve()
    if unique_matches:
        raise RuntimeError(f"Ambiguous selected FOMOD source: {source}")
    raise RuntimeError(f"Selected FOMOD source not found: {source}")


def moveHeadlessFomodSelectionPayload(extract_root, target_dir, layout_plan):
    """Move selected FOMOD file/folder mappings into ``target_dir``."""
    extract_root = Path(extract_root)
    target_dir = Path(target_dir)
    extracted = 0
    for mapping in (layout_plan or {}).get("mappings") or []:
        source = normalizedArchiveMemberPath(mapping.get("source"))
        destination = (
            str(mapping.get("destination") or "").replace("\\", "/").strip("/")
        )
        if not source:
            raise RuntimeError("Unsafe FOMOD source path.")

        source_path = resolveHeadlessFomodSourcePath(extract_root, source)

        if source_path.is_file():
            output_name = "/".join(
                part for part in (destination, source_path.name) if part
            )
            target_path = safeArchiveMemberTarget(target_dir, output_name)
            if target_path is None:
                raise RuntimeError(f"Unsafe FOMOD destination path: {output_name}")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(target_path))
            extracted += 1
            continue

        for child in source_path.rglob("*"):
            if not child.is_file():
                continue
            relative_child = child.relative_to(source_path).as_posix()
            output_name = "/".join(
                part for part in (destination, relative_child) if part
            )
            target_path = safeArchiveMemberTarget(target_dir, output_name)
            if target_path is None:
                raise RuntimeError(f"Unsafe FOMOD destination path: {output_name}")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(target_path))
            extracted += 1
    if extracted <= 0:
        raise RuntimeError("FOMOD selection contained no installable files.")
    return extracted


def headlessPayloadRootValid(target_dir):
    """Return True when an extracted mod folder looks valid to MO2."""
    target_dir = Path(target_dir)
    if not target_dir.exists():
        return False
    member_names = [
        path.relative_to(target_dir).as_posix()
        for path in target_dir.rglob("*")
        if path.is_file()
    ]
    for name in member_names:
        parts = name.split("/")
        if not parts:
            continue
        first = parts[0].casefold()
        suffix = Path(parts[0]).suffix.casefold()
        if first in DIRECT_INSTALL_MARKER_DIRS:
            return True
        if (
            len(parts) == 1
            and parts[0].casefold() != "meta.ini"
            and suffix in DIRECT_INSTALL_MARKER_FILE_EXTENSIONS
        ):
            return True
        if len(parts) == 1 and suffix in DIRECT_INSTALL_PLUGIN_EXTENSIONS:
            return True
    return False


def singleWrapperPayloadRoot(target_dir, max_depth=4):
    """Return the nested valid wrapper root that can be safely lifted, if any."""
    current = Path(target_dir)
    for _depth in range(max_depth):
        if headlessPayloadRootValid(current):
            return current
        children = [child for child in current.iterdir() if child.name != "meta.ini"]
        if len(children) != 1 or not children[0].is_dir():
            return None
        current = children[0]
    if headlessPayloadRootValid(current):
        return current
    return None


def repairSingleWrapperPayload(target_dir):
    """Lift a single valid wrapper folder into the mod root when safe."""
    target_dir = Path(target_dir)
    if headlessPayloadRootValid(target_dir):
        return False
    wrapper = singleWrapperPayloadRoot(target_dir)
    if wrapper is None or wrapper == target_dir:
        return False
    wrapper_children = [
        child for child in wrapper.iterdir() if child.name.casefold() != "meta.ini"
    ]
    for child in wrapper_children:
        destination = target_dir / child.name
        if destination.exists():
            return False
    for child in wrapper_children:
        shutil.move(str(child), str(target_dir / child.name))
    nested_meta = wrapper / "meta.ini"
    if nested_meta.exists():
        try:
            nested_meta.unlink()
        except OSError:
            return False
    current = wrapper
    while current != target_dir:
        parent = current.parent
        try:
            current.rmdir()
        except OSError:
            return False
        current = parent
    return True


def extractHeadlessZipArchive(archive_path, target_dir, layout_plan):
    """Extract a preflighted ZIP archive into ``target_dir`` and return file count."""
    strip_prefix = str((layout_plan or {}).get("strip_prefix") or "")
    extracted = 0
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            member_name = member.filename.replace("\\", "/")
            if member.is_dir():
                continue
            if strip_prefix and not member_name.startswith(strip_prefix):
                continue
            output_name = (
                member_name[len(strip_prefix) :] if strip_prefix else member_name
            )
            target_path = safeArchiveMemberTarget(target_dir, output_name)
            if target_path is None:
                raise RuntimeError(f"Unsafe archive member path: {member.filename}")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, open(target_path, "wb") as dest:
                shutil.copyfileobj(source, dest)
            extracted += 1
    if extracted <= 0:
        raise RuntimeError("Archive contained no installable files.")
    return extracted


def headlessInstallMetaIni(
    mod_id,
    file_id,
    archive_name,
    mod_name,
    file_name,
    timestamp,
    file_version="",
    mod_version="",
    nexus_category=0,
):
    """Return MO2 metadata for an already-extracted collection mod."""
    version = str(file_version or mod_version or "")
    newest_version = str(mod_version or file_version or "")
    try:
        nexus_category_value = int(nexus_category or 0)
    except (TypeError, ValueError):
        nexus_category_value = 0
    category = mo2CategoryField(nexus_category_value) or '"-1,"'
    return "\n".join(
        [
            "[General]",
            "gameName=SkyrimSE",
            f"modid={int(mod_id)}",
            "ignoredVersion=",
            f"version={version}",
            f"newestVersion={newest_version}",
            f"category={category}",
            "nexusFileStatus=1",
            f"installationFile={archive_name}",
            "repository=Nexus",
            "comments=",
            "notes=",
            "nexusDescription=",
            "url=",
            "hasCustomURL=false",
            f"lastNexusQuery={timestamp}",
            f"lastNexusUpdate={timestamp}",
            f"nexusLastModified={timestamp}",
            f"nexusCategory={nexus_category_value}",
            "converted=false",
            "validated=false",
            r"color=@Variant(\0\0\0\x43\0\xff\xff\0\0\0\0\0\0\0\0)",
            "tracked=0",
            "",
            "[installedFiles]",
            "size=1",
            f"1\\modid={int(mod_id)}",
            f"1\\fileid={int(file_id)}",
            "",
            "[NXMCollectionDL]",
            "installedBy=headless-archive",
            f"modName={mod_name}",
            f"fileName={file_name}",
            "",
        ]
    )


def mo2CategoryField(nexus_category):
    """Return MO2's quoted category list for a category id, or ``None`` if unknown."""
    try:
        category = int(nexus_category or 0)
    except (TypeError, ValueError):
        return None
    if category > 0:
        return f'"{category},"'
    return None


def mo2CategoryNameMap(categories_file):
    """Return ``{category name: category id}`` from MO2's categories.dat."""
    result = {}
    if not categories_file:
        return result
    try:
        lines = (
            Path(categories_file)
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
        )
    except OSError:
        return result
    for line in lines:
        parts = line.split("|")
        if len(parts) < 2:
            continue
        try:
            category_id = int(parts[0])
        except ValueError:
            continue
        name = parts[1].strip()
        if name:
            result[name.casefold()] = category_id
    return result


def collectionEntryMetadataFields(mod_info, category_name_map=None):
    """Return deterministic MO2 ``meta.ini`` fields available from a manifest entry."""
    file_data = (mod_info or {}).get("file") or {}
    nexus_mod = file_data.get("mod") or {}
    fields = {}

    file_version = str(file_data.get("version") or "")
    mod_version = str(nexus_mod.get("version") or "")
    if file_version or mod_version:
        fields["version"] = file_version or mod_version
    if mod_version:
        fields["newestVersion"] = mod_version
    elif file_version:
        fields["newestVersion"] = file_version

    category_value = nexus_mod.get("category")
    category_field = mo2CategoryField(category_value)
    if category_field:
        fields["nexusCategory"] = str(int(category_value))
        fields["category"] = category_field
    elif category_value:
        fields["nexusCategory"] = str(category_value)
        mapped_category = (category_name_map or {}).get(str(category_value).casefold())
        category_field = mo2CategoryField(mapped_category)
        if category_field:
            fields["category"] = category_field
    return fields


def collectionPluginActivationTargetModNames(installed_mods, mods_to_activate):
    """Return every installed collection container whose plugins should be reconciled."""
    return list(
        dict.fromkeys(list(installed_mods or []) + list(mods_to_activate or []))
    )


def collectionPluginNamesFromModDirs(mods_dir, mod_names):
    """Return plugin filenames present in the supplied MO2 mod containers."""
    mods_dir = Path(mods_dir)
    result = []
    seen = set()
    for mod_name in mod_names or []:
        mod_dir = mods_dir / mod_name
        if not mod_dir.exists():
            continue
        try:
            files = mod_dir.rglob("*")
            for path in files:
                if not path.is_file():
                    continue
                if path.suffix.casefold() not in DIRECT_INSTALL_PLUGIN_EXTENSIONS:
                    continue
                plugin_name = path.name
                key = plugin_name.casefold()
                if key in seen:
                    continue
                result.append(plugin_name)
                seen.add(key)
        except OSError:
            continue
    return result


def collectionInvalidPayloadModNames(mods_dir, mod_names):
    """Return installed collection containers that MO2 should not enable as game data."""
    mods_dir = Path(mods_dir)
    result = []
    for mod_name in mod_names or []:
        mod_dir = mods_dir / mod_name
        if not (mod_dir / "meta.ini").exists():
            continue
        if not headlessPayloadRootValid(mod_dir):
            result.append(mod_name)
    return result


def updateMetaIniGeneralFields(metadata_file, fields, backup_file=None):
    """Update selected ``[General]`` keys in-place without reserializing meta.ini."""
    fields = {str(k): str(v) for k, v in (fields or {}).items() if v is not None}
    if not fields:
        return False

    metadata_file = Path(metadata_file)
    try:
        lines = metadata_file.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        return False

    changed = False
    in_general = False
    seen = set()
    output = []
    insert_at = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_general and insert_at is None:
                insert_at = len(output)
            in_general = stripped.casefold() == "[general]"
        if in_general and "=" in line and not stripped.startswith(("#", ";")):
            key, _value = line.split("=", 1)
            key = key.strip()
            if key in fields:
                replacement = f"{key}={fields[key]}\n"
                if line != replacement:
                    line = replacement
                    changed = True
                seen.add(key)
        output.append(line)

    if in_general and insert_at is None:
        insert_at = len(output)
    if insert_at is not None:
        missing = [key for key in fields if key not in seen]
        if missing:
            output[insert_at:insert_at] = [f"{key}={fields[key]}\n" for key in missing]
            changed = True

    if changed:
        try:
            if backup_file is not None:
                backup_file = Path(backup_file)
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                if not backup_file.exists():
                    backup_file.write_bytes(metadata_file.read_bytes())
            metadata_file.write_text("".join(output), encoding="utf-8")
        except OSError:
            return False
    return changed


def repairInstalledCollectionModMetadata(
    mods_dir, installed_records, mods_by_key, category_name_map=None, backup_dir=None
):
    """Repair deterministic Nexus metadata fields for installed collection mods."""
    result = {"checked": 0, "repaired": 0, "failed": 0, "backed_up": 0}
    mods_dir = Path(mods_dir)
    backup_dir = Path(backup_dir) if backup_dir is not None else None
    for nexus_key, mod_names in (installed_records or {}).items():
        fields = collectionEntryMetadataFields(
            (mods_by_key or {}).get(nexus_key),
            category_name_map=category_name_map,
        )
        if not fields:
            continue
        for mod_name in mod_names:
            metadata_file = mods_dir / mod_name / "meta.ini"
            if not metadata_file.exists():
                result["failed"] += 1
                continue
            result["checked"] += 1
            backup_file = backup_dir / mod_name / "meta.ini" if backup_dir else None
            backup_preexisted = bool(backup_file and backup_file.exists())
            repaired = updateMetaIniGeneralFields(
                metadata_file, fields, backup_file=backup_file
            )
            if repaired:
                result["repaired"] += 1
                if backup_file and not backup_preexisted and backup_file.exists():
                    result["backed_up"] += 1
    return result


def _hasDirectInstallMarkers(paths):
    for path in paths:
        parts = path.split("/")
        if not parts:
            continue
        first = parts[0].casefold()
        suffix = Path(parts[-1]).suffix.casefold()
        if first in DIRECT_INSTALL_MARKER_DIRS:
            return True
        if first in DIRECT_INSTALL_MARKER_FILES:
            return True
        if len(parts) == 1 and suffix in DIRECT_INSTALL_PLUGIN_EXTENSIONS:
            return True
    return False


def _installPayloadPaths(paths):
    result = []
    for path in paths:
        parts = path.split("/")
        suffix = Path(parts[-1]).suffix.casefold() if parts else ""
        if (
            len(parts) == 1
            and suffix in IGNORABLE_ARCHIVE_ROOT_FILE_EXTENSIONS
        ):
            continue
        if (
            len(parts) == 2
            and suffix in IGNORABLE_ARCHIVE_ROOT_FILE_EXTENSIONS
            and parts[0].casefold() not in DIRECT_INSTALL_MARKER_DIRS
        ):
            continue
        result.append(path)
    return result


def _singleCommonWrapperLayout(payload_paths, max_depth=4):
    for depth in range(1, max_depth + 1):
        prefixes = set()
        stripped = []
        for path in payload_paths:
            parts = path.split("/")
            if len(parts) <= depth:
                prefixes = set()
                break
            prefixes.add("/".join(parts[:depth]) + "/")
            stripped.append("/".join(parts[depth:]))
        if len(prefixes) == 1 and _hasDirectInstallMarkers(stripped):
            return {
                "installable": True,
                "reason": "single common wrapper folder",
                "strip_prefix": next(iter(prefixes)),
            }
    return None


def _archiveLayoutDiagnostics(install_paths):
    roots = sorted({path.split("/", 1)[0] for path in install_paths if path})
    marker_roots = []
    plugin_files = []
    variant_data_prefixes = []
    for path in install_paths:
        parts = path.split("/")
        if not parts:
            continue
        first = parts[0].casefold()
        if first in DIRECT_INSTALL_MARKER_DIRS and parts[0] not in marker_roots:
            marker_roots.append(parts[0])
        if Path(path).suffix.casefold() in DIRECT_INSTALL_PLUGIN_EXTENSIONS:
            plugin_files.append(path)
        for index, part in enumerate(parts[:-1]):
            if part.casefold() != "data" or index == 0:
                continue
            prefix = "/".join(parts[: index + 1]) + "/"
            if prefix not in variant_data_prefixes:
                variant_data_prefixes.append(prefix)
    details = []
    if roots:
        details.append("top-level entries: " + ", ".join(roots[:8]))
    if marker_roots:
        details.append("direct game-data folders: " + ", ".join(marker_roots[:8]))
    if plugin_files:
        details.append("plugin files: " + ", ".join(plugin_files[:8]))
    if variant_data_prefixes:
        details.append(
            "nested Data candidates: " + ", ".join(variant_data_prefixes[:8])
        )
    return {
        "top_level_entries": roots[:20],
        "direct_game_data_folders": marker_roots[:20],
        "plugin_files": plugin_files[:20],
        "nested_data_candidates": variant_data_prefixes[:20],
        "summary": "; ".join(details),
    }


def headlessArchiveInstallLayout(member_names):
    """Return a conservative root-stripping plan for direct archive extraction."""
    payload_paths = []
    for name in member_names:
        normalized = normalizedArchiveMemberPath(name)
        if not normalized or str(name).replace("\\", "/").endswith("/"):
            continue
        payload_paths.append(normalized)

    if not payload_paths:
        return {"installable": False, "reason": "empty archive", "strip_prefix": ""}

    if any(
        path.casefold().endswith("fomod/moduleconfig.xml") for path in payload_paths
    ):
        return {
            "installable": False,
            "reason": "FOMOD installer present",
            "strip_prefix": "",
        }

    install_paths = _installPayloadPaths(payload_paths)
    if not install_paths:
        return {
            "installable": False,
            "reason": "documentation-only archive",
            "strip_prefix": "",
        }

    data_prefix = None
    data_rooted = []
    for path in install_paths:
        if not path.casefold().startswith("data/"):
            continue
        prefix = path[: len("Data/")]
        if data_prefix is None:
            data_prefix = prefix
        data_rooted.append(path[len(prefix) :])
    if len(data_rooted) == len(install_paths) and _hasDirectInstallMarkers(data_rooted):
        return {
            "installable": True,
            "reason": "data root layout",
            "strip_prefix": data_prefix or "Data/",
        }

    if _hasDirectInstallMarkers(install_paths):
        return {
            "installable": True,
            "reason": "mod root layout",
            "strip_prefix": "",
        }

    roots = {path.split("/", 1)[0] for path in install_paths}
    if len(roots) == 1:
        root = next(iter(roots))
        rooted = [path.split("/", 1)[1] for path in install_paths if "/" in path]
        data_prefix = None
        data_rooted = []
        for path in install_paths:
            parts = path.split("/", 2)
            if len(parts) < 3 or parts[0] != root or parts[1].casefold() != "data":
                continue
            prefix = f"{parts[0]}/{parts[1]}/"
            if data_prefix is None:
                data_prefix = prefix
            data_rooted.append(path[len(prefix) :])
        if len(data_rooted) == len(install_paths) and _hasDirectInstallMarkers(
            data_rooted
        ):
            return {
                "installable": True,
                "reason": "single wrapper Data folder",
                "strip_prefix": data_prefix or root + "/Data/",
            }
        if rooted and _hasDirectInstallMarkers(rooted):
            return {
                "installable": True,
                "reason": "single wrapper folder",
                "strip_prefix": root + "/",
            }

        variant_prefixes = []
        variant_payload_counts = {}
        variant_data_rooted = []
        all_variant_data_rooted = True
        for path in install_paths:
            parts = path.split("/", 3)
            if (
                len(parts) < 4
                or parts[0] != root
                or parts[2].casefold() != "data"
            ):
                all_variant_data_rooted = False
                break
            prefix = f"{parts[0]}/{parts[1]}/{parts[2]}/"
            if prefix not in variant_payload_counts:
                variant_prefixes.append(prefix)
                variant_payload_counts[prefix] = 0
            variant_payload_counts[prefix] += 1
            variant_data_rooted.append(path[len(prefix) :])
        if (
            all_variant_data_rooted
            and len(variant_prefixes) > 1
            and _hasDirectInstallMarkers(variant_data_rooted)
        ):
            return {
                "installable": True,
                "reason": "single wrapper variant Data folder",
                "strip_prefix": variant_prefixes[0],
            }

    common_wrapper = _singleCommonWrapperLayout(install_paths)
    if common_wrapper:
        return common_wrapper

    return {
        "installable": False,
        "reason": "ambiguous archive layout",
        "strip_prefix": "",
        "diagnostics": _archiveLayoutDiagnostics(install_paths),
    }


def headlessZipInstallLayout(member_names):
    """Backward-compatible alias for older ZIP-only callers."""
    return headlessArchiveInstallLayout(member_names)


def collectionInstallRoute(
    archive_path,
    fomod_state,
    separate_file_installs=True,
    manual_install_pass=False,
    normal_dialog_retry_pass=False,
    headless_archive_installs=True,
    headless_zip_installs=None,
):
    """Return the preferred installer route before the batch opens dialogs."""
    if headless_zip_installs is not None:
        headless_archive_installs = headless_zip_installs
    if not headless_archive_installs:
        return "mo2"
    if not separate_file_installs or manual_install_pass or normal_dialog_retry_pass:
        return "mo2"
    if fomod_state is True:
        return "mo2"
    if Path(str(archive_path)).suffix.casefold() not in HEADLESS_ARCHIVE_EXTENSIONS:
        return "mo2"
    return "headless-archive"


def shouldRetryInvalidInstalledCollectionArchive(fomod_state, layout_plan=None):
    """Return True when an invalid installed container should be reinstalled.

    A replay should not preserve an installed collection entry that produced no
    MO2-valid game data if the original archive has a real installer or a safe
    headless layout. Ambiguous non-FOMOD payloads stay installed-but-disabled so
    replay does not open Quick Install as an accidental fallback.
    """
    if fomod_state is True:
        return True
    if layout_plan and layout_plan.get("installable"):
        return True
    return False


def installedPayloadFileCount(mod_dir):
    """Return installed files other than MO2 metadata for an installed mod directory."""
    mod_dir = Path(mod_dir)
    count = 0
    try:
        for path in mod_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(mod_dir)
            except ValueError:
                relative = path
            if len(relative.parts) == 1 and relative.name.casefold() == "meta.ini":
                continue
            count += 1
    except OSError:
        return 0
    return count


def installedModHasCompletionPayload(mod_dir):
    """Return True when an installer result contains files beyond MO2 metadata."""
    return installedPayloadFileCount(mod_dir) > 0


def installedModCompletionIssueReason(mod_dir):
    """Return why an installed mod container is not usable, or None."""
    if not installedModHasCompletionPayload(mod_dir):
        return EMPTY_INSTALLER_OUTPUT_REASON
    if not headlessPayloadRootValid(mod_dir):
        return INVALID_INSTALLER_OUTPUT_REASON
    return None


def invalidInstalledCollectionPlanAction(mod_dir, source_archive_available):
    """Return how a planner should treat an already-installed collection entry."""
    if installedModCompletionIssueReason(mod_dir) is None:
        return "installed"
    if not source_archive_available:
        return "fail-missing-archive"
    if not installedModHasCompletionPayload(mod_dir):
        return "repair-empty"
    return "review-invalid"


def isBenignEmptyFomodInstallerResult(fomod_state, guide):
    """Return True when an empty FOMOD result is a valid no-op for this profile."""
    if fomod_state is not True:
        return False
    if not isinstance(guide, dict):
        return False
    if not guide.get("module_config"):
        return False
    if guide.get("error") or guide.get("parse_error"):
        return False
    if guide.get("manual_choices") or guide.get("safe_singleton_prompts"):
        return False
    return True


def shouldQueueFomodProbeRetry(failed_entry):
    """Return True when a failed FOMOD entry is safe for blind Next/Install retry.

    Default FOMOD automation is useful for installer dialogs whose first pass did
    not reach a terminal result. It is not useful after MO2 successfully creates
    a metadata-only container: retrying the same archive with the same default
    choices just reopens modal prompts and produces another empty result.
    """
    if not failed_entry or failed_entry.get("fomod_state") != "true":
        return False
    if not failed_entry.get("archive"):
        return False
    reason = str(failed_entry.get("reason") or "").casefold()
    if EMPTY_INSTALLER_OUTPUT_REASON in reason:
        return False
    if "manual fomod choices required" in reason:
        return False
    return True


def splitQueuedFomodRecoveryEntries(failed_entries, enabled):
    """Separate review failures from entries queued for FOMOD recovery probes."""
    queued_entries = []
    queued_keys = set()
    if enabled:
        for entry in failed_entries or []:
            if not shouldQueueFomodProbeRetry(entry):
                continue
            archive = entry.get("archive")
            target = entry.get("target_mod_name") or entry.get("mod")
            queued_entry = {
                "archive": str(archive),
                "target": str(target) if target else None,
                "observe": False,
            }
            queued_entries.append(queued_entry)
            queued_keys.add((queued_entry["archive"], queued_entry["target"]))

    review_entries = []
    for entry in failed_entries or []:
        key = (
            str(entry.get("archive")),
            str(entry.get("target_mod_name") or entry.get("mod")),
        )
        if key in queued_keys:
            continue
        review_entries.append(entry)
    return review_entries, queued_entries


def warningReportNeedsWrite(
    warnings,
    failed_entries,
    root_level_entries,
    no_applicable_entries,
    queued_fomod_recovery_entries,
    recovery_count,
    download_metadata_audit=None,
):
    """Return True when the installer has review data worth persisting."""
    download_metadata_audit = download_metadata_audit or {}
    try:
        recovery_count = int(recovery_count)
    except (TypeError, ValueError):
        recovery_count = 0
    return bool(
        warnings
        or failed_entries
        or root_level_entries
        or no_applicable_entries
        or queued_fomod_recovery_entries
        or recovery_count > 0
        or download_metadata_audit.get("downloaded_only")
        or download_metadata_audit.get("missing_archive")
        or download_metadata_audit.get("unknown_installed_state")
    )


def headlessArchivePreflightFallback(layout_plan):
    """Return the next route when a candidate archive is not headless-safe."""
    reason = str((layout_plan or {}).get("reason") or "").casefold()
    if "fomod installer present" in reason:
        return "mo2"
    # Unknown, ambiguous, or adapter-failed archives should be reported for
    # manual review instead of falling back into focus-stealing Quick Install UI.
    return "manual"


def isTransientManualFomodPlanFailure(reason):
    """Return True for retryable FOMOD planning infrastructure failures."""
    reason_text = str(reason or "").casefold()
    return (
        "native archive worker is not running" in reason_text
        or "native archive worker unavailable" in reason_text
        or "native archive worker timed out" in reason_text
        or "native archive worker result unreadable" in reason_text
        or "could not list archive with subprocess 7z" in reason_text
        or "invalid handle" in reason_text
        or (
            "fomod xml parse error" in reason_text
            and "line 1, column 0" in reason_text
        )
    )


def nativePathForArchiveInspection(path_text, wineprefix=None):
    """Convert Wine drive paths to native host paths for external 7z/7zz."""
    text = str(path_text)
    if len(text) < 3 or text[1] != ":" or text[2] not in ("\\", "/"):
        return text.replace("\\", "/") if text.startswith("\\") else text

    drive = text[0].lower()
    tail = text[3:].replace("\\", "/")
    if drive == "z":
        return "/" + tail.lstrip("/")

    prefix = wineprefix or os.environ.get("WINEPREFIX")
    if not prefix and os.environ.get("STEAM_COMPAT_DATA_PATH"):
        prefix = str(Path(os.environ["STEAM_COMPAT_DATA_PATH"]) / "pfx")
    if drive == "c" and prefix:
        native_prefix = nativePathForArchiveInspection(prefix, wineprefix="")
        return native_prefix.rstrip("/\\").replace("\\", "/") + "/drive_c/" + tail

    return text


def preferredCanonicalDownloadArchive(download_path):
    """Prefer MO2's original archive over numbered duplicate downloads."""
    path = Path(download_path)
    canonical_name = re.sub(r"^\d+_", "", path.name)
    if canonical_name == path.name:
        return path

    canonical_path = path.with_name(canonical_name)
    try:
        if (
            canonical_path.exists()
            and canonical_path.is_file()
            and canonical_path.stat().st_size == path.stat().st_size
        ):
            return canonical_path
    except OSError:
        return path

    return path


def isQuotaLimitText(text):
    """Return True when text looks like a Nexus quota/rate-limit stop."""
    normalized = " ".join(str(text or "").casefold().split())
    if not normalized:
        return False

    if re.search(r"\b(?:http|status|code|response)\s*[:=]?\s*429\b", normalized):
        return True

    quota_patterns = (
        "too many requests",
        "rate limit",
        "rate-limit",
        "rate limited",
        "ratelimit",
        "quota exhausted",
        "quota exceeded",
        "quota limit",
        "daily limit reached",
        "daily api limit reached",
        "hourly limit reached",
        "hourly api limit reached",
        "download limit reached",
        "request limit reached",
        "api usage limit",
    )
    return any(pattern in normalized for pattern in quota_patterns)


def nexusQuotaRemainingFromText(text):
    """Parse MO2's visible Nexus quota status text when available."""
    normalized = str(text or "")
    match = re.search(
        r"\bDaily:\s*([0-9]+)\s*\|\s*Hourly:\s*([0-9]+)\b",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return {}
    return {"hourly": int(match.group(2)), "daily": int(match.group(1))}


def retryAfterSeconds(value, now=None):
    """Parse an HTTP Retry-After value into seconds."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0, int(float(text)))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_at is None:
        return None

    try:
        import datetime as _datetime

        now_dt = now or _datetime.datetime.now(retry_at.tzinfo)
        return max(0, int((retry_at - now_dt).total_seconds()))
    except Exception:
        return None


def _intHeaderValue(headers, *names):
    for name in names:
        value = headers.get(name)
        if value is None:
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def nexusQuotaStateFromHeaders(headers, status=None, now=None):
    """Return normalized Nexus quota state from response headers, if present."""
    import time as _time

    try:
        header_items = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    except AttributeError:
        return None

    interesting_names = {
        "x-ratelimit-hourly-remaining",
        "x-ratelimit-daily-remaining",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "x-rate-limit-reset",
        "retry-after",
    }
    interesting_headers = {
        key: value for key, value in header_items.items() if key in interesting_names
    }
    if not interesting_headers:
        return None

    remaining = {}
    hourly = _intHeaderValue(header_items, "x-ratelimit-hourly-remaining")
    daily = _intHeaderValue(header_items, "x-ratelimit-daily-remaining")
    generic = _intHeaderValue(header_items, "x-ratelimit-remaining")
    if hourly is not None:
        remaining["hourly"] = hourly
    if daily is not None:
        remaining["daily"] = daily
    if generic is not None and not remaining:
        remaining["generic"] = generic

    reset_epoch = _intHeaderValue(
        header_items, "x-ratelimit-reset", "x-rate-limit-reset"
    )
    retry_after = retryAfterSeconds(header_items.get("retry-after"), now=None)
    if retry_after is None and reset_epoch is not None:
        retry_after = max(
            0, reset_epoch - int(now if now is not None else _time.time())
        )

    return {
        "observed_at": int(now if now is not None else _time.time()),
        "status": status,
        "remaining": remaining,
        "retry_after_seconds": retry_after,
        "reset_epoch": reset_epoch,
        "headers": interesting_headers,
    }


def quotaLimitMessage(status=None, headers=None, body=""):
    """Return a user-facing quota message when HTTP/details indicate a limit."""
    header_items = {}
    if headers:
        try:
            header_items = {str(k).lower(): str(v) for k, v in headers.items()}
        except AttributeError:
            header_items = {}

    retry_after = retryAfterSeconds(header_items.get("retry-after"))
    reset_value = header_items.get("x-ratelimit-reset") or header_items.get(
        "x-rate-limit-reset"
    )
    if retry_after is None and reset_value:
        try:
            import time as _time

            retry_after = max(0, int(float(reset_value) - _time.time()))
        except ValueError:
            retry_after = None

    limited = status == 429 or isQuotaLimitText(body)
    if not limited:
        limited = any(isQuotaLimitText(f"{k}: {v}") for k, v in header_items.items())
    if not limited:
        return None

    if retry_after is not None:
        return (
            f"Nexus quota/rate limit reached; retry after about {retry_after} seconds."
        )
    return "Nexus quota/rate limit reached; pause downloads and retry later."


def quotaResumeDelaySeconds(message, default_seconds=3600, attempt=0, max_seconds=7200):
    """Return bounded automatic retry delay for a quota stop message."""
    retry_match = re.search(r"retry after about\s+([0-9]+)\s+seconds", str(message))
    if retry_match:
        delay = int(retry_match.group(1))
    else:
        delay = int(default_seconds) * (2 ** max(0, int(attempt)))
    return max(0, min(int(max_seconds), delay))


def proactiveQuotaStopMessage(remaining, hourly_floor=50, daily_floor=50):
    """Return a proactive pause message when remaining quota is near a floor."""
    remaining = remaining or {}
    hourly = remaining.get("hourly")
    daily = remaining.get("daily")
    if hourly is not None and int(hourly) <= int(hourly_floor):
        return (
            "Nexus hourly API quota is near the safety floor "
            f"({int(hourly)} remaining); downloads will resume automatically after reset."
        )
    if daily is not None and int(daily) <= int(daily_floor):
        return (
            "Nexus daily API quota is near the safety floor "
            f"({int(daily)} remaining); downloads will resume automatically after reset."
        )
    return None


def normalizedButtonLabel(label):
    return " ".join(
        str(label)
        .replace("&", "")
        .replace("<", "")
        .replace(">", "")
        .strip()
        .lower()
        .split()
    )


def isRequiredFomodGroupTitle(title):
    """Return True for FOMOD option groups that are safe to auto-select.

    Some FOMODs leave a single required choice unchecked, which blocks the
    normal Next/Install button path. Restricting auto-selection to required
    looking groups avoids silently opting into ordinary optional patches.
    """
    normalized = normalizedButtonLabel(title)
    if not normalized:
        return False

    required_markers = {
        "required",
        "main",
        "main file",
        "main files",
        "base",
        "bases",
        "base mod",
        "base plugin",
        "install",
        "select resolution",
        "select texture size",
        "texture resolution",
        "texture size",
    }
    if normalized in required_markers:
        return True

    return any(
        marker in normalized
        for marker in (
            "required",
            "main file",
            "base mod",
            "base plugin",
            "select resolution",
            "texture resolution",
            "texture size",
        )
    )


def isSafeSingletonFomodOption(group_title, option_label):
    """Return True for one-option FOMOD groups that are safe to auto-select."""
    if isRequiredFomodGroupTitle(group_title):
        return True

    normalized_group = normalizedButtonLabel(group_title)
    normalized_option = normalizedButtonLabel(option_label)
    informational_groups = {
        "",
        "do you know what you're doing?",
        "finish installation",
        "inform",
        "note about config file",
        "quick notice",
        "quick notice.",
        "read first",
        "user information",
        "welcome",
    }
    informational_actions = {
        "continue",
        "dont forget to check config.txt",
        "next",
        "ok",
        "okay!",
        "proceed",
        "start the installation",
        "thank you!",
    }
    return (
        normalized_group in informational_groups
        and normalized_option in informational_actions
    )


def preferredRequiredFomodFallbackOption(option_labels):
    """Return the safest fallback label for an otherwise unselected required group."""
    preferred = (
        "none",
        "no",
        "skip",
        "do not install",
        "do not use",
        "not installed",
    )
    normalized_options = [
        (normalizedButtonLabel(label), label) for label in (option_labels or [])
    ]
    for preferred_label in preferred:
        for normalized, original in normalized_options:
            if normalized == preferred_label:
                return original
    return None


def _xmlLocalName(tag):
    return str(tag).rsplit("}", 1)[-1]


def _directChildren(element, name):
    return [child for child in list(element) if _xmlLocalName(child.tag) == name]


def decodedXmlText(xml_payload):
    """Decode XML bytes from archives, including UTF-16 FOMOD configs."""
    if isinstance(xml_payload, str):
        return xml_payload
    data = bytes(xml_payload or b"")
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        if "<" in text and "\x00" not in text[:100]:
            return text
    return data.decode("utf-8", errors="replace")


def _fomodModuleBasePrefix(module_config_path):
    normalized = normalizedArchiveMemberPath(module_config_path)
    if not normalized:
        return ""
    lower = normalized.casefold()
    marker = "fomod/moduleconfig.xml"
    if lower.endswith(marker):
        return normalized[: -len(marker)]
    return ""


def _fomodSelectionEvidenceLabel(text):
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = Path(normalized.replace("\\", "/")).name
    normalized = re.sub(r"\.[Ee][Ss][PpMmLl]$", "", normalized)
    normalized = re.sub(r"^\s*\d+[\s._-]+", "", normalized)
    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", normalized)
    normalized = normalized.casefold()
    normalized = re.sub(r"\b(patch|patches)\s+for\b", " ", normalized)
    normalized = re.sub(r"\b(patch|patches|locations|location)\b", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _fomodMeaningfulTokens(label):
    stop_words = {"a", "an", "and", "for", "of", "se", "sse", "the", "to", "with"}
    return [
        token
        for token in _fomodSelectionEvidenceLabel(label).split()
        if token and token not in stop_words
    ]


def _fomodOptionMatchesEvidence(option_name, evidence_labels):
    option_label = _fomodSelectionEvidenceLabel(option_name)
    if not option_label:
        return False
    option_tokens = _fomodMeaningfulTokens(option_label)
    if not option_tokens:
        return False
    for evidence_label in evidence_labels:
        evidence_tokens = _fomodMeaningfulTokens(evidence_label)
        if not evidence_tokens:
            continue
        evidence_text = " ".join(evidence_tokens)
        option_text = " ".join(option_tokens)
        if option_text == evidence_text:
            return True
        if len(option_tokens) >= 2 and option_text in evidence_text:
            return True
        if len(evidence_tokens) >= 2 and evidence_text in option_text:
            return True
    return False


def _fomodOptionEvidenceScore(option_name, evidence_labels):
    option_tokens = _fomodMeaningfulTokens(option_name)
    if not option_tokens:
        return 0
    option_text = " ".join(option_tokens)
    score = 0
    for evidence_label in evidence_labels:
        evidence_tokens = _fomodMeaningfulTokens(evidence_label)
        if not evidence_tokens:
            continue
        evidence_text = " ".join(evidence_tokens)
        if option_text == evidence_text:
            score += 100 + len(option_tokens)
            continue
        if len(option_tokens) >= 2 and option_text in evidence_text:
            score += 10 + len(option_tokens)
            continue
        if len(evidence_tokens) >= 2 and evidence_text in option_text:
            score += 10 + len(evidence_tokens)
            continue
        common_tokens = set(option_tokens).intersection(evidence_tokens)
        score += len(common_tokens)
    return score


def _fomodPluginFileMappings(plugin, module_base_prefix):
    mappings = []
    for files_node in _directChildren(plugin, "files"):
        for child in list(files_node):
            local_name = _xmlLocalName(child.tag)
            if local_name not in {"file", "folder"}:
                continue
            source = child.attrib.get("source", "")
            if not source:
                continue
            normalized_source = normalizedArchiveMemberPath(
                module_base_prefix + source.replace("\\", "/")
            )
            if not normalized_source:
                continue
            mappings.append(
                {
                    "type": local_name,
                    "source": normalized_source,
                    "destination": child.attrib.get("destination", ""),
                    "priority": child.attrib.get("priority", "0"),
                }
            )
    return mappings


def _fomodPluginType(plugin):
    for descriptor in _directChildren(plugin, "typeDescriptor"):
        for type_node in _directChildren(descriptor, "type"):
            return type_node.attrib.get("name", "")
    return ""


def _fomodEvidenceFileNames(evidence_names):
    names = set()
    for name in evidence_names or []:
        base = Path(str(name or "").replace("\\", "/")).name.casefold()
        if base:
            names.add(base)
    return names


def _fomodDependencyMatches(dependencies_node, evidence_files):
    operator = dependencies_node.attrib.get("operator", "And").casefold()
    results = []
    for dependency in list(dependencies_node):
        if _xmlLocalName(dependency.tag) != "fileDependency":
            continue
        dependency_file = Path(
            dependency.attrib.get("file", "").replace("\\", "/")
        ).name.casefold()
        if not dependency_file:
            continue
        dependency_state = dependency.attrib.get("state", "").casefold()
        if dependency_state == "active":
            results.append(dependency_file in evidence_files)
        elif dependency_state == "missing":
            results.append(dependency_file not in evidence_files)
        elif dependency_state == "inactive":
            results.append(dependency_file not in evidence_files)
        else:
            results.append(False)
    if not results:
        return False
    if operator == "or":
        return any(results)
    return all(results)


def _fomodDependencyPluginType(plugin, evidence_files):
    for descriptor in _directChildren(plugin, "typeDescriptor"):
        for dependency_type in _directChildren(descriptor, "dependencyType"):
            default_type = "optional"
            for default_node in _directChildren(dependency_type, "defaultType"):
                default_type = default_node.attrib.get("name", default_type).casefold()
                break
            for patterns_node in _directChildren(dependency_type, "patterns"):
                for pattern in _directChildren(patterns_node, "pattern"):
                    dependencies_match = False
                    for dependencies in _directChildren(pattern, "dependencies"):
                        dependencies_match = _fomodDependencyMatches(
                            dependencies, evidence_files
                        )
                        break
                    if not dependencies_match:
                        continue
                    for type_node in _directChildren(pattern, "type"):
                        return type_node.attrib.get("name", default_type).casefold()
            return default_type
    return _fomodPluginType(plugin).casefold()


def _fomodPluginTypeScore(plugin_type):
    return {
        "required": 80,
        "recommended": 70,
        "couldbeusable": 50,
        "could be usable": 50,
        "optional": 10,
    }.get(str(plugin_type or "").casefold(), 0)


def _fomodDependencyCandidateDiagnostic(
    option_name, plugin_type, score, matched_profile_evidence, mappings
):
    return {
        "option": option_name,
        "plugin_type": plugin_type or "",
        "score": score,
        "matched_profile_evidence": bool(matched_profile_evidence),
        "payload_items": len(mappings or []),
    }


def headlessFomodDependencyInstallLayout(
    module_config_xml, module_config_path, member_names, evidence_names
):
    """Return a conservative direct-install plan for simple dependency patch FOMODs.

    The planner only automates choices whose option names match installed/active
    evidence from the current profile. It supports optional patch hubs well, but
    intentionally refuses subjective single-choice theme/configuration pickers.
    """
    try:
        root = ElementTree.fromstring(decodedXmlText(module_config_xml))
    except ElementTree.ParseError as e:
        return {
            "installable": False,
            "reason": f"FOMOD XML parse error: {e}",
            "mappings": [],
            "fomod_selection": True,
        }

    module_base_prefix = _fomodModuleBasePrefix(module_config_path)
    archive_members = {
        normalizedArchiveMemberPath(name)
        for name in (member_names or [])
        if normalizedArchiveMemberPath(name)
    }
    evidence_labels = [
        _fomodSelectionEvidenceLabel(name)
        for name in (evidence_names or [])
        if _fomodSelectionEvidenceLabel(name)
    ]
    evidence_files = _fomodEvidenceFileNames(evidence_names)
    selected_records = []
    ambiguous_groups = []

    for group in root.iter():
        if _xmlLocalName(group.tag) != "group":
            continue
        group_type = group.attrib.get("type", "")
        if group_type not in {
            "SelectAny",
            "SelectExactlyOne",
            "SelectAtLeastOne",
            "SelectAtMostOne",
        }:
            continue

        plugins = []
        for plugins_node in _directChildren(group, "plugins"):
            plugins.extend(_directChildren(plugins_node, "plugin"))

        candidates = []
        for plugin in plugins:
            option_name = plugin.attrib.get("name", "")
            plugin_type = _fomodDependencyPluginType(plugin, evidence_files)
            if normalizedButtonLabel(option_name) in {"skip", "none", "reminder"}:
                continue
            if plugin_type in {"notusable", "not usable"}:
                continue
            mappings = _fomodPluginFileMappings(plugin, module_base_prefix)
            if not mappings:
                continue
            if not all(
                mapping["source"] in archive_members
                or any(
                    member.startswith(mapping["source"].rstrip("/") + "/")
                    for member in archive_members
                )
                for mapping in mappings
            ):
                continue
            matched_profile_evidence = _fomodOptionMatchesEvidence(
                option_name, evidence_labels
            )
            if matched_profile_evidence:
                score = (
                    _fomodOptionEvidenceScore(option_name, evidence_labels)
                    + _fomodPluginTypeScore(plugin_type)
                )
                candidates.append(
                    (
                        option_name,
                        mappings,
                        score,
                        plugin_type,
                        matched_profile_evidence,
                    )
                )
            elif _fomodPluginTypeScore(plugin_type) >= 50:
                score = _fomodPluginTypeScore(plugin_type)
                candidates.append(
                    (
                        option_name,
                        mappings,
                        score,
                        plugin_type,
                        matched_profile_evidence,
                    )
                )

        if group_type == "SelectAny":
            for option_name, mappings, score, plugin_type, evidence_match in candidates:
                selected_records.append(
                    (option_name, mappings, score, plugin_type, evidence_match)
                )
            continue

        selected_candidate = None
        if len(candidates) == 1:
            selected_candidate = candidates[0]
        elif candidates:
            ranked_candidates = sorted(
                candidates,
                key=lambda candidate: candidate[2],
                reverse=True,
            )
            if ranked_candidates[0][2] > ranked_candidates[1][2]:
                selected_candidate = ranked_candidates[0]

        if selected_candidate:
            option_name, mappings, score, plugin_type, evidence_match = selected_candidate
            selected_records.append(
                (option_name, mappings, score, plugin_type, evidence_match)
            )
        elif candidates:
            ranked_candidates = sorted(
                candidates,
                key=lambda candidate: (candidate[2], candidate[0].casefold()),
                reverse=True,
            )
            ambiguous_groups.append(
                {
                    "group": group.attrib.get("name", ""),
                    "type": group_type,
                    "candidates": [
                        _fomodDependencyCandidateDiagnostic(
                            option_name,
                            plugin_type,
                            score,
                            evidence_match,
                            mappings,
                        )
                        for (
                            option_name,
                            mappings,
                            score,
                            plugin_type,
                            evidence_match,
                        ) in ranked_candidates
                    ],
                }
            )

    if ambiguous_groups:
        ambiguous_names = [
            str(group.get("group") or "")
            for group in ambiguous_groups
            if group.get("group")
        ]
        return {
            "installable": False,
            "reason": "ambiguous FOMOD dependency choices: "
            + ", ".join(ambiguous_names),
            "mappings": [],
            "fomod_selection": True,
            "ambiguous_dependency_groups": ambiguous_groups,
        }
    pruned_records = []
    for option_name, mappings, score, plugin_type, evidence_match in selected_records:
        option_tokens = set(_fomodMeaningfulTokens(option_name))
        superseded = False
        for (
            other_name,
            _other_mappings,
            other_score,
            _other_plugin_type,
            _other_evidence_match,
        ) in selected_records:
            if other_name == option_name or other_score <= score:
                continue
            other_tokens = set(_fomodMeaningfulTokens(other_name))
            if option_tokens and option_tokens < other_tokens:
                superseded = True
                break
        if not superseded:
            pruned_records.append(
                (option_name, mappings, score, plugin_type, evidence_match)
            )

    selected_options = [
        option_name
        for option_name, _mappings, _score, _plugin_type, _evidence_match in pruned_records
    ]
    selected_mappings = [
        mapping
        for _option_name, mappings, _score, _plugin_type, _evidence_match in pruned_records
        for mapping in mappings
    ]

    if not selected_mappings:
        return {
            "installable": False,
            "reason": "no FOMOD options matched installed profile evidence",
            "mappings": [],
            "fomod_selection": True,
        }
    return {
        "installable": True,
        "reason": "dependency-selected FOMOD payload",
        "mappings": selected_mappings,
        "selected_options": selected_options,
        "fomod_selection": True,
    }


def fomodManualChoiceGuide(module_config_xml):
    """Return unresolved required FOMOD choices from a ModuleConfig.xml payload."""
    try:
        root = ElementTree.fromstring(decodedXmlText(module_config_xml))
    except ElementTree.ParseError as e:
        return {
            "parse_error": str(e),
            "manual_choices": [],
            "safe_singleton_prompts": [],
        }

    manual_choices = []
    safe_singleton_prompts = []
    for step in root.iter():
        if _xmlLocalName(step.tag) != "installStep":
            continue

        step_name = step.attrib.get("name", "")
        for group in step.iter():
            if _xmlLocalName(group.tag) != "group":
                continue

            group_type = group.attrib.get("type", "")
            if group_type not in {"SelectExactlyOne", "SelectAtLeastOne"}:
                continue

            plugins = []
            for plugins_node in _directChildren(group, "plugins"):
                plugins.extend(_directChildren(plugins_node, "plugin"))
            if not plugins:
                continue

            selected = [
                plugin
                for plugin in plugins
                if normalizedButtonLabel(plugin.attrib.get("default", ""))
                in {"true", "on", "yes"}
            ]
            if selected:
                continue

            option_names = [plugin.attrib.get("name", "").strip() for plugin in plugins]
            group_name = group.attrib.get("name", "")
            prompt = {
                "step": step_name,
                "group": group_name,
                "type": group_type,
                "options": option_names,
            }
            if len(option_names) == 1 and isSafeSingletonFomodOption(
                group_name, option_names[0]
            ):
                safe_singleton_prompts.append(prompt)
            else:
                manual_choices.append(prompt)

    return {
        "parse_error": None,
        "manual_choices": manual_choices,
        "safe_singleton_prompts": safe_singleton_prompts,
    }


def installerDefaultActionLabel(window_title, buttons):
    """Return the FOMOD default action to click, or None when unsafe.

    ``buttons`` is an iterable of ``(label, enabled)`` pairs. The helper is kept
    GUI-free so the dialog classification can be unit-tested outside MO2.
    """
    if window_title in FOMOD_ADVANCE_EXCLUDED_TITLES:
        return None

    enabled_by_label = {}
    labels = set()
    for label, enabled in buttons:
        normalized = normalizedButtonLabel(label)
        if not normalized:
            continue
        labels.add(normalized)
        enabled_by_label[normalized] = bool(enabled)

    if "cancel" not in labels:
        return None

    # Avoid generic confirmation prompts and MO2 utility dialogs. FOMOD wizard
    # pages normally expose Back/Next/Cancel, or Back/Install/Cancel on the
    # final page.
    if "back" not in labels and "next" not in labels and "install" not in labels:
        return None

    for label in ("next", "install"):
        if enabled_by_label.get(label):
            return label

    return None


def shouldUseCollectionTargetModName(separate_file_installs, manual_install_pass):
    """Return True when collection install should force a unique MO2 mod name."""
    return bool(separate_file_installs) and not bool(manual_install_pass)


def shouldUseArchiveDefaultForFomodCompatibility(
    separate_file_installs,
    manual_install_pass,
    fomod_state,
):
    """Return True when MO2's native installer path is safer than forced naming."""
    return (
        bool(separate_file_installs)
        and not bool(manual_install_pass)
        and fomod_state is True
    )


def shouldPassTargetNameToInstallMod(
    separate_file_installs,
    manual_install_pass,
    normal_dialog_retry_pass,
    use_archive_default_for_fomod,
):
    """Return True when bulk install should use MO2's target-name overload."""
    return (
        bool(separate_file_installs)
        and not bool(manual_install_pass)
        and not bool(normal_dialog_retry_pass)
        and not bool(use_archive_default_for_fomod)
    )


def invalidInstallContentDialogAction(window_title, labels, buttons):
    """Return the action for MO2's invalid-content install dialog, if present."""
    if window_title != "Install Mods":
        return None

    if not any("does not look valid" in str(label).lower() for label in labels):
        return None

    enabled_by_label = {
        normalizedButtonLabel(label): bool(enabled)
        for label, enabled in buttons
        if normalizedButtonLabel(label)
    }
    if enabled_by_label.get("ok"):
        return "ok"
    if enabled_by_label.get("cancel"):
        return "cancel"

    return None


def contentTreeWarningDialogAction(window_title, labels, buttons):
    """Return the action for MO2's content-tree warning dialog, if present."""
    if window_title != "Continue?":
        return None

    text = " ".join(str(label) for label in labels).casefold()
    warning_markers = (
        "content-tree",
        "probably not set up correctly",
        "missing requirement",
        "should be active, but was missing",
        "plugin not found",
    )
    if not any(marker in text for marker in warning_markers):
        return None

    enabled_by_label = {
        normalizedButtonLabel(label): bool(enabled)
        for label, enabled in buttons
        if normalizedButtonLabel(label)
    }
    if enabled_by_label.get("ignore"):
        return "ignore"

    return None


def knownPostInstallErrorDialogMessage(labels):
    """Return durable text for MO2 post-install error dialogs we auto-dismiss."""
    parts = [
        str(label or "").strip()
        for label in (labels or [])
        if str(label or "").strip()
    ]
    if not parts:
        return None
    message = "\n".join(parts)
    if (
        "invalid origin name:" in message
        or "Plugin not found:" in message
        or "failed to receive data from secondary process" in message
    ):
        return message
    return None


def pluginNotFoundNamesFromMessage(message):
    """Return plugin names from MO2 'Plugin not found' warning text."""
    names = []
    seen = set()
    for match in re.finditer(
        r"Plugin not found:\s*([^\r\n]+)",
        str(message or ""),
        flags=re.IGNORECASE,
    ):
        plugin_name = match.group(1).strip().strip(".")
        if not plugin_name:
            continue
        key = plugin_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(plugin_name)
    return names


def suppressedPostInstallErrorReviewEntries(warnings):
    """Return review entries for actionable MO2 errors dismissed by automation."""
    entries = []
    seen = set()
    blocking_categories = {
        "plugin_state_missing",
        "invalid_origin_name",
        "secondary_process_error",
    }
    for warning in warnings or []:
        if warning.get("category") not in blocking_categories:
            continue
        message = str(warning.get("message") or "").strip()
        if not message:
            continue
        key = (
            str(warning.get("mod") or "post-install"),
            str(warning.get("file") or ""),
            message,
        )
        if key in seen:
            continue
        seen.add(key)
        missing_plugins = (
            pluginNotFoundNamesFromMessage(message)
            if warning.get("category") == "plugin_state_missing"
            else []
        )
        reason = f"post-install MO2 error dialog: {message}"
        suggested_action = ""
        if missing_plugins:
            suggested_action = (
                "install the mod or optional patch source that provides "
                + ", ".join(missing_plugins)
                + "; disable the dependent patch if that plugin is not intended"
            )
        entries.append(
            {
                "mod": key[0],
                "file": missing_plugins[0] if len(missing_plugins) == 1 else key[1],
                "mod_id": "unknown",
                "file_id": "unknown",
                "archive": "",
                "missing_plugins": missing_plugins,
                "suggested_action": suggested_action,
                "reason": reason,
            }
        )
    return entries


def pluginActivationReviewEntries(missing_plugins, source="plugin activation"):
    """Return review entries for plugins that could not be enabled in profile state."""
    entries = []
    seen = set()
    source = str(source or "plugin activation")
    for plugin_name in missing_plugins or []:
        plugin_name = str(plugin_name or "").strip()
        if not plugin_name:
            continue
        key = plugin_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "mod": source,
                "file": plugin_name,
                "mod_id": "unknown",
                "file_id": "unknown",
                "archive": "",
                "missing_plugins": [plugin_name],
                "suggested_action": (
                    "install the mod or optional patch source that provides "
                    f"{plugin_name}; disable the dependent patch if that plugin "
                    "is not intended"
                ),
                "reason": (
                    "plugin not present in profile plugin list after refresh: "
                    f"{plugin_name}"
                ),
            }
        )
    return entries


def pluginMasterDependencyAudit(
    target_plugins,
    available_plugins,
    active_plugins,
    masters_by_plugin,
    include_inactive_targets=False,
):
    """Return active target plugins whose masters are missing or inactive."""
    available_by_key = {}
    for plugin_name in available_plugins or []:
        plugin_name = str(plugin_name or "").strip()
        if not plugin_name:
            continue
        available_by_key.setdefault(plugin_name.casefold(), plugin_name)

    active_by_key = {}
    for plugin_name in active_plugins or []:
        plugin_name = str(plugin_name or "").strip()
        if not plugin_name:
            continue
        active_by_key.setdefault(plugin_name.casefold(), plugin_name)

    target_by_key = {}
    for plugin_name in target_plugins or []:
        plugin_name = str(plugin_name or "").strip()
        if not plugin_name:
            continue
        target_by_key.setdefault(plugin_name.casefold(), plugin_name)

    problems = []
    for plugin_key, plugin_name in target_by_key.items():
        plugin_active = plugin_key in active_by_key
        if not plugin_active and not include_inactive_targets:
            continue

        missing = []
        inactive = []
        seen_masters = set()
        for master_name in (masters_by_plugin or {}).get(plugin_name, []):
            master_name = str(master_name or "").strip()
            if not master_name:
                continue
            master_key = master_name.casefold()
            if master_key in seen_masters:
                continue
            seen_masters.add(master_key)
            if master_key not in available_by_key:
                missing.append(master_name)
            elif master_key not in active_by_key:
                inactive.append(available_by_key[master_key])

        if missing or inactive:
            problems.append(
                {
                    "plugin": plugin_name,
                    "plugin_active": plugin_active,
                    "missing_masters": missing,
                    "inactive_masters": inactive,
                }
            )
    return problems


def pluginMasterDependencyReviewEntries(
    dependency_problems, source="plugin dependency audit"
):
    """Return review entries for plugins with missing or inactive masters."""
    entries = []
    seen = set()
    source = str(source or "plugin dependency audit")
    for problem in dependency_problems or []:
        if not isinstance(problem, dict):
            continue
        plugin_name = str(problem.get("plugin") or "").strip()
        if not plugin_name:
            continue
        missing = [
            str(name).strip()
            for name in problem.get("missing_masters") or []
            if str(name).strip()
        ]
        inactive = [
            str(name).strip()
            for name in problem.get("inactive_masters") or []
            if str(name).strip()
        ]
        if not missing and not inactive:
            continue
        key = (
            plugin_name.casefold(),
            tuple(name.casefold() for name in missing),
            tuple(name.casefold() for name in inactive),
        )
        if key in seen:
            continue
        seen.add(key)

        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if inactive:
            details.append("inactive " + ", ".join(inactive))
        recommendation_parts = []
        if not problem.get("plugin_active", True):
            recommendation_parts.append(
                f"enable {plugin_name} after its masters are resolved"
            )
        if missing:
            recommendation_parts.append(
                "install the mod or optional patch source that provides "
                + ", ".join(missing)
            )
        if inactive:
            recommendation_parts.append("enable " + ", ".join(inactive))
        recommendation_parts.append(
            f"disable {plugin_name} if those masters are not intended"
        )
        entries.append(
            {
                "mod": source,
                "file": plugin_name,
                "mod_id": "unknown",
                "file_id": "unknown",
                "archive": "",
                "missing_masters": missing,
                "inactive_masters": inactive,
                "suggested_action": "; ".join(recommendation_parts),
                "reason": (
                    "plugin has unresolved master dependencies"
                    + (
                        " and is currently inactive: "
                        if not problem.get("plugin_active", True)
                        else ": "
                    )
                    + "; ".join(details)
                ),
            }
        )
    return entries


def pluginRepairFailureReviewEntries(failed_count, source="plugin activation"):
    """Return review entries for plugin profile repairs that failed on disk."""
    try:
        failed_count = int(failed_count)
    except (TypeError, ValueError):
        failed_count = 0
    if failed_count <= 0:
        return []
    source = str(source or "plugin activation")
    return [
        {
            "mod": source,
            "file": "plugins.txt",
            "mod_id": "unknown",
            "file_id": "unknown",
            "archive": "",
            "reason": (
                "could not repair profile plugin enabled state on disk "
                f"({failed_count} failure(s))"
            ),
        }
    ]


def nativeArchiveWorkerHeartbeatStatus(
    payload,
    now,
    max_age_seconds=30.0,
    tracked_pid=None,
    tracked_exit_code=None,
):
    """Return whether a native archive worker heartbeat is current and usable."""
    if not isinstance(payload, dict) or not payload.get("ok"):
        return {"ok": False, "reason": "missing or unsuccessful heartbeat"}
    try:
        heartbeat_time = float(payload.get("time"))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "heartbeat has invalid time"}
    if float(now) - heartbeat_time > float(max_age_seconds):
        return {"ok": False, "reason": "heartbeat is stale"}

    heartbeat_pid = payload.get("pid")
    try:
        heartbeat_pid = int(heartbeat_pid)
    except (TypeError, ValueError):
        heartbeat_pid = None
    try:
        tracked_pid = int(tracked_pid)
    except (TypeError, ValueError):
        tracked_pid = None

    if (
        heartbeat_pid is not None
        and tracked_pid is not None
        and heartbeat_pid == tracked_pid
        and tracked_exit_code is not None
    ):
        return {"ok": False, "reason": "tracked worker process exited"}
    return {"ok": True, "reason": ""}


def resetNativeArchiveWorkerTrackedProcess(process, timeout=2.0):
    """Terminate a tracked native archive worker process and report the outcome."""
    result = {"terminated": False, "killed": False, "error": ""}
    if process is None:
        return result
    try:
        if process.poll() is None:
            process.terminate()
            result["terminated"] = True
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                result["killed"] = True
                process.wait(timeout=timeout)
    except Exception as e:
        result["error"] = str(e)
    return result


def warningsAfterCleanInstallDiscard(warnings, warning_start):
    """Drop ordinary transient warnings while preserving actionable MO2 errors."""
    blocking_categories = {
        "plugin_state_missing",
        "invalid_origin_name",
        "secondary_process_error",
    }
    kept = list(warnings[:warning_start])
    kept.extend(
        warning
        for warning in warnings[warning_start:]
        if warning.get("source") == "suppressed_dialog"
        or warning.get("category") in blocking_categories
    )
    return kept


def installNoResultReason(invalid_content_cancelled, warning_messages):
    """Return a useful reason when MO2 returns no installed mod object."""
    if invalid_content_cancelled:
        return (
            "invalid install content warning accepted, but MO2 returned no "
            "installed mod"
        )

    if any(
        "[fomodinstallerdialog.cpp:" in str(message) for message in warning_messages
    ):
        return (
            "MO2 FOMOD installer returned no installed mod; likely needs "
            "manual choices or unsupported default automation"
        )

    return (
        "MO2 installer returned no installed mod; likely cancelled, manual, "
        "or unsupported install"
    )


def duplicateDownloadPromptActionLabel(window_title, buttons):
    """Return the duplicate-download prompt action to click, or None.

    MO2 shows this confirmation when a same-named archive already exists in the
    downloads directory. During a collection batch, declining the duplicate is
    the least surprising non-interactive choice: keep the existing archive and
    let the collection tracker reconcile the item from disk.

    MO2 can also show ``Already Started`` or ``Already Queued`` when its
    download manager already has an entry for the Nexus file. Those dialogs
    have no useful choice for the batch flow, so acknowledging them lets the
    tracker wait for MO2's existing entry.
    """
    labels = {
        normalizedButtonLabel(label): bool(enabled)
        for label, enabled in buttons
        if normalizedButtonLabel(label)
    }

    if window_title == "Download again?":
        if not {"yes", "no"}.issubset(labels):
            return None
        if labels.get("no"):
            return "no"

    if window_title in {"Already Started", "Already Queued"} and labels.get("ok"):
        return "ok"

    return None


def downloadPromptKeyFromLabels(labels, valid_keys=None):
    """Extract a Nexus (mod_id, file_id) key from MO2 download prompt text."""
    valid_keys = set(valid_keys or [])
    text = "\n".join(str(label) for label in labels)
    match = re.search(r"\bMod\s+(\d+)\s*:.*?\bFile\s+(\d+)\s*:", text, re.I | re.S)
    if not match:
        return None

    key = (int(match.group(1)), int(match.group(2)))
    if valid_keys and key not in valid_keys:
        return None
    return key


def downloadPromptKeyFromArchiveLabels(labels, downloads_dir, valid_keys=None):
    """Extract a Nexus key from MO2 duplicate prompts that only name an archive."""
    valid_keys = set(valid_keys or [])
    if not downloads_dir:
        return None

    downloads_dir = Path(downloads_dir)
    text = "\n".join(str(label) for label in labels)
    archive_names = re.findall(r'"([^"]+\.(?:7z|zip|rar))"', text, re.I)
    for archive_name in archive_names:
        metadata_file = downloads_dir / f"{archive_name}.meta"
        key = readDownloadMetaKey(metadata_file)
        if key is None:
            continue
        if valid_keys and key not in valid_keys:
            continue
        return key

    return None


def duplicateDownloadPromptArchiveAction(labels, downloads_dir):
    """Return yes/no for archive-only duplicate prompts from the downloads dir."""
    if not downloads_dir:
        return None

    downloads_dir = Path(downloads_dir)
    text = "\n".join(str(label) for label in labels)
    archive_names = re.findall(r'"([^"]+\.(?:7z|zip|rar))"', text, re.I)
    for archive_name in archive_names:
        candidates = [archive_name]
        unprefixed = re.sub(r"^\d+_", "", archive_name)
        if unprefixed != archive_name:
            candidates.append(unprefixed)

        for candidate in candidates:
            archive_file = downloads_dir / candidate
            unfinished_archive = Path(str(archive_file) + ".unfinished")
            unfinished_metadata = Path(str(archive_file) + ".unfinished.meta")
            try:
                if (
                    archive_file.exists()
                    and archive_file.stat().st_size > 0
                    and not unfinished_archive.exists()
                    and not unfinished_metadata.exists()
                ):
                    return "no"
            except OSError:
                continue

    if archive_names:
        return "yes"
    return None


def downloadCompletionPlan(failed_count, has_on_complete, close_on_success, delay_ms):
    """Return terminal actions for a successful download progress dialog."""
    if failed_count:
        return {
            "run_complete": False,
            "close_immediately": False,
            "close_delay_ms": 0,
        }

    close_delay_ms = max(0, int(delay_ms or 0))
    return {
        "run_complete": bool(has_on_complete),
        "close_immediately": bool(close_on_success),
        "close_delay_ms": 0 if close_on_success else close_delay_ms,
    }


def shouldAutoCloseInstallSummary(
    auto_close_on_success,
    cancelled,
    failed_count,
    recovery_count=0,
    no_applicable_count=0,
):
    """Return True when a successful automatic install summary can close itself."""
    return (
        bool(auto_close_on_success)
        and not cancelled
        and int(failed_count or 0) == 0
        and int(recovery_count or 0) == 0
        and int(no_applicable_count or 0) == 0
    )


def collectionInstallCompletedCount(
    total_count, review_count=0, queued_recovery_count=0, no_applicable_count=0
):
    """Return entries that were truly completed or externally handled."""
    completed = (
        int(total_count or 0)
        - int(review_count or 0)
        - int(queued_recovery_count or 0)
        - int(no_applicable_count or 0)
    )
    return max(0, completed)


def installPlanExecutionAction(fast_finish):
    """Return the next execution step for a prepared collection install plan."""
    return "fast-finish" if fast_finish else "install-next"


def fastFinishMetadataRepairKeys(plan_entries):
    """Return Nexus keys that need metadata repair during a no-op replay.

    A fast-finished replay still validates installed mod containers in the final
    postcondition sweep. Only root/game-directory entries need explicit metadata
    reconciliation here because they have no MO2 mod container to audit.
    """
    keys = set()
    for entry in plan_entries or []:
        if entry.get("status") != "root":
            continue
        key = entry.get("install_key")
        if isinstance(key, tuple) and len(key) == 2:
            try:
                keys.add((int(key[0]), int(key[1])))
            except (TypeError, ValueError):
                continue
    return keys


def downloadCompletionChoices(
    state,
    has_on_complete,
    restart_required=False,
    quota_limited=False,
):
    """Return the visible terminal choices for a completed download pass."""
    successful = int(state.get("successful") or 0)
    failed = int(state.get("failed") or 0)
    has_failures = bool(state.get("has_failures"))
    blocked_until_resume = bool(quota_limited)
    install_visible = (
        bool(has_on_complete) and successful > 0 and not blocked_until_resume
    )

    return {
        "retry_visible": failed > 0
        and not blocked_until_resume
        and not bool(restart_required),
        "install_visible": install_visible,
        "install_label": "Install Available" if has_failures else "Install Collection",
        "fomod_defaults_visible": bool(has_on_complete)
        and install_visible
        and not blocked_until_resume,
    }


def collectionLinkCompletionPolicy():
    """Return terminal download behavior for Nexus Add Collection links.

    Add Collection should continue into install on clean download completion by
    default. Partial download failures still stop for an explicit user decision.
    """
    return {
        "attach_install_callback": True,
        "prompt_after_download": False,
        "close_on_success": True,
        "auto_install_after_download_default": True,
    }


def downloadProgressFormat(state):
    """Return a QProgressBar format that does not hide partial failures."""
    successful = int(state.get("successful") or 0)
    total = int(state.get("total") or 0)
    if state.get("has_failures"):
        return f"{successful}/{total} downloaded"
    return "%p%"


def downloadProgressState(total_mods, completed_keys, failed_keys, key_counts):
    """Return collection download progress without treating failures as success."""
    successful = sum(key_counts.get(key, 1) for key in completed_keys)
    failed = sum(
        key_counts.get(key, 1) for key in set(failed_keys) - set(completed_keys)
    )
    total = max(0, int(total_mods or 0))
    progress = min(total, successful)
    processed = min(total, successful + failed)
    remaining = max(0, total - processed)

    return {
        "total": total,
        "successful": successful,
        "failed": failed,
        "progress": progress,
        "processed": processed,
        "remaining": remaining,
        "has_failures": failed > 0,
        "is_terminal": processed >= total,
    }


def downloadProgressCanClose(
    is_tracking,
    state,
    restart_required=False,
    quota_limited=False,
):
    """Return whether a collection download progress dialog may be closed."""
    if not is_tracking:
        return True
    if state.get("is_terminal"):
        return True
    return bool(restart_required) or bool(quota_limited)


def shouldDelayTerminalDownloadFailure(has_failures, attempts, max_attempts):
    """Return True when terminal failure should wait for late MO2 prompts."""
    return bool(has_failures) and int(attempts or 0) < int(max_attempts or 0)


def activeDownloadPromptKey(active_key, context_key, context_expires_at, now):
    """Return the Nexus key that owns a currently visible MO2 download prompt."""
    if active_key is not None:
        return active_key
    if context_key is not None and now <= context_expires_at:
        return context_key
    return None


def sanitizeModName(mod_name):
    clean_name = str(mod_name).replace("/", "-").replace("\\", "-")
    clean_name = " ".join(clean_name.split())
    clean_name = clean_name.rstrip(" .")
    return clean_name or "Collection Mod"


def safeDisplayText(text):
    """Return UI text that avoids MO2/Wine font gaps for collection labels."""
    safe_chars = []
    for char in str(text):
        if unicodedata.category(char).startswith("C"):
            continue
        if ord(char) > 0xFFFF:
            continue
        safe_chars.append(char)

    return " ".join("".join(safe_chars).split()) or "Unknown Collection"


def isCollectionTransientModDirName(name):
    """Return True for plugin-owned temporary MO2 mod directories."""
    return str(name or "").startswith(COLLECTION_TRANSIENT_MOD_DIR_PREFIXES)


def coerceBoolSetting(value, default=False):
    """Return a bool for MO2 plugin settings stored as bools or strings."""
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return value != 0

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    return bool(value)


def coerceIntSetting(value, default=0, minimum=None):
    """Return an int for MO2 plugin settings while tolerating stale text values."""
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        coerced = int(default)

    if minimum is not None:
        return max(coerced, minimum)
    return coerced


def allocateUniqueModName(mod_name, used_mod_names, mod_name_counts):
    """Return a stable MO2 mod name without merging collection file entries."""
    base_name = sanitizeModName(mod_name)
    next_index = mod_name_counts.get(base_name, 1)

    if next_index == 1 and base_name not in used_mod_names:
        mod_name_counts[base_name] = 2
        used_mod_names.add(base_name)
        return base_name

    next_index = max(next_index, 2)
    while True:
        candidate = f"{base_name} #{next_index}"
        if candidate not in used_mod_names:
            mod_name_counts[base_name] = next_index + 1
            used_mod_names.add(candidate)
            return candidate
        next_index += 1


def detachedInstallCacheKeyFromPath(path):
    """Recover a Nexus identity from detached install cache archive names."""
    filename = Path(str(path).replace("\\", "/")).name
    match = re.match(r"^([0-9]+)-([0-9]+)-.+$", filename)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def parseCollectionAddress(address):
    """Parse supported Nexus collection web and nxm:// addresses."""
    normalized = address.strip()
    normalized = normalized.replace("http://", "https://")
    normalized = normalized.split("?", 1)[0].split("#", 1)[0]
    for suffix in ["/about", "/mods", "/comments", "/changelog", "/bugs"]:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]

    web_match = re.match(
        r"^(?:https:\/\/)?(?:www\.)?nexusmods\.com\/games\/([a-zA-Z0-9_\-]+)"
        r"\/collections\/([a-zA-Z0-9_\-]+)"
        r"(?:\/revisions\/([0-9]+))?\/?$",
        normalized,
        re.IGNORECASE,
    )
    if web_match:
        return {
            "uri": (
                "https://www.nexusmods.com/games/"
                f"{web_match.group(1)}/collections/{web_match.group(2)}"
            ),
            "game": web_match.group(1),
            "collection": web_match.group(2),
            "revision": int(web_match.group(3)) if web_match.group(3) else None,
        }

    nxm_match = re.match(
        r"^nxm:\/\/([a-zA-Z0-9_\-]+)\/collections\/([a-zA-Z0-9_\-]+)"
        r"(?:\/revisions\/([0-9]+))?\/?$",
        normalized,
        re.IGNORECASE,
    )
    if nxm_match:
        return {
            "uri": (
                "https://www.nexusmods.com/games/"
                f"{nxm_match.group(1)}/collections/{nxm_match.group(2)}"
            ),
            "game": nxm_match.group(1),
            "collection": nxm_match.group(2),
            "revision": int(nxm_match.group(3)) if nxm_match.group(3) else None,
        }

    return None


def collectionDownloadExpectedSizes(mods_to_download):
    """Return expected archive sizes keyed by Nexus (mod_id, file_id)."""
    sizes = {}
    for mod in mods_to_download or []:
        nexus_key = collectionEntryNexusKey(mod)
        if nexus_key is None:
            continue
        try:
            expected_size = int(mod["file"]["sizeInBytes"])
        except (TypeError, KeyError, ValueError):
            continue

        if expected_size > 0:
            sizes[nexus_key] = expected_size

    return sizes


def collectionEntryNexusKey(mod):
    """Return the Nexus (mod_id, file_id) identity for one collection entry."""
    try:
        return (int(mod["file"]["mod"]["modId"]), int(mod["file"]["fileId"]))
    except (TypeError, KeyError, ValueError):
        return None


def collectionExpectedNexusKeys(mods_to_download):
    """Return all valid Nexus file identities from a collection manifest."""
    keys = set()
    for mod in mods_to_download or []:
        nexus_key = collectionEntryNexusKey(mod)
        if nexus_key is not None:
            keys.add(nexus_key)
    return keys


def collectionExpectedFileNames(mods_to_download):
    """Return collection file display names keyed by Nexus file identity."""
    names = {}
    for mod in mods_to_download or []:
        nexus_key = collectionEntryNexusKey(mod)
        if nexus_key is None:
            continue
        try:
            file_name = mod["file"]["name"]
        except (TypeError, KeyError):
            continue
        if file_name:
            names[nexus_key] = str(file_name)

    return names


def collectionEntriesFromMetadata(metadata):
    """Return installable collection entries from saved collection metadata."""
    if not isinstance(metadata, dict):
        return []
    entries = []
    for key in ("essentialMods", "chosenOptional"):
        value = metadata.get(key)
        if isinstance(value, list):
            entries.extend(value)
    return entries


def collectionMetadataFromFile(collection_file):
    """Load one saved collection metadata file."""
    try:
        return json.loads(Path(collection_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def recordCollectionLinkLaunch(metadata_file, launched_at=None):
    """Record one Nexus Add Collection launch against saved metadata.

    A second or later launch for the same collection revision is useful, but it
    is a recovery signal. Persisting that count keeps reruns visible in the
    installer summary and warning reports.
    """
    metadata_file = Path(metadata_file)
    metadata = collectionMetadataFromFile(metadata_file) or {}

    previous_count = metadata.get("addCollectionLaunchCount", 0)
    try:
        previous_count = int(previous_count)
    except (TypeError, ValueError):
        previous_count = 0
    launch_count = max(0, previous_count) + 1

    launch_record = {
        "count": launch_count,
        "timestamp": launched_at or datetime.now().isoformat(timespec="seconds"),
        "mode": "recovery" if launch_count > 1 else "primary",
    }
    launches = metadata.get("addCollectionLaunches")
    if not isinstance(launches, list):
        launches = []
    launches.append(launch_record)

    metadata["addCollectionLaunchCount"] = launch_count
    metadata["addCollectionRecoveryCount"] = max(0, launch_count - 1)
    metadata["addCollectionLaunchMode"] = launch_record["mode"]
    metadata["addCollectionLaunches"] = launches[-20:]

    with open(metadata_file, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, ensure_ascii=False)

    return metadata


def collectionMetadataFiles(base_path, game="skyrimspecialedition"):
    """Return saved collection metadata files from an MO2 instance."""
    collection_dir = Path(base_path) / "collections" / str(game)
    if not collection_dir.exists():
        return []
    return sorted(collection_dir.glob("*.json"))


def collectionExpectedStateFromMetadataFiles(collection_files):
    """Return expected Nexus keys and file names from saved collection metadata."""
    expected_keys = set()
    expected_file_names = {}
    loaded_collections = []
    for collection_file in collection_files or []:
        metadata = collectionMetadataFromFile(collection_file)
        if not metadata:
            continue
        entries = collectionEntriesFromMetadata(metadata)
        expected_keys.update(collectionExpectedNexusKeys(entries))
        expected_file_names.update(collectionExpectedFileNames(entries))
        loaded_collections.append(
            {
                "path": str(collection_file),
                "name": metadata.get("name") or Path(collection_file).stem,
                "entries": len(entries),
            }
        )
    return {
        "collections": loaded_collections,
        "expected_keys": expected_keys,
        "expected_file_names": expected_file_names,
    }


def collectionRecoveryTargets(installed_records, expected_keys=None, mods_dir=None):
    """Return local recovery targets for already-installed collection files.

    ``installed_records`` maps Nexus ``(mod_id, file_id)`` identities to MO2
    mod container names. Recovery is intentionally conservative: it only returns
    downloads and mod containers for exact expected Nexus files that are already
    installed locally. When ``mods_dir`` is supplied, local containers must also
    contain valid MO2 game data before their download metadata is repaired.
    """
    installed_records = installed_records or {}
    installed_keys = set(installed_records)
    expected_keys = set(expected_keys or installed_keys)
    candidate_keys = expected_keys & installed_keys
    mods_dir = Path(mods_dir) if mods_dir is not None else None
    recoverable_keys = set()
    mod_names = []
    seen_names = set()
    for nexus_key in sorted(candidate_keys):
        valid_names = []
        for mod_name in installed_records.get(nexus_key, []):
            if mods_dir is not None and not headlessPayloadRootValid(mods_dir / mod_name):
                continue
            valid_names.append(mod_name)
            if mod_name in seen_names:
                continue
            mod_names.append(mod_name)
            seen_names.add(mod_name)
        if valid_names:
            recoverable_keys.add(nexus_key)
    return {
        "installed_keys": recoverable_keys,
        "missing_keys": expected_keys - recoverable_keys,
        "mod_names": mod_names,
    }


def downloadedArchiveNameKeys(downloads_dir, mods_to_download):
    """Return completed archive keys inferred from names and manifest sizes.

    This covers MO2 download archives whose `.meta` sidecar was lost or stale.
    The match is intentionally conservative: the archive filename must contain
    the Nexus mod id, its byte size must match the collection manifest, and the
    `(mod_id, size)` pair must identify exactly one collection file.
    """
    keys = set()
    if not downloads_dir or not downloads_dir.exists():
        return keys

    candidates_by_mod_size = {}
    manifest_mod_ids = set()
    for mod in mods_to_download or []:
        try:
            mod_id = int(mod["file"]["mod"]["modId"])
            file_id = int(mod["file"]["fileId"])
            expected_size = int(mod["file"]["sizeInBytes"])
        except (TypeError, KeyError, ValueError):
            continue
        manifest_mod_ids.add(mod_id)
        if expected_size > 0:
            candidates_by_mod_size.setdefault((mod_id, expected_size), set()).add(
                (mod_id, file_id)
            )

    for archive_file in downloads_dir.iterdir():
        if not archive_file.is_file():
            continue
        name = archive_file.name
        lower_name = name.casefold()
        if lower_name.endswith(".meta") or ".unfinished" in lower_name:
            continue
        if not lower_name.endswith((".7z", ".zip", ".rar")):
            continue

        unprefixed_name = re.sub(r"^\d+_", "", name)
        matched_mod_ids = {
            int(match.group(1))
            for match in re.finditer(r"-(\d+)(?=[-.])", unprefixed_name)
            if int(match.group(1)) in manifest_mod_ids
        }
        if len(matched_mod_ids) != 1:
            continue
        mod_id = next(iter(matched_mod_ids))

        try:
            archive_size = archive_file.stat().st_size
        except OSError:
            continue
        if archive_size <= 0:
            continue

        matches = candidates_by_mod_size.get((mod_id, archive_size), set())
        if len(matches) == 1:
            keys.update(matches)

    return keys


def downloadedFileKeys(downloads_dir, expected_sizes=None):
    """Return Nexus (mod_id, file_id) pairs with a completed archive on disk."""
    keys = set()
    expected_sizes = expected_sizes or {}
    if not downloads_dir or not downloads_dir.exists():
        return keys

    for metadata_file in downloads_dir.glob("*.meta"):
        if metadata_file.name.endswith(".unfinished.meta"):
            continue

        parser = ConfigParser()
        try:
            parser.read(metadata_file, encoding="utf-8")
            general = parser["General"]
            mod_id = int(general["modID"])
            file_id = int(general["fileID"])
        except (OSError, KeyError, ValueError):
            continue

        archive_file = metadata_file.with_suffix("")
        unfinished_archive = Path(str(archive_file) + ".unfinished")
        unfinished_metadata = Path(str(archive_file) + ".unfinished.meta")
        if unfinished_archive.exists() or unfinished_metadata.exists():
            continue

        try:
            archive_size = archive_file.stat().st_size
        except OSError:
            continue

        if archive_size <= 0:
            continue

        expected_size = expected_sizes.get((mod_id, file_id))
        if expected_size and archive_size < expected_size:
            continue

        keys.add((mod_id, file_id))

    return keys


def readDownloadMetaKey(metadata_file):
    """Read a Nexus (mod_id, file_id) pair from an MO2 download metadata file."""
    parser = ConfigParser()
    try:
        parser.read(metadata_file, encoding="utf-8")
        general = parser["General"]
        return (int(general["modID"]), int(general["fileID"]))
    except (OSError, KeyError, ValueError):
        return None


def _archiveKeyFromExpectedFileNames(archive_name, expected_file_names):
    """Infer a Nexus key from a downloaded archive name and collection names."""
    if not archive_name or not expected_file_names:
        return None

    expected_by_mod_id = {}
    for key, file_name in expected_file_names.items():
        try:
            mod_id, file_id = int(key[0]), int(key[1])
        except (TypeError, ValueError):
            continue
        expected_by_mod_id.setdefault(mod_id, []).append(
            ((mod_id, file_id), _normalizeArchiveIdentityText(file_name))
        )

    normalized_archive = _normalizeArchiveIdentityText(archive_name)
    archive_mod_ids = {
        int(match.group(1))
        for match in re.finditer(r"-(\d+)(?=[-.])", str(archive_name))
        if int(match.group(1)) in expected_by_mod_id
    }
    matches = set()
    for mod_id in archive_mod_ids:
        for expected_key, normalized_name in expected_by_mod_id.get(mod_id, []):
            if normalized_name and normalized_archive.startswith(normalized_name):
                matches.add(expected_key)
    if len(matches) == 1:
        return next(iter(matches))
    return None


def downloadMetaInstalledValue(metadata_file):
    """Return the installed= value from an MO2 download metadata file."""
    try:
        for line in metadata_file.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if line.strip().lower().startswith("installed="):
                return line.split("=", 1)[1].strip().lower()
    except OSError:
        return None
    return None


def topLevelDownloadMetadataAudit(downloads_dir):
    """Audit MO2-visible top-level download metadata install flags.

    MO2's Downloads pane is backed by archive sidecars directly under the
    downloads directory. Nested quarantine or stale-backup directories can hold
    old ``*.meta`` files, but those should not make the visible queue look dirty.
    """
    result = {
        "checked": 0,
        "installed": [],
        "downloaded_only": [],
        "missing_archive": [],
        "unknown_installed_state": [],
    }
    downloads_dir = Path(downloads_dir)
    if not downloads_dir.exists():
        return result

    for metadata_file in sorted(downloads_dir.glob("*.meta")):
        if metadata_file.name.endswith(".unfinished.meta"):
            continue
        archive_file = metadata_file.with_suffix("")
        result["checked"] += 1
        metadata_path = str(metadata_file)
        installed_value = downloadMetaInstalledValue(metadata_file)
        if not archive_file.is_file():
            result["missing_archive"].append(metadata_path)
        if installed_value == "true":
            result["installed"].append(metadata_path)
        elif installed_value == "false":
            result["downloaded_only"].append(metadata_path)
        else:
            result["unknown_installed_state"].append(metadata_path)
    return result


def setDownloadMetaGeneralValues(metadata_file, values):
    """Set selected [General] values in an MO2 download metadata sidecar."""
    try:
        lines = metadata_file.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        return False

    if not lines:
        lines = ["[General]\n"]

    general_start = None
    general_end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.casefold() == "[general]":
            general_start = index
            continue
        if general_start is not None and index > general_start:
            if stripped.startswith("[") and stripped.endswith("]"):
                general_end = index
                break

    if general_start is None:
        lines.insert(0, "[General]\n")
        general_start = 0
        general_end = len(lines)

    changed = False
    remaining = {
        str(key).casefold(): (str(key), str(value)) for key, value in values.items()
    }
    for index in range(general_start + 1, general_end):
        line = lines[index]
        if "=" not in line:
            continue
        key, _value = line.split("=", 1)
        lookup = key.strip().casefold()
        if lookup not in remaining:
            continue
        original_key, desired_value = remaining.pop(lookup)
        newline = ""
        if line.endswith("\r\n"):
            newline = "\r\n"
        elif line.endswith("\n"):
            newline = "\n"
        replacement = f"{original_key}={desired_value}{newline}"
        if line != replacement:
            lines[index] = replacement
            changed = True

    if remaining:
        newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
        insert_lines = [
            f"{key}={value}{newline}" for key, value in remaining.values()
        ]
        lines[general_end:general_end] = insert_lines
        changed = True

    if not changed:
        return True

    try:
        metadata_file.write_text("".join(lines), encoding="utf-8")
    except OSError:
        return False
    return True


def setDownloadMetaInstalledFlag(metadata_file, installed):
    """Set installed=true/false in an MO2 download metadata file without reserializing it."""
    desired = "true" if installed else "false"
    return setDownloadMetaGeneralValues(metadata_file, {"installed": desired})


def repairDownloadMetadataInstalledFlags(
    downloads_dir,
    installed_keys,
    desired_installed=True,
    backup_dir=None,
    expected_file_names=None,
):
    """Repair MO2 download metadata install flags for exact Nexus keys.

    Only completed downloads with an existing archive are touched. The caller
    supplies the authoritative installed Nexus key set. When MO2 left a
    sidecar unqueried, the collection manifest filename can identify it so the
    Nexus identity fields can be restored along with the install flag.
    """
    result = {
        "checked": 0,
        "repaired": 0,
        "failed": 0,
        "skipped": 0,
        "metadata": [],
    }
    downloads_dir = Path(downloads_dir)
    installed_keys = set(installed_keys or set())
    if not downloads_dir.exists():
        return result

    desired_value = "true" if desired_installed else "false"
    for metadata_file in sorted(downloads_dir.glob("*.meta")):
        if metadata_file.name.endswith(".unfinished.meta"):
            result["skipped"] += 1
            continue

        archive_file = metadata_file.with_suffix("")
        key = readDownloadMetaKey(metadata_file)
        inferred_key = None
        if key is None:
            inferred_key = _archiveKeyFromExpectedFileNames(
                archive_file.name, expected_file_names
            )
            key = inferred_key
        if key not in installed_keys:
            result["skipped"] += 1
            continue

        if not archive_file.exists() or archive_file.is_dir():
            result["skipped"] += 1
            continue

        result["checked"] += 1
        repair_values = {"installed": desired_value}
        if inferred_key is not None:
            repair_values.update(
                {
                    "gameName": "SkyrimSE",
                    "modID": str(inferred_key[0]),
                    "fileID": str(inferred_key[1]),
                    "repository": "Nexus",
                }
            )
        elif downloadMetaInstalledValue(metadata_file) == desired_value:
            continue

        if backup_dir is not None:
            try:
                backup_dir = Path(backup_dir)
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_file = backup_dir / metadata_file.name
                backup_file.write_bytes(metadata_file.read_bytes())
            except OSError:
                result["failed"] += 1
                continue

        if setDownloadMetaGeneralValues(metadata_file, repair_values):
            result["repaired"] += 1
            result["metadata"].append(str(metadata_file))
        else:
            result["failed"] += 1

    return result


def _normalizeArchiveIdentityText(value):
    text = Path(str(value or "").replace("\\", "/")).name.casefold()
    text = re.sub(r"\.(7z|zip|rar)(\.meta)?$", "", text)
    text = re.sub(r"^\d+[-_]\d+[-_]", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def installedModRecordsFromDirectory(
    mods_dir, downloads_dir=None, expected_file_names=None
):
    """Return installed Nexus file records from MO2 mod container metadata.

    The result maps ``(mod_id, file_id)`` to the MO2 internal mod names that
    record that Nexus file in ``meta.ini``. This is the authoritative filesystem
    evidence that an archive was installed into a mod container.
    """
    records = {}
    download_key_by_archive = {}
    if downloads_dir is not None:
        downloads_dir = Path(downloads_dir)
        if downloads_dir.exists():
            for download_metadata in downloads_dir.glob("*.meta"):
                key = readDownloadMetaKey(download_metadata)
                if key is not None:
                    archive_name = download_metadata.name[: -len(".meta")]
                    download_key_by_archive[archive_name] = key

    expected_file_names = expected_file_names or {}
    expected_by_mod_id = {}
    for key, file_name in expected_file_names.items():
        try:
            mod_id, file_id = int(key[0]), int(key[1])
        except (TypeError, ValueError):
            continue
        expected_by_mod_id.setdefault(mod_id, []).append(
            ((mod_id, file_id), _normalizeArchiveIdentityText(file_name))
        )

    mods_dir = Path(mods_dir)
    if not mods_dir.exists():
        return records

    for metadata_file in sorted(mods_dir.glob("*/meta.ini")):
        parser = ConfigParser(strict=False)
        parser.optionxform = str
        try:
            parser.read(metadata_file, encoding="utf-8")
        except OSError:
            continue

        installation_file = None
        if parser.has_section("General"):
            installation_file = parser.get("General", "installationFile", fallback=None)

        if parser.has_section("installedFiles"):
            for key, value in parser.items("installedFiles"):
                if not key.endswith("\\modid"):
                    continue
                index = key.split("\\", 1)[0]
                file_id = parser.get(
                    "installedFiles", f"{index}\\fileid", fallback=None
                )
                try:
                    nexus_key = (int(value), int(file_id))
                except (TypeError, ValueError):
                    continue
                if nexus_key != (0, 0):
                    records.setdefault(nexus_key, []).append(metadata_file.parent.name)

        archive_name = Path(str(installation_file or "").replace("\\", "/")).name
        if archive_name in download_key_by_archive:
            records.setdefault(download_key_by_archive[archive_name], []).append(
                metadata_file.parent.name
            )
            continue

        try:
            general_mod_id = int(parser.get("General", "modid", fallback="0"))
        except ValueError:
            general_mod_id = 0
        normalized_archive = _normalizeArchiveIdentityText(archive_name)
        for expected_key, normalized_name in expected_by_mod_id.get(general_mod_id, []):
            if normalized_name and normalized_archive.startswith(normalized_name):
                records.setdefault(expected_key, []).append(metadata_file.parent.name)
                break

    return records


def gameRootFileEvidenceForCollectionEntry(nexus_key, game_root):
    """Return present game-root files proving a root-level collection install."""
    try:
        normalized_key = (int(nexus_key[0]), int(nexus_key[1]))
    except (TypeError, ValueError, IndexError):
        return []

    expected_files = KNOWN_GAME_ROOT_FILE_EVIDENCE.get(normalized_key, ())
    if not expected_files:
        return []

    game_root = Path(game_root)
    present = []
    for relative_file in expected_files:
        candidate = game_root / relative_file
        if candidate.is_file():
            present.append(relative_file)
    return present


def nativeGameRootPathCandidate(path_value):
    """Return a native host ``Path`` candidate for an MO2 game path value."""
    if not path_value:
        return None
    text = str(path_value)

    for method_name in ("absolutePath", "path", "toString"):
        method = getattr(path_value, method_name, None)
        if not method:
            continue
        try:
            method_value = method()
        except Exception:
            continue
        if method_value:
            text = str(method_value)
            break

    native_text = nativePathForArchiveInspection(text)
    if not native_text:
        return None
    return Path(native_text)


def steamGameRootFromMo2BasePath(base_path, game_name="Skyrim Special Edition"):
    """Infer a Steam game directory from an MO2 instance inside compatdata."""
    native_base = nativeGameRootPathCandidate(base_path)
    if native_base is None:
        return None

    parts = native_base.parts
    try:
        steamapps_index = parts.index("steamapps")
    except ValueError:
        return None

    steamapps = Path(*parts[: steamapps_index + 1])
    candidate = steamapps / "common" / game_name
    if candidate.exists():
        return candidate
    return None


def modlistEntryStates(modlist_text):
    """Return MO2 profile modlist states by internal mod name.

    Values are ``"+"`` for enabled, ``"-"`` for disabled, and ``""`` for bare
    lines. Comments and blank lines are ignored.
    """
    states = {}
    for raw_line in str(modlist_text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line[0] in "+-":
            states[line[1:]] = line[0]
        else:
            states[line] = ""
    return states


def collectionInstallPostconditionAudit(
    downloads_dir,
    mods_dir,
    modlist_text,
    expected_keys=None,
    expected_file_names=None,
    handled_keys=None,
):
    """Audit collection install postconditions across MO2 filesystem state."""
    raw_installed_records = installedModRecordsFromDirectory(
        mods_dir, downloads_dir, expected_file_names=expected_file_names
    )
    raw_installed_keys = set(raw_installed_records)
    expected_keys = set(expected_keys or raw_installed_keys)
    handled_keys = set(handled_keys or set())

    mods_dir = Path(mods_dir)
    installed_records = {}
    invalid_payload_mods = []
    invalid_payload_keys = set()
    for key in sorted(expected_keys & raw_installed_keys):
        for mod_name in raw_installed_records.get(key, []):
            if headlessPayloadRootValid(mods_dir / mod_name):
                installed_records.setdefault(key, []).append(mod_name)
            else:
                invalid_payload_mods.append(mod_name)
                invalid_payload_keys.add(key)

    installed_keys = set(installed_records)
    expected_installed_keys = expected_keys & installed_keys
    modlist_states = modlistEntryStates(modlist_text)

    missing_installs = sorted(expected_keys - installed_keys - handled_keys)
    disabled_mods = []
    missing_modlist_entries = []
    for key in sorted(expected_installed_keys):
        for mod_name in installed_records.get(key, []):
            state = modlist_states.get(mod_name)
            if state == "-":
                disabled_mods.append(mod_name)
            elif state is None:
                missing_modlist_entries.append(mod_name)

    download_mismatches = []
    false_installed_download_metadata = []
    download_checked = 0
    downloads_dir = Path(downloads_dir)
    if downloads_dir.exists():
        for metadata_file in sorted(downloads_dir.glob("*.meta")):
            archive_file = metadata_file.with_suffix("")
            key = readDownloadMetaKey(metadata_file)
            missing_identity = False
            if key is None:
                key = _archiveKeyFromExpectedFileNames(
                    archive_file.name, expected_file_names
                )
                missing_identity = key is not None
            if key not in expected_keys or key in handled_keys:
                continue
            if not archive_file.exists() or archive_file.is_dir():
                continue
            download_checked += 1
            installed_value = downloadMetaInstalledValue(metadata_file)
            if key in invalid_payload_keys:
                if missing_identity or installed_value == "true":
                    false_installed_download_metadata.append(str(metadata_file))
                continue
            if key not in expected_installed_keys:
                if missing_identity or installed_value == "true":
                    false_installed_download_metadata.append(str(metadata_file))
                continue
            if missing_identity or installed_value != "true":
                download_mismatches.append(str(metadata_file))

    return {
        "ok": not missing_installs
        and not disabled_mods
        and not missing_modlist_entries
        and not download_mismatches
        and not invalid_payload_mods
        and not false_installed_download_metadata,
        "expected_keys": len(expected_keys),
        "installed_keys": len(installed_keys),
        "raw_installed_keys": len(raw_installed_keys),
        "expected_installed_keys": len(expected_installed_keys),
        "missing_installs": missing_installs,
        "disabled_mods": disabled_mods,
        "missing_modlist_entries": missing_modlist_entries,
        "invalid_payload_mods": invalid_payload_mods,
        "invalid_payload_keys": sorted(invalid_payload_keys),
        "download_metadata_checked": download_checked,
        "download_metadata_mismatches": download_mismatches,
        "false_installed_download_metadata": false_installed_download_metadata,
    }


def quarantineInvalidPayloadModContainers(mods_dir, mod_names, quarantine_dir):
    """Move invalid collection containers out of MO2's active mods directory."""
    result = {"moved": [], "missing": [], "failed": []}
    mods_dir = Path(mods_dir)
    quarantine_dir = Path(quarantine_dir)
    requested = [name for name in dict.fromkeys(mod_names or []) if name]
    if not requested:
        return result

    try:
        quarantine_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        result["failed"] = [
            f"{name}: quarantine directory unavailable: {e}" for name in requested
        ]
        return result

    for mod_name in requested:
        source = mods_dir / mod_name
        if not source.exists():
            result["missing"].append(mod_name)
            continue
        if not source.is_dir():
            result["failed"].append(f"{mod_name}: not a directory")
            continue

        destination = quarantine_dir / mod_name
        suffix = 2
        while destination.exists():
            destination = quarantine_dir / f"{mod_name} #{suffix}"
            suffix += 1
        try:
            shutil.move(str(source), str(destination))
            result["moved"].append(mod_name)
        except OSError as e:
            result["failed"].append(f"{mod_name}: {e}")

    return result


def repairModlistEnabledStates(modlist_path, mod_names, backup_dir=None):
    """Enable matching MO2 profile modlist entries while preserving order."""
    result = {
        "checked": 0,
        "enabled": 0,
        "already_enabled": 0,
        "missing": [],
        "failed": 0,
    }
    mod_names = set(mod_names or [])
    if not mod_names:
        return result

    modlist_path = Path(modlist_path)
    try:
        lines = modlist_path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        result["failed"] += 1
        result["missing"] = sorted(mod_names)
        return result

    seen = set()
    changed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped[0] in "+-":
            mod_name = stripped[1:]
            if mod_name not in mod_names:
                continue
            result["checked"] += 1
            seen.add(mod_name)
            if stripped[0] == "+":
                result["already_enabled"] += 1
                continue
            newline = (
                "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            )
            lines[index] = f"+{mod_name}{newline}"
            result["enabled"] += 1
            changed = True

    result["missing"] = sorted(mod_names - seen)
    if not changed:
        return result

    if backup_dir is not None:
        try:
            _backupModlistFile(modlist_path, backup_dir)
        except OSError:
            result["failed"] += 1
            return result

    try:
        modlist_path.write_text("".join(lines), encoding="utf-8")
    except OSError:
        result["failed"] += 1

    return result


MO2_MODLIST_HEADER = "# This file was automatically generated by Mod Organizer."
MO2_BASE_DLC_ORDER = {
    "DLC: Dawnguard": 0,
    "DLC: HearthFires": 1,
    "DLC: Dragonborn": 2,
}


def splitMo2ModlistHeader(lines):
    """Return (header, body) while keeping MO2's generated comment first.

    MO2 stores ``modlist.txt`` in visible priority order after the generated
    header. Raw file edits should therefore keep unmanaged DLC/Creation Club
    entries at the top and append new high-priority collection entries at the
    bottom.
    """
    header = []
    body = []
    for line in lines:
        if line.strip() == MO2_MODLIST_HEADER:
            if not header:
                header.append(line if line.endswith(("\n", "\r\n")) else line + "\n")
            continue
        body.append(line)
    if not header:
        header = [MO2_MODLIST_HEADER + "\n"]
    return header, body


def _backupModlistFile(modlist_path, backup_dir):
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / Path(modlist_path).name
    if not backup_path.exists():
        backup_path.write_bytes(Path(modlist_path).read_bytes())


def _mo2ModlistEntryName(line):
    stripped = str(line or "").strip()
    if not stripped or stripped.startswith("#"):
        return ""
    if stripped[0] in "+-":
        return stripped[1:]
    return stripped


def mo2BaseModlistOrderNeedsRepair(modlist_text):
    """Return whether unmanaged DLC/Creation Club entries are misplaced."""
    seen_managed = False
    last_base_rank = -1
    for raw_line in str(modlist_text or "").splitlines():
        name = _mo2ModlistEntryName(raw_line)
        if not name:
            continue
        if name in MO2_BASE_DLC_ORDER:
            rank = MO2_BASE_DLC_ORDER[name]
            if seen_managed or rank < last_base_rank:
                return True
            last_base_rank = rank
            continue
        if name.startswith("Creation Club: "):
            rank = len(MO2_BASE_DLC_ORDER)
            if seen_managed or rank < last_base_rank:
                return True
            last_base_rank = rank
            continue
        seen_managed = True
    return False


def repairMo2BaseModlistOrder(modlist_path, backup_dir=None):
    """Keep unmanaged DLC/Creation Club entries at the visible left-pane top."""
    result = {"moved": 0, "failed": 0}
    modlist_path = Path(modlist_path)
    try:
        lines = modlist_path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        result["failed"] += 1
        return result

    header, body = splitMo2ModlistHeader(lines)
    base_entries = []
    managed_entries = []
    indexed_base = []
    for index, line in enumerate(body):
        name = _mo2ModlistEntryName(line)
        if name in MO2_BASE_DLC_ORDER:
            indexed_base.append((MO2_BASE_DLC_ORDER[name], index, line))
        elif name.startswith("Creation Club: "):
            indexed_base.append((len(MO2_BASE_DLC_ORDER), index, line))
        else:
            managed_entries.append(line)

    indexed_base.sort(key=lambda item: (item[0], item[1]))
    base_entries = [line for _, _, line in indexed_base]
    rewritten = header + base_entries + managed_entries
    if rewritten == lines:
        return result

    result["moved"] = len(base_entries)
    if backup_dir is not None:
        try:
            _backupModlistFile(modlist_path, backup_dir)
        except OSError:
            result["failed"] += 1
            return result

    try:
        modlist_path.write_text("".join(rewritten), encoding="utf-8")
    except OSError:
        result["failed"] += 1

    return result


def moveModlistEntriesToUiBottom(modlist_path, mod_names, backup_dir=None):
    """Move matching entries to MO2's left-pane bottom/highest priority.

    This writes entries at the end of ``modlist.txt`` because MO2's on-disk
    order matches the visible left-pane order after the generated header.
    """
    result = {"moved": 0, "missing": [], "failed": 0}
    requested = [name for name in dict.fromkeys(mod_names or []) if name]
    if not requested:
        return result

    modlist_path = Path(modlist_path)
    try:
        lines = modlist_path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        result["failed"] += 1
        result["missing"] = sorted(requested)
        return result

    header, body = splitMo2ModlistHeader(lines)
    by_name = {}
    kept = []
    for line in body:
        stripped = line.strip()
        if stripped and stripped[0] in "+-":
            by_name[stripped[1:]] = line
            if stripped[1:] in requested:
                continue
        kept.append(line)

    moved = []
    missing = []
    for name in requested:
        line = by_name.get(name)
        if line is None:
            missing.append(name)
            continue
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        stripped = line.strip()
        prefix = stripped[0] if stripped and stripped[0] in "+-" else "+"
        moved.append(f"{prefix}{name}{newline}")

    result["moved"] = len(moved)
    result["missing"] = missing
    rewritten = header + kept + moved
    if rewritten == lines:
        return result

    if backup_dir is not None:
        try:
            _backupModlistFile(modlist_path, backup_dir)
        except OSError:
            result["failed"] += 1
            return result

    try:
        modlist_path.write_text("".join(rewritten), encoding="utf-8")
    except OSError:
        result["failed"] += 1

    return result


def collectionPriorityOrderNeedsRepair(current_priorities, base_priority=None):
    """Return whether a collection block is misordered or placed too early.

    MO2 priority numbers increase down the visible left pane. A collection can
    therefore be internally sorted while still being wrong if it landed before
    the pre-install ``base_priority``.
    """
    priorities = list(current_priorities or [])
    if priorities != sorted(priorities):
        return True
    if not priorities or base_priority is None:
        return False
    try:
        return min(priorities) < int(base_priority)
    except (TypeError, ValueError):
        return False


def repairModlistDisabledStates(modlist_path, mod_names, backup_dir=None):
    """Disable matching MO2 profile modlist entries while preserving order."""
    result = {
        "checked": 0,
        "disabled": 0,
        "already_disabled": 0,
        "missing": [],
        "failed": 0,
    }
    mod_names = set(mod_names or [])
    if not mod_names:
        return result

    modlist_path = Path(modlist_path)
    try:
        lines = modlist_path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        result["failed"] += 1
        result["missing"] = sorted(mod_names)
        return result

    seen = set()
    changed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped[0] in "+-":
            mod_name = stripped[1:]
            if mod_name not in mod_names:
                continue
            result["checked"] += 1
            seen.add(mod_name)
            if stripped[0] == "-":
                result["already_disabled"] += 1
                continue
            newline = (
                "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            )
            lines[index] = f"-{mod_name}{newline}"
            result["disabled"] += 1
            changed = True

    result["missing"] = sorted(mod_names - seen)
    if not changed:
        return result

    if backup_dir is not None:
        try:
            _backupModlistFile(modlist_path, backup_dir)
        except OSError:
            result["failed"] += 1
            return result

    try:
        modlist_path.write_text("".join(lines), encoding="utf-8")
    except OSError:
        result["failed"] += 1

    return result


def repairPluginEnabledStates(
    plugins_path, plugin_names, backup_dir=None, append_missing=False
):
    """Enable matching MO2 profile plugin entries while preserving load order.

    MO2 can expose newly discovered plugins in the live plugin model before the
    profile ``plugins.txt`` file contains an entry for them. Callers that have
    independently verified a plugin exists may opt into appending missing names;
    the default is strict so stale plugin names remain visible as blocked work.
    """
    result = {
        "checked": 0,
        "enabled": 0,
        "already_enabled": 0,
        "missing": [],
        "failed": 0,
    }
    requested_names = {str(name).casefold(): str(name) for name in plugin_names or []}
    plugin_names = set(requested_names)
    if not plugin_names:
        return result

    plugins_path = Path(plugins_path)
    try:
        lines = plugins_path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        result["failed"] += 1
        result["missing"] = sorted(requested_names.values())
        return result

    seen = set()
    changed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        marker = "*" if stripped.startswith("*") else ""
        plugin_name = stripped[1:] if marker else stripped
        key = plugin_name.casefold()
        if key not in plugin_names:
            continue
        result["checked"] += 1
        seen.add(key)
        requested_name = requested_names[key]
        newline = (
            "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        )
        if marker == "*":
            result["already_enabled"] += 1
            if plugin_name != requested_name:
                lines[index] = f"*{requested_name}{newline}"
                changed = True
            continue
        lines[index] = f"*{requested_name}{newline}"
        result["enabled"] += 1
        changed = True

    result["missing"] = sorted(requested_names[key] for key in plugin_names - seen)
    if result["missing"] and append_missing:
        if lines and not lines[-1].endswith(("\n", "\r\n")):
            lines[-1] += "\n"
        for plugin_name in result["missing"]:
            lines.append(f"*{plugin_name}\n")
            result["enabled"] += 1
            changed = True
        result["missing"] = []
    if not changed:
        return result

    if backup_dir is not None:
        try:
            backup_dir = Path(backup_dir)
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / plugins_path.name).write_bytes(plugins_path.read_bytes())
        except OSError:
            result["failed"] += 1
            return result

    try:
        plugins_path.write_text("".join(lines), encoding="utf-8")
    except OSError:
        result["failed"] += 1

    return result


def inferModIdFromDownloadName(name):
    """Infer a Nexus mod ID from an MO2 archive filename when metadata is missing."""
    clean_name = str(name)
    if clean_name.endswith(".unfinished"):
        clean_name = clean_name[: -len(".unfinished")]

    for match in re.finditer(r"-(\d+)(?=[-.])", clean_name):
        value = int(match.group(1))
        if value >= 1000:
            return value
    return None


def orphanUnfinishedDownloadEntries(downloads_dir):
    """Return unfinished archives that have no MO2 metadata sidecar."""
    entries = []
    if not downloads_dir or not downloads_dir.exists():
        return entries

    for archive_file in downloads_dir.glob("*.unfinished"):
        metadata_file = Path(str(archive_file) + ".meta")
        if metadata_file.exists():
            continue

        try:
            stat = archive_file.stat()
        except OSError:
            continue

        entries.append(
            {
                "archive": archive_file,
                "archive_size": stat.st_size,
                "mtime": stat.st_mtime,
                "mod_id": inferModIdFromDownloadName(archive_file.name),
            }
        )

    return entries


def matchingPartialOrphanUnfinishedEntries(
    downloads_dir,
    key,
    collection_keys,
    completed_on_disk=None,
    completed_keys=None,
    failed_keys=None,
):
    """Return non-empty orphan unfinished files that uniquely match ``key``.

    MO2 can create ``.unfinished`` archives before it writes the metadata sidecar.
    In that state the only useful identifier is usually the Nexus mod id embedded
    in the archive name. Only treat such an orphan as belonging to a collection
    entry when that mod id maps to one remaining, not-yet-complete key.
    """
    completed_on_disk = set(completed_on_disk or set())
    completed_keys = set(completed_keys or set())
    failed_keys = set(failed_keys or set())
    collection_keys = set(collection_keys or set())

    matches = []
    for entry in orphanUnfinishedDownloadEntries(downloads_dir):
        if entry.get("archive_size", 0) <= 0:
            continue

        mod_id = entry.get("mod_id")
        if mod_id is None:
            continue

        candidates = [
            candidate
            for candidate in collection_keys
            if candidate[0] == mod_id
            and candidate not in completed_on_disk
            and candidate not in completed_keys
            and candidate not in failed_keys
        ]
        if candidates == [key]:
            matches.append(entry)

    return matches


def unfinishedDownloadEntries(downloads_dir):
    """Return unfinished MO2 download files indexed by Nexus (mod_id, file_id)."""
    entries = {}
    if not downloads_dir or not downloads_dir.exists():
        return entries

    for metadata_file in downloads_dir.glob("*.unfinished.meta"):
        key = readDownloadMetaKey(metadata_file)
        if key is None:
            continue

        archive_file = Path(str(metadata_file)[: -len(".meta")])
        try:
            archive_size = archive_file.stat().st_size if archive_file.exists() else 0
            metadata_mtime = metadata_file.stat().st_mtime
            archive_mtime = archive_file.stat().st_mtime if archive_file.exists() else 0
        except OSError:
            continue

        entries.setdefault(key, []).append(
            {
                "archive": archive_file,
                "metadata": metadata_file,
                "archive_size": archive_size,
                "mtime": max(metadata_mtime, archive_mtime),
            }
        )

    return entries


def zeroByteUnfinishedEntries(entries):
    """Return entries safe to remove before starting a fresh download.

    A zero-byte ``.unfinished`` archive means MO2 created the placeholder but no
    archive data has been written. If all matching leftovers are still empty,
    removing them lets MO2 queue the same Nexus file without a duplicate-name
    prompt. Any non-empty partial keeps the whole set intact so MO2 can resume it.
    """
    if not entries:
        return []
    if any(entry["archive_size"] > 0 for entry in entries):
        return []
    return list(entries)


def removeUnfinishedEntries(entries):
    """Remove unfinished archive/metadata pairs and return removed file count."""
    removed = 0
    for entry in entries or []:
        for path_key in ("archive", "metadata"):
            path = entry[path_key]
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except OSError:
                continue

    return removed


def staleOrphanUnfinishedDownloadEntries(entries, now, stale_seconds):
    """Return stale orphan unfinished archives after the tracker has waited."""
    if stale_seconds <= 0:
        return []

    return [entry for entry in entries or [] if now - entry["mtime"] >= stale_seconds]


def removeOrphanUnfinishedEntries(entries):
    """Remove exact orphan unfinished archive entries and return file count."""
    removed = 0
    seen = set()
    for entry in entries or []:
        archive = entry.get("archive") if isinstance(entry, dict) else None
        if not archive:
            continue
        archive = Path(archive)
        if archive in seen:
            continue
        seen.add(archive)
        try:
            if archive.exists():
                archive.unlink()
                removed += 1
        except OSError:
            continue

    return removed


def removeOrphanUnfinishedDownloadsForKeys(downloads_dir, keys, include_nonzero=False):
    """Remove zero-byte orphan unfinished archives inferred to belong to keys."""
    mod_ids = {int(key[0]) for key in keys}
    removed = 0

    for entry in orphanUnfinishedDownloadEntries(downloads_dir):
        if entry["mod_id"] not in mod_ids:
            continue
        if entry["archive_size"] > 0 and not include_nonzero:
            continue

        try:
            if entry["archive"].exists():
                entry["archive"].unlink()
                removed += 1
        except OSError:
            continue

    return removed


def cleanupZeroByteUnfinishedDownloads(downloads_dir, pending_keys):
    """Remove empty unfinished downloads for the requested Nexus file keys.

    MO2 prompts before queueing if a same-named ``.unfinished`` placeholder is
    still present. This preflight only removes entries whose matching archive is
    still zero bytes, preserving real partial downloads for normal MO2 resume.
    MO2 can also leave zero-byte orphan placeholders before it writes a
    ``.unfinished.meta`` sidecar; those carry no resumable data, so remove them
    as well before a collection retry.
    """
    entries_by_key = unfinishedDownloadEntries(downloads_dir)
    cleaned_keys = set()
    removed_files = 0

    for key in pending_keys:
        cleanup_entries = zeroByteUnfinishedEntries(entries_by_key.get(key))
        if not cleanup_entries:
            continue

        removed = removeUnfinishedEntries(cleanup_entries)
        if removed:
            cleaned_keys.add(key)
            removed_files += removed

    orphan_entries = [
        entry
        for entry in orphanUnfinishedDownloadEntries(downloads_dir)
        if entry.get("archive_size", 0) <= 0
    ]
    orphan_removed = removeOrphanUnfinishedEntries(orphan_entries)
    if orphan_removed:
        removed_files += orphan_removed
        orphan_mod_ids = {
            entry.get("mod_id")
            for entry in orphan_entries
            if entry.get("mod_id") is not None
        }
        cleaned_keys.update(
            key for key in pending_keys if int(key[0]) in orphan_mod_ids
        )

    return {
        "cleaned_keys": cleaned_keys,
        "removed_files": removed_files,
    }


def hasPartialUnfinishedEntries(entries):
    """Return True when MO2 has already written archive data for this file."""
    return bool(entries) and any(entry["archive_size"] > 0 for entry in entries)


def activeUnfinishedDownloadFingerprint(
    entries_by_key,
    orphan_entries=None,
    pending_keys=None,
):
    """Return a stable fingerprint for non-empty unfinished download activity."""
    pending_keys = set(pending_keys or [])
    restrict_to_pending = bool(pending_keys)
    fingerprint = []

    for key, entries in (entries_by_key or {}).items():
        if restrict_to_pending and key not in pending_keys:
            continue
        for entry in entries or []:
            size = int(entry.get("archive_size", 0) or 0)
            if size <= 0:
                continue
            fingerprint.append(
                (
                    "metadata",
                    int(key[0]),
                    int(key[1]),
                    size,
                    float(entry.get("mtime", 0) or 0),
                )
            )

    pending_mod_ids = {int(key[0]) for key in pending_keys}
    for entry in orphan_entries or []:
        size = int(entry.get("archive_size", 0) or 0)
        if size <= 0:
            continue
        mod_id = entry.get("mod_id")
        if mod_id is None:
            continue
        mod_id = int(mod_id)
        if restrict_to_pending and mod_id not in pending_mod_ids:
            continue
        fingerprint.append(
            (
                "orphan",
                mod_id,
                size,
                float(entry.get("mtime", 0) or 0),
            )
        )

    return tuple(sorted(fingerprint))


def staleZeroByteUnfinishedEntries(entries, now, stale_seconds):
    """Return unfinished entries safe to discard before a retry.

    MO2 may leave a zero-byte ``.unfinished`` archive behind when a download
    never actually starts. Requeueing the same file while that stale entry is
    present can trigger a blocking "Download again?" dialog. Non-empty partial
    downloads are intentionally preserved so MO2 can resume them.
    """
    if stale_seconds <= 0 or not entries:
        return []

    newest_mtime = max(entry["mtime"] for entry in entries)
    if now - newest_mtime < stale_seconds:
        return []

    return zeroByteUnfinishedEntries(entries)


def staleUnfinishedEntries(entries, now, stale_seconds):
    """Return stale unfinished entries that can be retried from scratch.

    This is stricter than pre-queue cleanup: it only runs after the progress
    tracker has already waited for MO2 callbacks and disk updates. At that
    point, removing the stale partial lets MO2 request a fresh signed URL
    instead of leaving the collection blocked forever.
    """
    if stale_seconds <= 0 or not entries:
        return []

    newest_mtime = max(entry["mtime"] for entry in entries)
    if now - newest_mtime < stale_seconds:
        return []

    return list(entries)


def staleDownloadStartAction(attempts, max_retries):
    """Return the next action for a download start that never produced bytes."""
    try:
        attempts = int(attempts or 0)
    except (TypeError, ValueError):
        attempts = 0
    try:
        max_retries = int(max_retries or 0)
    except (TypeError, ValueError):
        max_retries = 0

    if attempts < max(0, max_retries):
        return "retry"
    return "restart_required"


def zeroByteDownloadStartIsStalled(first_seen_at, now, timeout_seconds):
    """Return True when a zero-byte MO2 placeholder has exceeded its grace."""
    try:
        first_seen_at = float(first_seen_at)
        now = float(now)
    except (TypeError, ValueError):
        return False
    try:
        timeout_seconds = max(0, float(timeout_seconds or 0))
    except (TypeError, ValueError):
        timeout_seconds = 0
    return now - first_seen_at >= timeout_seconds


def staleAlreadyStartedAction(has_metadata_entry):
    """Return the action for an expired MO2 Already Started prompt."""
    return "wait" if has_metadata_entry else "restart_required"


def downloadTailBoundaryReached(
    total,
    successful,
    failed,
    unresolved,
    unresolved_limit,
    boundary_started_at,
    now,
    grace_seconds,
    completion_ratio=0.75,
):
    """Return True when the remaining download tail should stop blocking progress."""
    try:
        total = int(total or 0)
        successful = int(successful or 0)
        failed = int(failed or 0)
        unresolved = int(unresolved or 0)
        unresolved_limit = int(unresolved_limit or 0)
    except (TypeError, ValueError):
        return False

    try:
        completion_ratio = float(completion_ratio)
    except (TypeError, ValueError):
        completion_ratio = 0.75
    completion_ratio = min(1.0, max(0.0, completion_ratio))

    if total <= 0 or unresolved_limit <= 0:
        return False
    adaptive_unresolved_limit = max(
        1,
        total - int(math.ceil(total * completion_ratio)),
    )
    effective_unresolved_limit = min(unresolved_limit, adaptive_unresolved_limit)
    if unresolved < effective_unresolved_limit:
        return False

    terminal = max(0, successful) + max(0, failed)
    if terminal <= 0:
        return False

    if terminal / total < completion_ratio:
        return False

    try:
        boundary_started_at = float(boundary_started_at)
        now = float(now)
        grace_seconds = max(0.0, float(grace_seconds or 0))
    except (TypeError, ValueError):
        return False

    return now - boundary_started_at >= grace_seconds


def downloadTailBoundaryArmTime(last_progress_at, now):
    """Return when the current quiet download tail should be considered started."""
    try:
        now = float(now)
    except (TypeError, ValueError):
        now = 0.0
    try:
        last_progress_at = float(last_progress_at)
    except (TypeError, ValueError):
        return now
    if last_progress_at <= 0 or last_progress_at > now:
        return now
    return last_progress_at


def adaptiveDownloadTailGraceSeconds(
    total,
    unresolved_limit,
    max_retries,
    stale_unfinished_seconds,
    minimum=20,
    maximum=180,
):
    """Return a bounded tail grace that scales with run size and retry policy."""
    try:
        total = max(0, int(total or 0))
        unresolved_limit = max(1, int(unresolved_limit or 1))
        max_retries = max(0, int(max_retries or 0))
        stale_unfinished_seconds = max(0, int(stale_unfinished_seconds or 0))
        minimum = max(0, int(minimum or 0))
        maximum = max(minimum, int(maximum or minimum))
    except (TypeError, ValueError):
        return 60

    scale_steps = max(1, (total + unresolved_limit - 1) // unresolved_limit)
    size_seconds = 5 * scale_steps
    retry_seconds = 10 * max_retries
    stale_seconds = min(60, max(0, stale_unfinished_seconds // 10))
    return min(maximum, max(minimum, size_seconds + retry_seconds + stale_seconds))


def downloadProgressIsStalled(last_progress_at, now, stall_seconds):
    """Return True when no overall download progress happened inside the window."""
    try:
        last_progress_at = float(last_progress_at)
        now = float(now)
        stall_seconds = max(0.0, float(stall_seconds or 0))
    except (TypeError, ValueError):
        return False
    return now - last_progress_at >= stall_seconds


def downloadTailLaggardPlan(stalled_keys, retry_attempts, retry_budget):
    """Partition stalled tail keys into retryable and exhausted work."""
    try:
        retry_budget = max(0, int(retry_budget or 0))
    except (TypeError, ValueError):
        retry_budget = 0

    retry_keys = set()
    restart_required_keys = set()
    for key in set(stalled_keys or []):
        try:
            attempts = int((retry_attempts or {}).get(key, 0) or 0)
        except (TypeError, ValueError, AttributeError):
            attempts = 0
        if attempts < retry_budget:
            retry_keys.add(key)
        else:
            restart_required_keys.add(key)

    return {
        "retry": retry_keys,
        "restart_required": restart_required_keys,
    }


def coerceDownloadId(download_id):
    """Return a usable MO2 download id, or None for failed queue-start values."""
    try:
        coerced = int(download_id)
    except (TypeError, ValueError):
        return None

    return coerced if coerced >= 0 else None


def popDownloadKey(download_ids, download_id):
    """Pop a tracked MO2 download key while tolerating invalid callback IDs."""
    coerced_download_id = coerceDownloadId(download_id)
    if coerced_download_id is None:
        return None
    return download_ids.pop(coerced_download_id, None)


def steamVdfScalar(text, key, start=0):
    """Return the first Steam VDF scalar value for ``key`` at or after ``start``."""
    if text is None:
        return None

    pattern = re.compile(r'"' + re.escape(str(key)) + r'"\s+"([^"]*)"')
    match = pattern.search(str(text), max(0, int(start or 0)))
    return match.group(1) if match else None


def _steamVdfBlock(text, key, start=0):
    """Return a simple Steam VDF block body for ``key``.

    Steam's VDF format is simple enough for a brace-balanced scanner here. This
    keeps the guard checks dependency-free and testable without touching Steam.
    """
    if text is None:
        return None

    source = str(text)
    key_match = re.search(
        r'"' + re.escape(str(key)) + r'"\s*\{', source[max(0, int(start or 0)) :]
    )
    if not key_match:
        return None

    brace = max(0, int(start or 0)) + key_match.end() - 1
    depth = 0
    in_string = False
    escaped = False
    body_start = brace + 1
    for index in range(brace, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[body_start:index]

    return None


def steamLaunchOptions(localconfig_text, app_id="489830"):
    """Return Steam launch options for an app from ``localconfig.vdf`` text."""
    app_block = _steamVdfBlock(localconfig_text, app_id)
    if app_block is not None:
        value = steamVdfScalar(app_block, "LaunchOptions")
        if value is not None:
            return value
    return steamVdfScalar(localconfig_text, "LaunchOptions")


def steamDefaultLaunchOption(localconfig_text, app_id="489830"):
    """Return Steam's selected launch-option value for an app, if present."""
    source = str(localconfig_text or "")
    for match in re.finditer(r'"' + re.escape(str(app_id)) + r'"\s*\{', source):
        app_block = _steamVdfBlock(source, app_id, match.start())
        launch_block = _steamVdfBlock(app_block, "DefaultLaunchOption")
        if launch_block is None:
            continue

        option = re.search(r'"[^"]+"\s+"([^"]*)"', launch_block)
        if option:
            return option.group(1)

    return None


def latestSteamLaunchCommand(gameprocess_log_text, app_id="489830"):
    """Return the latest logged Steam launch command for an app."""
    latest = None
    pattern = re.compile(
        r"\[[^\]]+\]\s+AppID\s+"
        + re.escape(str(app_id))
        + r'\s+adding PID \d+ as a tracked process "([^"]+)"'
    )
    for match in pattern.finditer(str(gameprocess_log_text or "")):
        latest = match.group(1)
    return latest


def steamShaderProcessingQueue(config_text):
    """Return app IDs listed in Steam's shader processing queue."""
    queue = steamVdfScalar(config_text, "ProcessingQueue")
    if not queue:
        return []
    return [item for item in re.split(r"[;\s,]+", queue.strip()) if item]


def steamShaderCacheDisabled(config_text):
    """Return True when Steam's shader cache is disabled in ``config.vdf``."""
    return steamVdfScalar(config_text, "DisableShaderCache") == "1"


def steamAppShaderCacheSize(config_text, app_id="489830"):
    """Return the recorded shader cache size for an app, if present."""
    shader_block = _steamVdfBlock(config_text, "ShaderCacheManager")
    app_block = (
        _steamVdfBlock(shader_block, app_id) if shader_block is not None else None
    )
    value = (
        steamVdfScalar(app_block, "ShaderCacheSize") if app_block is not None else None
    )
    if value is None:
        pattern = re.compile(
            r'"'
            + re.escape(str(app_id))
            + r'"\s*\{[^{}]*"ShaderCacheSize"\s+"([^"]*)"',
            re.S,
        )
        match = pattern.search(str(config_text or ""))
        value = match.group(1) if match else None
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def steamAppInfoHasLaunchExecutable(appinfo_text, executable):
    """Return True when Steam app metadata mentions a launch executable."""
    return bool(executable) and str(executable) in str(appinfo_text or "")


def isImmutableLsattrLine(line):
    """Return True when an ``lsattr`` output line includes the immutable flag."""
    if not line:
        return False
    attrs = str(line).split(None, 1)[0]
    return "i" in attrs


def steamMo2GuardAudit(
    localconfig_text="",
    steam_config_text="",
    compat_text="",
    appinfo_text="",
    gameprocess_log_text="",
    localconfig_lsattr="",
    steam_config_lsattr="",
    compat_lsattr="",
    appinfo_lsattr="",
    appmanifest_lsattr="",
    shadercache_lsattr="",
    mods_count=None,
    downloads_count=None,
    app_id="489830",
    expected_launch_options="",
    expected_default_launch_option=None,
    expected_launch_executable="mo2-redirector.exe",
    require_clean_mo2=False,
    require_latest_launch=False,
):
    """Audit Steam/MO2 test guard state and return actionable problems."""
    problems = []
    warnings = []

    launch_options = steamLaunchOptions(localconfig_text, app_id)
    if launch_options != expected_launch_options:
        if launch_options == "USER=steamuser %command%":
            problems.append(
                "Steam launch option bypasses MO2 redirector; expected "
                f"{expected_launch_options!r}, found {launch_options!r}."
            )
        else:
            problems.append(
                "Steam launch option changed; expected "
                f"{expected_launch_options!r}, found {launch_options!r}."
            )

    default_launch_option = steamDefaultLaunchOption(localconfig_text, app_id)
    if default_launch_option != expected_default_launch_option:
        problems.append(
            "Steam default launch option changed; expected "
            f"{expected_default_launch_option!r}, found {default_launch_option!r}."
        )

    latest_launch_command = latestSteamLaunchCommand(gameprocess_log_text, app_id)
    if require_latest_launch:
        if not latest_launch_command:
            problems.append(
                f"No Steam gameprocess launch command found for app {app_id}."
            )
        elif expected_launch_executable not in latest_launch_command:
            problems.append(
                "Latest Steam launch command did not execute MO2 redirector; "
                f"expected {expected_launch_executable!r} in {latest_launch_command!r}."
            )
        elif "SkyrimSELauncher.exe" in latest_launch_command:
            problems.append(
                "Latest Steam launch command still includes SkyrimSELauncher.exe: "
                f"{latest_launch_command!r}."
            )

    if str(app_id) in steamShaderProcessingQueue(steam_config_text):
        problems.append(f"Steam shader processing queue still contains app {app_id}.")

    if not steamShaderCacheDisabled(steam_config_text):
        problems.append("Steam shader cache is not disabled in config.vdf.")

    shader_size = steamAppShaderCacheSize(steam_config_text, app_id)
    if shader_size not in (None, 0):
        problems.append(
            f"Steam shader cache size for app {app_id} is {shader_size}, expected 0."
        )

    if compat_text is not None and str(app_id) not in str(compat_text):
        problems.append(f"Steam compat.vdf does not mention app {app_id}.")

    if not steamAppInfoHasLaunchExecutable(appinfo_text, expected_launch_executable):
        problems.append(
            "Steam appinfo.vdf does not expose the MO2 launch executable "
            f"{expected_launch_executable!r}."
        )

    lock_checks = {
        "localconfig.vdf": localconfig_lsattr,
        "config.vdf": steam_config_lsattr,
        "compat.vdf": compat_lsattr,
        "appinfo.vdf": appinfo_lsattr,
        f"appmanifest_{app_id}.acf": appmanifest_lsattr,
        f"shadercache/{app_id}": shadercache_lsattr,
    }
    for name, lsattr_line in lock_checks.items():
        if not isImmutableLsattrLine(lsattr_line):
            problems.append(f"{name} is not immutable according to lsattr.")

    if require_clean_mo2:
        if mods_count not in (None, 0):
            problems.append(
                f"MO2 managed mods directory is not clean: {mods_count} entries."
            )
        if downloads_count not in (None, 0):
            problems.append(
                f"MO2 downloads directory is not clean: {downloads_count} entries."
            )

    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "launch_options": launch_options,
        "default_launch_option": default_launch_option,
        "latest_launch_command": latest_launch_command,
        "shader_processing_queue": steamShaderProcessingQueue(steam_config_text),
        "shader_cache_disabled": steamShaderCacheDisabled(steam_config_text),
        "shader_cache_size": shader_size,
        "appinfo_has_launch_executable": steamAppInfoHasLaunchExecutable(
            appinfo_text, expected_launch_executable
        ),
        "mods_count": mods_count,
        "downloads_count": downloads_count,
    }

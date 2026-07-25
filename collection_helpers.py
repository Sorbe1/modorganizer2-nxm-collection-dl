import re
import unicodedata
from configparser import ConfigParser
from pathlib import Path

FOMOD_ADVANCE_EXCLUDED_TITLES = {
    "",
    "Error",
    "Install Mods",
    "Mod Exists",
    "Quick Install",
    "NXM Collection Installer - Installing Mods",
    "NXM Collection Installer - Select Collection",
}

INSTALLER_SETTING_DEFAULTS = {
    "auto_accept_quick_install": True,
    "auto_dismiss_known_post_install_errors": True,
    "auto_merge_existing_mods": False,
    "auto_advance_fomod_defaults": False,
    "auto_advance_fomod_max_steps": 20,
    "install_files_as_separate_mods": True,
    "activate_mods_after_install": True,
}


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


def duplicateDownloadPromptActionLabel(window_title, buttons):
    """Return the duplicate-download prompt action to click, or None.

    MO2 shows this confirmation when a same-named archive already exists in the
    downloads directory. During a collection batch, declining the duplicate is
    the least surprising non-interactive choice: keep the existing archive and
    let the collection tracker reconcile the item from disk.

    MO2 can also show ``Already Started`` when its download manager already has
    an entry for the Nexus file. That dialog has no useful choice for the batch
    flow, so acknowledging it lets the tracker wait for MO2's existing entry.
    """
    labels = {
        normalizedButtonLabel(label): bool(enabled)
        for label, enabled in buttons
        if normalizedButtonLabel(label)
    }

    if window_title == "Download again?":
        if not {"yes", "no", "cancel"}.issubset(labels):
            return None
        if labels.get("no"):
            return "no"

    if window_title == "Already Started" and labels.get("ok"):
        return "ok"

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


def downloadCompletionChoices(state, has_on_complete):
    """Return the visible terminal choices for a completed download pass."""
    successful = int(state.get("successful") or 0)
    failed = int(state.get("failed") or 0)
    has_failures = bool(state.get("has_failures"))
    install_visible = bool(has_on_complete) and successful > 0

    return {
        "retry_visible": failed > 0,
        "install_visible": install_visible,
        "install_label": "Install Available" if has_failures else "Install Collection",
        "fomod_defaults_visible": bool(has_on_complete),
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


def coerceBoolSetting(value):
    """Return a bool for MO2 plugin settings stored as bools or strings."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
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


def downloadedFileKeys(downloads_dir):
    """Return Nexus (mod_id, file_id) pairs with a completed archive on disk."""
    keys = set()
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
        if archive_file.exists() and archive_file.stat().st_size > 0:
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
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue

    return removed


def staleOrphanUnfinishedDownloadEntries(entries, now, stale_seconds):
    """Return stale orphan unfinished archives after the tracker has waited."""
    if stale_seconds <= 0:
        return []

    return [entry for entry in entries or [] if now - entry["mtime"] >= stale_seconds]


def removeOrphanUnfinishedDownloadsForKeys(downloads_dir, keys):
    """Remove zero-byte orphan unfinished archives inferred to belong to keys."""
    mod_ids = {int(key[0]) for key in keys}
    removed = 0

    for entry in orphanUnfinishedDownloadEntries(downloads_dir):
        if entry["archive_size"] > 0 or entry["mod_id"] not in mod_ids:
            continue

        try:
            entry["archive"].unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue

    return removed


def cleanupZeroByteUnfinishedDownloads(downloads_dir, pending_keys):
    """Remove empty unfinished downloads for the requested Nexus file keys.

    MO2 prompts before queueing if a same-named ``.unfinished`` placeholder is
    still present. This preflight only removes entries whose matching archive is
    still zero bytes, preserving real partial downloads for normal MO2 resume.
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

    return {
        "cleaned_keys": cleaned_keys,
        "removed_files": removed_files,
    }


def hasPartialUnfinishedEntries(entries):
    """Return True when MO2 has already written archive data for this file."""
    return bool(entries) and any(entry["archive_size"] > 0 for entry in entries)


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

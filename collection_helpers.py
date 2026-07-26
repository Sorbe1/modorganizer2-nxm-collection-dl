import re
import unicodedata
from configparser import ConfigParser
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

INSTALLER_SETTING_DEFAULTS = {
    "auto_accept_quick_install": True,
    "auto_dismiss_known_post_install_errors": True,
    "auto_cancel_invalid_install_content": True,
    "auto_merge_existing_mods": False,
    "auto_advance_fomod_defaults": True,
    "auto_advance_fomod_max_steps": 80,
    "install_files_as_separate_mods": True,
    "activate_mods_after_install": True,
    "activate_mods_during_install": False,
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


def _xmlLocalName(tag):
    return str(tag).rsplit("}", 1)[-1]


def _directChildren(element, name):
    return [child for child in list(element) if _xmlLocalName(child.tag) == name]


def fomodManualChoiceGuide(module_config_xml):
    """Return unresolved required FOMOD choices from a ModuleConfig.xml payload."""
    try:
        root = ElementTree.fromstring(module_config_xml)
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

            option_names = [
                plugin.attrib.get("name", "").strip() for plugin in plugins
            ]
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


def installNoResultReason(invalid_content_cancelled, warning_messages):
    """Return a useful reason when MO2 returns no installed mod object."""
    if invalid_content_cancelled:
        return (
            "invalid install content warning accepted, but MO2 returned no "
            "installed mod"
        )

    if any("[fomodinstallerdialog.cpp:" in str(message) for message in warning_messages):
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
        if not {"yes", "no"}.issubset(labels):
            return None
        if labels.get("no"):
            return "no"

    if window_title == "Already Started" and labels.get("ok"):
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


def collectionLinkCompletionPolicy():
    """Return terminal download behavior for Nexus Add Collection links.

    Add Collection should continue into install on clean download completion by
    default. Partial download failures still stop for an explicit user decision.
    """
    return {
        "attach_install_callback": True,
        "prompt_after_download": True,
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
        try:
            mod_id = int(mod["file"]["mod"]["modId"])
            file_id = int(mod["file"]["fileId"])
            expected_size = int(mod["file"]["sizeInBytes"])
        except (TypeError, KeyError, ValueError):
            continue

        if expected_size > 0:
            sizes[(mod_id, file_id)] = expected_size

    return sizes


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


def removeOrphanUnfinishedDownloadsForKeys(
    downloads_dir, keys, include_nonzero=False
):
    """Remove zero-byte orphan unfinished archives inferred to belong to keys."""
    mod_ids = {int(key[0]) for key in keys}
    removed = 0

    for entry in orphanUnfinishedDownloadEntries(downloads_dir):
        if entry["mod_id"] not in mod_ids:
            continue
        if entry["archive_size"] > 0 and not include_nonzero:
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
    key_match = re.search(r'"' + re.escape(str(key)) + r'"\s*\{', source[max(0, int(start or 0)) :])
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
    app_block = _steamVdfBlock(shader_block, app_id) if shader_block is not None else None
    value = steamVdfScalar(app_block, "ShaderCacheSize") if app_block is not None else None
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
        if launch_options == "USER=tkb %command%":
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
            problems.append(f"No Steam gameprocess launch command found for app {app_id}.")
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
            problems.append(f"MO2 managed mods directory is not clean: {mods_count} entries.")
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

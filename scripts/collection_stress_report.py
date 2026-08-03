#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_helpers import (
    auditMo2ProfileState,
    detachedInstallCacheKeyFromPath,
    downloadMetaInstalledValue,
    failedInstallReviewEntriesWithCategories,
    failedInstallReviewCategoryCounts,
    headlessArchiveInstallLayout,
    readDownloadMetaKey,
    sevenZipArchiveMemberPaths,
    validInstalledDownloadKeysForProfile,
)


REPORT_PREFIX = "nxm-collection-install-warnings-"
REPORT_SUFFIX = ".json"


def _parse_report_sort_key(report_path, report):
    generated = report.get("generated")
    if generated:
        try:
            return (datetime.fromisoformat(generated).timestamp(), str(report_path))
        except ValueError:
            pass
    try:
        return (Path(report_path).stat().st_mtime, str(report_path))
    except OSError:
        return (0, str(report_path))


def _download_metadata_needs_review(download_metadata):
    download_metadata = download_metadata or {}
    return any(
        download_metadata.get(key)
        for key in (
            "downloaded_only",
            "missing_archive",
            "unknown_installed_state",
            "installed_without_valid_container",
        )
    )


def _trim_failed_entry(entry):
    kept = {}
    for key in (
        "mod",
        "name",
        "file",
        "archive",
        "mod_id",
        "file_id",
        "reason",
        "review_category",
        "recommended_action",
    ):
        value = entry.get(key)
        if value is not None:
            kept[key] = value
    return kept


def _report_archive_metadata_path(archive_path, base_path=None):
    if not archive_path:
        return None

    direct_metadata = Path(str(archive_path) + ".meta")
    if direct_metadata.is_file():
        return direct_metadata

    if base_path is None:
        return direct_metadata

    archive_name = Path(str(archive_path).replace("\\", "/")).name
    if not archive_name:
        return direct_metadata
    return Path(base_path) / "downloads" / f"{archive_name}.meta"


def _entry_nexus_key(entry):
    try:
        return (int(entry["mod_id"]), int(entry["file_id"]))
    except (KeyError, TypeError, ValueError):
        return None


def _entry_archive_name(entry):
    archive = entry.get("archive")
    if not archive:
        return ""
    return Path(str(archive).replace("\\", "/")).name.casefold()


def _intentional_removed_state_dirs(base_path):
    base_path = Path(base_path)
    roots = (
        base_path / "removed-downloads",
        base_path / "removed-mod-containers",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            name = child.name.casefold()
            if name.startswith("definite-no-go") or "quarantine" in name:
                yield child
            elif root.name == "removed-mod-containers" and name.startswith(
                "disabled-unwanted"
            ):
                yield child


def _read_removed_meta_key(path):
    key = readDownloadMetaKey(path)
    if key is not None:
        return key

    parser = ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
        general = parser["General"]
        return (int(general["modid"]), int(general["fileid"]))
    except (OSError, KeyError, ValueError):
        return None


def quarantinedDownloadEvidence(base_path):
    """Return Nexus IDs and archive names intentionally removed from this profile."""
    if base_path is None:
        return {"keys": set(), "archives": set()}

    keys = set()
    archives = set()
    for state_dir in _intentional_removed_state_dirs(base_path):
        for path in state_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.name.endswith(".meta"):
                key = _read_removed_meta_key(path)
                if key is not None:
                    keys.add(key)
                archive_name = path.name[: -len(".meta")]
                if archive_name:
                    archives.add(archive_name.casefold())
            elif path.name == "meta.ini":
                key = _read_removed_meta_key(path)
                if key is not None:
                    keys.add(key)
            else:
                archives.add(path.name.casefold())
    return {"keys": keys, "archives": archives}


def failedEntryResolvedByCurrentProfile(entry, valid_installed_keys, base_path=None):
    """Return True when a historical failed entry is now valid in the profile."""
    if valid_installed_keys is None:
        return False

    valid_installed_keys = set(valid_installed_keys)
    entry_key = _entry_nexus_key(entry)
    if entry_key in valid_installed_keys:
        return True

    metadata_path = _report_archive_metadata_path(entry.get("archive"), base_path)
    if metadata_path is None or not metadata_path.is_file():
        return False
    if downloadMetaInstalledValue(metadata_path) != "true":
        return False

    return readDownloadMetaKey(metadata_path) in valid_installed_keys


def failedEntryQuarantinedByCurrentProfile(entry, quarantined_evidence):
    """Return True when a historical failed entry was intentionally quarantined."""
    quarantined_evidence = quarantined_evidence or {}
    entry_key = _entry_nexus_key(entry)
    if entry_key in (quarantined_evidence.get("keys") or set()):
        return True

    archive_name = _entry_archive_name(entry)
    return bool(
        archive_name and archive_name in (quarantined_evidence.get("archives") or set())
    )


def _cached_install_archives(base_path):
    if base_path is None:
        return {}

    cache_dir = Path(base_path) / "nxm-collection-dl-install-cache"
    if not cache_dir.is_dir():
        return {}

    archives = {}
    for path in cache_dir.iterdir():
        if not path.is_file() or path.name.endswith(".unfinished"):
            continue
        key = detachedInstallCacheKeyFromPath(path)
        if key is None:
            continue
        try:
            if path.stat().st_size <= 0:
                continue
        except OSError:
            continue
        current = archives.get(key)
        if current is None or len(path.name) < len(current.name):
            archives[key] = path
    return archives


def _archive_members_from_7z(archive_path):
    try:
        result = subprocess.run(
            ["7z", "l", "-slt", str(archive_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None
    return sevenZipArchiveMemberPaths(result.stdout)


def _cached_archive_layout_plan(entry, cached_archives):
    entry_key = _entry_nexus_key(entry)
    if entry_key is None:
        return None
    archive_path = (cached_archives or {}).get(entry_key)
    if archive_path is None:
        return None

    members = _archive_members_from_7z(archive_path)
    if not members:
        return None
    return headlessArchiveInstallLayout(members)


def failedEntryResolvedByCurrentArchiveLayout(entry, cached_archives):
    """Return True when current archive layout rules can now install the entry."""
    if str(entry.get("review_category") or "") != "ambiguous_archive_layout":
        return False
    if "ambiguous archive layout" not in str(entry.get("reason") or "").casefold():
        return False

    plan = _cached_archive_layout_plan(entry, cached_archives)
    if not plan:
        return False
    return bool(plan.get("installable"))


def reclassifyFailedEntryByCurrentArchiveLayout(entry, cached_archives):
    """Use current cache inspection to improve stale historical failure buckets."""
    reason = str(entry.get("reason") or "")
    if str(entry.get("review_category") or "") != "native_worker_timeout":
        return entry
    if "native archive worker timed out" not in reason.casefold():
        return entry

    plan = _cached_archive_layout_plan(entry, cached_archives)
    if not plan or plan.get("reason") != "FOMOD installer present":
        return entry

    updated = dict(entry)
    updated["reason"] = (
        "manual FOMOD choices required: FOMOD installer present in cached archive "
        "after native worker timeout"
    )
    return failedInstallReviewEntriesWithCategories([updated])[0]


def _mark_resolved_failed_entry(
    entry,
    historical_status="resolved",
    resolution_field="resolved_by_current_profile",
):
    item = _trim_failed_entry(entry)
    item["historical_status"] = historical_status
    item[resolution_field] = True
    return item


def resolve_failed_entries_against_profile(
    summary,
    valid_installed_keys,
    base_path=None,
    quarantined_evidence=None,
    cached_archives=None,
):
    failed_entries = summary.get("failed_entries") or []
    if not failed_entries or valid_installed_keys is None:
        return summary

    unresolved = []
    resolved = []
    reclassified = False
    for entry in failed_entries:
        if failedEntryResolvedByCurrentProfile(entry, valid_installed_keys, base_path):
            resolved.append(_mark_resolved_failed_entry(entry))
        elif failedEntryQuarantinedByCurrentProfile(entry, quarantined_evidence):
            resolved.append(
                _mark_resolved_failed_entry(
                    entry,
                    "quarantined",
                    "quarantined_by_current_profile",
                )
            )
        elif failedEntryResolvedByCurrentArchiveLayout(entry, cached_archives):
            resolved.append(
                _mark_resolved_failed_entry(
                    entry,
                    "resolved",
                    "resolved_by_current_archive_layout",
                )
            )
        else:
            updated = reclassifyFailedEntryByCurrentArchiveLayout(entry, cached_archives)
            reclassified = reclassified or updated != entry
            unresolved.append(updated)

    if not resolved and not reclassified:
        return summary

    summary = dict(summary)
    summary["failed_entries"] = unresolved
    summary["resolved_failed_entries"] = resolved
    summary["resolved_failed_count"] = len(resolved)
    summary["failed_count"] = len(unresolved)
    summary["failed_categories"] = failedInstallReviewCategoryCounts(unresolved)
    summary["actionable_review_count"] = max(
        0,
        int(summary.get("actionable_review_count") or 0) - len(resolved),
    )

    if summary["actionable_review_count"]:
        summary["review_severity"] = "actionable"
    elif summary.get("informational_count"):
        summary["review_severity"] = "informational"
    else:
        summary["review_severity"] = "clean"

    has_review = any(
        (
            summary.get("warning_count"),
            summary.get("unique_warning_count"),
            summary.get("failed_count"),
            summary.get("review_count"),
            summary.get("root_level_count"),
            summary.get("no_applicable_count"),
            summary.get("queued_fomod_recovery_count"),
            summary.get("download_metadata_review"),
        )
    )
    summary["status"] = "needs_review" if has_review else "clean"
    return summary


def summarize_collection_report(report_path):
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failed_entries = report.get("failed_entries") or []
    review_entries = report.get("review_entries") or []
    root_level_entries = report.get("root_level_entries") or []
    no_applicable_entries = report.get("no_applicable_entries") or []
    queued_fomod_recovery_entries = report.get("queued_fomod_recovery_entries") or []
    warning_summary = report.get("warning_summary") or []
    download_metadata_audit = report.get("download_metadata_audit") or {}
    warning_count = int(report.get("warning_count") or 0)
    unique_warning_count = int(report.get("unique_warning_count") or 0)
    failed_count = len(failed_entries)
    categorized_failed_entries = failedInstallReviewEntriesWithCategories(
        failed_entries
    )
    review_count = len(review_entries)
    download_metadata_review = _download_metadata_needs_review(download_metadata_audit)
    actionable_review_count = (
        failed_count
        + review_count
        + unique_warning_count
        + len(queued_fomod_recovery_entries)
        + (1 if download_metadata_review else 0)
    )
    informational_count = len(root_level_entries) + len(no_applicable_entries)
    needs_review = any(
        (
            warning_count,
            unique_warning_count,
            failed_count,
            review_count,
            len(root_level_entries),
            len(no_applicable_entries),
            len(queued_fomod_recovery_entries),
            download_metadata_review,
        )
    )
    if actionable_review_count:
        review_severity = "actionable"
    elif informational_count:
        review_severity = "informational"
    else:
        review_severity = "clean"
    return {
        "collection": report.get("collection"),
        "revision": report.get("revision"),
        "name": report.get("name"),
        "generated": report.get("generated"),
        "report": str(report_path),
        "status": "needs_review" if needs_review else "clean",
        "review_severity": review_severity,
        "actionable_review_count": actionable_review_count,
        "informational_count": informational_count,
        "warning_count": warning_count,
        "unique_warning_count": unique_warning_count,
        "warning_categories": {
            str(item.get("category")): int(item.get("occurrences") or 0)
            for item in warning_summary
            if item.get("category")
        },
        "failed_count": failed_count,
        "failed_entries": [
            _trim_failed_entry(item) for item in categorized_failed_entries
        ],
        "resolved_failed_entries": [],
        "resolved_failed_count": 0,
        "review_count": review_count,
        "failed_categories": failedInstallReviewCategoryCounts(
            categorized_failed_entries
        ),
        "root_level_count": len(root_level_entries),
        "no_applicable_count": len(no_applicable_entries),
        "queued_fomod_recovery_count": len(queued_fomod_recovery_entries),
        "download_metadata_review": download_metadata_review,
        "add_collection_launch_count": int(
            report.get("add_collection_launch_count") or 0
        ),
        "add_collection_recovery_count": int(
            report.get("add_collection_recovery_count") or 0
        ),
        "sort_key": _parse_report_sort_key(report_path, report),
    }


def discover_collection_reports(logs_dir):
    logs_dir = Path(logs_dir)
    if not logs_dir.exists():
        return []
    return sorted(logs_dir.glob(f"{REPORT_PREFIX}*{REPORT_SUFFIX}"))


def latest_collection_summaries(report_paths):
    latest = {}
    for report_path in report_paths:
        try:
            summary = summarize_collection_report(report_path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        key = (summary.get("collection"), summary.get("revision"))
        if key not in latest or summary["sort_key"] > latest[key]["sort_key"]:
            latest[key] = summary
    return sorted(latest.values(), key=lambda item: item["sort_key"])


def all_collection_summaries(report_paths):
    summaries = []
    for report_path in report_paths:
        try:
            summaries.append(summarize_collection_report(report_path))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return sorted(summaries, key=lambda item: item["sort_key"])


def _strip_sort_keys(summaries):
    clean = []
    for item in summaries:
        item = dict(item)
        item.pop("sort_key", None)
        clean.append(item)
    return clean


def _filter_failed_entries_by_categories(summary, failed_category_filters):
    filters = {
        str(item).casefold()
        for item in (failed_category_filters or [])
        if str(item or "").strip()
    }
    if not filters:
        return summary
    matching = [
        item
        for item in (summary.get("failed_entries") or [])
        if str(item.get("review_category") or "").casefold() in filters
    ]
    if not matching:
        return None
    summary = dict(summary)
    summary["failed_entries"] = matching
    summary["failed_count"] = len(matching)
    summary["failed_categories"] = failedInstallReviewCategoryCounts(matching)
    summary["resolved_failed_entries"] = []
    summary["resolved_failed_count"] = 0
    summary["status"] = "needs_review"
    return summary


def _filter_summaries(
    summaries,
    collection_filters=None,
    needs_review_only=False,
    failed_category_filters=None,
):
    filters = {str(item).lower() for item in (collection_filters or []) if item}
    filtered = []
    for item in summaries:
        if filters:
            labels = {
                str(item.get("collection") or "").lower(),
                str(item.get("name") or "").lower(),
            }
            if not labels.intersection(filters):
                continue
        if needs_review_only and item.get("status") != "needs_review":
            continue
        item = _filter_failed_entries_by_categories(item, failed_category_filters)
        if item is None:
            continue
        filtered.append(item)
    return filtered


def summarize_stress_totals(summaries):
    failed_categories = {
        "missing_download": 0,
        "duplicate_container": 0,
        "native_worker_timeout": 0,
        "ambiguous_archive_layout": 0,
        "fomod_choices": 0,
        "empty_installer_output": 0,
        "manual_or_review": 0,
        "other_failure": 0,
    }
    warning_categories = {}
    totals = {
        "failed_count": 0,
        "review_count": 0,
        "warning_count": 0,
        "unique_warning_count": 0,
        "actionable_review_count": 0,
        "informational_count": 0,
        "root_level_count": 0,
        "no_applicable_count": 0,
        "queued_fomod_recovery_count": 0,
        "add_collection_launch_count": 0,
        "add_collection_recovery_count": 0,
        "download_metadata_review_count": 0,
        "resolved_failed_count": 0,
        "failed_categories": failed_categories,
        "warning_categories": warning_categories,
    }
    for item in summaries:
        totals["failed_count"] += int(item.get("failed_count") or 0)
        totals["resolved_failed_count"] += int(item.get("resolved_failed_count") or 0)
        totals["review_count"] += int(item.get("review_count") or 0)
        totals["warning_count"] += int(item.get("warning_count") or 0)
        totals["unique_warning_count"] += int(item.get("unique_warning_count") or 0)
        totals["actionable_review_count"] += int(
            item.get("actionable_review_count") or 0
        )
        totals["informational_count"] += int(item.get("informational_count") or 0)
        totals["root_level_count"] += int(item.get("root_level_count") or 0)
        totals["no_applicable_count"] += int(item.get("no_applicable_count") or 0)
        totals["queued_fomod_recovery_count"] += int(
            item.get("queued_fomod_recovery_count") or 0
        )
        totals["add_collection_launch_count"] += int(
            item.get("add_collection_launch_count") or 0
        )
        totals["add_collection_recovery_count"] += int(
            item.get("add_collection_recovery_count") or 0
        )
        if item.get("download_metadata_review"):
            totals["download_metadata_review_count"] += 1
        for category, count in (item.get("failed_categories") or {}).items():
            failed_categories[category] = failed_categories.get(category, 0) + int(
                count or 0
            )
        for category, count in (item.get("warning_categories") or {}).items():
            warning_categories[category] = warning_categories.get(category, 0) + int(
                count or 0
            )
    return totals


def build_stress_report(
    logs_dir,
    base_path=None,
    profile_name="Default",
    all_runs=False,
    collection_filters=None,
    needs_review_only=False,
    failed_category_filters=None,
):
    report_paths = discover_collection_reports(logs_dir)
    summaries = (
        all_collection_summaries(report_paths)
        if all_runs
        else latest_collection_summaries(report_paths)
    )
    summaries = _filter_summaries(
        summaries,
        collection_filters=collection_filters,
        needs_review_only=needs_review_only,
        failed_category_filters=failed_category_filters,
    )
    if base_path is not None:
        valid_installed_keys = validInstalledDownloadKeysForProfile(base_path)
        quarantined_evidence = quarantinedDownloadEvidence(base_path)
        cached_archives = _cached_install_archives(base_path)
        summaries = [
            resolve_failed_entries_against_profile(
                summary,
                valid_installed_keys,
                base_path=base_path,
                quarantined_evidence=quarantined_evidence,
                cached_archives=cached_archives,
            )
            for summary in summaries
        ]
        if needs_review_only:
            summaries = [
                summary for summary in summaries if summary.get("status") == "needs_review"
            ]
    clean_summaries = _strip_sort_keys(summaries)
    result = {
        "logs_dir": str(Path(logs_dir)),
        "mode": "all_runs" if all_runs else "latest_per_collection_revision",
        "filters": {
            "collections": list(collection_filters or []),
            "needs_review_only": bool(needs_review_only),
            "failed_categories": list(failed_category_filters or []),
        },
        "report_count": len(clean_summaries),
        "clean_count": sum(1 for item in clean_summaries if item["status"] == "clean"),
        "needs_review_count": sum(
            1 for item in clean_summaries if item["status"] == "needs_review"
        ),
        "actionable_review_count": sum(
            1
            for item in clean_summaries
            if item.get("review_severity") == "actionable"
        ),
        "informational_review_count": sum(
            1
            for item in clean_summaries
            if item.get("review_severity") == "informational"
        ),
        "totals": summarize_stress_totals(clean_summaries),
        "collections": clean_summaries,
    }
    if base_path is not None:
        profile_audit = auditMo2ProfileState(
            base_path=Path(base_path),
            profile_name=profile_name,
        )
        profile_issues = profile_audit.get("issues", [])
        profile_warnings = profile_audit.get("warnings", [])
        result["profile_audit"] = {
            "clean": profile_audit.get("clean"),
            "issues": profile_issues,
            "warnings": profile_warnings,
            "plugin_capacity_audit": profile_audit.get("plugin_capacity_audit"),
            "download_metadata_audit": profile_audit.get("download_metadata_audit"),
        }
        result["current_profile_gate"] = {
            "clean": bool(profile_audit.get("clean")),
            "issue_count": len(profile_issues),
            "warning_count": len(profile_warnings),
            "historical_needs_review_count": result["needs_review_count"],
            "historical_actionable_review_count": result[
                "actionable_review_count"
            ],
            "historical_informational_review_count": result[
                "informational_review_count"
            ],
            "historical_review_only": bool(
                profile_audit.get("clean") and result["needs_review_count"]
            ),
        }
    return result


def print_text_report(report):
    print(f"Logs: {report['logs_dir']}")
    print(f"Mode: {report['mode']}")
    print(
        "Collections: "
        f"{report['report_count']} total, "
        f"{report['clean_count']} clean, "
        f"{report['needs_review_count']} needing review "
        f"({report.get('actionable_review_count', 0)} actionable, "
        f"{report.get('informational_review_count', 0)} informational)"
    )
    profile = report.get("profile_audit")
    if profile is not None:
        print(f"Profile audit: {'clean' if profile.get('clean') else 'needs review'}")
    current_gate = report.get("current_profile_gate")
    if current_gate is not None:
        print(
            "Current profile gate: "
            f"{'clean' if current_gate.get('clean') else 'needs review'}"
        )
        for issue in (profile or {}).get("issues") or []:
            issue_type = issue.get("type", "unknown")
            details = []
            if issue.get("count") is not None:
                details.append(f"{issue['count']} item(s)")
            examples = issue.get("examples") or []
            if examples:
                details.append(f"examples: {', '.join(str(item) for item in examples[:3])}")
            message = issue.get("message")
            suffix = f": {'; '.join(details)}" if details else ""
            print(f"  Profile issue - {issue_type}{suffix}")
            if message:
                print(f"    {message}")
        for warning in (profile or {}).get("warnings") or []:
            warning_type = warning.get("type", "unknown")
            details = []
            if warning.get("count") is not None:
                details.append(f"{warning['count']} item(s)")
            message = warning.get("message")
            suffix = f": {'; '.join(details)}" if details else ""
            print(f"  Profile warning - {warning_type}{suffix}")
            if message:
                print(f"    {message}")
        if current_gate.get("historical_review_only"):
            print(
                "Historical review rows remain, but the current profile audit is clean."
            )
    totals = report.get("totals") or {}
    if totals:
        print(
            "Review totals: "
            f"{totals.get('failed_count', 0)} failed, "
            f"{totals.get('resolved_failed_count', 0)} resolved historical, "
            f"{totals.get('warning_count', 0)} warning(s), "
            f"{totals.get('add_collection_recovery_count', 0)} recovery launch(es)"
        )
    for item in report["collections"]:
        label = f"{item['collection']} r{item['revision']}"
        name = item.get("name")
        if name:
            label += f" - {name}"
        print(f"- {label}: {item['status']}")
        details = []
        if item.get("review_severity") and item.get("review_severity") != "clean":
            details.append(f"{item['review_severity']} review")
        if item["failed_count"]:
            details.append(f"{item['failed_count']} failed")
        if item.get("resolved_failed_count"):
            details.append(f"{item['resolved_failed_count']} resolved historical")
        if item["warning_count"]:
            warning_categories = item.get("warning_categories") or {}
            if warning_categories:
                category_text = ", ".join(
                    f"{category}={count}"
                    for category, count in sorted(warning_categories.items())
                )
                details.append(f"{item['warning_count']} warning(s): {category_text}")
            else:
                details.append(f"{item['warning_count']} warning(s)")
        if item.get("no_applicable_count"):
            details.append(f"{item['no_applicable_count']} no-applicable note(s)")
        if item.get("root_level_count"):
            details.append(f"{item['root_level_count']} root-level note(s)")
        if item["add_collection_recovery_count"]:
            details.append(f"{item['add_collection_recovery_count']} recovery launch(es)")
        if item["download_metadata_review"]:
            details.append("download metadata review")
        if details:
            print(f"  {', '.join(details)}")


def stress_report_is_clean(report):
    if int(report.get("needs_review_count") or 0):
        return False
    profile = report.get("profile_audit")
    if profile is not None and not profile.get("clean"):
        return False
    return True


def current_profile_gate_is_clean(report):
    gate = report.get("current_profile_gate")
    if gate is not None:
        return bool(gate.get("clean"))
    profile = report.get("profile_audit")
    if profile is not None:
        return bool(profile.get("clean"))
    return stress_report_is_clean(report)


def write_json_report(report, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--logs",
        type=Path,
        default=None,
        help=(
            "MO2 logs directory. Defaults to <base>/logs when --base or "
            "NXM_COLLECTION_DL_MO2_BASE is available."
        ),
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
    parser.add_argument("--profile", default="Default")
    parser.add_argument("--all-runs", action="store_true")
    parser.add_argument(
        "--collection",
        action="append",
        default=None,
        help=(
            "Only include reports whose collection slug or display name exactly "
            "matches this value. May be supplied more than once."
        ),
    )
    parser.add_argument(
        "--needs-review-only",
        action="store_true",
        help="Only include collection summaries that currently need review.",
    )
    parser.add_argument(
        "--failed-category",
        action="append",
        default=None,
        help=(
            "Only include failed entries whose review_category exactly matches "
            "this value. May be supplied more than once."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit non-zero when any collection report needs review or the "
            "included profile audit is not clean."
        ),
    )
    parser.add_argument(
        "--strict-profile",
        action="store_true",
        help=(
            "Exit non-zero only when the current profile audit is not clean. "
            "Historical collection review rows remain visible in the report."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the complete stress report JSON to this path.",
    )
    args = parser.parse_args()

    base = args.base
    if base is None:
        env_base = os.environ.get("NXM_COLLECTION_DL_MO2_BASE")
        if env_base:
            base = Path(env_base)

    logs = args.logs
    if logs is None:
        if base is None:
            parser.error("provide --logs or --base/NXM_COLLECTION_DL_MO2_BASE")
        logs = base / "logs"

    report = build_stress_report(
        logs,
        base_path=base,
        profile_name=args.profile,
        all_runs=args.all_runs,
        collection_filters=args.collection,
        needs_review_only=args.needs_review_only,
        failed_category_filters=args.failed_category,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text_report(report)
    if args.output is not None:
        output_path = write_json_report(report, args.output)
        print(f"Wrote JSON report: {output_path}")
    if args.strict_profile and not current_profile_gate_is_clean(report):
        return 1
    if args.strict and not stress_report_is_clean(report):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

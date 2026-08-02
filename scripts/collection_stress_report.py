#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_helpers import (
    auditMo2ProfileState,
    failedInstallReviewCategoryCounts,
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
    review_count = len(review_entries)
    download_metadata_review = _download_metadata_needs_review(download_metadata_audit)
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
    return {
        "collection": report.get("collection"),
        "revision": report.get("revision"),
        "name": report.get("name"),
        "generated": report.get("generated"),
        "report": str(report_path),
        "status": "needs_review" if needs_review else "clean",
        "warning_count": warning_count,
        "unique_warning_count": unique_warning_count,
        "warning_categories": {
            str(item.get("category")): int(item.get("occurrences") or 0)
            for item in warning_summary
            if item.get("category")
        },
        "failed_count": failed_count,
        "review_count": review_count,
        "failed_categories": failedInstallReviewCategoryCounts(failed_entries),
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


def build_stress_report(logs_dir, base_path=None, profile_name="Default", all_runs=False):
    report_paths = discover_collection_reports(logs_dir)
    summaries = (
        all_collection_summaries(report_paths)
        if all_runs
        else latest_collection_summaries(report_paths)
    )
    clean_summaries = _strip_sort_keys(summaries)
    result = {
        "logs_dir": str(Path(logs_dir)),
        "mode": "all_runs" if all_runs else "latest_per_collection_revision",
        "report_count": len(clean_summaries),
        "clean_count": sum(1 for item in clean_summaries if item["status"] == "clean"),
        "needs_review_count": sum(
            1 for item in clean_summaries if item["status"] == "needs_review"
        ),
        "collections": clean_summaries,
    }
    if base_path is not None:
        profile_audit = auditMo2ProfileState(
            base_path=Path(base_path),
            profile_name=profile_name,
        )
        result["profile_audit"] = {
            "clean": profile_audit.get("clean"),
            "issues": profile_audit.get("issues", []),
            "warnings": profile_audit.get("warnings", []),
            "plugin_capacity_audit": profile_audit.get("plugin_capacity_audit"),
            "download_metadata_audit": profile_audit.get("download_metadata_audit"),
        }
    return result


def print_text_report(report):
    print(f"Logs: {report['logs_dir']}")
    print(f"Mode: {report['mode']}")
    print(
        "Collections: "
        f"{report['report_count']} total, "
        f"{report['clean_count']} clean, "
        f"{report['needs_review_count']} needing review"
    )
    profile = report.get("profile_audit")
    if profile is not None:
        print(f"Profile audit: {'clean' if profile.get('clean') else 'needs review'}")
    for item in report["collections"]:
        label = f"{item['collection']} r{item['revision']}"
        name = item.get("name")
        if name:
            label += f" - {name}"
        print(f"- {label}: {item['status']}")
        details = []
        if item["failed_count"]:
            details.append(f"{item['failed_count']} failed")
        if item["warning_count"]:
            details.append(f"{item['warning_count']} warning(s)")
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
        "--strict",
        action="store_true",
        help=(
            "Exit non-zero when any collection report needs review or the "
            "included profile audit is not clean."
        ),
    )
    parser.add_argument("--json", action="store_true")
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
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text_report(report)
    if args.strict and not stress_report_is_clean(report):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

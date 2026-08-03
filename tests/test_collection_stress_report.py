from importlib.util import module_from_spec, spec_from_file_location
import contextlib
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collection_stress_report.py"
SPEC = spec_from_file_location("collection_stress_report", SCRIPT)
collection_stress_report = module_from_spec(SPEC)
SPEC.loader.exec_module(collection_stress_report)


def write_warning_report(logs, name, payload):
    path = logs / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class CollectionStressReportTests(unittest.TestCase):
    def test_summarizes_latest_report_per_collection_revision(self):
        with TemporaryDirectory() as tmp:
            logs = Path(tmp)
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-alpha-1-20260802-010000.json",
                {
                    "collection": "alpha",
                    "revision": 1,
                    "name": "Alpha",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 1,
                    "unique_warning_count": 1,
                    "warning_summary": [
                        {"category": "plugin_state_missing", "occurrences": 1}
                    ],
                    "failed_entries": [],
                    "add_collection_recovery_count": 0,
                },
            )
            latest = write_warning_report(
                logs,
                "nxm-collection-install-warnings-alpha-1-20260802-020000.json",
                {
                    "collection": "alpha",
                    "revision": 1,
                    "name": "Alpha",
                    "generated": "2026-08-02T02:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [],
                    "add_collection_recovery_count": 1,
                },
            )

            result = collection_stress_report.build_stress_report(logs)

            self.assertEqual(result["report_count"], 1)
            self.assertEqual(result["clean_count"], 1)
            self.assertEqual(result["collections"][0]["report"], str(latest))
            self.assertEqual(result["collections"][0]["status"], "clean")
            self.assertEqual(result["collections"][0]["review_severity"], "clean")
            self.assertEqual(result["actionable_review_count"], 0)
            self.assertEqual(result["informational_review_count"], 0)
            self.assertEqual(result["collections"][0]["add_collection_recovery_count"], 1)

    def test_classifies_failed_entries_and_download_metadata_review(self):
        with TemporaryDirectory() as tmp:
            logs = Path(tmp)
            metadata = logs / "Downloaded-1-2.7z.meta"
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-beta-3-20260802-010000.json",
                {
                    "collection": "beta",
                    "revision": 3,
                    "name": "Beta",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [
                        {"mod": "Missing", "reason": "not found in downloads"},
                        {"mod": "Manual", "reason": "ambiguous archive layout"},
                        {"mod": "Other", "reason": "unexpected failure"},
                    ],
                    "download_metadata_audit": {
                        "downloaded_only": [str(metadata)],
                        "missing_archive": [],
                        "unknown_installed_state": [],
                        "installed_without_valid_container": [],
                    },
                },
            )

            result = collection_stress_report.build_stress_report(logs)
            summary = result["collections"][0]

            self.assertEqual(result["needs_review_count"], 1)
            self.assertEqual(result["actionable_review_count"], 1)
            self.assertEqual(result["informational_review_count"], 0)
            self.assertEqual(summary["status"], "needs_review")
            self.assertEqual(summary["review_severity"], "actionable")
            self.assertEqual(summary["actionable_review_count"], 4)
            self.assertEqual(summary["informational_count"], 0)
            self.assertEqual(summary["failed_count"], 3)
            self.assertEqual(
                summary["failed_categories"],
                {
                    "missing_download": 1,
                    "duplicate_container": 0,
                    "native_worker_timeout": 0,
                    "ambiguous_archive_layout": 1,
                    "fomod_choices": 0,
                    "empty_installer_output": 0,
                    "manual_or_review": 0,
                    "other_failure": 1,
                },
            )
            self.assertEqual(
                summary["failed_entries"],
                [
                    {
                        "mod": "Missing",
                        "reason": "not found in downloads",
                        "review_category": "missing_download",
                        "recommended_action": (
                            "Download the missing archive, then rerun Add Collection "
                            "or retry manually."
                        ),
                    },
                    {
                        "mod": "Manual",
                        "reason": "ambiguous archive layout",
                        "review_category": "ambiguous_archive_layout",
                        "recommended_action": (
                            "Review the archive content tree and choose the intended "
                            "game-data root."
                        ),
                    },
                    {
                        "mod": "Other",
                        "reason": "unexpected failure",
                        "review_category": "other_failure",
                        "recommended_action": (
                            "Inspect the warning report and plugin log before "
                            "retrying this entry."
                        ),
                    },
                ],
            )
            self.assertTrue(summary["download_metadata_review"])

    def test_resolves_historical_failed_entries_against_current_profile(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            downloads = root / "downloads"
            logs.mkdir()
            downloads.mkdir()
            metadata = downloads / "Resolved-1-2.7z.meta"
            metadata.write_text(
                "[General]\nmodID=1\nfileID=2\ninstalled=true\n",
                encoding="utf-8",
            )
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-resolved-1-20260802-010000.json",
                {
                    "collection": "resolved",
                    "revision": 1,
                    "name": "Resolved",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [
                        {
                            "mod": "Recovered",
                            "file": "Recovered",
                            "archive": (
                                "C:\\users\\steamuser\\AppData\\Local\\"
                                "ModOrganizer\\Skyrim Special Edition - Derp\\"
                                "downloads\\Resolved-1-2.7z"
                            ),
                            "reason": "duplicate MO2 mod container",
                        }
                    ],
                },
            )

            with mock.patch.object(
                collection_stress_report,
                "validInstalledDownloadKeysForProfile",
                return_value={(1, 2)},
            ), mock.patch.object(
                collection_stress_report,
                "auditMo2ProfileState",
                return_value={"clean": True, "issues": [], "warnings": []},
            ):
                result = collection_stress_report.build_stress_report(
                    logs,
                    base_path=root,
                )

            summary = result["collections"][0]
            self.assertEqual(result["report_count"], 1)
            self.assertEqual(result["clean_count"], 1)
            self.assertEqual(result["needs_review_count"], 0)
            self.assertEqual(result["totals"]["failed_count"], 0)
            self.assertEqual(result["totals"]["resolved_failed_count"], 1)
            self.assertEqual(summary["status"], "clean")
            self.assertEqual(summary["review_severity"], "clean")
            self.assertEqual(summary["failed_count"], 0)
            self.assertEqual(summary["actionable_review_count"], 0)
            self.assertEqual(summary["resolved_failed_count"], 1)
            self.assertEqual(
                summary["resolved_failed_entries"][0]["historical_status"],
                "resolved",
            )
            self.assertTrue(
                summary["resolved_failed_entries"][0]["resolved_by_current_profile"]
            )

    def test_needs_review_filter_excludes_profile_resolved_failures(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            downloads = root / "downloads"
            logs.mkdir()
            downloads.mkdir()
            (downloads / "Resolved-1-2.7z.meta").write_text(
                "[General]\nmodID=1\nfileID=2\ninstalled=true\n",
                encoding="utf-8",
            )
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-resolved-1-20260802-010000.json",
                {
                    "collection": "resolved",
                    "revision": 1,
                    "name": "Resolved",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [
                        {
                            "mod": "Recovered",
                            "archive": "C:\\downloads\\Resolved-1-2.7z",
                            "reason": "duplicate MO2 mod container",
                        }
                    ],
                },
            )

            with mock.patch.object(
                collection_stress_report,
                "validInstalledDownloadKeysForProfile",
                return_value={(1, 2)},
            ), mock.patch.object(
                collection_stress_report,
                "auditMo2ProfileState",
                return_value={"clean": True, "issues": [], "warnings": []},
            ):
                result = collection_stress_report.build_stress_report(
                    logs,
                    base_path=root,
                    needs_review_only=True,
                )

            self.assertEqual(result["report_count"], 0)
            self.assertEqual(result["needs_review_count"], 0)
            self.assertEqual(result["totals"]["failed_count"], 0)
            self.assertEqual(result["totals"]["resolved_failed_count"], 0)

    def test_resolves_historical_failed_entries_from_nexus_ids_without_archive(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-resolved-1-20260802-010000.json",
                {
                    "collection": "resolved",
                    "revision": 1,
                    "name": "Resolved",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [
                        {
                            "mod": "Recovered",
                            "file": "Recovered",
                            "mod_id": 11,
                            "file_id": 22,
                            "reason": "Native archive worker timed out",
                        }
                    ],
                },
            )

            with mock.patch.object(
                collection_stress_report,
                "validInstalledDownloadKeysForProfile",
                return_value={(11, 22)},
            ), mock.patch.object(
                collection_stress_report,
                "auditMo2ProfileState",
                return_value={"clean": True, "issues": [], "warnings": []},
            ):
                result = collection_stress_report.build_stress_report(
                    logs,
                    base_path=root,
                )

            summary = result["collections"][0]
            self.assertEqual(result["needs_review_count"], 0)
            self.assertEqual(result["totals"]["failed_count"], 0)
            self.assertEqual(result["totals"]["resolved_failed_count"], 1)
            self.assertEqual(summary["failed_count"], 0)
            self.assertEqual(summary["resolved_failed_count"], 1)

    def test_keeps_unresolved_historical_failures_actionable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            downloads = root / "downloads"
            logs.mkdir()
            downloads.mkdir()
            (downloads / "Unresolved-1-2.7z.meta").write_text(
                "[General]\nmodID=1\nfileID=2\ninstalled=false\n",
                encoding="utf-8",
            )
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-unresolved-1-20260802-010000.json",
                {
                    "collection": "unresolved",
                    "revision": 1,
                    "name": "Unresolved",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [
                        {
                            "mod": "Still Failed",
                            "archive": "C:\\downloads\\Unresolved-1-2.7z",
                            "reason": "duplicate MO2 mod container",
                        }
                    ],
                },
            )

            with mock.patch.object(
                collection_stress_report,
                "validInstalledDownloadKeysForProfile",
                return_value={(1, 2)},
            ), mock.patch.object(
                collection_stress_report,
                "auditMo2ProfileState",
                return_value={"clean": True, "issues": [], "warnings": []},
            ):
                result = collection_stress_report.build_stress_report(
                    logs,
                    base_path=root,
                )

            summary = result["collections"][0]
            self.assertEqual(result["needs_review_count"], 1)
            self.assertEqual(result["totals"]["failed_count"], 1)
            self.assertEqual(result["totals"]["resolved_failed_count"], 0)
            self.assertEqual(summary["failed_count"], 1)
            self.assertEqual(summary["resolved_failed_count"], 0)

    def test_can_include_profile_audit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            profile = root / "profiles" / "Default"
            logs.mkdir()
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text("+A\n", encoding="utf-8")
            (profile / "plugins.txt").write_text("*A.esp\n", encoding="utf-8")
            (profile / "loadorder.txt").write_text("A.esp\n", encoding="utf-8")
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-gamma-1-20260802-010000.json",
                {
                    "collection": "gamma",
                    "revision": 1,
                    "name": "Gamma",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [],
                },
            )

            result = collection_stress_report.build_stress_report(
                logs,
                base_path=root,
            )

            self.assertTrue(result["profile_audit"]["clean"])
            self.assertEqual(result["profile_audit"]["issues"], [])
            self.assertEqual(
                result["current_profile_gate"],
                {
                    "clean": True,
                    "issue_count": 0,
                    "warning_count": 0,
                    "historical_needs_review_count": 0,
                    "historical_actionable_review_count": 0,
                    "historical_informational_review_count": 0,
                    "historical_review_only": False,
                },
            )

    def test_no_applicable_only_report_is_informational_review(self):
        with TemporaryDirectory() as tmp:
            logs = Path(tmp)
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-info-1-20260802-010000.json",
                {
                    "collection": "info",
                    "revision": 1,
                    "name": "Info",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [],
                    "no_applicable_entries": [{"mod": "Optional Patch"}],
                },
            )

            result = collection_stress_report.build_stress_report(logs)
            summary = result["collections"][0]

            self.assertEqual(result["needs_review_count"], 1)
            self.assertEqual(result["actionable_review_count"], 0)
            self.assertEqual(result["informational_review_count"], 1)
            self.assertEqual(summary["status"], "needs_review")
            self.assertEqual(summary["review_severity"], "informational")
            self.assertEqual(summary["actionable_review_count"], 0)
            self.assertEqual(summary["informational_count"], 1)

    def test_filters_reports_by_collection_slug_or_name(self):
        with TemporaryDirectory() as tmp:
            logs = Path(tmp)
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-alpha-1-20260802-010000.json",
                {
                    "collection": "alpha",
                    "revision": 1,
                    "name": "Alpha Pack",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [],
                },
            )
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-beta-1-20260802-010000.json",
                {
                    "collection": "beta",
                    "revision": 1,
                    "name": "Beta Pack",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [],
                },
            )

            by_slug = collection_stress_report.build_stress_report(
                logs,
                collection_filters=["alpha"],
            )
            by_name = collection_stress_report.build_stress_report(
                logs,
                collection_filters=["Beta Pack"],
            )

            self.assertEqual(by_slug["report_count"], 1)
            self.assertEqual(by_slug["collections"][0]["collection"], "alpha")
            self.assertEqual(by_slug["filters"]["collections"], ["alpha"])
            self.assertEqual(by_name["report_count"], 1)
            self.assertEqual(by_name["collections"][0]["collection"], "beta")

    def test_filters_reports_to_needs_review_only(self):
        with TemporaryDirectory() as tmp:
            logs = Path(tmp)
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-clean-1-20260802-010000.json",
                {
                    "collection": "clean",
                    "revision": 1,
                    "name": "Clean",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [],
                },
            )
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-dirty-1-20260802-010000.json",
                {
                    "collection": "dirty",
                    "revision": 1,
                    "name": "Dirty",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [
                        {"mod": "Manual", "reason": "ambiguous archive layout"}
                    ],
                },
            )

            result = collection_stress_report.build_stress_report(
                logs,
                needs_review_only=True,
            )

            self.assertEqual(result["report_count"], 1)
            self.assertEqual(result["clean_count"], 0)
            self.assertEqual(result["needs_review_count"], 1)
            self.assertTrue(result["filters"]["needs_review_only"])
            self.assertEqual(result["collections"][0]["collection"], "dirty")

    def test_filters_failed_entries_by_review_category(self):
        with TemporaryDirectory() as tmp:
            logs = Path(tmp)
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-alpha-1-20260802-010000.json",
                {
                    "collection": "alpha",
                    "revision": 1,
                    "name": "Alpha",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [
                        {"mod": "Ambiguous", "reason": "ambiguous archive layout"},
                        {
                            "mod": "Duplicate",
                            "reason": "duplicate MO2 mod container",
                        },
                    ],
                },
            )
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-beta-1-20260802-010000.json",
                {
                    "collection": "beta",
                    "revision": 1,
                    "name": "Beta",
                    "generated": "2026-08-02T02:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [
                        {"mod": "Empty", "reason": "empty mod container"},
                    ],
                },
            )

            result = collection_stress_report.build_stress_report(
                logs,
                failed_category_filters=["duplicate_container"],
            )

            self.assertEqual(result["report_count"], 1)
            self.assertEqual(result["needs_review_count"], 1)
            self.assertEqual(result["actionable_review_count"], 1)
            self.assertEqual(
                result["filters"]["failed_categories"],
                ["duplicate_container"],
            )
            summary = result["collections"][0]
            self.assertEqual(summary["collection"], "alpha")
            self.assertEqual(summary["failed_count"], 1)
            self.assertEqual(
                summary["failed_entries"],
                [
                    {
                        "mod": "Duplicate",
                        "reason": "duplicate MO2 mod container",
                        "review_category": "duplicate_container",
                        "recommended_action": (
                            "Resolve the duplicate MO2 mod container with an "
                            "explicit rename, merge, or replace choice."
                        ),
                    }
                ],
            )
            self.assertEqual(
                result["totals"]["failed_categories"]["duplicate_container"],
                1,
            )
            self.assertEqual(result["totals"]["failed_count"], 1)

    def test_summarizes_report_totals(self):
        with TemporaryDirectory() as tmp:
            logs = Path(tmp)
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-alpha-1-20260802-010000.json",
                {
                    "collection": "alpha",
                    "revision": 1,
                    "name": "Alpha",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 2,
                    "unique_warning_count": 1,
                    "warning_summary": [
                        {"category": "plugin_state_missing", "occurrences": 2}
                    ],
                    "failed_entries": [
                        {"mod": "Missing", "reason": "not found in downloads"},
                        {"mod": "Manual", "reason": "ambiguous archive layout"},
                    ],
                    "root_level_entries": [{"mod": "Root"}],
                    "add_collection_recovery_count": 3,
                },
            )
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-beta-1-20260802-010000.json",
                {
                    "collection": "beta",
                    "revision": 1,
                    "name": "Beta",
                    "generated": "2026-08-02T02:00:00",
                    "warning_count": 1,
                    "unique_warning_count": 1,
                    "warning_summary": [
                        {"category": "metadata_update_failed", "occurrences": 1}
                    ],
                    "failed_entries": [
                        {"mod": "Other", "reason": "unexpected failure"},
                    ],
                    "download_metadata_audit": {
                        "downloaded_only": ["Downloaded.7z.meta"],
                    },
                },
            )

            result = collection_stress_report.build_stress_report(logs)
            totals = result["totals"]

            self.assertEqual(totals["failed_count"], 3)
            self.assertEqual(totals["warning_count"], 3)
            self.assertEqual(totals["unique_warning_count"], 2)
            self.assertEqual(totals["actionable_review_count"], 6)
            self.assertEqual(totals["informational_count"], 1)
            self.assertEqual(totals["root_level_count"], 1)
            self.assertEqual(totals["add_collection_recovery_count"], 3)
            self.assertEqual(totals["download_metadata_review_count"], 1)
            self.assertEqual(
                totals["failed_categories"],
                {
                    "missing_download": 1,
                    "duplicate_container": 0,
                    "native_worker_timeout": 0,
                    "ambiguous_archive_layout": 1,
                    "fomod_choices": 0,
                    "empty_installer_output": 0,
                    "manual_or_review": 0,
                    "other_failure": 1,
                },
            )
            self.assertEqual(
                totals["warning_categories"],
                {
                    "plugin_state_missing": 2,
                    "metadata_update_failed": 1,
                },
            )

    def test_strict_gate_accepts_clean_report(self):
        self.assertTrue(
            collection_stress_report.stress_report_is_clean(
                {
                    "needs_review_count": 0,
                    "profile_audit": {"clean": True},
                }
            )
        )

    def test_strict_gate_rejects_needs_review_report(self):
        self.assertFalse(
            collection_stress_report.stress_report_is_clean(
                {
                    "needs_review_count": 1,
                    "profile_audit": {"clean": True},
                }
            )
        )

    def test_strict_gate_rejects_dirty_profile_audit(self):
        self.assertFalse(
            collection_stress_report.stress_report_is_clean(
                {
                    "needs_review_count": 0,
                    "profile_audit": {"clean": False},
                }
            )
        )

    def test_current_profile_gate_allows_historical_review_when_profile_clean(self):
        self.assertTrue(
            collection_stress_report.current_profile_gate_is_clean(
                {
                    "needs_review_count": 1,
                    "profile_audit": {"clean": True},
                    "current_profile_gate": {
                        "clean": True,
                        "historical_review_only": True,
                    },
                }
            )
        )

    def test_current_profile_gate_rejects_dirty_profile(self):
        self.assertFalse(
            collection_stress_report.current_profile_gate_is_clean(
                {
                    "needs_review_count": 0,
                    "profile_audit": {"clean": False},
                    "current_profile_gate": {"clean": False},
                }
            )
        )

    def test_writes_json_report_artifact(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "nested" / "stress.json"

            written = collection_stress_report.write_json_report(
                {"needs_review_count": 0, "collections": []},
                output,
            )

            self.assertEqual(written, output)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {"needs_review_count": 0, "collections": []},
            )

    def test_main_can_write_output_artifact(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            output = root / "artifacts" / "stress.json"
            write_warning_report(
                logs,
                "nxm-collection-install-warnings-gamma-1-20260802-010000.json",
                {
                    "collection": "gamma",
                    "revision": 1,
                    "name": "Gamma",
                    "generated": "2026-08-02T01:00:00",
                    "warning_count": 0,
                    "unique_warning_count": 0,
                    "warning_summary": [],
                    "failed_entries": [],
                },
            )

            old_argv = sys.argv
            try:
                sys.argv = [
                    str(SCRIPT),
                    "--logs",
                    str(logs),
                    "--output",
                    str(output),
                ]
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(collection_stress_report.main(), 0)
            finally:
                sys.argv = old_argv

            artifact = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(artifact["report_count"], 1)
            self.assertEqual(artifact["collections"][0]["collection"], "gamma")


if __name__ == "__main__":
    unittest.main()

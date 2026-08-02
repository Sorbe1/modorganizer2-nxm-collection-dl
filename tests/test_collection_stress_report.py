from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


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
            self.assertEqual(summary["status"], "needs_review")
            self.assertEqual(summary["failed_count"], 3)
            self.assertEqual(
                summary["failed_categories"],
                {
                    "missing_download": 1,
                    "manual_or_review": 1,
                    "other_failure": 1,
                },
            )
            self.assertTrue(summary["download_metadata_review"])

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


if __name__ == "__main__":
    unittest.main()

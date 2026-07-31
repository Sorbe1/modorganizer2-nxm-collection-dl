from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "snapshot_profile_state.py"
SPEC = spec_from_file_location("snapshot_profile_state", SCRIPT)
snapshot_profile_state = module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot_profile_state)


class SnapshotProfileStateTests(unittest.TestCase):
    def test_safe_label_removes_path_and_shell_unfriendly_text(self):
        self.assertEqual(snapshot_profile_state.safe_label(" known good / v1 "), "known-good-v1")
        self.assertEqual(snapshot_profile_state.safe_label(""), "snapshot")

    def test_snapshot_copies_profile_order_files_and_writes_manifest(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text(
                "+Enabled Mod\n-Disabled Mod\n", encoding="utf-8"
            )
            (profile / "plugins.txt").write_text(
                "*Enabled.esp\nDisabled.esp\n", encoding="utf-8"
            )
            (profile / "loadorder.txt").write_text(
                "Skyrim.esm\nExample.esp\n", encoding="utf-8"
            )

            snapshot_dir, manifest = snapshot_profile_state.snapshot_profile_state(
                base, label="known good"
            )

            self.assertTrue((snapshot_dir / "modlist.txt").exists())
            self.assertTrue((snapshot_dir / "plugins.txt").exists())
            self.assertTrue((snapshot_dir / "loadorder.txt").exists())
            saved_manifest = json.loads(
                (snapshot_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_manifest["label"], "known good")
            self.assertEqual(manifest["files"]["modlist.txt"]["enabled"], 1)
            self.assertEqual(manifest["files"]["modlist.txt"]["disabled"], 1)
            self.assertEqual(manifest["files"]["plugins.txt"]["enabled"], 1)
            self.assertEqual(manifest["files"]["loadorder.txt"]["lines"], 2)

    def test_missing_profile_fails_without_creating_snapshot(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)

            with self.assertRaises(FileNotFoundError):
                snapshot_profile_state.snapshot_profile_state(base)

            self.assertFalse((base / "profile-snapshots").exists())


if __name__ == "__main__":
    unittest.main()

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reset_collection_state.py"
SPEC = spec_from_file_location("reset_collection_state", SCRIPT)
reset_collection_state = module_from_spec(SPEC)
SPEC.loader.exec_module(reset_collection_state)


class ResetCollectionStateTests(unittest.TestCase):
    def test_main_requires_base_argument_or_environment(self):
        with mock.patch("sys.argv", ["reset_collection_state.py", "missing_1"]):
            with self.assertRaises(SystemExit) as raised:
                reset_collection_state.main()

        self.assertEqual(raised.exception.code, 2)

    def test_main_accepts_base_from_environment(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            collection_dir = base / "collections" / "skyrimspecialedition"
            collection_dir.mkdir(parents=True)
            (base / "mods").mkdir()
            (base / "downloads").mkdir()
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            collection = collection_dir / "empty_1.json"
            collection.write_text(
                '{"name":"Empty","essentialMods":[],"chosenOptional":[]}',
                encoding="utf-8",
            )

            with mock.patch("sys.argv", ["reset_collection_state.py", "empty_1"]):
                with mock.patch.dict(
                    "os.environ", {"NXM_COLLECTION_DL_MO2_BASE": str(base)}
                ):
                    reset_collection_state.main()

            backups = list((base / "reset-backups").glob("empty_1-full-reset-*"))
            self.assertEqual(len(backups), 1)

    def test_delete_paths_removes_files_and_directories_without_backup_payloads(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive.7z"
            archive.write_bytes(b"archive")
            mod_dir = root / "Installed Mod"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")

            names = reset_collection_state.delete_paths([archive, mod_dir])

            self.assertEqual(names, ["archive.7z", "Installed Mod"])
            self.assertFalse(archive.exists())
            self.assertFalse(mod_dir.exists())

    def test_matching_download_files_includes_orphan_unfinished_for_collection_mod(
        self,
    ):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            downloads = base / "downloads"
            downloads.mkdir()
            orphan = downloads / (
                "Malacath No Vanilla Snow Shader ESL-63117-1-0-0-1644059503.zip.unfinished"
            )
            orphan.write_bytes(b"partial")
            other = downloads / "Other Mod-99999-1-0.zip.unfinished"
            other.write_bytes(b"partial")

            files = reset_collection_state.matching_download_files(
                base,
                {(63117, 261882)},
                {63117},
            )

            self.assertEqual(files, [orphan])

    def test_matching_download_files_keeps_metadata_backed_unfinished_with_archive(
        self,
    ):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            downloads = base / "downloads"
            downloads.mkdir()
            archive = downloads / "Target-1111-2222.zip"
            archive.write_bytes(b"archive")
            metadata = downloads / "Target-1111-2222.zip.meta"
            metadata.write_text(
                "[General]\nmodID=1111\nfileID=2222\n", encoding="utf-8"
            )
            unfinished = downloads / "Target-1111-2222.zip.unfinished"
            unfinished.write_bytes(b"partial")

            files = reset_collection_state.matching_download_files(
                base,
                {(1111, 2222)},
                {1111},
            )

            self.assertEqual(files, [metadata, archive, unfinished])


if __name__ == "__main__":
    unittest.main()

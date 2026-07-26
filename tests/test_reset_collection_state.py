from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pr-artifacts" / "reset_collection_state.py"
SPEC = spec_from_file_location("reset_collection_state", SCRIPT)
reset_collection_state = module_from_spec(SPEC)
SPEC.loader.exec_module(reset_collection_state)


class ResetCollectionStateTests(unittest.TestCase):
    def test_matching_download_files_includes_orphan_unfinished_for_collection_mod(self):
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

    def test_matching_download_files_keeps_metadata_backed_unfinished_with_archive(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            downloads = base / "downloads"
            downloads.mkdir()
            archive = downloads / "Target-1111-2222.zip"
            archive.write_bytes(b"archive")
            metadata = downloads / "Target-1111-2222.zip.meta"
            metadata.write_text("[General]\nmodID=1111\nfileID=2222\n", encoding="utf-8")
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

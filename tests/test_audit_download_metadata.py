from importlib.util import module_from_spec, spec_from_file_location
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_download_metadata.py"
SPEC = spec_from_file_location("audit_download_metadata", SCRIPT)
audit_download_metadata = module_from_spec(SPEC)
SPEC.loader.exec_module(audit_download_metadata)


class AuditDownloadMetadataScriptTests(unittest.TestCase):
    def test_resolves_downloads_from_base_argument(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)

            downloads, resolved_base = audit_download_metadata.resolve_download_audit_paths(
                base=base
            )

            self.assertEqual(downloads, base / "downloads")
            self.assertEqual(resolved_base, base)

    def test_dirty_when_installed_metadata_lacks_valid_container(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            downloads = base / "downloads"
            (base / "mods").mkdir()
            downloads.mkdir()
            archive = downloads / "Stale-123-456.7z"
            archive.write_bytes(b"archive")
            metadata = downloads / "Stale-123-456.7z.meta"
            metadata.write_text(
                "[General]\nmodID=123\nfileID=456\ninstalled=true\n",
                encoding="utf-8",
            )

            with mock.patch(
                "sys.argv",
                ["audit_download_metadata.py", "--base", str(base), "--json"],
            ):
                with redirect_stdout(StringIO()):
                    exit_code = audit_download_metadata.main()

            self.assertEqual(exit_code, 1)

    def test_clean_installed_metadata_with_valid_container_exits_zero(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            downloads = base / "downloads"
            mods = base / "mods"
            downloads.mkdir()
            mod_dir = mods / "Example Mod"
            (mod_dir / "textures").mkdir(parents=True)
            archive = downloads / "Example-123-456.7z"
            archive.write_bytes(b"archive")
            metadata = downloads / "Example-123-456.7z.meta"
            metadata.write_text(
                "[General]\nmodID=123\nfileID=456\ninstalled=true\n",
                encoding="utf-8",
            )
            (mod_dir / "meta.ini").write_text(
                "[General]\n"
                "repository=Nexus\n"
                "modid=123\n"
                "fileid=456\n"
                f"installationFile={archive.name}\n",
                encoding="utf-8",
            )
            (mod_dir / "textures" / "example.dds").write_bytes(b"dds")

            with mock.patch(
                "sys.argv",
                ["audit_download_metadata.py", "--base", str(base), "--json"],
            ):
                with redirect_stdout(StringIO()):
                    exit_code = audit_download_metadata.main()

            self.assertEqual(exit_code, 0)

    def test_reports_unknown_state_as_dirty(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Unknown-123-456.7z"
            archive.write_bytes(b"archive")
            metadata = downloads / "Unknown-123-456.7z.meta"
            metadata.write_text(
                "[General]\nmodID=123\nfileID=456\ninstalled=maybe\n",
                encoding="utf-8",
            )

            with mock.patch(
                "sys.argv",
                ["audit_download_metadata.py", "--downloads", str(downloads)],
            ):
                with redirect_stdout(StringIO()):
                    exit_code = audit_download_metadata.main()

            self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()

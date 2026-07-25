from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from collection_helpers import (
    INSTALLER_SETTING_DEFAULTS,
    activeDownloadPromptKey,
    allocateUniqueModName,
    cleanupZeroByteUnfinishedDownloads,
    coerceBoolSetting,
    coerceDownloadId,
    coerceIntSetting,
    collectionLinkCompletionPolicy,
    collectionDownloadExpectedSizes,
    duplicateDownloadPromptActionLabel,
    downloadCompletionChoices,
    downloadCompletionPlan,
    downloadProgressFormat,
    downloadProgressState,
    downloadedFileKeys,
    hasPartialUnfinishedEntries,
    inferModIdFromDownloadName,
    installerDefaultActionLabel,
    normalizedButtonLabel,
    orphanUnfinishedDownloadEntries,
    parseCollectionAddress,
    popDownloadKey,
    removeOrphanUnfinishedDownloadsForKeys,
    removeUnfinishedEntries,
    safeDisplayText,
    sanitizeModName,
    staleOrphanUnfinishedDownloadEntries,
    staleUnfinishedEntries,
    staleZeroByteUnfinishedEntries,
    unfinishedDownloadEntries,
    zeroByteUnfinishedEntries,
)


class ParseCollectionAddressTests(unittest.TestCase):
    def test_parses_nexus_collection_url_without_revision(self):
        parsed = parseCollectionAddress(
            "https://www.nexusmods.com/games/skyrimspecialedition/collections/8vdyr1"
        )

        self.assertEqual(
            parsed,
            {
                "uri": "https://www.nexusmods.com/games/skyrimspecialedition/collections/8vdyr1",
                "game": "skyrimspecialedition",
                "collection": "8vdyr1",
                "revision": None,
            },
        )

    def test_parses_nexus_collection_url_with_revision(self):
        parsed = parseCollectionAddress(
            "https://www.nexusmods.com/games/skyrimspecialedition/collections/xxsqm4/revisions/99"
        )

        self.assertEqual(parsed["collection"], "xxsqm4")
        self.assertEqual(parsed["revision"], 99)

    def test_parses_scheme_less_nexus_collection_url(self):
        parsed = parseCollectionAddress(
            "www.nexusmods.com/games/skyrimspecialedition/collections/8vdyr1/about"
        )

        self.assertEqual(
            parsed["uri"],
            "https://www.nexusmods.com/games/skyrimspecialedition/collections/8vdyr1",
        )
        self.assertEqual(parsed["revision"], None)

    def test_parses_nxm_collection_url(self):
        parsed = parseCollectionAddress(
            "nxm://skyrimspecialedition/collections/xxsqm4/revisions/99"
        )

        self.assertEqual(
            parsed["uri"],
            "https://www.nexusmods.com/games/skyrimspecialedition/collections/xxsqm4",
        )
        self.assertEqual(parsed["game"], "skyrimspecialedition")
        self.assertEqual(parsed["collection"], "xxsqm4")
        self.assertEqual(parsed["revision"], 99)

    def test_parses_nxm_collection_url_without_revision(self):
        parsed = parseCollectionAddress("nxm://skyrimspecialedition/collections/xxsqm4")

        self.assertEqual(
            parsed["uri"],
            "https://www.nexusmods.com/games/skyrimspecialedition/collections/xxsqm4",
        )
        self.assertEqual(parsed["revision"], None)

    def test_rejects_non_collection_nxm_url(self):
        self.assertIsNone(
            parseCollectionAddress("nxm://skyrimspecialedition/mods/123/files/456")
        )


class DownloadedFileKeysTests(unittest.TestCase):
    def test_accepts_archive_matching_expected_size(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Example Mod-123-456.7z"
            archive.write_bytes(b"archive")
            (downloads / "Example Mod-123-456.7z.meta").write_text(
                "[General]\nmodID=123\nfileID=456\n", encoding="utf-8"
            )

            self.assertEqual(
                downloadedFileKeys(downloads, {(123, 456): 7}), {(123, 456)}
            )

    def test_rejects_archive_smaller_than_expected(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Example Mod-123-456.7z"
            archive.write_bytes(b"part")
            (downloads / "Example Mod-123-456.7z.meta").write_text(
                "[General]\nmodID=123\nfileID=456\n", encoding="utf-8"
            )

            self.assertEqual(downloadedFileKeys(downloads, {(123, 456): 7}), set())

    def test_rejects_archive_with_unfinished_sibling(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Example Mod-123-456.7z"
            archive.write_bytes(b"archive")
            Path(str(archive) + ".unfinished").write_bytes(b"partial")
            (downloads / "Example Mod-123-456.7z.meta").write_text(
                "[General]\nmodID=123\nfileID=456\n", encoding="utf-8"
            )

            self.assertEqual(downloadedFileKeys(downloads, {(123, 456): 7}), set())

    def test_accepts_archive_without_expected_size(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Example Mod-123-456.7z"
            archive.write_bytes(b"archive")
            (downloads / "Example Mod-123-456.7z.meta").write_text(
                "[General]\nmodID=123\nfileID=456\n", encoding="utf-8"
            )

            self.assertEqual(downloadedFileKeys(downloads, {}), {(123, 456)})

    def test_returns_only_completed_archives_with_valid_metadata(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Example Mod-123-456.7z"
            archive.write_bytes(b"archive")
            (downloads / "Example Mod-123-456.7z.meta").write_text(
                "[General]\nmodID=123\nfileID=456\n", encoding="utf-8"
            )

            unfinished = downloads / "Partial Mod-111-222.7z.unfinished"
            unfinished.write_bytes(b"partial")
            (downloads / "Partial Mod-111-222.7z.unfinished.meta").write_text(
                "[General]\nmodID=111\nfileID=222\n", encoding="utf-8"
            )

            missing_archive = downloads / "Missing Archive-333-444.7z.meta"
            missing_archive.write_text(
                "[General]\nmodID=333\nfileID=444\n", encoding="utf-8"
            )

            self.assertEqual(downloadedFileKeys(downloads), {(123, 456)})


class CollectionDownloadExpectedSizesTests(unittest.TestCase):
    def test_extracts_expected_download_sizes(self):
        mods = [
            {
                "file": {
                    "fileId": "456",
                    "sizeInBytes": "7",
                    "mod": {"modId": "123"},
                }
            }
        ]

        self.assertEqual(collectionDownloadExpectedSizes(mods), {(123, 456): 7})

    def test_ignores_missing_or_invalid_sizes(self):
        mods = [
            {
                "file": {
                    "fileId": "456",
                    "sizeInBytes": "0",
                    "mod": {"modId": "123"},
                }
            },
            {
                "file": {
                    "fileId": "bad",
                    "sizeInBytes": "7",
                    "mod": {"modId": "123"},
                }
            },
        ]

        self.assertEqual(collectionDownloadExpectedSizes(mods), {})


class UnfinishedDownloadEntriesTests(unittest.TestCase):
    def test_returns_unfinished_archive_entries_by_nexus_key(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Partial Mod-111-222.7z.unfinished"
            archive.write_bytes(b"")
            metadata = downloads / "Partial Mod-111-222.7z.unfinished.meta"
            metadata.write_text("[General]\nmodID=111\nfileID=222\n", encoding="utf-8")

            entries = unfinishedDownloadEntries(downloads)

            self.assertEqual(set(entries), {(111, 222)})
            self.assertEqual(entries[(111, 222)][0]["archive"], archive)
            self.assertEqual(entries[(111, 222)][0]["metadata"], metadata)
            self.assertEqual(entries[(111, 222)][0]["archive_size"], 0)

    def test_ignores_unfinished_metadata_without_nexus_ids(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            (downloads / "Broken.7z.unfinished").write_bytes(b"")
            (downloads / "Broken.7z.unfinished.meta").write_text(
                "[General]\nmodID=bad\n", encoding="utf-8"
            )

            self.assertEqual(unfinishedDownloadEntries(downloads), {})

    def test_infers_mod_id_from_orphan_unfinished_names(self):
        self.assertEqual(
            inferModIdFromDownloadName(
                "Trade Routes-12358-3-0-beta-3-1612588897.7z.unfinished"
            ),
            12358,
        )
        self.assertEqual(
            inferModIdFromDownloadName(
                "2-4k. New Farrer Statue-68861-1-0-0-1654437722.zip.unfinished"
            ),
            68861,
        )
        self.assertIsNone(inferModIdFromDownloadName("not-a-nexus-name.7z"))

    def test_returns_orphan_unfinished_entries_without_metadata(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            orphan = downloads / (
                "Trade Routes-12358-3-0-beta-3-1612588897.7z.unfinished"
            )
            orphan.write_bytes(b"")
            backed = downloads / "Backed-1111-2222.7z.unfinished"
            backed.write_bytes(b"")
            (downloads / "Backed-1111-2222.7z.unfinished.meta").write_text(
                "[General]\nmodID=1111\nfileID=2222\n", encoding="utf-8"
            )

            entries = orphanUnfinishedDownloadEntries(downloads)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["archive"], orphan)
            self.assertEqual(entries[0]["archive_size"], 0)
            self.assertEqual(entries[0]["mod_id"], 12358)

    def test_identifies_stale_orphan_unfinished_entries(self):
        entries = [
            {
                "archive": Path("x.unfinished"),
                "archive_size": 0,
                "mtime": 100,
                "mod_id": 12358,
            }
        ]

        self.assertEqual(
            staleOrphanUnfinishedDownloadEntries(entries, 200, 60), entries
        )
        self.assertEqual(staleOrphanUnfinishedDownloadEntries(entries, 120, 60), [])

    def test_removes_matching_zero_byte_orphan_unfinished_downloads(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            target = downloads / (
                "Trade Routes-12358-3-0-beta-3-1612588897.7z.unfinished"
            )
            target.write_bytes(b"")
            non_empty = downloads / (
                "Trade Routes-12358-3-0-beta-3-1612588897-copy.7z.unfinished"
            )
            non_empty.write_bytes(b"partial")
            other = downloads / "Other-99999-1-0.7z.unfinished"
            other.write_bytes(b"")
            backed = downloads / "Backed-12358-777.7z.unfinished"
            backed.write_bytes(b"")
            (downloads / "Backed-12358-777.7z.unfinished.meta").write_text(
                "[General]\nmodID=12358\nfileID=777\n", encoding="utf-8"
            )

            removed = removeOrphanUnfinishedDownloadsForKeys(downloads, {(12358, 1)})

            self.assertEqual(removed, 1)
            self.assertFalse(target.exists())
            self.assertTrue(non_empty.exists())
            self.assertTrue(other.exists())
            self.assertTrue(backed.exists())

    def test_identifies_stale_zero_byte_unfinished_entries(self):
        entries = [
            {
                "archive": Path("Partial.7z.unfinished"),
                "metadata": Path("Partial.7z.unfinished.meta"),
                "archive_size": 0,
                "mtime": 100,
            }
        ]

        self.assertEqual(staleZeroByteUnfinishedEntries(entries, 200, 60), entries)
        self.assertEqual(staleUnfinishedEntries(entries, 200, 60), entries)

    def test_identifies_stale_partial_unfinished_entries_for_retry(self):
        entries = [
            {
                "archive": Path("Partial.7z.unfinished"),
                "metadata": Path("Partial.7z.unfinished.meta"),
                "archive_size": 128,
                "mtime": 100,
            }
        ]

        self.assertEqual(staleUnfinishedEntries(entries, 200, 60), entries)
        self.assertEqual(staleZeroByteUnfinishedEntries(entries, 200, 60), [])

    def test_identifies_zero_byte_unfinished_entries_for_prequeue_cleanup(self):
        entries = [
            {
                "archive": Path("Queued.7z.unfinished"),
                "metadata": Path("Queued.7z.unfinished.meta"),
                "archive_size": 0,
                "mtime": 200,
            }
        ]

        self.assertEqual(zeroByteUnfinishedEntries(entries), entries)

    def test_preserves_fresh_or_non_empty_unfinished_entries(self):
        fresh_entry = {
            "archive": Path("Fresh.7z.unfinished"),
            "metadata": Path("Fresh.7z.unfinished.meta"),
            "archive_size": 0,
            "mtime": 190,
        }
        partial_entry = {
            "archive": Path("Partial.7z.unfinished"),
            "metadata": Path("Partial.7z.unfinished.meta"),
            "archive_size": 128,
            "mtime": 100,
        }

        self.assertEqual(staleZeroByteUnfinishedEntries([fresh_entry], 200, 60), [])
        self.assertEqual(staleZeroByteUnfinishedEntries([partial_entry], 200, 60), [])
        self.assertEqual(staleUnfinishedEntries([fresh_entry], 200, 60), [])
        self.assertEqual(zeroByteUnfinishedEntries([partial_entry]), [])

    def test_identifies_resumable_partial_unfinished_entries(self):
        partial_entry = {
            "archive": Path("Partial.7z.unfinished"),
            "metadata": Path("Partial.7z.unfinished.meta"),
            "archive_size": 128,
            "mtime": 100,
        }
        empty_entry = {
            "archive": Path("Empty.7z.unfinished"),
            "metadata": Path("Empty.7z.unfinished.meta"),
            "archive_size": 0,
            "mtime": 100,
        }

        self.assertTrue(hasPartialUnfinishedEntries([partial_entry]))
        self.assertTrue(hasPartialUnfinishedEntries([empty_entry, partial_entry]))
        self.assertFalse(hasPartialUnfinishedEntries([empty_entry]))
        self.assertFalse(hasPartialUnfinishedEntries([]))

    def test_removes_unfinished_entry_files(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Queued.7z.unfinished"
            archive.write_bytes(b"")
            metadata = downloads / "Queued.7z.unfinished.meta"
            metadata.write_text("[General]\nmodID=111\nfileID=222\n", encoding="utf-8")

            removed = removeUnfinishedEntries(
                [{"archive": archive, "metadata": metadata}]
            )

            self.assertEqual(removed, 2)
            self.assertFalse(archive.exists())
            self.assertFalse(metadata.exists())

    def test_preflight_cleans_only_requested_zero_byte_entries(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)

            target_archive = downloads / "Target.7z.unfinished"
            target_archive.write_bytes(b"")
            target_meta = downloads / "Target.7z.unfinished.meta"
            target_meta.write_text(
                "[General]\nmodID=111\nfileID=222\n", encoding="utf-8"
            )

            other_archive = downloads / "Other.7z.unfinished"
            other_archive.write_bytes(b"")
            other_meta = downloads / "Other.7z.unfinished.meta"
            other_meta.write_text(
                "[General]\nmodID=333\nfileID=444\n", encoding="utf-8"
            )

            cleanup = cleanupZeroByteUnfinishedDownloads(downloads, {(111, 222)})

            self.assertEqual(cleanup["cleaned_keys"], {(111, 222)})
            self.assertEqual(cleanup["removed_files"], 2)
            self.assertFalse(target_archive.exists())
            self.assertFalse(target_meta.exists())
            self.assertTrue(other_archive.exists())
            self.assertTrue(other_meta.exists())

    def test_preflight_preserves_partial_downloads(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Partial.7z.unfinished"
            archive.write_bytes(b"partial")
            metadata = downloads / "Partial.7z.unfinished.meta"
            metadata.write_text("[General]\nmodID=111\nfileID=222\n", encoding="utf-8")

            cleanup = cleanupZeroByteUnfinishedDownloads(downloads, {(111, 222)})

            self.assertEqual(cleanup["cleaned_keys"], set())
            self.assertEqual(cleanup["removed_files"], 0)
            self.assertTrue(archive.exists())
            self.assertTrue(metadata.exists())


class CoerceDownloadIdTests(unittest.TestCase):
    def test_accepts_non_negative_integer_values(self):
        self.assertEqual(coerceDownloadId(0), 0)
        self.assertEqual(coerceDownloadId("42"), 42)

    def test_rejects_missing_invalid_and_negative_values(self):
        self.assertIsNone(coerceDownloadId(None))
        self.assertIsNone(coerceDownloadId("not-an-id"))
        self.assertIsNone(coerceDownloadId(-1))

    def test_pops_tracked_key_for_valid_download_id(self):
        download_ids = {42: (123, 456)}

        self.assertEqual(popDownloadKey(download_ids, "42"), (123, 456))
        self.assertEqual(download_ids, {})

    def test_ignores_invalid_download_id_without_mutating_state(self):
        download_ids = {42: (123, 456)}

        self.assertIsNone(popDownloadKey(download_ids, "not-an-id"))
        self.assertEqual(download_ids, {42: (123, 456)})


class InstallerDefaultActionLabelTests(unittest.TestCase):
    def test_normalizes_decorated_button_labels(self):
        self.assertEqual(normalizedButtonLabel("&Next >"), "next")
        self.assertEqual(normalizedButtonLabel("< &Back"), "back")

    def test_selects_enabled_next_on_fomod_dialog(self):
        self.assertEqual(
            installerDefaultActionLabel(
                "Glorious Doors of Skyrim (GDOS) SE",
                [("&Back", False), ("&Next >", True), ("Cancel", True)],
            ),
            "next",
        )

    def test_selects_enabled_install_on_final_fomod_page(self):
        self.assertEqual(
            installerDefaultActionLabel(
                "UNP Female Body Renewal",
                [("&Back", True), ("Install", True), ("Cancel", True)],
            ),
            "install",
        )

    def test_ignores_mo2_known_dialogs(self):
        for title in ("Quick Install", "Mod Exists", "Error"):
            self.assertIsNone(
                installerDefaultActionLabel(
                    title,
                    [("Next", True), ("Install", True), ("Cancel", True)],
                )
            )

    def test_requires_cancel_button_to_avoid_generic_next_prompts(self):
        self.assertIsNone(
            installerDefaultActionLabel("NXM Collection Link Handler", [("Next", True)])
        )

    def test_rejects_cancel_only_confirmation_dialog(self):
        self.assertIsNone(
            installerDefaultActionLabel(
                "Download again?",
                [("Yes", True), ("No", True), ("Cancel", True)],
            )
        )

    def test_ignores_empty_button_text(self):
        self.assertEqual(
            installerDefaultActionLabel(
                "Some FOMOD",
                [("", True), ("&Next >", True), ("Cancel", True)],
            ),
            "next",
        )


class DuplicateDownloadPromptActionLabelTests(unittest.TestCase):
    def test_declines_mo2_duplicate_download_prompt(self):
        self.assertEqual(
            duplicateDownloadPromptActionLabel(
                "Download again?",
                [("&Yes", True), ("&No", True), ("Cancel", True)],
            ),
            "no",
        )

    def test_ignores_fomod_dialog_with_no_button(self):
        self.assertIsNone(
            duplicateDownloadPromptActionLabel(
                "Some FOMOD",
                [("&Back", True), ("&Next >", True), ("Cancel", True)],
            )
        )

    def test_requires_complete_confirmation_button_set(self):
        self.assertIsNone(
            duplicateDownloadPromptActionLabel(
                "Download again?",
                [("&Yes", True), ("Cancel", True)],
            )
        )

    def test_acknowledges_already_started_download_prompt(self):
        self.assertEqual(
            duplicateDownloadPromptActionLabel("Already Started", [("OK", True)]),
            "ok",
        )

    def test_ignores_disabled_already_started_prompt(self):
        self.assertIsNone(
            duplicateDownloadPromptActionLabel("Already Started", [("OK", False)])
        )

    def test_ignores_unrelated_ok_prompt(self):
        self.assertIsNone(
            duplicateDownloadPromptActionLabel("Error", [("OK", True)])
        )


class DownloadCompletionPlanTests(unittest.TestCase):
    def test_failed_downloads_keep_dialog_open_for_review(self):
        self.assertEqual(
            downloadCompletionPlan(
                failed_count=1,
                has_on_complete=True,
                close_on_success=True,
                delay_ms=5000,
            ),
            {
                "run_complete": False,
                "close_immediately": False,
                "close_delay_ms": 0,
            },
        )

    def test_auto_install_flow_closes_before_running_callback(self):
        self.assertEqual(
            downloadCompletionPlan(
                failed_count=0,
                has_on_complete=True,
                close_on_success=True,
                delay_ms=5000,
            ),
            {
                "run_complete": True,
                "close_immediately": True,
                "close_delay_ms": 0,
            },
        )

    def test_manual_followup_flow_still_gets_success_close_delay(self):
        self.assertEqual(
            downloadCompletionPlan(
                failed_count=0,
                has_on_complete=True,
                close_on_success=False,
                delay_ms=5000,
            ),
            {
                "run_complete": True,
                "close_immediately": False,
                "close_delay_ms": 5000,
            },
        )

    def test_plain_success_flow_uses_success_close_delay(self):
        self.assertEqual(
            downloadCompletionPlan(
                failed_count=0,
                has_on_complete=False,
                close_on_success=False,
                delay_ms=5000,
            ),
            {
                "run_complete": False,
                "close_immediately": False,
                "close_delay_ms": 5000,
            },
        )


class DownloadCompletionChoicesTests(unittest.TestCase):
    def test_collection_links_keep_partial_install_choice_available(self):
        policy = collectionLinkCompletionPolicy()
        choices = downloadCompletionChoices(
            {"successful": 552, "failed": 7, "has_failures": True},
            has_on_complete=policy["attach_install_callback"],
        )

        self.assertTrue(policy["prompt_after_download"])
        self.assertFalse(policy["close_on_success"])
        self.assertTrue(choices["retry_visible"])
        self.assertTrue(choices["install_visible"])
        self.assertEqual(choices["install_label"], "Install Available")

    def test_failed_downloads_offer_retry_and_install_available(self):
        self.assertEqual(
            downloadCompletionChoices(
                {"successful": 552, "failed": 7, "has_failures": True},
                has_on_complete=True,
            ),
            {
                "retry_visible": True,
                "install_visible": True,
                "install_label": "Install Available",
                "fomod_defaults_visible": True,
            },
        )

    def test_failed_downloads_without_install_callback_only_offer_retry(self):
        self.assertEqual(
            downloadCompletionChoices(
                {"successful": 552, "failed": 7, "has_failures": True},
                has_on_complete=False,
            ),
            {
                "retry_visible": True,
                "install_visible": False,
                "install_label": "Install Available",
                "fomod_defaults_visible": False,
            },
        )

    def test_successful_downloads_offer_install_collection(self):
        self.assertEqual(
            downloadCompletionChoices(
                {"successful": 75, "failed": 0, "has_failures": False},
                has_on_complete=True,
            ),
            {
                "retry_visible": False,
                "install_visible": True,
                "install_label": "Install Collection",
                "fomod_defaults_visible": True,
            },
        )


class DownloadProgressFormatTests(unittest.TestCase):
    def test_failed_downloads_show_completed_count_instead_of_percent(self):
        self.assertEqual(
            downloadProgressFormat(
                {"successful": 552, "total": 559, "has_failures": True}
            ),
            "552/559 downloaded",
        )

    def test_successful_downloads_use_default_percent_format(self):
        self.assertEqual(
            downloadProgressFormat(
                {"successful": 75, "total": 75, "has_failures": False}
            ),
            "%p%",
        )


class ActiveDownloadPromptKeyTests(unittest.TestCase):
    def test_prefers_current_active_queue_key(self):
        self.assertEqual(
            activeDownloadPromptKey(
                active_key=(1, 2),
                context_key=(3, 4),
                context_expires_at=50,
                now=100,
            ),
            (1, 2),
        )

    def test_uses_unexpired_prompt_context(self):
        self.assertEqual(
            activeDownloadPromptKey(
                active_key=None,
                context_key=(3, 4),
                context_expires_at=105,
                now=100,
            ),
            (3, 4),
        )

    def test_ignores_expired_prompt_context(self):
        self.assertIsNone(
            activeDownloadPromptKey(
                active_key=None,
                context_key=(3, 4),
                context_expires_at=99,
                now=100,
            )
        )


class AllocateUniqueModNameTests(unittest.TestCase):
    def test_sanitizes_path_separators_and_empty_names(self):
        self.assertEqual(sanitizeModName(" A/B\\C  "), "A-B-C")
        self.assertEqual(sanitizeModName("   "), "Collection Mod")

    def test_uses_base_name_when_available(self):
        used = set()
        counts = {}

        self.assertEqual(
            allocateUniqueModName("New Statue", used, counts), "New Statue"
        )
        self.assertEqual(used, {"New Statue"})
        self.assertEqual(counts, {"New Statue": 2})

    def test_suffixes_duplicate_collection_entries(self):
        used = set()
        counts = {}

        self.assertEqual(
            allocateUniqueModName("New Statue", used, counts), "New Statue"
        )
        self.assertEqual(
            allocateUniqueModName("New Statue", used, counts), "New Statue #2"
        )
        self.assertEqual(
            allocateUniqueModName("New Statue", used, counts), "New Statue #3"
        )

    def test_skips_existing_profile_names(self):
        used = {"New Statue", "New Statue #2"}
        counts = {}

        self.assertEqual(
            allocateUniqueModName("New Statue", used, counts), "New Statue #3"
        )
        self.assertEqual(counts, {"New Statue": 4})


class SafeDisplayTextTests(unittest.TestCase):
    def test_preserves_regular_unicode_text(self):
        self.assertEqual(safeDisplayText("Café Statues"), "Café Statues")

    def test_removes_non_bmp_and_control_characters(self):
        self.assertEqual(safeDisplayText("Sexy Statues\U0001f5ff\n"), "Sexy Statues")

    def test_falls_back_for_empty_display_text(self):
        self.assertEqual(safeDisplayText("\U0001f5ff"), "Unknown Collection")


class CoerceBoolSettingTests(unittest.TestCase):
    def test_accepts_native_bool_and_numeric_values(self):
        self.assertTrue(coerceBoolSetting(True))
        self.assertFalse(coerceBoolSetting(False))
        self.assertTrue(coerceBoolSetting(1))
        self.assertFalse(coerceBoolSetting(0))

    def test_accepts_common_string_values(self):
        self.assertTrue(coerceBoolSetting("true"))
        self.assertTrue(coerceBoolSetting(" YES "))
        self.assertTrue(coerceBoolSetting("on"))
        self.assertFalse(coerceBoolSetting("false"))
        self.assertFalse(coerceBoolSetting("0"))
        self.assertFalse(coerceBoolSetting(" off "))

    def test_unknown_values_fall_back_to_python_truthiness(self):
        self.assertTrue(coerceBoolSetting("custom"))
        self.assertFalse(coerceBoolSetting(""))
        self.assertFalse(coerceBoolSetting(None))


class CoerceIntSettingTests(unittest.TestCase):
    def test_accepts_native_and_string_integer_values(self):
        self.assertEqual(coerceIntSetting(3, default=1), 3)
        self.assertEqual(coerceIntSetting("7", default=1), 7)

    def test_uses_default_for_missing_or_invalid_values(self):
        self.assertEqual(coerceIntSetting(None, default=5), 5)
        self.assertEqual(coerceIntSetting("not-a-number", default=5), 5)

    def test_clamps_to_minimum_when_requested(self):
        self.assertEqual(coerceIntSetting("-3", default=5, minimum=0), 0)
        self.assertEqual(coerceIntSetting("2", default=5, minimum=1), 2)


class InstallerSettingDefaultsTests(unittest.TestCase):
    def test_duplicate_mod_merging_is_opt_in(self):
        self.assertFalse(INSTALLER_SETTING_DEFAULTS["auto_merge_existing_mods"])

    def test_non_interactive_collection_defaults_match_verified_workflow(self):
        self.assertTrue(INSTALLER_SETTING_DEFAULTS["auto_accept_quick_install"])
        self.assertTrue(
            INSTALLER_SETTING_DEFAULTS["auto_dismiss_known_post_install_errors"]
        )
        self.assertTrue(INSTALLER_SETTING_DEFAULTS["install_files_as_separate_mods"])
        self.assertTrue(INSTALLER_SETTING_DEFAULTS["activate_mods_after_install"])
        self.assertFalse(INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"])
        self.assertEqual(INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_max_steps"], 20)


class DownloadProgressStateTests(unittest.TestCase):
    def test_failures_do_not_count_as_successful(self):
        state = downloadProgressState(
            3,
            {(1, 1)},
            {(2, 2)},
            {(1, 1): 1, (2, 2): 1, (3, 3): 1},
        )

        self.assertEqual(state["successful"], 1)
        self.assertEqual(state["failed"], 1)
        self.assertEqual(state["progress"], 1)
        self.assertEqual(state["processed"], 2)
        self.assertEqual(state["remaining"], 1)
        self.assertFalse(state["is_terminal"])

    def test_completed_key_overrides_previous_failure(self):
        state = downloadProgressState(
            2,
            {(1, 1)},
            {(1, 1)},
            {(1, 1): 1, (2, 2): 1},
        )

        self.assertEqual(state["successful"], 1)
        self.assertEqual(state["failed"], 0)
        self.assertEqual(state["progress"], 1)
        self.assertEqual(state["remaining"], 1)

    def test_counts_duplicate_collection_entries_by_key_weight(self):
        state = downloadProgressState(
            3,
            {(1, 1)},
            set(),
            {(1, 1): 2, (2, 2): 1},
        )

        self.assertEqual(state["successful"], 2)
        self.assertEqual(state["progress"], 2)
        self.assertEqual(state["processed"], 2)
        self.assertEqual(state["remaining"], 1)


if __name__ == "__main__":
    unittest.main()

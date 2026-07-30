from pathlib import Path
import importlib.util
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
import zipfile

from collection_helpers import (
    AUTOMATED_INSTALL_CADENCE_DEFAULTS,
    INSTALLER_SETTING_DEFAULTS,
    activeDownloadPromptKey,
    allocateUniqueModName,
    archiveInspectionSubprocessKwargs,
    cleanupZeroByteUnfinishedDownloads,
    coerceBoolSetting,
    coerceDownloadId,
    coerceIntSetting,
    collectionEntryNexusKey,
    collectionEntriesFromMetadata,
    collectionExpectedFileNames,
    collectionExpectedNexusKeys,
    collectionExpectedStateFromMetadataFiles,
    collectionInstallPostconditionAudit,
    collectionInstallRoute,
    collectionLinkCompletionPolicy,
    collectionMetadataFiles,
    collectionMetadataFromFile,
    collectionRecoveryTargets,
    contentTreeWarningDialogAction,
    collectionDownloadExpectedSizes,
    detachedInstallCacheKeyFromPath,
    downloadedArchiveNameKeys,
    duplicateDownloadPromptArchiveAction,
    duplicateDownloadPromptActionLabel,
    downloadCompletionChoices,
    downloadCompletionPlan,
    downloadProgressCanClose,
    downloadPromptKeyFromLabels,
    downloadPromptKeyFromArchiveLabels,
    downloadProgressFormat,
    downloadProgressState,
    downloadedFileKeys,
    extractHeadlessZipArchive,
    fastFinishMetadataRepairKeys,
    fomodManualChoiceGuide,
    gameRootFileEvidenceForCollectionEntry,
    headlessFomodDependencyInstallLayout,
    headlessArchivePreflightFallback,
    hasPartialUnfinishedEntries,
    headlessPayloadRootValid,
    headlessInstallMetaIni,
    headlessZipInstallLayout,
    inferModIdFromDownloadName,
    installedModHasCompletionPayload,
    installedPayloadFileCount,
    installedModRecordsFromDirectory,
    installPlanExecutionAction,
    collectionPluginActivationTargetModNames,
    collectionInvalidPayloadModNames,
    collectionPluginNamesFromModDirs,
    invalidInstallContentDialogAction,
    installNoResultReason,
    installerDefaultActionLabel,
    isRequiredFomodGroupTitle,
    isQuotaLimitText,
    isSafeSingletonFomodOption,
    isTransientManualFomodPlanFailure,
    matchingPartialOrphanUnfinishedEntries,
    mo2CategoryField,
    mo2CategoryNameMap,
    moveHeadlessArchivePayload,
    moveHeadlessFomodSelectionPayload,
    nativeGameRootPathCandidate,
    nativePathForArchiveInspection,
    nexusQuotaRemainingFromText,
    nexusQuotaStateFromHeaders,
    normalizedButtonLabel,
    orphanUnfinishedDownloadEntries,
    parseCollectionAddress,
    popDownloadKey,
    preferredCanonicalDownloadArchive,
    removeOrphanUnfinishedEntries,
    removeOrphanUnfinishedDownloadsForKeys,
    removeUnfinishedEntries,
    repairDownloadMetadataInstalledFlags,
    repairInstalledCollectionModMetadata,
    repairModlistDisabledStates,
    repairModlistEnabledStates,
    repairPluginEnabledStates,
    repairSingleWrapperPayload,
    recordCollectionLinkLaunch,
    retryAfterSeconds,
    quotaLimitMessage,
    quotaResumeDelaySeconds,
    proactiveQuotaStopMessage,
    safeArchiveMemberTarget,
    safeDisplayText,
    sanitizeModName,
    sevenZipModuleConfigPathFromListing,
    sevenZipArchiveMemberPaths,
    shouldAutoCloseInstallSummary,
    shouldPassTargetNameToInstallMod,
    shouldQueueFomodProbeRetry,
    shouldRetryInvalidInstalledCollectionArchive,
    shouldUseArchiveDefaultForFomodCompatibility,
    shouldUseCollectionTargetModName,
    shouldDelayTerminalDownloadFailure,
    staleAlreadyStartedAction,
    adaptiveDownloadTailGraceSeconds,
    downloadProgressIsStalled,
    downloadTailBoundaryReached,
    staleOrphanUnfinishedDownloadEntries,
    staleDownloadStartAction,
    staleUnfinishedEntries,
    staleZeroByteUnfinishedEntries,
    steamGameRootFromMo2BasePath,
    steamAppShaderCacheSize,
    steamDefaultLaunchOption,
    steamAppInfoHasLaunchExecutable,
    latestSteamLaunchCommand,
    steamLaunchOptions,
    steamMo2GuardAudit,
    steamShaderCacheDisabled,
    steamShaderProcessingQueue,
    unfinishedDownloadEntries,
    zeroByteDownloadStartIsStalled,
    zeroByteUnfinishedEntries,
    zipArchiveMemberPaths,
)

_WORKER_PATH = Path(__file__).resolve().parent / "scripts" / "native_archive_worker.py"
_WORKER_SPEC = importlib.util.spec_from_file_location(
    "native_archive_worker", _WORKER_PATH
)
native_archive_worker = importlib.util.module_from_spec(_WORKER_SPEC)
_WORKER_SPEC.loader.exec_module(native_archive_worker)


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


class DownloadedArchiveNameKeysTests(unittest.TestCase):
    def test_accepts_unique_archive_matching_mod_id_and_size(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            (
                downloads / "Starting Outfit Suppressed-43967-1-20-1628312587.7z"
            ).write_bytes(b"x" * 1198)
            mods = [
                {
                    "file": {
                        "fileId": "219398",
                        "sizeInBytes": "1198",
                        "mod": {"modId": "43967"},
                    }
                }
            ]

            self.assertEqual(
                downloadedArchiveNameKeys(downloads, mods), {(43967, 219398)}
            )

    def test_accepts_archive_name_match_with_unfinished_sibling(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "SMIM SE 2-08-659-2-08.7z"
            archive.write_bytes(b"x" * 7)
            Path(str(archive) + ".unfinished").write_bytes(b"partial")
            mods = [
                {
                    "file": {
                        "fileId": "59069",
                        "sizeInBytes": "7",
                        "mod": {"modId": "659"},
                    }
                }
            ]

            self.assertEqual(downloadedArchiveNameKeys(downloads, mods), {(659, 59069)})

    def test_rejects_ambiguous_same_mod_and_size(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            (downloads / "Example-1234-1.7z").write_bytes(b"x" * 7)
            mods = [
                {
                    "file": {
                        "fileId": "10",
                        "sizeInBytes": "7",
                        "mod": {"modId": "1234"},
                    }
                },
                {
                    "file": {
                        "fileId": "11",
                        "sizeInBytes": "7",
                        "mod": {"modId": "1234"},
                    }
                },
            ]

            self.assertEqual(downloadedArchiveNameKeys(downloads, mods), set())


class PreferredCanonicalDownloadArchiveTests(unittest.TestCase):
    def test_prefers_unprefixed_archive_when_duplicate_size_matches(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            canonical = downloads / "Blended Roads-8834-1-7.7z"
            duplicate = downloads / "3_Blended Roads-8834-1-7.7z"
            canonical.write_bytes(b"archive")
            duplicate.write_bytes(b"archive")

            self.assertEqual(preferredCanonicalDownloadArchive(duplicate), canonical)

    def test_keeps_numbered_archive_when_canonical_size_differs(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            canonical = downloads / "Blended Roads-8834-1-7.7z"
            duplicate = downloads / "3_Blended Roads-8834-1-7.7z"
            canonical.write_bytes(b"old")
            duplicate.write_bytes(b"archive")

            self.assertEqual(preferredCanonicalDownloadArchive(duplicate), duplicate)


class RepairDownloadMetadataInstalledFlagsTests(unittest.TestCase):
    def test_sets_installed_true_for_matching_completed_download(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Example Mod-123-456.7z"
            archive.write_bytes(b"archive")
            metadata = downloads / "Example Mod-123-456.7z.meta"
            metadata.write_text(
                "[General]\nmodID=123\nfileID=456\ninstalled=false\n",
                encoding="utf-8",
            )

            result = repairDownloadMetadataInstalledFlags(
                downloads, {(123, 456)}, desired_installed=True
            )

            self.assertEqual(result["repaired"], 1)
            self.assertIn("installed=true", metadata.read_text(encoding="utf-8"))

    def test_skips_non_matching_download_metadata(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Example Mod-123-456.7z"
            archive.write_bytes(b"archive")
            metadata = downloads / "Example Mod-123-456.7z.meta"
            metadata.write_text(
                "[General]\nmodID=123\nfileID=456\ninstalled=false\n",
                encoding="utf-8",
            )

            result = repairDownloadMetadataInstalledFlags(
                downloads, {(999, 456)}, desired_installed=True
            )

            self.assertEqual(result["repaired"], 0)
            self.assertIn("installed=false", metadata.read_text(encoding="utf-8"))

    def test_backs_up_changed_metadata(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = root / "downloads"
            backups = root / "backups"
            downloads.mkdir()
            archive = downloads / "Example Mod-123-456.7z"
            archive.write_bytes(b"archive")
            metadata = downloads / "Example Mod-123-456.7z.meta"
            metadata.write_text(
                "[General]\nmodID=123\nfileID=456\ninstalled=false\n",
                encoding="utf-8",
            )

            result = repairDownloadMetadataInstalledFlags(
                downloads, {(123, 456)}, backup_dir=backups
            )

            self.assertEqual(result["repaired"], 1)
            self.assertIn(
                "installed=false",
                (backups / metadata.name).read_text(encoding="utf-8"),
            )


class InstalledCollectionMetadataRepairTests(unittest.TestCase):
    def test_repairs_manifest_version_and_mo2_category_from_nexus_category(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            mod_dir = mods / "Example Mod"
            mod_dir.mkdir(parents=True)
            metadata = mod_dir / "meta.ini"
            metadata.write_text(
                "[General]\n"
                "modid=123\n"
                "version=\n"
                "newestVersion=\n"
                'category="7,"\n'
                "nexusCategory=0\n"
                "installationFile=Example-123-456.7z\n"
                "\n"
                "[installedFiles]\n"
                "size=1\n"
                "1\\modid=123\n"
                "1\\fileid=456\n",
                encoding="utf-8",
            )

            result = repairInstalledCollectionModMetadata(
                mods,
                {(123, 456): ["Example Mod"]},
                {
                    (123, 456): {
                        "file": {
                            "version": "1.2.3",
                            "mod": {"version": "1.2", "category": "User Interface"},
                        }
                    }
                },
                category_name_map={"user interface": 42},
            )

            repaired = metadata.read_text(encoding="utf-8")
            self.assertEqual(result["repaired"], 1)
            self.assertIn("version=1.2.3", repaired)
            self.assertIn("newestVersion=1.2", repaired)
            self.assertIn("nexusCategory=User Interface", repaired)
            self.assertIn('category="42,"', repaired)

    def test_repairs_blank_file_version_from_manifest_mod_version(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            mod_dir = mods / "Example Mod"
            mod_dir.mkdir(parents=True)
            metadata = mod_dir / "meta.ini"
            metadata.write_text(
                "[General]\n"
                "modid=123\n"
                "version=\n"
                "newestVersion=\n"
                "\n"
                "[installedFiles]\n"
                "size=1\n"
                "1\\modid=123\n"
                "1\\fileid=456\n",
                encoding="utf-8",
            )

            result = repairInstalledCollectionModMetadata(
                mods,
                {(123, 456): ["Example Mod"]},
                {
                    (123, 456): {
                        "file": {
                            "version": "",
                            "mod": {"version": "1.05", "category": "Combat"},
                        }
                    }
                },
            )

            repaired = metadata.read_text(encoding="utf-8")
            self.assertEqual(result["repaired"], 1)
            self.assertIn("version=1.05", repaired)
            self.assertIn("newestVersion=1.05", repaired)


class CollectionPluginActivationTargetTests(unittest.TestCase):
    def test_reconciles_already_installed_mods_on_replay(self):
        self.assertEqual(
            collectionPluginActivationTargetModNames(
                ["Already Installed", "Needs Enable"], ["Needs Enable"]
            ),
            ["Already Installed", "Needs Enable"],
        )


class CollectionPluginNamesFromModDirsTests(unittest.TestCase):
    def test_discovers_plugins_under_collection_mods(self):
        with TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            first = mods / "First"
            second = mods / "Second"
            first.mkdir(parents=True)
            second.mkdir()
            (first / "Plugin.esp").write_text("", encoding="utf-8")
            (second / "nested").mkdir()
            (second / "nested" / "Patch.esl").write_text("", encoding="utf-8")
            (second / "readme.txt").write_text("", encoding="utf-8")

            self.assertEqual(
                collectionPluginNamesFromModDirs(mods, ["First", "Second"]),
                ["Plugin.esp", "Patch.esl"],
            )


class CollectionInvalidPayloadModNamesTests(unittest.TestCase):
    def test_flags_empty_collection_container(self):
        with TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            invalid = mods / "Empty Patch"
            valid = mods / "Valid Patch"
            invalid.mkdir(parents=True)
            valid.mkdir()
            (invalid / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (valid / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (valid / "patch.esp").write_text("", encoding="utf-8")

            self.assertEqual(
                collectionInvalidPayloadModNames(mods, ["Empty Patch", "Valid Patch"]),
                ["Empty Patch"],
            )


class Mo2CategoryFieldTests(unittest.TestCase):
    def test_uses_nexus_category_when_available(self):
        self.assertEqual(mo2CategoryField(42), '"42,"')

    def test_uses_unknown_category_when_missing(self):
        self.assertIsNone(mo2CategoryField(0))

    def test_reads_mo2_category_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "categories.dat"
            path.write_text(
                "15|User Interface|0\nbad|Ignored|0\n16||0\n",
                encoding="utf-8",
            )

            self.assertEqual(mo2CategoryNameMap(path), {"user interface": 15})


class CollectionInstallPostconditionAuditTests(unittest.TestCase):
    def write_mod_metadata(self, mods, name, mod_id=123, file_id=456):
        mod_dir = mods / name
        mod_dir.mkdir(parents=True)
        (mod_dir / "meta.ini").write_text(
            "[General]\n"
            f"modid={mod_id}\n"
            f"installationFile={mod_id}-{file_id}-Example.7z\n"
            "\n"
            "[installedFiles]\n"
            "size=1\n"
            f"1\\modid={mod_id}\n"
            f"1\\fileid={file_id}\n",
            encoding="utf-8",
        )

    def write_download_metadata(self, downloads, installed="true"):
        archive = downloads / "Example-123-456.7z"
        archive.write_bytes(b"archive")
        (downloads / "Example-123-456.7z.meta").write_text(
            f"[General]\nmodID=123\nfileID=456\ninstalled={installed}\n",
            encoding="utf-8",
        )

    def test_fails_when_installed_mod_is_disabled_in_profile(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            self.write_mod_metadata(mods, "Example Mod")
            self.write_download_metadata(downloads, installed="true")

            result = collectionInstallPostconditionAudit(
                downloads,
                mods,
                "# generated\n-Example Mod\n",
                expected_keys={(123, 456)},
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["disabled_mods"], ["Example Mod"])

    def test_fails_when_download_metadata_still_says_not_installed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            self.write_mod_metadata(mods, "Example Mod")
            self.write_download_metadata(downloads, installed="false")

            result = collectionInstallPostconditionAudit(
                downloads,
                mods,
                "# generated\n+Example Mod\n",
                expected_keys={(123, 456)},
            )

            self.assertFalse(result["ok"])
            self.assertEqual(len(result["download_metadata_mismatches"]), 1)

    def test_passes_when_metadata_container_and_profile_agree(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            self.write_mod_metadata(mods, "Example Mod")
            self.write_download_metadata(downloads, installed="true")

            result = collectionInstallPostconditionAudit(
                downloads,
                mods,
                "# generated\n+Example Mod\n",
                expected_keys={(123, 456)},
            )

            self.assertTrue(result["ok"])

    def test_reads_installed_records_from_mo2_metadata(self):
        with TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            mods.mkdir()
            self.write_mod_metadata(mods, "Example Mod")

            self.assertEqual(
                installedModRecordsFromDirectory(mods),
                {(123, 456): ["Example Mod"]},
            )

    def test_reads_installed_record_from_installation_file_download_metadata(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            mod_dir = mods / "Example Mod"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text(
                "[General]\n"
                "modid=123\n"
                "installationFile=Example-123-456.7z\n"
                "\n"
                "[installedFiles]\n"
                "size=1\n"
                "1\\modid=0\n"
                "1\\fileid=0\n",
                encoding="utf-8",
            )
            self.write_download_metadata(downloads, installed="true")

            self.assertEqual(
                installedModRecordsFromDirectory(mods, downloads),
                {(123, 456): ["Example Mod"]},
            )

    def test_reads_installed_record_from_collection_file_name_when_mo2_key_is_zero(
        self,
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            mod_dir = mods / "Example Mod"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text(
                "[General]\n"
                "modid=123\n"
                "installationFile=Example Payload-123-1-0.7z\n"
                "\n"
                "[installedFiles]\n"
                "size=1\n"
                "1\\modid=0\n"
                "1\\fileid=0\n",
                encoding="utf-8",
            )

            self.assertEqual(
                installedModRecordsFromDirectory(
                    mods,
                    downloads,
                    expected_file_names={(123, 456): "Example Payload"},
                ),
                {(123, 456): ["Example Mod"]},
            )

    def test_reads_live_style_installed_record_from_collection_file_name(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            mod_dir = mods / "Faster HDT-SMP"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text(
                "[General]\n"
                "modid=57339\n"
                "installationFile=Faster HDT-SMP-57339-2-5-1-1728377043.7z\n"
                "\n"
                "[installedFiles]\n"
                "size=1\n"
                "1\\modid=0\n"
                "1\\fileid=0\n",
                encoding="utf-8",
            )

            self.assertEqual(
                installedModRecordsFromDirectory(
                    mods,
                    downloads,
                    expected_file_names={(57339, 550156): "Faster HDT-SMP"},
                ),
                {(57339, 550156): ["Faster HDT-SMP"]},
            )

    def test_detects_known_game_root_install_evidence(self):
        with TemporaryDirectory() as tmp:
            game_root = Path(tmp)
            (game_root / "d3dx9_42.dll").write_bytes(b"preloader")

            self.assertEqual(
                gameRootFileEvidenceForCollectionEntry((17230, 658442), game_root),
                ["d3dx9_42.dll"],
            )

    def test_ignores_missing_or_unknown_game_root_install_evidence(self):
        with TemporaryDirectory() as tmp:
            game_root = Path(tmp)

            self.assertEqual(
                gameRootFileEvidenceForCollectionEntry((17230, 658442), game_root),
                [],
            )
            self.assertEqual(
                gameRootFileEvidenceForCollectionEntry((57339, 550156), game_root),
                [],
            )


class GameRootPathResolutionTests(unittest.TestCase):
    def test_maps_wine_z_game_root_to_host_path(self):
        self.assertEqual(
            nativeGameRootPathCandidate(
                r"Z:\mnt\steam-library\SteamLibrary\steamapps\common\Skyrim Special Edition"
            ),
            Path(
                "/mnt/steam-library/SteamLibrary/steamapps/common/Skyrim Special Edition"
            ),
        )

    def test_infers_steam_game_root_from_mo2_compatdata_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "SteamLibrary" / "steamapps" / "common"
            game_root = common / "Skyrim Special Edition"
            game_root.mkdir(parents=True)
            mo2_base = (
                root
                / "SteamLibrary"
                / "steamapps"
                / "compatdata"
                / "489830"
                / "pfx"
                / "drive_c"
                / "users"
                / "steamuser"
                / "AppData"
                / "Local"
                / "ModOrganizer"
                / "Skyrim Special Edition - Test"
            )

            self.assertEqual(
                steamGameRootFromMo2BasePath(mo2_base),
                game_root,
            )


class ManualFomodPlanFailureCacheTests(unittest.TestCase):
    def test_treats_native_worker_failures_as_transient(self):
        self.assertTrue(
            isTransientManualFomodPlanFailure(
                "Native archive worker is not running; start scripts/native_archive_worker.py"
            )
        )
        self.assertTrue(
            isTransientManualFomodPlanFailure("Native archive worker timed out")
        )
        self.assertTrue(
            isTransientManualFomodPlanFailure(
                "Could not list archive with subprocess 7z: [WinError 6] Invalid handle"
            )
        )
        self.assertTrue(
            isTransientManualFomodPlanFailure(
                "FOMOD XML parse error: not well-formed (invalid token): "
                "line 1, column 0"
            )
        )
        self.assertFalse(
            isTransientManualFomodPlanFailure(
                "FOMOD contains unresolved required choices"
            )
        )


class NativeArchiveWorkerTests(unittest.TestCase):
    def test_decodes_utf16_fomod_xml_from_7z_stdout(self):
        payload = (
            b"\xff\xfe<\x00c\x00o\x00n\x00f\x00i\x00g\x00>\x00"
            b"<\x00m\x00o\x00d\x00u\x00l\x00e\x00N\x00a\x00m\x00e\x00>\x00"
            b"N\x00A\x00T\x00<\x00/\x00m\x00o\x00d\x00u\x00l\x00e\x00N\x00"
            b"a\x00m\x00e\x00>\x00<\x00/\x00c\x00o\x00n\x00f\x00i\x00g\x00>\x00"
        )

        decoded = native_archive_worker.decode_7z_stdout(payload)

        self.assertTrue(decoded.startswith("<config>"))
        self.assertIn("<moduleName>NAT</moduleName>", decoded)
        self.assertNotIn("\x00", decoded[:100])


class RepairModlistEnabledStatesTests(unittest.TestCase):
    def test_enables_matching_disabled_entries_without_touching_others(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            modlist = root / "modlist.txt"
            modlist.write_text(
                "# This file was automatically generated by Mod Organizer.\n"
                "-Example Mod\n"
                "-Other Mod\n"
                "+Already Enabled\n",
                encoding="utf-8",
            )

            result = repairModlistEnabledStates(
                modlist, {"Example Mod", "Already Enabled"}
            )

            self.assertEqual(result["enabled"], 1)
            self.assertEqual(result["already_enabled"], 1)
            self.assertIn("+Example Mod", modlist.read_text(encoding="utf-8"))
            self.assertIn("-Other Mod", modlist.read_text(encoding="utf-8"))

    def test_backs_up_changed_modlist(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup = root / "backup"
            modlist = root / "modlist.txt"
            modlist.write_text("-Example Mod\n", encoding="utf-8")

            result = repairModlistEnabledStates(
                modlist, {"Example Mod"}, backup_dir=backup
            )

            self.assertEqual(result["enabled"], 1)
            self.assertEqual(
                (backup / "modlist.txt").read_text(encoding="utf-8"),
                "-Example Mod\n",
            )


class RepairModlistDisabledStatesTests(unittest.TestCase):
    def test_disables_matching_enabled_entries_without_touching_others(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            modlist = root / "modlist.txt"
            modlist.write_text(
                "+Example Mod\n+Other Mod\n-Already Disabled\n",
                encoding="utf-8",
            )

            result = repairModlistDisabledStates(
                modlist, {"Example Mod", "Already Disabled"}
            )

            self.assertEqual(result["disabled"], 1)
            self.assertEqual(result["already_disabled"], 1)
            self.assertIn("-Example Mod", modlist.read_text(encoding="utf-8"))
            self.assertIn("+Other Mod", modlist.read_text(encoding="utf-8"))


class RepairPluginEnabledStatesTests(unittest.TestCase):
    def test_enables_matching_disabled_plugins_without_touching_others(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = root / "plugins.txt"
            plugins.write_text(
                "# This file was automatically generated by Mod Organizer.\n"
                "*AlreadyActive.esp\n"
                "NeedsEnable.esl\n"
                "Other.esp\n",
                encoding="utf-8",
            )

            result = repairPluginEnabledStates(
                plugins, {"AlreadyActive.esp", "NeedsEnable.esl"}
            )

            text = plugins.read_text(encoding="utf-8")
            self.assertEqual(result["enabled"], 1)
            self.assertEqual(result["already_enabled"], 1)
            self.assertIn("*AlreadyActive.esp", text)
            self.assertIn("*NeedsEnable.esl", text)
            self.assertIn("Other.esp", text)

    def test_matches_plugin_names_case_insensitively(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = root / "plugins.txt"
            plugins.write_text("wd03otrainers.esp\n", encoding="utf-8")

            result = repairPluginEnabledStates(plugins, {"WD03OTrainers.esp"})

            self.assertEqual(result["enabled"], 1)
            self.assertEqual(result["missing"], [])
            self.assertEqual(
                plugins.read_text(encoding="utf-8"), "*WD03OTrainers.esp\n"
            )

    def test_canonicalizes_already_enabled_plugin_names(self):
        with TemporaryDirectory() as tmp:
            plugins = Path(tmp) / "plugins.txt"
            plugins.write_text("*wd03otrainers.esp\n", encoding="utf-8")

            result = repairPluginEnabledStates(plugins, {"WD03OTrainers.esp"})

            self.assertEqual(result["enabled"], 0)
            self.assertEqual(result["already_enabled"], 1)
            self.assertEqual(
                plugins.read_text(encoding="utf-8"), "*WD03OTrainers.esp\n"
            )

    def test_appends_missing_plugins_as_enabled_entries(self):
        with TemporaryDirectory() as tmp:
            plugins = Path(tmp) / "plugins.txt"
            plugins.write_text("*Other.esp\n", encoding="utf-8")

            result = repairPluginEnabledStates(plugins, {"WD03OTrainers.esp"})

            self.assertEqual(result["enabled"], 1)
            self.assertEqual(result["missing"], [])
            self.assertIn(
                "*WD03OTrainers.esp",
                plugins.read_text(encoding="utf-8"),
            )


class CollectionDownloadExpectedSizesTests(unittest.TestCase):
    def test_extracts_collection_nexus_identity(self):
        mod = {
            "file": {
                "fileId": "456",
                "name": "Example File",
                "mod": {"modId": "123"},
            }
        }

        self.assertEqual(collectionEntryNexusKey(mod), (123, 456))

    def test_ignores_invalid_collection_nexus_identity(self):
        self.assertIsNone(collectionEntryNexusKey({"file": {"fileId": "bad"}}))

    def test_extracts_collection_expected_keys_and_file_names(self):
        mods = [
            {
                "file": {
                    "fileId": "456",
                    "name": "Example File",
                    "mod": {"modId": "123"},
                }
            },
            {"file": {"fileId": "bad", "name": "Broken", "mod": {"modId": "123"}}},
        ]

        self.assertEqual(collectionExpectedNexusKeys(mods), {(123, 456)})
        self.assertEqual(
            collectionExpectedFileNames(mods), {(123, 456): "Example File"}
        )

    def test_reads_saved_collection_metadata_entries(self):
        metadata = {
            "essentialMods": [{"file": {"fileId": 456, "mod": {"modId": 123}}}],
            "chosenOptional": [{"file": {"fileId": 654, "mod": {"modId": 321}}}],
        }

        self.assertEqual(len(collectionEntriesFromMetadata(metadata)), 2)

    def test_loads_saved_collection_files_and_expected_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            collection_dir = root / "collections" / "skyrimspecialedition"
            collection_dir.mkdir(parents=True)
            collection_file = collection_dir / "example_1.json"
            collection_file.write_text(
                json.dumps(
                    {
                        "name": "Example Collection",
                        "essentialMods": [
                            {
                                "file": {
                                    "fileId": 456,
                                    "name": "Example File",
                                    "mod": {"modId": 123},
                                }
                            }
                        ],
                        "chosenOptional": [],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(collectionMetadataFiles(root), [collection_file])
            self.assertEqual(
                collectionMetadataFromFile(collection_file)["name"],
                "Example Collection",
            )
            state = collectionExpectedStateFromMetadataFiles([collection_file])

            self.assertEqual(state["expected_keys"], {(123, 456)})
            self.assertEqual(state["expected_file_names"], {(123, 456): "Example File"})
            self.assertEqual(state["collections"][0]["entries"], 1)

    def test_collection_recovery_targets_only_exact_installed_expected_files(self):
        result = collectionRecoveryTargets(
            {
                (123, 456): ["Example Mod"],
                (321, 654): ["Other Mod"],
            },
            expected_keys={(123, 456), (999, 111)},
        )

        self.assertEqual(result["installed_keys"], {(123, 456)})
        self.assertEqual(result["missing_keys"], {(999, 111)})
        self.assertEqual(result["mod_names"], ["Example Mod"])

    def test_record_collection_link_launch_marks_first_launch_primary(self):
        with TemporaryDirectory() as tmp:
            metadata_file = Path(tmp) / "example_1.json"
            metadata_file.write_text(
                json.dumps({"collection": "example", "revision": 1}),
                encoding="utf-8",
            )

            metadata = recordCollectionLinkLaunch(
                metadata_file, launched_at="2026-07-30T01:02:03"
            )

            self.assertEqual(metadata["addCollectionLaunchCount"], 1)
            self.assertEqual(metadata["addCollectionRecoveryCount"], 0)
            self.assertEqual(metadata["addCollectionLaunchMode"], "primary")
            self.assertEqual(metadata["addCollectionLaunches"][0]["mode"], "primary")
            self.assertEqual(
                collectionMetadataFromFile(metadata_file)["addCollectionLaunchCount"],
                1,
            )

    def test_record_collection_link_launch_marks_rerun_as_recovery(self):
        with TemporaryDirectory() as tmp:
            metadata_file = Path(tmp) / "example_1.json"
            metadata_file.write_text(
                json.dumps({"collection": "example", "revision": 1}),
                encoding="utf-8",
            )

            recordCollectionLinkLaunch(metadata_file, launched_at="first")
            metadata = recordCollectionLinkLaunch(metadata_file, launched_at="second")

            self.assertEqual(metadata["addCollectionLaunchCount"], 2)
            self.assertEqual(metadata["addCollectionRecoveryCount"], 1)
            self.assertEqual(metadata["addCollectionLaunchMode"], "recovery")
            self.assertEqual(
                [entry["mode"] for entry in metadata["addCollectionLaunches"]],
                ["primary", "recovery"],
            )

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
        self.assertEqual(
            inferModIdFromDownloadName(
                "Lanterns of Skyrim SE-2429-1-01.rar.unfinished"
            ),
            2429,
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

    def test_matches_partial_orphan_when_only_remaining_key_for_mod(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            orphan = downloads / (
                "Malacath No Vanilla Snow Shader ESL-63117-1-0-0-1644059503.zip.unfinished"
            )
            orphan.write_bytes(b"partial")

            entries = matchingPartialOrphanUnfinishedEntries(
                downloads,
                (63117, 261882),
                {(63117, 261882), (63117, 261878)},
                completed_on_disk={(63117, 261878)},
            )

            self.assertEqual([entry["archive"] for entry in entries], [orphan])

    def test_removes_exact_orphan_unfinished_entries(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            first = downloads / "First-1234-1.zip.unfinished"
            second = downloads / "Second-1234-2.zip.unfinished"
            first.write_bytes(b"partial")
            second.write_bytes(b"partial")

            removed = removeOrphanUnfinishedEntries(
                [{"archive": first}, {"archive": first}]
            )

            self.assertEqual(removed, 1)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())

    def test_ignores_partial_orphan_with_ambiguous_pending_keys(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            orphan = downloads / (
                "Malacath No Vanilla Snow Shader ESL-63117-1-0-0-1644059503.zip.unfinished"
            )
            orphan.write_bytes(b"partial")

            entries = matchingPartialOrphanUnfinishedEntries(
                downloads,
                (63117, 261882),
                {(63117, 261882), (63117, 261878)},
            )

            self.assertEqual(entries, [])

    def test_ignores_zero_byte_orphan_for_partial_wait(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            orphan = downloads / (
                "Azura No Snow Shader-52695-2-0-0-1652612215.zip.unfinished"
            )
            orphan.write_bytes(b"")

            entries = matchingPartialOrphanUnfinishedEntries(
                downloads,
                (52695, 283925),
                {(52695, 283925)},
            )

            self.assertEqual(entries, [])

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

    def test_can_remove_nonzero_orphan_unfinished_after_failed_callback(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            target = downloads / (
                "Trade Routes-12358-3-0-beta-3-1612588897.7z.unfinished"
            )
            target.write_bytes(b"partial")

            removed = removeOrphanUnfinishedDownloadsForKeys(
                downloads, {(12358, 1)}, include_nonzero=True
            )

            self.assertEqual(removed, 1)
            self.assertFalse(target.exists())

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


class StaleDownloadStartActionTests(unittest.TestCase):
    def test_retries_stale_zero_byte_start_before_restart_boundary(self):
        self.assertEqual(staleDownloadStartAction(0, 20), "retry")
        self.assertEqual(staleDownloadStartAction(19, 20), "retry")

    def test_retries_stale_already_started_before_restart_boundary(self):
        self.assertEqual(staleDownloadStartAction("2", "20"), "retry")

    def test_escalates_after_retry_budget_is_exhausted(self):
        self.assertEqual(staleDownloadStartAction(20, 20), "restart_required")
        self.assertEqual(staleDownloadStartAction(21, 20), "restart_required")


class ZeroByteDownloadStartIsStalledTests(unittest.TestCase):
    def test_keeps_fresh_placeholder_inside_grace_window(self):
        self.assertFalse(zeroByteDownloadStartIsStalled(100.0, 114.9, 15))

    def test_stalls_after_grace_window(self):
        self.assertTrue(zeroByteDownloadStartIsStalled(100.0, 115.0, 15))

    def test_invalid_timestamps_are_not_stalled(self):
        self.assertFalse(zeroByteDownloadStartIsStalled(None, 115.0, 15))


class StaleAlreadyStartedActionTests(unittest.TestCase):
    def test_waits_when_mo2_has_metadata_backed_unfinished_entry(self):
        self.assertEqual(staleAlreadyStartedAction(True), "wait")

    def test_escalates_when_already_started_has_no_metadata_entry(self):
        self.assertEqual(staleAlreadyStartedAction(False), "restart_required")


class DownloadTailBoundaryTests(unittest.TestCase):
    def test_waits_before_collection_is_mostly_settled(self):
        self.assertFalse(
            downloadTailBoundaryReached(
                total=100,
                successful=60,
                failed=5,
                unresolved=16,
                unresolved_limit=16,
                boundary_started_at=100,
                now=200,
                grace_seconds=20,
            )
        )

    def test_waits_before_tail_grace_expires(self):
        self.assertFalse(
            downloadTailBoundaryReached(100, 75, 0, 16, 16, 100, 119, 20)
        )

    def test_stops_tail_after_majority_and_grace(self):
        self.assertTrue(
            downloadTailBoundaryReached(100, 75, 0, 16, 16, 100, 120, 20)
        )

    def test_adaptive_grace_scales_but_stays_bounded(self):
        small = adaptiveDownloadTailGraceSeconds(50, 16, 0, 30)
        large = adaptiveDownloadTailGraceSeconds(800, 16, 2, 600)

        self.assertGreaterEqual(small, 20)
        self.assertGreater(large, small)
        self.assertLessEqual(large, 180)

    def test_progress_stall_waits_while_recent_progress_exists(self):
        self.assertFalse(downloadProgressIsStalled(100, 119, 20))

    def test_progress_stall_trips_after_quiet_window(self):
        self.assertTrue(downloadProgressIsStalled(100, 120, 20))


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


class RequiredFomodGroupTitleTests(unittest.TestCase):
    def test_accepts_required_singleton_group_names_seen_in_logs(self):
        for title in (
            "Main File",
            "Main Files (Required)",
            "Required Mods",
            "Base Plugin",
            "Bases",
            "Install",
            "Select texture size",
            "Texture Resolution",
        ):
            self.assertTrue(isRequiredFomodGroupTitle(title), title)

    def test_rejects_optional_patch_group_names_seen_in_logs(self):
        for title in (
            "Bowl Ingredients",
            "04 Option",
            "Xtra Options - Ultra-Sized Textures Addon",
            "Ropes 3D - Solitude Docks",
        ):
            self.assertFalse(isRequiredFomodGroupTitle(title), title)


class SafeSingletonFomodOptionTests(unittest.TestCase):
    def test_accepts_required_groups(self):
        self.assertTrue(
            isSafeSingletonFomodOption("Main File", "Playable Sun Elves Race")
        )
        self.assertTrue(isSafeSingletonFomodOption("Bases", "ESL Flagged Base"))

    def test_accepts_informational_singleton_actions_seen_in_logs(self):
        for group_title, option_label in (
            ("Inform", "Thank you!"),
            ("Note about config file", "Next"),
            ("Finish Installation", "Dont forget to check config.txt"),
            ("", "Start the installation"),
            ("Welcome", "Next"),
            ("Read first", "Proceed"),
            ("Do you know what you're doing?", "Proceed"),
            ("Quick notice.", "Okay!"),
            ("User information", "Ok"),
        ):
            self.assertTrue(
                isSafeSingletonFomodOption(group_title, option_label),
                (group_title, option_label),
            )

    def test_rejects_optional_patch_singletons_seen_in_logs(self):
        for group_title, option_label in (
            ("04 Option", "Wet Body (CMO) specular"),
            (
                "Axe - Warning! Missing Sounds! Replacer not XPMS(S)E style!",
                "Axes on Back",
            ),
            ("Gravity config", "Install Gravity Config"),
        ):
            self.assertFalse(
                isSafeSingletonFomodOption(group_title, option_label),
                (group_title, option_label),
            )


class ArchiveInspectionSubprocessKwargsTests(unittest.TestCase):
    def test_redirects_all_standard_handles_for_gui_processes(self):
        kwargs = archiveInspectionSubprocessKwargs(timeout=12)

        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(kwargs["timeout"], 12)

    def test_can_keep_extracted_xml_stdout_clean(self):
        kwargs = archiveInspectionSubprocessKwargs(stderr_to_stdout=False)

        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)


class SevenZipModuleConfigPathFromListingTests(unittest.TestCase):
    def test_finds_module_config_with_forward_slashes(self):
        listing = "\n".join(
            [
                "Path = docs/readme.txt",
                "Path = fomod/ModuleConfig.xml",
                "Size = 42",
            ]
        )

        self.assertEqual(
            sevenZipModuleConfigPathFromListing(listing),
            "fomod/ModuleConfig.xml",
        )

    def test_finds_module_config_with_backslashes_and_case(self):
        listing = "\n".join(
            [
                "Path = archive root",
                "Path = FOMOD\\MODULECONFIG.XML",
            ]
        )

        self.assertEqual(
            sevenZipModuleConfigPathFromListing(listing),
            "FOMOD/MODULECONFIG.XML",
        )

    def test_finds_module_config_from_subprocess_bytes(self):
        listing = b"Path = docs/readme.txt\nPath = fomod/ModuleConfig.xml\n"

        self.assertEqual(
            sevenZipModuleConfigPathFromListing(listing),
            "fomod/ModuleConfig.xml",
        )

    def test_returns_none_when_no_module_config_exists(self):
        self.assertIsNone(
            sevenZipModuleConfigPathFromListing("Path = textures/example.dds")
        )


class SevenZipArchiveMemberPathsTests(unittest.TestCase):
    def test_reads_member_paths_after_listing_separator(self):
        listing = "\n".join(
            [
                "Path = Example.7z",
                "Type = 7z",
                "----------",
                "Path = Example/meshes/road.nif",
                "Size = 4",
                "Path = Example/textures/road.dds",
                "Size = 7",
            ]
        )

        self.assertEqual(
            sevenZipArchiveMemberPaths(listing),
            ["Example/meshes/road.nif", "Example/textures/road.dds"],
        )

    def test_skips_directory_records_without_trailing_slashes(self):
        listing = "\n".join(
            [
                "Path = Example.rar",
                "Type = Rar5",
                "----------",
                "Path = Example/Data",
                "Folder = +",
                "Size = 0",
                "Path = Example/Data/sound/effect.wav",
                "Folder = -",
                "Size = 4",
            ]
        )

        self.assertEqual(
            sevenZipArchiveMemberPaths(listing),
            ["Example/Data/sound/effect.wav"],
        )

    def test_ignores_archive_header_path(self):
        self.assertEqual(
            sevenZipArchiveMemberPaths("Path = Archive.7z\nType = 7z\n"),
            [],
        )


class ZipArchiveMemberPathsTests(unittest.TestCase):
    def test_lists_zip_members_without_external_archive_worker(self):
        with TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "example.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("fomod/ModuleConfig.xml", "<config />")
                archive.writestr("Folder\\Nested.esp", "plugin")

            self.assertEqual(
                zipArchiveMemberPaths(archive_path),
                ["fomod/ModuleConfig.xml", "Folder/Nested.esp"],
            )


class HeadlessFomodDependencyInstallLayoutTests(unittest.TestCase):
    def test_selects_matching_select_any_patch_option(self):
        module_config = """\
<config>
  <installSteps>
    <installStep name="Select patches">
      <optionalFileGroups>
        <group name="Patches" type="SelectAny">
          <plugins>
            <plugin name="Patch for Alternate Start - Live Another Life">
              <files><folder source="00 Alternate start" destination="" /></files>
              <typeDescriptor><type name="Optional" /></typeDescriptor>
            </plugin>
            <plugin name="Patch for Cutting Room Floor">
              <files><folder source="01 Cutting Room Floor" destination="" /></files>
              <typeDescriptor><type name="Optional" /></typeDescriptor>
            </plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
  </installSteps>
</config>
"""

        plan = headlessFomodDependencyInstallLayout(
            module_config,
            "Landscape/fomod/ModuleConfig.xml",
            [
                "Landscape/00 Alternate start/example.esp",
                "Landscape/01 Cutting Room Floor/example.esp",
            ],
            ["Alternate Perspective - Alternate Start", "Cutting Room Floor.esp"],
        )

        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "dependency-selected FOMOD payload")
        self.assertEqual(plan["selected_options"], ["Patch for Cutting Room Floor"])
        self.assertEqual(
            plan["mappings"],
            [
                {
                    "type": "folder",
                    "source": "Landscape/01 Cutting Room Floor",
                    "destination": "",
                    "priority": "0",
                }
            ],
        )

    def test_selects_unique_best_select_at_most_one_patch_option(self):
        module_config = """\
<config>
  <installSteps>
    <installStep name="Select patches">
      <optionalFileGroups>
        <group name="Main Versions" type="SelectAtMostOne">
          <plugins>
            <plugin name="True Storms Pure">
              <files><folder source="03TrueStormsPure" destination="" /></files>
              <typeDescriptor><type name="Optional" /></typeDescriptor>
            </plugin>
            <plugin name="True Storms Pure - Water's Edge Fix">
              <files><folder source="06TrueStormsPure_WaterFix" destination="" /></files>
              <typeDescriptor><type name="Optional" /></typeDescriptor>
            </plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
  </installSteps>
</config>
"""

        plan = headlessFomodDependencyInstallLayout(
            module_config,
            "NAT - TrueStorms Merged Compatibility/FOMod/ModuleConfig.xml",
            [
                "NAT - TrueStorms Merged Compatibility/03TrueStormsPure/example.esp",
                "NAT - TrueStorms Merged Compatibility/06TrueStormsPure_WaterFix/example.esp",
            ],
            ["TrueStormsSE.esp", "Cathedral - Water.esp", "NAT.esp"],
        )

        self.assertTrue(plan["installable"])
        self.assertEqual(
            plan["selected_options"],
            ["True Storms Pure - Water's Edge Fix"],
        )
        self.assertEqual(
            plan["mappings"][0]["source"],
            "NAT - TrueStorms Merged Compatibility/06TrueStormsPure_WaterFix",
        )

    def test_refuses_ambiguous_single_choice_theme_picker(self):
        module_config = """\
<config>
  <installSteps>
    <installStep name="Pick your patch">
      <optionalFileGroups>
        <group name="Dear Diary" type="SelectExactlyOne">
          <plugins>
            <plugin name="Dear Diary Light Mode - Fixed Journal">
              <files><folder source="10_Dear Diary Light/Interface" destination="Interface" /></files>
              <typeDescriptor><type name="Optional" /></typeDescriptor>
            </plugin>
            <plugin name="Dear Diary Dark Mode - Fixed Journal">
              <files><folder source="11_Dear Diary Dark/Interface" destination="Interface" /></files>
              <typeDescriptor><type name="Optional" /></typeDescriptor>
            </plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
  </installSteps>
</config>
"""

        plan = headlessFomodDependencyInstallLayout(
            module_config,
            "fomod/ModuleConfig.xml",
            [
                "10_Dear Diary Light/Interface/quest_journal.swf",
                "11_Dear Diary Dark/Interface/quest_journal.swf",
            ],
            ["Dear Diary"],
        )

        self.assertFalse(plan["installable"])
        self.assertEqual(
            plan["reason"], "ambiguous FOMOD dependency choices: Dear Diary"
        )

    def test_moves_selected_folder_payload_to_requested_destination(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            extract_root = root / "extract"
            target = root / "target"
            source = extract_root / "Archive" / "Patch A"
            source.mkdir(parents=True)
            (source / "Example.esp").write_text("plugin", encoding="utf-8")
            (source / "meshes").mkdir()
            (source / "meshes" / "example.nif").write_text("mesh", encoding="utf-8")

            moved = moveHeadlessFomodSelectionPayload(
                extract_root,
                target,
                {
                    "fomod_selection": True,
                    "mappings": [
                        {
                            "type": "folder",
                            "source": "Archive/Patch A",
                            "destination": "",
                        }
                    ],
                },
            )

            self.assertEqual(moved, 2)
            self.assertTrue((target / "Example.esp").exists())
            self.assertTrue((target / "meshes" / "example.nif").exists())


class NativeArchiveWorkerTests(unittest.TestCase):
    def test_reports_missing_archive_path(self):
        from scripts.native_archive_worker import handle_request

        self.assertEqual(
            handle_request({"action": "list"}),
            {"ok": False, "error": "Request missing archive path."},
        )

    def test_finds_fomod_module_config_path(self):
        from scripts.native_archive_worker import seven_zip_module_config_path

        listing = "\n".join(
            [
                "Path = Example.7z",
                "Type = 7z",
                "----------",
                "Path = Wrapper/FOMOD\\MODULECONFIG.XML",
                "Size = 42",
            ]
        )

        self.assertEqual(
            seven_zip_module_config_path(listing),
            "Wrapper/FOMOD/MODULECONFIG.XML",
        )

    def test_processes_one_json_request(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            request_dir = tmp_path / "requests"
            result_path = tmp_path / "result.json"
            request_dir.mkdir()
            request_path = request_dir / "one.request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "action": "bad-action",
                        "archive": str(tmp_path / "archive.7z"),
                        "result_path": str(result_path),
                    }
                ),
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    "scripts/native_archive_worker.py",
                    str(request_dir),
                    "--once",
                ],
                cwd=Path(__file__).resolve().parent,
                check=True,
            )

            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertFalse(result["ok"])
            self.assertIn("Unsupported action", result["error"])
            self.assertFalse(request_path.exists())

    def test_once_mode_writes_worker_heartbeat(self):
        with TemporaryDirectory() as tmp:
            request_dir = Path(tmp) / "requests"

            subprocess.run(
                [
                    sys.executable,
                    "scripts/native_archive_worker.py",
                    str(request_dir),
                    "--once",
                ],
                cwd=Path(__file__).resolve().parent,
                check=True,
            )

            heartbeat = request_dir / "native-archive-worker.heartbeat.json"
            payload = json.loads(heartbeat.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])
            self.assertIn("time", payload)


class NativePathForArchiveInspectionTests(unittest.TestCase):
    def test_leaves_native_paths_unchanged(self):
        self.assertEqual(
            nativePathForArchiveInspection("/tmp/archive.7z"),
            "/tmp/archive.7z",
        )

    def test_maps_z_drive_to_native_root(self):
        self.assertEqual(
            nativePathForArchiveInspection(r"Z:\mnt\steam-library\archive.7z"),
            "/mnt/steam-library/archive.7z",
        )

    def test_maps_c_drive_through_wineprefix(self):
        self.assertEqual(
            nativePathForArchiveInspection(
                r"C:\users\steamuser\Downloads\archive.7z",
                wineprefix="/tmp/pfx",
            ),
            "/tmp/pfx/drive_c/users/steamuser/Downloads/archive.7z",
        )

    def test_maps_c_drive_through_wine_z_prefix(self):
        self.assertEqual(
            nativePathForArchiveInspection(
                r"C:\users\steamuser\Downloads\archive.7z",
                wineprefix=r"Z:\mnt\steam-library\SteamLibrary\steamapps\compatdata\489830\pfx",
            ),
            "/mnt/steam-library/SteamLibrary/steamapps/compatdata/489830/pfx/"
            "drive_c/users/steamuser/Downloads/archive.7z",
        )

    def test_maps_c_drive_through_steam_compat_data_path(self):
        with mock.patch.dict(
            "os.environ",
            {"STEAM_COMPAT_DATA_PATH": "/tmp/compatdata/489830"},
            clear=True,
        ):
            self.assertEqual(
                nativePathForArchiveInspection(
                    r"C:\users\steamuser\Downloads\archive.7z"
                ),
                "/tmp/compatdata/489830/pfx/drive_c/users/steamuser/Downloads/archive.7z",
            )


class FomodManualChoiceGuideTests(unittest.TestCase):
    def test_lists_required_groups_without_defaults(self):
        guide = fomodManualChoiceGuide(
            """
            <config>
              <installSteps>
                <installStep name="Game">
                  <optionalFileGroups>
                    <group name="Game Version" type="SelectExactlyOne">
                      <plugins>
                        <plugin name="Skyrim Special Edition" />
                        <plugin name="Skyrim VR" />
                      </plugins>
                    </group>
                  </optionalFileGroups>
                </installStep>
              </installSteps>
            </config>
            """
        )

        self.assertIsNone(guide["parse_error"])
        self.assertEqual(guide["safe_singleton_prompts"], [])
        self.assertEqual(
            guide["manual_choices"],
            [
                {
                    "step": "Game",
                    "group": "Game Version",
                    "type": "SelectExactlyOne",
                    "options": ["Skyrim Special Edition", "Skyrim VR"],
                }
            ],
        )

    def test_omits_groups_with_defaults(self):
        guide = fomodManualChoiceGuide(
            """
            <config>
              <installSteps>
                <installStep name="Plugin">
                  <optionalFileGroups>
                    <group name="Plugin Type" type="SelectExactlyOne">
                      <plugins>
                        <plugin name="ESP" default="true" />
                        <plugin name="ESL" />
                      </plugins>
                    </group>
                  </optionalFileGroups>
                </installStep>
              </installSteps>
            </config>
            """
        )

        self.assertEqual(guide["manual_choices"], [])
        self.assertEqual(guide["safe_singleton_prompts"], [])

    def test_separates_safe_singleton_prompts_from_manual_choices(self):
        guide = fomodManualChoiceGuide(
            """
            <config>
              <installSteps>
                <installStep name="Intro">
                  <optionalFileGroups>
                    <group name="Read first" type="SelectExactlyOne">
                      <plugins>
                        <plugin name="Proceed" />
                      </plugins>
                    </group>
                  </optionalFileGroups>
                </installStep>
              </installSteps>
            </config>
            """
        )

        self.assertEqual(guide["manual_choices"], [])
        self.assertEqual(
            guide["safe_singleton_prompts"][0]["options"],
            ["Proceed"],
        )


class InvalidInstallContentDialogActionTests(unittest.TestCase):
    def test_accepts_mo2_invalid_content_dialog(self):
        self.assertEqual(
            invalidInstallContentDialogAction(
                "Install Mods",
                ["The content of <data> does not look valid."],
                [("OK", True), ("Cancel", True)],
            ),
            "ok",
        )

    def test_ignores_valid_install_dialog(self):
        self.assertIsNone(
            invalidInstallContentDialogAction(
                "Install Mods",
                ["Looks good."],
                [("OK", True), ("Cancel", True)],
            )
        )

    def test_ignores_other_dialog_titles(self):
        self.assertIsNone(
            invalidInstallContentDialogAction(
                "Quick Install",
                ["The content of <data> does not look valid."],
                [("OK", True), ("Cancel", True)],
            )
        )


class ContentTreeWarningDialogActionTests(unittest.TestCase):
    def test_accepts_mo2_content_tree_warning_dialog(self):
        self.assertEqual(
            contentTreeWarningDialogAction(
                "Continue?",
                [
                    "This mod was probably NOT set up correctly, most likely it will "
                    "NOT work. You should first correct the directory layout using "
                    "the content-tree."
                ],
                [("Ignore", True), ("Cancel", True)],
            ),
            "ignore",
        )

    def test_ignores_unrelated_continue_dialog(self):
        self.assertIsNone(
            contentTreeWarningDialogAction(
                "Continue?",
                ["Do you want to continue?"],
                [("Ignore", True), ("Cancel", True)],
            )
        )

    def test_requires_enabled_ignore_button(self):
        self.assertIsNone(
            contentTreeWarningDialogAction(
                "Continue?",
                [
                    "This mod was probably NOT set up correctly. Correct the "
                    "directory layout using the content-tree."
                ],
                [("Ignore", False), ("Cancel", True)],
            )
        )


class InstallNoResultReasonTests(unittest.TestCase):
    def test_classifies_invalid_content_cancellation_as_manual_root_install(self):
        self.assertEqual(
            installNoResultReason(True, []),
            "invalid install content warning accepted, but MO2 returned no installed mod",
        )

    def test_classifies_fomod_warning_as_manual_fomod_install(self):
        self.assertEqual(
            installNoResultReason(
                False,
                [
                    '[2026-07-25 W] [fomodinstallerdialog.cpp:967] Plugin "Main" requires selection'
                ],
            ),
            "MO2 FOMOD installer returned no installed mod; likely needs manual choices or unsupported default automation",
        )

    def test_classifies_unknown_no_result_generically(self):
        self.assertEqual(
            installNoResultReason(False, ["Some other warning"]),
            "MO2 installer returned no installed mod; likely cancelled, manual, or unsupported install",
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

    def test_declines_mo2_duplicate_download_prompt_without_cancel_button(self):
        self.assertEqual(
            duplicateDownloadPromptActionLabel(
                "Download again?",
                [("&Yes", True), ("&No", True)],
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

    def test_requires_yes_and_no_confirmation_buttons(self):
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

    def test_acknowledges_already_queued_download_prompt(self):
        self.assertEqual(
            duplicateDownloadPromptActionLabel("Already Queued", [("OK", True)]),
            "ok",
        )

    def test_ignores_disabled_already_started_prompt(self):
        self.assertIsNone(
            duplicateDownloadPromptActionLabel("Already Started", [("OK", False)])
        )

    def test_ignores_unrelated_ok_prompt(self):
        self.assertIsNone(duplicateDownloadPromptActionLabel("Error", [("OK", True)]))


class QuotaLimitTests(unittest.TestCase):
    def test_detects_common_quota_and_rate_limit_text(self):
        self.assertTrue(isQuotaLimitText("HTTP 429 Too Many Requests"))
        self.assertTrue(isQuotaLimitText("Nexus download limit reached"))
        self.assertTrue(isQuotaLimitText("daily quota exhausted"))

    def test_ignores_unrelated_failure_text(self):
        self.assertFalse(isQuotaLimitText("The requested file was not found"))
        self.assertFalse(
            isQuotaLimitText("API: Queued: 0 | Daily: 12939 | Hourly: 1657")
        )
        self.assertFalse(
            isQuotaLimitText(
                "Already Started\nThere is already a download started for this "
                "file.\nMod 2429:\tLanterns of Skyrim SE"
            )
        )

    def test_parses_visible_mo2_quota_status_counts(self):
        self.assertEqual(
            nexusQuotaRemainingFromText("API: Queued: 0 | Daily: 12939 | Hourly: 87"),
            {"hourly": 87, "daily": 12939},
        )

    def test_parses_quota_state_from_response_headers(self):
        self.assertEqual(
            nexusQuotaStateFromHeaders(
                {
                    "X-RateLimit-Hourly-Remaining": "49",
                    "X-RateLimit-Daily-Remaining": "1200",
                    "X-RateLimit-Reset": "1780000000",
                    "Retry-After": "90",
                    "Content-Type": "application/json",
                },
                status=429,
                now=1779999900,
            ),
            {
                "observed_at": 1779999900,
                "status": 429,
                "remaining": {"hourly": 49, "daily": 1200},
                "retry_after_seconds": 90,
                "reset_epoch": 1780000000,
                "headers": {
                    "x-ratelimit-hourly-remaining": "49",
                    "x-ratelimit-daily-remaining": "1200",
                    "x-ratelimit-reset": "1780000000",
                    "retry-after": "90",
                },
            },
        )

    def test_generic_quota_header_is_not_assumed_to_be_hourly(self):
        self.assertEqual(
            nexusQuotaStateFromHeaders(
                {"X-RateLimit-Remaining": "7"},
                status=200,
                now=1779999900,
            )["remaining"],
            {"generic": 7},
        )

    def test_proactive_quota_floor_pauses_before_exhaustion(self):
        self.assertEqual(
            proactiveQuotaStopMessage({"hourly": 87, "daily": 12939}, 100, 100),
            "Nexus hourly API quota is near the safety floor (87 remaining); "
            "downloads will resume automatically after reset.",
        )
        self.assertIsNone(
            proactiveQuotaStopMessage({"hourly": 1657, "daily": 12939}, 100, 100)
        )

    def test_parses_numeric_retry_after_seconds(self):
        self.assertEqual(retryAfterSeconds("120"), 120)

    def test_quota_message_uses_retry_after_header(self):
        self.assertEqual(
            quotaLimitMessage(
                429,
                {"Retry-After": "90"},
                "Too Many Requests",
            ),
            "Nexus quota/rate limit reached; retry after about 90 seconds.",
        )

    def test_quota_message_detects_limit_body_without_status(self):
        self.assertEqual(
            quotaLimitMessage(403, {}, "Hourly API usage limit reached."),
            "Nexus quota/rate limit reached; pause downloads and retry later.",
        )

    def test_quota_resume_delay_uses_retry_after_but_stays_bounded(self):
        self.assertEqual(
            quotaResumeDelaySeconds(
                "Nexus quota/rate limit reached; retry after about 90 seconds.",
                default_seconds=3600,
            ),
            90,
        )
        self.assertEqual(
            quotaResumeDelaySeconds("quota stop", default_seconds=3600),
            3600,
        )
        self.assertEqual(
            quotaResumeDelaySeconds(
                "quota stop",
                default_seconds=300,
                attempt=2,
                max_seconds=1800,
            ),
            1200,
        )
        self.assertEqual(
            quotaResumeDelaySeconds("retry after about 7200 seconds", 300, 0, 1800),
            1800,
        )


class DownloadPromptKeyFromLabelsTests(unittest.TestCase):
    def test_extracts_key_from_mo2_already_started_prompt_text(self):
        self.assertEqual(
            downloadPromptKeyFromLabels(
                [
                    "There is already a download started for this file.\n\n"
                    "Mod 68861:        NewFalmerStatueFixes\n"
                    "File 288970:      NewFalmerStatueFixes-68861-1-0-0.zip"
                ],
                {(68861, 288970)},
            ),
            (68861, 288970),
        )

    def test_ignores_prompt_key_outside_collection(self):
        self.assertIsNone(
            downloadPromptKeyFromLabels(
                ["Mod 68861: Name\nFile 288970: Archive.zip"],
                {(1, 2)},
            )
        )

    def test_ignores_prompt_without_mod_and_file_ids(self):
        self.assertIsNone(downloadPromptKeyFromLabels(["No Nexus IDs here"]))


class DownloadPromptKeyFromArchiveLabelsTests(unittest.TestCase):
    def test_extracts_key_from_duplicate_prompt_archive_name(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            metadata = downloads / "Royal Armory V2.2-6994-2-2.7z.meta"
            metadata.write_text(
                "[General]\nmodID=6994\nfileID=46583\n",
                encoding="utf-8",
            )

            self.assertEqual(
                downloadPromptKeyFromArchiveLabels(
                    [
                        'A file with the same name "Royal Armory V2.2-6994-2-2.7z" '
                        "has already been downloaded. Do you want to download it again?"
                    ],
                    downloads,
                    {(6994, 46583)},
                ),
                (6994, 46583),
            )

    def test_ignores_archive_prompt_key_outside_collection(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            metadata = downloads / "Royal Armory V2.2-6994-2-2.7z.meta"
            metadata.write_text(
                "[General]\nmodID=6994\nfileID=46583\n",
                encoding="utf-8",
            )

            self.assertIsNone(
                downloadPromptKeyFromArchiveLabels(
                    [
                        'A file with the same name "Royal Armory V2.2-6994-2-2.7z" exists.'
                    ],
                    downloads,
                    {(1, 2)},
                )
            )

    def test_ignores_archive_prompt_without_metadata(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(
                downloadPromptKeyFromArchiveLabels(
                    ['A file with the same name "Missing-1-2.7z" exists.'],
                    Path(tmp),
                    {(1, 2)},
                )
            )


class DuplicateDownloadPromptArchiveActionTests(unittest.TestCase):
    def test_declines_when_named_archive_is_complete(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            (
                downloads / "Relationship Dialogue Overhaul - RDO Final-1187-Final.7z"
            ).write_bytes(b"archive")

            self.assertEqual(
                duplicateDownloadPromptArchiveAction(
                    [
                        'A file with the same name "Relationship Dialogue Overhaul - RDO Final-1187-Final.7z" '
                        "has already been downloaded."
                    ],
                    downloads,
                ),
                "no",
            )

    def test_accepts_when_named_archive_is_missing(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(
                duplicateDownloadPromptArchiveAction(
                    [
                        'A file with the same name "Missing-1-2.7z" has already been downloaded.'
                    ],
                    Path(tmp),
                ),
                "yes",
            )

    def test_declines_prefixed_duplicate_when_base_archive_is_complete(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            (
                downloads / "Starting Outfit Suppressed-43967-1-20-1628312587.7z"
            ).write_bytes(b"archive")
            (
                downloads / "3_Starting Outfit Suppressed-43967-1-20-1628312587.7z"
            ).write_bytes(b"")

            self.assertEqual(
                duplicateDownloadPromptArchiveAction(
                    [
                        'A file with the same name "3_Starting Outfit Suppressed-43967-1-20-1628312587.7z" '
                        "has already been downloaded."
                    ],
                    downloads,
                ),
                "no",
            )

    def test_accepts_when_named_archive_has_unfinished_sibling(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Partial-1-2.7z"
            archive.write_bytes(b"archive")
            Path(str(archive) + ".unfinished").write_bytes(b"partial")

            self.assertEqual(
                duplicateDownloadPromptArchiveAction(
                    [
                        'A file with the same name "Partial-1-2.7z" has already been downloaded.'
                    ],
                    downloads,
                ),
                "yes",
            )

    def test_ignores_labels_without_archive_name(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(
                duplicateDownloadPromptArchiveAction(
                    ["No archive name here"], Path(tmp)
                )
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


class InstallSummaryAutoCloseTests(unittest.TestCase):
    def test_auto_closes_successful_automatic_install_summary(self):
        self.assertTrue(shouldAutoCloseInstallSummary(True, False, 0))

    def test_keeps_manual_install_summary_visible(self):
        self.assertFalse(shouldAutoCloseInstallSummary(False, False, 0))

    def test_keeps_failed_install_summary_visible_for_review(self):
        self.assertFalse(shouldAutoCloseInstallSummary(True, False, 1))

    def test_keeps_cancelled_install_summary_visible(self):
        self.assertFalse(shouldAutoCloseInstallSummary(True, True, 0))


class InstallPlanExecutionActionTests(unittest.TestCase):
    def test_already_complete_plan_fast_finishes_immediately(self):
        self.assertEqual(installPlanExecutionAction(True), "fast-finish")

    def test_plan_with_remaining_work_uses_normal_install_loop(self):
        self.assertEqual(installPlanExecutionAction(False), "install-next")


class FastFinishMetadataRepairKeysTests(unittest.TestCase):
    def test_includes_installed_entries_because_fast_finish_skips_sweep(self):
        self.assertEqual(
            fastFinishMetadataRepairKeys(
                [{"status": "installed", "install_key": (123, 456)}]
            ),
            {(123, 456)},
        )

    def test_returns_installed_and_root_entries_without_failed_entries(self):
        self.assertEqual(
            fastFinishMetadataRepairKeys(
                [
                    {"status": "installed", "install_key": (123, 456)},
                    {"status": "root", "install_key": (321, 654)},
                    {"status": "failed", "install_key": (999, 111)},
                ]
            ),
            {(123, 456), (321, 654)},
        )


class DownloadCompletionChoicesTests(unittest.TestCase):
    def test_collection_links_keep_partial_install_choice_available(self):
        policy = collectionLinkCompletionPolicy()
        choices = downloadCompletionChoices(
            {"successful": 552, "failed": 7, "has_failures": True},
            has_on_complete=policy["attach_install_callback"],
        )

        self.assertFalse(policy["prompt_after_download"])
        self.assertTrue(policy["close_on_success"])
        self.assertTrue(policy["auto_install_after_download_default"])
        self.assertTrue(choices["retry_visible"])
        self.assertTrue(choices["install_visible"])
        self.assertEqual(choices["install_label"], "Install Available")

    def test_restart_required_boundary_blocks_in_process_choices(self):
        self.assertEqual(
            downloadCompletionChoices(
                {"successful": 552, "failed": 7, "has_failures": True},
                has_on_complete=True,
                restart_required=True,
            ),
            {
                "retry_visible": False,
                "install_visible": False,
                "install_label": "Install Available",
                "fomod_defaults_visible": False,
            },
        )

    def test_quota_boundary_blocks_in_process_choices(self):
        self.assertEqual(
            downloadCompletionChoices(
                {"successful": 552, "failed": 7, "has_failures": True},
                has_on_complete=True,
                quota_limited=True,
            ),
            {
                "retry_visible": False,
                "install_visible": False,
                "install_label": "Install Available",
                "fomod_defaults_visible": False,
            },
        )

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
        self.assertEqual(sanitizeModName("New Statue. "), "New Statue")
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


class DetachedInstallCacheKeyFromPathTests(unittest.TestCase):
    def test_parses_windows_detached_cache_path(self):
        self.assertEqual(
            detachedInstallCacheKeyFromPath(
                "C:/users/steamuser/AppData/Local/ModOrganizer/Skyrim Special Edition - Test/"
                "nxm-collection-dl-install-cache/52648-216332-JK MistveilKeep Sexframeworks Patch.rar"
            ),
            (52648, 216332),
        )

    def test_parses_backslash_path(self):
        self.assertEqual(
            detachedInstallCacheKeyFromPath(
                r"C:\MO2\nxm-collection-dl-install-cache\8834-133231-Blended Roads.7z"
            ),
            (8834, 133231),
        )

    def test_ignores_non_cache_names(self):
        self.assertIsNone(detachedInstallCacheKeyFromPath("Blended Roads.7z"))


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

    def test_missing_value_uses_explicit_default(self):
        self.assertTrue(coerceBoolSetting(None, default=True))


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

    def test_collection_install_defaults_match_verified_workflow(self):
        self.assertTrue(INSTALLER_SETTING_DEFAULTS["auto_accept_quick_install"])
        self.assertTrue(INSTALLER_SETTING_DEFAULTS["headless_archive_installs"])
        self.assertTrue(INSTALLER_SETTING_DEFAULTS["headless_zip_installs"])
        self.assertTrue(
            INSTALLER_SETTING_DEFAULTS["auto_dismiss_known_post_install_errors"]
        )
        self.assertTrue(
            INSTALLER_SETTING_DEFAULTS["auto_cancel_invalid_install_content"]
        )
        self.assertTrue(INSTALLER_SETTING_DEFAULTS["install_files_as_separate_mods"])
        self.assertTrue(INSTALLER_SETTING_DEFAULTS["activate_mods_after_install"])
        self.assertFalse(INSTALLER_SETTING_DEFAULTS["activate_mods_during_install"])
        self.assertTrue(INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"])
        self.assertEqual(INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_max_steps"], 80)
        self.assertFalse(INSTALLER_SETTING_DEFAULTS["trace_install_diagnostics"])


class CollectionInstallRouteTests(unittest.TestCase):
    def test_simple_zip_prefers_headless_install_route(self):
        self.assertEqual(
            collectionInstallRoute(
                "Example-1-2.zip",
                fomod_state=False,
                separate_file_installs=True,
            ),
            "headless-archive",
        )

    def test_unknown_archive_can_try_headless_after_layout_preflight(self):
        self.assertEqual(
            collectionInstallRoute(
                "Example-1-2.7z",
                fomod_state=None,
                separate_file_installs=True,
            ),
            "headless-archive",
        )

    def test_rar_can_try_headless_after_layout_preflight(self):
        self.assertEqual(
            collectionInstallRoute(
                "Example-1-2.rar",
                fomod_state=False,
                separate_file_installs=True,
            ),
            "headless-archive",
        )

    def test_confirmed_fomod_uses_mo2_installer(self):
        self.assertEqual(
            collectionInstallRoute(
                "Example-1-2.zip",
                fomod_state=True,
                separate_file_installs=True,
            ),
            "mo2",
        )

    def test_unsupported_extension_uses_mo2_installer(self):
        self.assertEqual(
            collectionInstallRoute(
                "Example-1-2.txt",
                fomod_state=False,
                separate_file_installs=True,
            ),
            "mo2",
        )

    def test_manual_retry_uses_mo2_installer(self):
        self.assertEqual(
            collectionInstallRoute(
                "Example-1-2.zip",
                fomod_state=False,
                separate_file_installs=True,
                manual_install_pass=True,
            ),
            "mo2",
        )

    def test_disabled_headless_archive_setting_uses_mo2_installer(self):
        self.assertEqual(
            collectionInstallRoute(
                "Example-1-2.7z",
                fomod_state=False,
                separate_file_installs=True,
                headless_archive_installs=False,
            ),
            "mo2",
        )

    def test_legacy_headless_zip_setting_still_disables_archive_route(self):
        self.assertEqual(
            collectionInstallRoute(
                "Example-1-2.7z",
                fomod_state=False,
                separate_file_installs=True,
                headless_archive_installs=True,
                headless_zip_installs=False,
            ),
            "mo2",
        )


class HeadlessArchivePreflightFallbackTests(unittest.TestCase):
    # These cases guard the main release invariant: safe archives install
    # headlessly, real FOMODs use MO2, and unknown failures do not open Quick
    # Install as accidental recovery.
    def test_confirmed_fomod_uses_mo2_installer(self):
        self.assertEqual(
            headlessArchivePreflightFallback(
                {"installable": False, "reason": "FOMOD installer present"}
            ),
            "mo2",
        )

    def test_ambiguous_layout_is_manual_not_quick_install(self):
        self.assertEqual(
            headlessArchivePreflightFallback(
                {"installable": False, "reason": "ambiguous archive layout"}
            ),
            "manual",
        )

    def test_missing_worker_is_manual_not_gui_fallback(self):
        self.assertEqual(
            headlessArchivePreflightFallback(
                {
                    "installable": False,
                    "reason": (
                        "Native archive worker timed out; start "
                        "scripts/native_archive_worker.py"
                    ),
                }
            ),
            "manual",
        )


class InvalidInstalledCollectionArchiveRetryTests(unittest.TestCase):
    def test_meta_only_invalid_container_has_no_payload_files(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Quest Journal Fixes"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")

            self.assertEqual(installedPayloadFileCount(mod_dir), 0)

    def test_nonempty_tool_container_has_payload_files(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Dynamic Interface Patcher"
            tool_dir = mod_dir / "DIP"
            tool_dir.mkdir(parents=True)
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (tool_dir / "dip.exe").write_text("tool", encoding="utf-8")

            self.assertEqual(installedPayloadFileCount(mod_dir), 1)
            self.assertTrue(installedModHasCompletionPayload(mod_dir))

    def test_empty_fomod_result_is_not_a_completed_install(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Opulent Thieves Guild Patch Collection"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")

            self.assertFalse(installedModHasCompletionPayload(mod_dir))

    def test_empty_fomod_result_does_not_queue_probe_retry(self):
        self.assertFalse(
            shouldQueueFomodProbeRetry(
                {
                    "fomod_state": "true",
                    "archive": "Quest Journal Fixes.7z",
                    "reason": (
                        "installer completed but produced an empty mod container; "
                        "review FOMOD/manual choices"
                    ),
                }
            )
        )

    def test_unresolved_fomod_failure_can_queue_probe_retry(self):
        self.assertTrue(
            shouldQueueFomodProbeRetry(
                {
                    "fomod_state": "true",
                    "archive": "Glorious Doors.7z",
                    "reason": "FOMOD requires manual choices before unattended install can continue",
                }
            )
        )

    def test_fomod_invalid_container_retries_with_native_installer(self):
        self.assertTrue(shouldRetryInvalidInstalledCollectionArchive(True))

    def test_safe_headless_layout_invalid_container_retries_headlessly(self):
        self.assertTrue(
            shouldRetryInvalidInstalledCollectionArchive(
                False,
                {"installable": True, "reason": "single wrapper folder"},
            )
        )

    def test_ambiguous_non_fomod_invalid_container_stays_disabled(self):
        self.assertFalse(
            shouldRetryInvalidInstalledCollectionArchive(
                False,
                {"installable": False, "reason": "ambiguous archive layout"},
            )
        )

    def test_unknown_fomod_state_does_not_retry_without_safe_layout(self):
        self.assertFalse(
            shouldRetryInvalidInstalledCollectionArchive(
                None,
                {"installable": False, "reason": "native archive worker timed out"},
            )
        )


class HeadlessZipInstallLayoutTests(unittest.TestCase):
    def test_accepts_direct_mod_root_layout(self):
        plan = headlessZipInstallLayout(
            ["meshes/road.nif", "textures/road.dds", "plugin.esp"]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["strip_prefix"], "")

    def test_strips_single_wrapper_folder(self):
        plan = headlessZipInstallLayout(
            ["Example Mod/meshes/road.nif", "Example Mod/textures/road.dds"]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["strip_prefix"], "Example Mod/")

    def test_strips_data_folder_wrapper(self):
        plan = headlessZipInstallLayout(
            ["Data/meshes/road.nif", "Data/textures/road.dds"]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["strip_prefix"], "Data/")

    def test_strips_data_folder_wrapper_with_root_readme(self):
        plan = headlessZipInstallLayout(
            [
                "Follower Readme.txt",
                "Data/SofiaFollower.bsa",
                "Data/SofiaFollower.esp",
            ]
        )

        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "data root layout")
        self.assertEqual(plan["strip_prefix"], "Data/")

    def test_strips_deep_single_common_wrapper_folder(self):
        plan = headlessZipInstallLayout(
            [
                "Neisa-1.6/Neisa Follower/Follower Neisa.esp",
                "Neisa-1.6/Neisa Follower/Meshes/Actors/Neisa/body.nif",
                "Neisa-1.6/Neisa Follower/Scripts/NeisaRestoreScript.pex",
            ]
        )

        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "single common wrapper folder")
        self.assertEqual(plan["strip_prefix"], "Neisa-1.6/Neisa Follower/")

    def test_strips_three_level_single_common_wrapper_folder(self):
        plan = headlessZipInstallLayout(
            [
                "OneanV1-3/Standalone Follower Onean SE-1-3/Standalone Follower Onean/Follower Onean.esp",
                "OneanV1-3/Standalone Follower Onean SE-1-3/Standalone Follower Onean/Meshes/actors/Onean/body.nif",
            ]
        )

        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "single common wrapper folder")
        self.assertEqual(
            plan["strip_prefix"],
            "OneanV1-3/Standalone Follower Onean SE-1-3/Standalone Follower Onean/",
        )

    def test_accepts_community_shaders_root(self):
        plan = headlessZipInstallLayout(
            [
                "Shaders/Features/CloudShadows.ini",
                "Shaders/CloudShadows/CloudShadows.hlsli",
            ]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "mod root layout")
        self.assertEqual(plan["strip_prefix"], "")

    def test_accepts_particle_light_root_with_preview_image(self):
        plan = headlessZipInstallLayout(
            [
                "before-after.png",
                "ParticleLights/candleglow01.ini",
            ]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "mod root layout")
        self.assertEqual(plan["strip_prefix"], "")

    def test_strips_lowercase_data_folder_wrapper(self):
        plan = headlessZipInstallLayout(
            ["data/meshes/road.nif", "data/textures/road.dds"]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["strip_prefix"], "data/")

    def test_strips_single_wrapper_lowercase_data_folder(self):
        plan = headlessZipInstallLayout(
            [
                "2-4k. New WispMother v2 nude/data/meshes/actor.nif",
                "2-4k. New WispMother v2 nude/data/textures/actor.dds",
            ]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "single wrapper Data folder")
        self.assertEqual(
            plan["strip_prefix"], "2-4k. New WispMother v2 nude/data/"
        )

    def test_strips_single_wrapper_data_folder_before_generic_wrapper(self):
        plan = headlessZipInstallLayout(
            [
                "2-4k. New Hagraven & Glenmoril Witch SE/Data/New Hagravens.esp",
                "2-4k. New Hagraven & Glenmoril Witch SE/Data/meshes/actor.nif",
                "2-4k. New Hagraven & Glenmoril Witch SE/Data/textures/actor.dds",
            ]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "single wrapper Data folder")
        self.assertEqual(
            plan["strip_prefix"],
            "2-4k. New Hagraven & Glenmoril Witch SE/Data/",
        )

    def test_accepts_single_wrapper_multiple_variant_data_roots(self):
        plan = headlessZipInstallLayout(
            [
                "Loadscreen New Female Giant/Loadscreen New Female Giant NUDE/Data/Meshes/loadscreenart/loadscreengiant01.nif",
                "Loadscreen New Female Giant/Loadscreen New Female Giant TOPLESS/DATA/Meshes/loadscreenart/loadscreengiant01.nif",
            ]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "single wrapper variant Data folder")
        self.assertEqual(
            plan["strip_prefix"],
            "Loadscreen New Female Giant/Loadscreen New Female Giant NUDE/Data/",
        )

    def test_rejects_fomod_installer_zip(self):
        plan = headlessZipInstallLayout(["fomod/ModuleConfig.xml", "meshes/road.nif"])
        self.assertFalse(plan["installable"])
        self.assertEqual(plan["reason"], "FOMOD installer present")

    def test_rejects_ambiguous_zip_without_mod_markers(self):
        plan = headlessZipInstallLayout(["readme.txt", "screenshots/shot.png"])
        self.assertFalse(plan["installable"])
        self.assertEqual(plan["reason"], "ambiguous archive layout")

    def test_rejects_zip_slip_member_targets(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(safeArchiveMemberTarget(Path(tmp), "../escape.txt"))
            self.assertIsNone(safeArchiveMemberTarget(Path(tmp), "/escape.txt"))
            self.assertIsNotNone(safeArchiveMemberTarget(Path(tmp), "meshes/safe.nif"))


class ExtractHeadlessZipArchiveTests(unittest.TestCase):
    def test_extracts_zip_with_wrapper_folder_stripped(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / "Example.zip"
            target_dir = tmp_path / "mod"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("Example/meshes/road.nif", b"mesh")
                archive.writestr("Example/textures/road.dds", b"texture")

            count = extractHeadlessZipArchive(
                archive_path,
                target_dir,
                {"strip_prefix": "Example/"},
            )

            self.assertEqual(count, 2)
            self.assertEqual((target_dir / "meshes/road.nif").read_bytes(), b"mesh")
            self.assertEqual(
                (target_dir / "textures/road.dds").read_bytes(), b"texture"
            )
            self.assertFalse((target_dir / "Example").exists())

    def test_rejects_unsafe_zip_member_during_extract(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / "Unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.txt", b"bad")

            with self.assertRaisesRegex(RuntimeError, "Unsafe archive member path"):
                extractHeadlessZipArchive(archive_path, tmp_path / "mod", {})

    def test_rejects_empty_zip_during_extract(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / "Empty.zip"
            with zipfile.ZipFile(archive_path, "w"):
                pass

            with self.assertRaisesRegex(RuntimeError, "no installable files"):
                extractHeadlessZipArchive(archive_path, tmp_path / "mod", {})

    def test_malformed_zip_raises_without_creating_output(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / "Broken.zip"
            archive_path.write_bytes(b"not a zip")
            target_dir = tmp_path / "mod"

            with self.assertRaises(zipfile.BadZipFile):
                extractHeadlessZipArchive(archive_path, target_dir, {})
            self.assertFalse(target_dir.exists())


class MoveHeadlessArchivePayloadTests(unittest.TestCase):
    def test_moves_preflighted_payload_without_wrapper_folder(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            extract_root = tmp_path / "extract"
            target_dir = tmp_path / "mod"
            (extract_root / "Example" / "meshes").mkdir(parents=True)
            (extract_root / "Example" / "textures").mkdir(parents=True)
            (extract_root / "Example" / "meshes" / "road.nif").write_bytes(b"mesh")
            (extract_root / "Example" / "textures" / "road.dds").write_bytes(b"texture")

            count = moveHeadlessArchivePayload(
                extract_root,
                target_dir,
                {"strip_prefix": "Example/"},
            )

            self.assertEqual(count, 2)
            self.assertEqual((target_dir / "meshes" / "road.nif").read_bytes(), b"mesh")
            self.assertEqual(
                (target_dir / "textures" / "road.dds").read_bytes(), b"texture"
            )
            self.assertFalse((target_dir / "Example").exists())


class HeadlessPayloadRootValidationTests(unittest.TestCase):
    def test_accepts_top_level_plugin_or_known_data_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            plugin_root.mkdir()
            (plugin_root / "Example.esp").write_text("", encoding="utf-8")
            self.assertTrue(headlessPayloadRootValid(plugin_root))

            mesh_root = root / "mesh"
            (mesh_root / "meshes").mkdir(parents=True)
            (mesh_root / "meshes" / "Example.nif").write_text("", encoding="utf-8")
            self.assertTrue(headlessPayloadRootValid(mesh_root))

            ini_root = root / "ini"
            ini_root.mkdir()
            (ini_root / "Example_DISTR.ini").write_text("", encoding="utf-8")
            self.assertTrue(headlessPayloadRootValid(ini_root))

            netscript_root = root / "netscript"
            (netscript_root / "NetScriptFramework" / "Plugins").mkdir(parents=True)
            (
                netscript_root / "NetScriptFramework" / "Plugins" / "GrassControl.dll"
            ).write_text("", encoding="utf-8")
            self.assertTrue(headlessPayloadRootValid(netscript_root))

            kreate_root = root / "kreate"
            (kreate_root / "KreatE" / "Presets" / "Aethos" / "CellLighting").mkdir(
                parents=True
            )
            (
                kreate_root
                / "KreatE"
                / "Presets"
                / "Aethos"
                / "CellLighting"
                / "Dragonsreach.ini"
            ).write_text("", encoding="utf-8")
            self.assertTrue(headlessPayloadRootValid(kreate_root))

    def test_rejects_plugin_hidden_under_unstripped_wrapper(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "Wrapper"
            wrapper.mkdir()
            (wrapper / "Example.esp").write_text("", encoding="utf-8")

            self.assertFalse(headlessPayloadRootValid(root))

    def test_repairs_single_valid_wrapper_payload(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "Glorious Doors of Skyrim (GDOS) - Update 1.04"
            wrapper.mkdir()
            (wrapper / "GDOS - Splendid Mechanized Dwemer Door.esp").write_text(
                "", encoding="utf-8"
            )
            (root / "meta.ini").write_text("[General]\n", encoding="utf-8")

            self.assertTrue(repairSingleWrapperPayload(root))
            self.assertTrue(
                (root / "GDOS - Splendid Mechanized Dwemer Door.esp").exists()
            )
            self.assertFalse(wrapper.exists())
            self.assertTrue(headlessPayloadRootValid(root))

    def test_repairs_nested_single_wrapper_payload(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "Outer" / "Inner"
            nested.mkdir(parents=True)
            (nested / "Example.esp").write_text("", encoding="utf-8")
            (root / "meta.ini").write_text("[General]\n", encoding="utf-8")

            self.assertTrue(repairSingleWrapperPayload(root))
            self.assertTrue((root / "Example.esp").exists())
            self.assertFalse((root / "Outer").exists())

    def test_repairs_single_wrapper_payload_without_overwriting_collection_metadata(
        self,
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "Wrapper"
            wrapper.mkdir()
            (wrapper / "meta.ini").write_text(
                "[General]\nversion=old\n", encoding="utf-8"
            )
            (wrapper / "SKSE").mkdir()
            (wrapper / "SKSE" / "Plugin.dll").write_text("", encoding="utf-8")
            (root / "meta.ini").write_text(
                "[General]\nversion=collection\n", encoding="utf-8"
            )

            self.assertTrue(repairSingleWrapperPayload(root))
            self.assertTrue((root / "SKSE" / "Plugin.dll").exists())
            self.assertEqual(
                (root / "meta.ini").read_text(encoding="utf-8"),
                "[General]\nversion=collection\n",
            )
            self.assertFalse(wrapper.exists())

    def test_does_not_repair_when_lift_would_overwrite_existing_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "Wrapper"
            wrapper.mkdir()
            (wrapper / "Example.esp").write_text("nested", encoding="utf-8")
            (root / "Example.esp").write_text("existing", encoding="utf-8")
            (root / "meta.ini").write_text("[General]\n", encoding="utf-8")

            self.assertFalse(repairSingleWrapperPayload(root))
            self.assertEqual(
                (root / "Example.esp").read_text(encoding="utf-8"), "existing"
            )
            self.assertTrue((wrapper / "Example.esp").exists())


class HeadlessInstallMetaIniTests(unittest.TestCase):
    def test_writes_resumable_local_nexus_identity_without_tracking(self):
        metadata = headlessInstallMetaIni(
            123,
            456,
            "Example-123-456.zip",
            "Example Mod",
            "Example File",
            "2026-07-28T12:00:00Z",
        )

        self.assertIn("modid=123", metadata)
        self.assertIn("installationFile=Example-123-456.zip", metadata)
        self.assertIn("tracked=0", metadata)
        self.assertIn("lastNexusQuery=2026-07-28T12:00:00Z", metadata)
        self.assertIn("1\\fileid=456", metadata)
        self.assertIn("installedBy=headless-archive", metadata)

    def test_preserves_collection_version_and_nexus_category(self):
        metadata = headlessInstallMetaIni(
            123,
            456,
            "Example-123-456.zip",
            "Example Mod",
            "Example File",
            "2026-07-28T12:00:00Z",
            file_version="1.2.3",
            mod_version="1.2",
            nexus_category=42,
        )

        self.assertIn("version=1.2.3", metadata)
        self.assertIn("newestVersion=1.2", metadata)
        self.assertIn("nexusCategory=42", metadata)
        self.assertIn('category="42,"', metadata)


class AutomatedInstallCadenceDefaultsTests(unittest.TestCase):
    def test_automated_install_cadence_stays_fast(self):
        self.assertLessEqual(
            AUTOMATED_INSTALL_CADENCE_DEFAULTS["next_mod_delay_ms"], 50
        )
        self.assertLessEqual(
            AUTOMATED_INSTALL_CADENCE_DEFAULTS["dialog_poll_initial_delay_ms"], 50
        )
        self.assertLessEqual(
            AUTOMATED_INSTALL_CADENCE_DEFAULTS["dialog_poll_interval_ms"], 50
        )
        self.assertLessEqual(
            AUTOMATED_INSTALL_CADENCE_DEFAULTS["fomod_advance_interval_ms"], 50
        )

    def test_automated_install_does_not_explicitly_steal_focus(self):
        self.assertFalse(
            AUTOMATED_INSTALL_CADENCE_DEFAULTS["restore_install_dialog_focus"]
        )
        self.assertFalse(
            AUTOMATED_INSTALL_CADENCE_DEFAULTS["raise_mo2_for_native_install"]
        )
        self.assertFalse(
            AUTOMATED_INSTALL_CADENCE_DEFAULTS["hide_progress_for_native_install"]
        )


class ShouldUseCollectionTargetModNameTests(unittest.TestCase):
    def test_separate_collection_install_forces_unique_target_names(self):
        self.assertTrue(
            shouldUseCollectionTargetModName(
                separate_file_installs=True,
                manual_install_pass=False,
            )
        )

    def test_manual_retry_uses_normal_mo2_installer_naming(self):
        self.assertFalse(
            shouldUseCollectionTargetModName(
                separate_file_installs=True,
                manual_install_pass=True,
            )
        )

    def test_merged_collection_install_uses_archive_default_naming(self):
        self.assertFalse(
            shouldUseCollectionTargetModName(
                separate_file_installs=False,
                manual_install_pass=False,
            )
        )


class ShouldUseArchiveDefaultForFomodCompatibilityTests(unittest.TestCase):
    def test_confirmed_fomod_uses_archive_default_for_compatibility(self):
        self.assertTrue(
            shouldUseArchiveDefaultForFomodCompatibility(
                separate_file_installs=True,
                manual_install_pass=False,
                fomod_state=True,
            )
        )

    def test_unknown_fomod_state_does_not_force_slow_fomod_compatibility_path(self):
        self.assertFalse(
            shouldUseArchiveDefaultForFomodCompatibility(
                separate_file_installs=True,
                manual_install_pass=False,
                fomod_state=None,
            )
        )

    def test_non_fomod_uses_collection_target_name(self):
        self.assertFalse(
            shouldUseArchiveDefaultForFomodCompatibility(
                separate_file_installs=True,
                manual_install_pass=False,
                fomod_state=False,
            )
        )

    def test_manual_retry_uses_normal_mo2_naming(self):
        self.assertFalse(
            shouldUseArchiveDefaultForFomodCompatibility(
                separate_file_installs=True,
                manual_install_pass=True,
                fomod_state=True,
            )
        )

    def test_merged_collection_install_uses_normal_mo2_naming(self):
        self.assertFalse(
            shouldUseArchiveDefaultForFomodCompatibility(
                separate_file_installs=False,
                manual_install_pass=False,
                fomod_state=True,
            )
        )


class ShouldPassTargetNameToInstallModTests(unittest.TestCase):
    def test_regular_separate_install_uses_collection_target_name(self):
        self.assertTrue(
            shouldPassTargetNameToInstallMod(
                separate_file_installs=True,
                manual_install_pass=False,
                normal_dialog_retry_pass=False,
                use_archive_default_for_fomod=False,
            )
        )

    def test_archive_default_policy_suppresses_target_name(self):
        self.assertFalse(
            shouldPassTargetNameToInstallMod(
                separate_file_installs=True,
                manual_install_pass=False,
                normal_dialog_retry_pass=False,
                use_archive_default_for_fomod=True,
            )
        )

    def test_manual_retry_does_not_force_collection_name(self):
        self.assertFalse(
            shouldPassTargetNameToInstallMod(
                separate_file_installs=True,
                manual_install_pass=True,
                normal_dialog_retry_pass=False,
                use_archive_default_for_fomod=False,
            )
        )


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

    def test_restart_required_progress_boundary_can_close(self):
        state = downloadProgressState(
            3,
            {(1, 1)},
            {(2, 2)},
            {(1, 1): 1, (2, 2): 1, (3, 3): 1},
        )

        self.assertFalse(state["is_terminal"])
        self.assertTrue(
            downloadProgressCanClose(
                True,
                state,
                restart_required=True,
            )
        )

    def test_active_nonterminal_progress_without_boundary_cannot_close(self):
        state = downloadProgressState(
            3,
            {(1, 1)},
            {(2, 2)},
            {(1, 1): 1, (2, 2): 1, (3, 3): 1},
        )

        self.assertFalse(downloadProgressCanClose(True, state))

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


class TerminalDownloadFailureDelayTests(unittest.TestCase):
    def test_delays_failed_terminal_state_for_late_mo2_prompts(self):
        self.assertTrue(
            shouldDelayTerminalDownloadFailure(
                has_failures=True,
                attempts=0,
                max_attempts=3,
            )
        )

    def test_stops_delaying_after_budget_is_exhausted(self):
        self.assertFalse(
            shouldDelayTerminalDownloadFailure(
                has_failures=True,
                attempts=3,
                max_attempts=3,
            )
        )

    def test_successful_terminal_state_is_not_delayed(self):
        self.assertFalse(
            shouldDelayTerminalDownloadFailure(
                has_failures=False,
                attempts=0,
                max_attempts=3,
            )
        )


class SteamMo2GuardAuditTests(unittest.TestCase):
    GOOD_LOCALCONFIG = """
    "UserLocalConfigStore"
    {
        "Software"
        {
            "Valve"
            {
                "Steam"
                {
                    "apps"
                    {
                        "489830"
                        {
                            "LaunchOptions" ""
                        }
                    }
                }
            }
        }
        "apps"
        {
                    "489830"
                    {
                    }
        }
    }
    """
    GOOD_STEAM_CONFIG = """
    "InstallConfigStore"
    {
        "Software"
        {
            "Valve"
            {
                "Steam"
                {
                    "DisableShaderCache" "1"
                    "ShaderCacheManager"
                    {
                        "ProcessingQueue" "123;456;"
                        "App"
                        {
                            "489830"
                            {
                                "ShaderCacheSize" "0"
                            }
                        }
                    }
                }
            }
        }
    }
    """
    GOOD_LOCK = "----i---------e------- /path/file"

    def guard(self, **overrides):
        values = {
            "localconfig_text": self.GOOD_LOCALCONFIG,
            "steam_config_text": self.GOOD_STEAM_CONFIG,
            "compat_text": '"489830" { "name" "Skyrim Special Edition" }',
            "appinfo_text": "SkyrimSELauncher.exe\0mo2-redirector.exe\0OPTION3\0Mod Organizer",
            "localconfig_lsattr": self.GOOD_LOCK,
            "steam_config_lsattr": self.GOOD_LOCK,
            "compat_lsattr": self.GOOD_LOCK,
            "appinfo_lsattr": self.GOOD_LOCK,
            "appmanifest_lsattr": self.GOOD_LOCK,
            "shadercache_lsattr": self.GOOD_LOCK,
            "mods_count": 0,
            "downloads_count": 0,
            "require_clean_mo2": True,
        }
        values.update(overrides)
        return steamMo2GuardAudit(**values)

    def test_parses_expected_steam_values(self):
        self.assertEqual(steamLaunchOptions(self.GOOD_LOCALCONFIG), "")
        self.assertIsNone(steamDefaultLaunchOption(self.GOOD_LOCALCONFIG))
        self.assertEqual(
            steamShaderProcessingQueue(self.GOOD_STEAM_CONFIG), ["123", "456"]
        )
        self.assertTrue(steamShaderCacheDisabled(self.GOOD_STEAM_CONFIG))
        self.assertEqual(steamAppShaderCacheSize(self.GOOD_STEAM_CONFIG), 0)
        self.assertTrue(
            steamAppInfoHasLaunchExecutable(
                "SkyrimSELauncher.exe\0mo2-redirector.exe\0OPTION3\0Mod Organizer",
                "mo2-redirector.exe",
            )
        )

    def test_clean_guard_state_passes(self):
        result = self.guard()

        self.assertTrue(result["ok"])
        self.assertEqual(result["problems"], [])

    def test_user_command_launch_option_is_caught(self):
        result = self.guard(
            localconfig_text='"489830" { "LaunchOptions" "USER=steamuser %command%" }'
        )

        self.assertFalse(result["ok"])
        self.assertTrue(any("bypasses MO2 redirector" in p for p in result["problems"]))

    def test_redirector_launch_option_argument_is_caught(self):
        result = self.guard(
            localconfig_text=(
                '"489830" { "LaunchOptions" "mo2-redirector.exe" } '
                '"apps" { "489830" { "DefaultLaunchOption" { "c0cebdd0" "3" } } }'
            )
        )

        self.assertFalse(result["ok"])
        self.assertTrue(any("launch option changed" in p for p in result["problems"]))

    def test_persisted_default_launch_option_is_caught(self):
        result = self.guard(
            localconfig_text=(
                '"489830" { "LaunchOptions" "" } '
                '"apps" { "489830" { "DefaultLaunchOption" { "c0cebdd0" "3" } } }'
            )
        )

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("default launch option changed" in p for p in result["problems"])
        )

    def test_expected_default_launch_option_can_be_required_explicitly(self):
        result = self.guard(
            localconfig_text=(
                '"489830" { "LaunchOptions" "" } '
                '"apps" { "489830" { "DefaultLaunchOption" { "c0cebdd0" "3" } } }'
            ),
            expected_default_launch_option="3",
        )

        self.assertTrue(result["ok"])

    def test_missing_appinfo_redirector_is_caught(self):
        result = self.guard(appinfo_text="SkyrimSELauncher.exe\0SkyrimSE.exe")

        self.assertFalse(result["ok"])
        self.assertTrue(any("appinfo.vdf" in p for p in result["problems"]))

    def test_latest_launch_log_uses_redirector_when_required(self):
        command = (
            "[2026-07-26 18:54:25] AppID 489830 adding PID 1 as a tracked process "
            '"/steam-wrapper -- proton waitforexitandrun '
            "'/mnt/steam-library/SteamLibrary/steamapps/common/Skyrim Special Edition/mo2-redirector.exe'\""
        )
        self.assertIn("mo2-redirector.exe", latestSteamLaunchCommand(command))

        result = self.guard(gameprocess_log_text=command, require_latest_launch=True)

        self.assertTrue(result["ok"])

    def test_latest_launcher_plus_redirector_argument_is_caught(self):
        command = (
            "[2026-07-26 20:25:56] AppID 489830 adding PID 1 as a tracked process "
            '"/steam-wrapper -- proton waitforexitandrun '
            "'/mnt/steam-library/SteamLibrary/steamapps/common/Skyrim Special Edition/SkyrimSELauncher.exe' "
            'mo2-redirector.exe"'
        )
        result = self.guard(gameprocess_log_text=command, require_latest_launch=True)

        self.assertFalse(result["ok"])
        self.assertTrue(any("SkyrimSELauncher.exe" in p for p in result["problems"]))

    def test_shader_processing_queue_requeue_is_caught(self):
        result = self.guard(
            steam_config_text='"DisableShaderCache" "1" "ProcessingQueue" "489830;123;"'
        )

        self.assertFalse(result["ok"])
        self.assertTrue(any("shader processing queue" in p for p in result["problems"]))

    def test_enabled_shader_cache_is_caught(self):
        result = self.guard(steam_config_text='"DisableShaderCache" "0"')

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("shader cache is not disabled" in p for p in result["problems"])
        )

    def test_nonzero_shader_cache_size_is_caught(self):
        result = self.guard(
            steam_config_text=(
                '"DisableShaderCache" "1" "489830" { "ShaderCacheSize" "2147483648" }'
            )
        )

        self.assertFalse(result["ok"])
        self.assertTrue(
            any(
                "ShaderCacheSize" in p or "shader cache size" in p
                for p in result["problems"]
            )
        )

    def test_missing_immutable_lock_is_caught(self):
        result = self.guard(localconfig_lsattr="--------------e------- /path/file")

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("localconfig.vdf is not immutable" in p for p in result["problems"])
        )

    def test_unlocked_steam_config_is_caught(self):
        result = self.guard(
            steam_config_lsattr="--------------e------- /path/config.vdf"
        )

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("config.vdf is not immutable" in p for p in result["problems"])
        )

    def test_unclean_mo2_state_is_caught_when_required(self):
        result = self.guard(mods_count=75, downloads_count=75)

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("managed mods directory is not clean" in p for p in result["problems"])
        )
        self.assertTrue(
            any("downloads directory is not clean" in p for p in result["problems"])
        )


if __name__ == "__main__":
    unittest.main()

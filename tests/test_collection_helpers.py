from pathlib import Path
import importlib.util
import json
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
import zipfile

from collection_helpers import (
    AUTOMATED_INSTALL_CADENCE_DEFAULTS,
    INSTALLER_SETTING_DEFAULTS,
    activeDownloadPromptKey,
    activeUnfinishedDownloadFingerprint,
    allocateUniqueModName,
    archiveInspectionSubprocessKwargs,
    auditMo2ProfileState,
    backgroundWorkerSubprocessKwargs,
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
    collectionPriorityOrderNeedsRepair,
    collectionRecoveryTargets,
    compareMo2ProfileStateSnapshots,
    contentTreeWarningDialogAction,
    collectionDownloadExpectedSizes,
    collectionInstallCompletedCount,
    dependencyIssueGuideLines,
    detachedInstallCacheKeyFromPath,
    downloadedArchiveNameKeys,
    duplicateDownloadPromptArchiveAction,
    duplicateDownloadPromptActionLabel,
    downloadMetadataReviewEntries,
    manualInstallGuidanceForReason,
    downloadCompletionChoices,
    downloadCompletionPlan,
    downloadProgressCanClose,
    downloadPromptKeyFromLabels,
    downloadPromptKeyFromArchiveLabels,
    downloadProgressFormat,
    downloadProgressState,
    downloadedFileKeys,
    extractHeadlessZipArchive,
    failedInstallReviewCategory,
    failedInstallReviewCategoryCounts,
    failedInstallReviewCategoryLabel,
    failedInstallReviewEntriesWithCategories,
    fastFinishMetadataRepairKeys,
    fomodDependencyOptionGuideLines,
    fomodManualChoiceGuide,
    gameRootFileEvidenceForCollectionEntry,
    headlessFomodDependencyInstallLayout,
    headlessArchivePreflightFallback,
    hasPartialUnfinishedEntries,
    headlessPayloadRootValid,
    headlessInstallMetaIni,
    headlessZipInstallLayout,
    inferModIdFromDownloadName,
    installedModRecordsFromDirectory,
    installedModCompletionIssueReason,
    installPlanExecutionAction,
    invalidInstalledCollectionPlanAction,
    invalidInstallContentDialogAction,
    installNoResultReason,
    installerDefaultActionLabel,
    installedModHasCompletionPayload,
    installedPayloadFileCount,
    knownPostInstallErrorDialogMessage,
    warningsAfterCleanInstallDiscard,
    isBenignEmptyFomodInstallerResult,
    isCollectionTransientModDirName,
    isRequiredFomodGroupTitle,
    isQuotaLimitText,
    isSafeSingletonFomodOption,
    isTransientManualFomodPlanFailure,
    matchingPartialOrphanUnfinishedEntries,
    mo2BaseModlistOrderNeedsRepair,
    moveHeadlessArchivePayload,
    moveModlistEntriesToUiBottom,
    nativeGameRootPathCandidate,
    nativeArchiveWorkerHeartbeatStatus,
    resetNativeArchiveWorkerTrackedProcess,
    nativePathForArchiveInspection,
    nexusQuotaRemainingFromText,
    nexusQuotaStateFromHeaders,
    normalizedButtonLabel,
    orphanUnfinishedDownloadEntries,
    parseCollectionAddress,
    pluginActivationReviewEntries,
    pluginCapacityAuditFromPluginsText,
    pluginMasterDependencyAudit,
    pluginMasterDependencyReviewEntries,
    pluginNotFoundNamesFromMessage,
    profileStateFileStats,
    profileStateSnapshotAuditSummary,
    pluginRepairFailureReviewEntries,
    popDownloadKey,
    preferredCanonicalDownloadArchive,
    preferredRequiredFomodFallbackOption,
    quarantineInvalidPayloadModContainers,
    removeOrphanUnfinishedEntries,
    removeOrphanUnfinishedDownloadsForKeys,
    removeUnfinishedEntries,
    repairDownloadMetadataInstalledFlags,
    repairInstalledCollectionModMetadata,
    repairMo2BaseModlistOrder,
    repairModlistEnabledStates,
    repairSingleWrapperPayload,
    restoreMo2ProfileStateSnapshot,
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
    shouldUseArchiveDefaultForFomodCompatibility,
    shouldUseCollectionTargetModName,
    snapshotMo2ProfileState,
    warningReportNeedsWrite,
    shouldDelayTerminalDownloadFailure,
    staleAlreadyStartedAction,
    adaptiveDownloadTailGraceSeconds,
    downloadProgressIsStalled,
    downloadTailLaggardPlan,
    downloadTailBoundaryArmTime,
    downloadTailBoundaryReached,
    staleOrphanUnfinishedDownloadEntries,
    staleDownloadStartAction,
    staleUnfinishedEntries,
    staleZeroByteUnfinishedEntries,
    steamGameRootFromMo2BasePath,
    suppressedPostInstallErrorReviewEntries,
    steamAppShaderCacheSize,
    steamDefaultLaunchOption,
    steamAppInfoHasLaunchExecutable,
    latestSteamLaunchCommand,
    steamLaunchOptions,
    steamMo2GuardAudit,
    steamShaderCacheDisabled,
    steamShaderProcessingQueue,
    topLevelDownloadMetadataAudit,
    unfinishedDownloadEntries,
    zeroByteDownloadStartIsStalled,
    zeroByteUnfinishedEntries,
)

_WORKER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "native_archive_worker.py"
)
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

    def test_repairs_unqueried_metadata_from_collection_file_name(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Faster HDT-SMP-57339-2-5-1-1728377043.7z"
            archive.write_bytes(b"archive")
            metadata = downloads / "Faster HDT-SMP-57339-2-5-1-1728377043.7z.meta"
            metadata.write_text(
                "[General]\r\ninstalled=true\r\nuninstalled=false\r\n",
                encoding="utf-8",
            )

            result = repairDownloadMetadataInstalledFlags(
                downloads,
                {(57339, 550156)},
                expected_file_names={(57339, 550156): "Faster HDT-SMP"},
            )

            repaired_metadata = metadata.read_text(encoding="utf-8")
            self.assertEqual(result["repaired"], 1)
            self.assertIn("modID=57339", repaired_metadata)
            self.assertIn("fileID=550156", repaired_metadata)
            self.assertIn("repository=Nexus", repaired_metadata)
            self.assertIn("installed=true", repaired_metadata)


class TopLevelDownloadMetadataAuditTests(unittest.TestCase):
    def test_ignores_nested_stale_metadata(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Installed-123-456.7z"
            archive.write_bytes(b"archive")
            (downloads / "Installed-123-456.7z.meta").write_text(
                "[General]\ninstalled=true\nuninstalled=false\n",
                encoding="utf-8",
            )
            stale = downloads / "_codex-stale"
            stale.mkdir()
            (stale / "Old-123-456.7z").write_bytes(b"archive")
            (stale / "Old-123-456.7z.meta").write_text(
                "[General]\ninstalled=false\n",
                encoding="utf-8",
            )

            audit = topLevelDownloadMetadataAudit(downloads)

            self.assertEqual(audit["checked"], 1)
            self.assertEqual(len(audit["installed"]), 1)
            self.assertEqual(audit["downloaded_only"], [])

    def test_reports_visible_downloaded_only_metadata_exactly(self):
        with TemporaryDirectory() as tmp:
            downloads = Path(tmp)
            archive = downloads / "Downloaded-123-456.7z"
            archive.write_bytes(b"archive")
            metadata = downloads / "Downloaded-123-456.7z.meta"
            metadata.write_text(
                "[General]\ninstalled=false\nuninstalled=false\n",
                encoding="utf-8",
            )

            audit = topLevelDownloadMetadataAudit(downloads)

            self.assertEqual(audit["downloaded_only"], [str(metadata)])


class DownloadMetadataReviewEntriesTests(unittest.TestCase):
    def test_builds_actionable_entries_for_dirty_metadata(self):
        entries = downloadMetadataReviewEntries(
            {
                "downloaded_only": ["/tmp/downloads/NeedsReview.7z.meta"],
                "missing_archive": ["/tmp/downloads/Missing.7z.meta"],
                "unknown_installed_state": ["/tmp/downloads/Unknown.7z.meta"],
            }
        )

        self.assertEqual(
            [entry["status"] for entry in entries],
            ["downloaded_only", "missing_archive", "unknown_installed_state"],
        )
        self.assertEqual(
            entries[0]["archive"], "/tmp/downloads/NeedsReview.7z"
        )
        self.assertIn("install/review", entries[0]["reason"])

    def test_empty_audit_has_no_review_entries(self):
        self.assertEqual(downloadMetadataReviewEntries({}), [])


class ManualInstallGuidanceForReasonTests(unittest.TestCase):
    def test_explains_ambiguous_archive_layout(self):
        guidance = manualInstallGuidanceForReason(
            "manual archive layout: ambiguous archive layout"
        )

        self.assertIn("content tree", guidance)
        self.assertIn("downloaded-only", guidance)

    def test_explains_invalid_game_data_container(self):
        guidance = manualInstallGuidanceForReason(
            "installed container has no valid game data"
        )

        self.assertIn("invalid MO2 container", guidance)

    def test_explains_downloaded_only_entries(self):
        guidance = manualInstallGuidanceForReason(
            "downloaded-only/no applicable files"
        )

        self.assertIn("downloaded-only", guidance)

    def test_explains_native_archive_worker_timeout(self):
        guidance = manualInstallGuidanceForReason(
            "Native archive worker timed out"
        )

        self.assertIn("native archive worker", guidance)

    def test_ignores_unrelated_reason(self):
        self.assertIsNone(manualInstallGuidanceForReason("other"))


class ProfileSnapshotTests(unittest.TestCase):
    def test_snapshots_profile_order_files_with_manifest(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text("+A\n-B\n", encoding="utf-8")
            (profile / "plugins.txt").write_text("*A.esp\nB.esp\n", encoding="utf-8")
            (profile / "loadorder.txt").write_text(
                "Skyrim.esm\nA.esp\n", encoding="utf-8"
            )
            downloads = base / "downloads"
            downloads.mkdir()
            (downloads / "Installed-1-2.7z").write_text("archive", encoding="utf-8")
            (downloads / "Installed-1-2.7z.meta").write_text(
                "[General]\ninstalled=true\n", encoding="utf-8"
            )
            (downloads / "NeedsReview-3-4.7z.meta").write_text(
                "[General]\ninstalled=false\n", encoding="utf-8"
            )

            snapshot_dir, manifest = snapshotMo2ProfileState(
                base_path=base,
                profile_name="Default",
                label="known good",
                timestamp="20260802-120000",
            )

            self.assertTrue((snapshot_dir / "modlist.txt").exists())
            self.assertTrue((snapshot_dir / "plugins.txt").exists())
            self.assertTrue((snapshot_dir / "loadorder.txt").exists())
            self.assertEqual(manifest["files"]["modlist.txt"]["enabled"], 1)
            self.assertEqual(manifest["files"]["modlist.txt"]["disabled"], 1)
            self.assertEqual(manifest["files"]["modlist.txt"]["bytes"], 6)
            self.assertEqual(
                manifest["files"]["modlist.txt"]["sha256"],
                "af9e5677f5ec28038d0c4b08954e5a6272932acc4ade9cb9ae5b2610600b8173",
            )
            self.assertEqual(
                manifest["download_metadata_audit"]["installed_count"], 1
            )
            self.assertEqual(
                manifest["download_metadata_audit"]["downloaded_only"],
                [str(downloads / "NeedsReview-3-4.7z.meta")],
            )
            self.assertEqual(
                manifest["download_metadata_audit"]["missing_archive"],
                [str(downloads / "NeedsReview-3-4.7z.meta")],
            )
            self.assertFalse(manifest["profile_audit"]["clean"])
            self.assertEqual(
                manifest["profile_audit"]["issues"],
                ["disabled_mods", "disabled_plugins", "download_metadata"],
            )
            self.assertEqual(manifest["profile_audit"]["disabled_mods_count"], 1)
            self.assertEqual(manifest["profile_audit"]["disabled_plugins_count"], 1)
            self.assertEqual(manifest["profile_audit"]["downloaded_only_count"], 1)
            saved_manifest = json.loads(
                (snapshot_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_manifest["label"], "known good")
            self.assertEqual(saved_manifest["profile_path"], str(profile))
            self.assertEqual(
                saved_manifest["files"]["modlist.txt"]["sha256"],
                manifest["files"]["modlist.txt"]["sha256"],
            )

    def test_restores_profile_snapshot_after_verifying_hashes(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text("+A\n-B\n", encoding="utf-8")
            (profile / "plugins.txt").write_text("*A.esp\n", encoding="utf-8")
            (profile / "loadorder.txt").write_text("A.esp\n", encoding="utf-8")
            snapshot_dir, _manifest = snapshotMo2ProfileState(
                base_path=base,
                profile_name="Default",
                label="known good",
                timestamp="20260802-120000",
            )

            (profile / "modlist.txt").write_text("-A\n+B\n", encoding="utf-8")
            (profile / "plugins.txt").write_text("# broken\n", encoding="utf-8")

            result = restoreMo2ProfileStateSnapshot(
                snapshot_dir,
                snapshot_root=base / "restore-backups",
                backup_label="before restore",
            )

            self.assertEqual(
                (profile / "modlist.txt").read_text(encoding="utf-8"),
                "+A\n-B\n",
            )
            self.assertEqual(
                sorted(result["restored"]),
                ["loadorder.txt", "modlist.txt", "plugins.txt"],
            )
            backup_dir = Path(result["backup_dir"])
            self.assertEqual(
                (backup_dir / "modlist.txt").read_text(encoding="utf-8"),
                "-A\n+B\n",
            )

    def test_restore_rejects_tampered_snapshot_file(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text("+A\n", encoding="utf-8")
            (profile / "plugins.txt").write_text("*A.esp\n", encoding="utf-8")
            (profile / "loadorder.txt").write_text("A.esp\n", encoding="utf-8")
            snapshot_dir, _manifest = snapshotMo2ProfileState(
                base_path=base,
                profile_name="Default",
                label="known good",
                timestamp="20260802-120000",
            )
            (snapshot_dir / "modlist.txt").write_text("+tampered\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                restoreMo2ProfileStateSnapshot(snapshot_dir)

    def test_compares_profile_snapshots(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text("+A\n", encoding="utf-8")
            (profile / "plugins.txt").write_text("*A.esp\n", encoding="utf-8")
            (profile / "loadorder.txt").write_text("A.esp\n", encoding="utf-8")
            downloads = base / "downloads"
            downloads.mkdir()
            (downloads / "Archive-1-2.7z").write_text("archive", encoding="utf-8")
            (downloads / "Archive-1-2.7z.meta").write_text(
                "[General]\ninstalled=true\n", encoding="utf-8"
            )

            first, _manifest = snapshotMo2ProfileState(
                base_path=base,
                timestamp="20260802-120000",
            )
            self.assertFalse(
                compareMo2ProfileStateSnapshots(first, first)["changed"]
            )

            (profile / "modlist.txt").write_text("+A\n+B\n", encoding="utf-8")
            second, _manifest = snapshotMo2ProfileState(
                base_path=base,
                timestamp="20260802-120001",
            )
            comparison = compareMo2ProfileStateSnapshots(first, second)
            self.assertTrue(comparison["changed"])
            self.assertEqual(comparison["changed_files"], ["modlist.txt"])
            self.assertFalse(comparison["download_metadata_changed"])

            (downloads / "NeedsReview-3-4.7z.meta").write_text(
                "[General]\ninstalled=false\n", encoding="utf-8"
            )
            third, _manifest = snapshotMo2ProfileState(
                base_path=base,
                timestamp="20260802-120002",
            )
            comparison = compareMo2ProfileStateSnapshots(second, third)
            self.assertTrue(comparison["changed"])
            self.assertEqual(comparison["changed_files"], [])
            self.assertTrue(comparison["download_metadata_changed"])
            self.assertTrue(comparison["profile_audit_changed"])

    def test_profile_snapshot_audit_flags_inverted_base_order(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text(
                "+Managed Mod\n"
                "+DLC: Dawnguard\n"
                "+Creation Club: ccbgssse001-fish\n",
                encoding="utf-8",
            )
            (profile / "plugins.txt").write_text("*Managed.esp\n", encoding="utf-8")

            files = {
                "modlist.txt": profileStateFileStats(profile / "modlist.txt"),
                "plugins.txt": profileStateFileStats(profile / "plugins.txt"),
            }
            summary = profileStateSnapshotAuditSummary(
                profile,
                base_path=base,
                files=files,
            )

            self.assertFalse(summary["clean"])
            self.assertTrue(summary["base_modlist_order_needs_repair"])
            self.assertIn("base_modlist_order", summary["issues"])


class PluginCapacityAuditTests(unittest.TestCase):
    def test_counts_enabled_regular_and_light_plugins_by_extension(self):
        audit = pluginCapacityAuditFromPluginsText(
            "# This file was automatically generated by Mod Organizer.\n"
            "*Skyrim.esm\n"
            "*Patch.esp\n"
            "*Small.esl\n"
            "Disabled.esp\n"
        )

        self.assertEqual(audit["enabled_count"], 3)
        self.assertEqual(audit["disabled_count"], 1)
        self.assertEqual(audit["regular_by_extension_count"], 2)
        self.assertEqual(audit["light_by_extension_count"], 1)
        self.assertFalse(audit["regular_limit_exceeded_by_extension"])

    def test_reports_regular_plugin_overage_by_extension(self):
        plugins_text = "\n".join(
            f"*Plugin{i:03d}.esp" for i in range(0xFD + 2)
        )

        audit = pluginCapacityAuditFromPluginsText(plugins_text)

        self.assertTrue(audit["regular_limit_exceeded_by_extension"])
        self.assertEqual(audit["regular_limit"], 0xFD)
        self.assertEqual(audit["regular_overage_by_extension"], 2)
        self.assertEqual(audit["regular_slots_remaining_by_extension"], 0)


class ProfileStateAuditTests(unittest.TestCase):
    def test_reports_clean_profile_state(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text(
                "# This file was automatically generated by Mod Organizer.\n"
                "+DLC: Dawnguard\n"
                "+DLC: HearthFires\n"
                "+DLC: Dragonborn\n"
                "+Creation Club: _ResourcePack\n"
                "+Managed Mod\n",
                encoding="utf-8",
            )
            (profile / "plugins.txt").write_text("*Managed.esp\n", encoding="utf-8")
            (profile / "loadorder.txt").write_text("Managed.esp\n", encoding="utf-8")
            downloads = base / "downloads"
            downloads.mkdir()
            (downloads / "Archive-1-2.7z").write_text("archive", encoding="utf-8")
            (downloads / "Archive-1-2.7z.meta").write_text(
                "[General]\ninstalled=true\n", encoding="utf-8"
            )

            result = auditMo2ProfileState(base_path=base)

            self.assertTrue(result["clean"])
            self.assertEqual(result["issues"], [])
            self.assertFalse(result["base_modlist_order_needs_repair"])
            self.assertEqual(result["download_metadata_audit"]["installed_count"], 1)
            self.assertEqual(
                result["plugin_capacity_audit"]["regular_by_extension_count"], 1
            )
            self.assertFalse(result["plugin_dependency_audit"]["supported"])
            self.assertEqual(result["plugin_dependency_audit"]["checked"], 0)

    def test_reports_dirty_profile_state(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text(
                "# This file was automatically generated by Mod Organizer.\n"
                "+Managed Mod\n"
                "+DLC: Dragonborn\n"
                "-Disabled Mod\n",
                encoding="utf-8",
            )
            (profile / "plugins.txt").write_text(
                "*Active.esp\nDisabled.esp\n", encoding="utf-8"
            )
            (profile / "loadorder.txt").write_text("Active.esp\n", encoding="utf-8")
            downloads = base / "downloads"
            downloads.mkdir()
            (downloads / "NeedsReview-3-4.7z").write_text(
                "archive", encoding="utf-8"
            )
            (downloads / "NeedsReview-3-4.7z.meta").write_text(
                "[General]\ninstalled=false\n", encoding="utf-8"
            )

            result = auditMo2ProfileState(base_path=base)

            self.assertFalse(result["clean"])
            self.assertTrue(result["base_modlist_order_needs_repair"])
            self.assertEqual(result["disabled_mods"], ["Disabled Mod"])
            self.assertEqual(result["disabled_plugins"], ["Disabled.esp"])
            self.assertEqual(
                [issue["type"] for issue in result["issues"]],
                [
                    "base_modlist_order",
                    "disabled_mods",
                    "disabled_plugins",
                    "download_metadata",
                ],
            )

    def test_reports_transient_and_invalid_active_mod_containers(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text(
                "+DLC: Dawnguard\n"
                "+DLC: HearthFires\n"
                "+DLC: Dragonborn\n"
                "+Valid Mod\n"
                "+Empty Mod\n"
                "+Nested Invalid Mod\n",
                encoding="utf-8",
            )
            (profile / "plugins.txt").write_text("*Valid.esp\n", encoding="utf-8")
            (profile / "loadorder.txt").write_text("Valid.esp\n", encoding="utf-8")
            mods = base / "mods"
            (mods / ".nxm-collection-extracting-leftover").mkdir(parents=True)
            valid = mods / "Valid Mod"
            valid.mkdir()
            (valid / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (valid / "meshes").mkdir()
            (valid / "meshes" / "valid.nif").write_text("nif", encoding="utf-8")
            empty = mods / "Empty Mod"
            empty.mkdir()
            (empty / "meta.ini").write_text("[General]\n", encoding="utf-8")
            nested = mods / "Nested Invalid Mod"
            (nested / "bad-wrapper").mkdir(parents=True)
            (nested / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (nested / "bad-wrapper" / "readme.txt").write_text(
                "not game data", encoding="utf-8"
            )

            result = auditMo2ProfileState(base_path=base)

            self.assertFalse(result["clean"])
            self.assertEqual(
                result["transient_mod_dirs"],
                [".nxm-collection-extracting-leftover"],
            )
            self.assertEqual(
                [item["name"] for item in result["invalid_active_mod_containers"]],
                ["Empty Mod", "Nested Invalid Mod"],
            )
            self.assertEqual(
                [issue["type"] for issue in result["issues"]],
                ["transient_mod_dirs", "invalid_active_mod_containers"],
            )

    def test_reports_plugin_capacity_warning(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / "profiles" / "Default"
            profile.mkdir(parents=True)
            (profile / "modlist.txt").write_text(
                "+DLC: Dawnguard\n+DLC: HearthFires\n+DLC: Dragonborn\n",
                encoding="utf-8",
            )
            (profile / "plugins.txt").write_text(
                "\n".join(f"*Plugin{i:03d}.esp" for i in range(0xFD + 1)),
                encoding="utf-8",
            )
            (profile / "loadorder.txt").write_text("", encoding="utf-8")

            result = auditMo2ProfileState(base_path=base)

            self.assertTrue(result["clean"])
            self.assertEqual(result["plugin_capacity_audit"]["regular_limit"], 0xFD)
            self.assertEqual(
                result["plugin_capacity_audit"]["regular_overage_by_extension"], 1
            )
            self.assertIn(
                "plugin_capacity_by_extension",
                [warning["type"] for warning in result["warnings"]],
            )


class InstalledCollectionMetadataRepairTests(unittest.TestCase):
    def test_repairs_manifest_version_and_nexus_category_without_local_category(self):
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
                            "mod": {"version": "1.2", "category": 42},
                        }
                    }
                },
            )

            repaired = metadata.read_text(encoding="utf-8")
            self.assertEqual(result["repaired"], 1)
            self.assertIn("version=1.2.3", repaired)
            self.assertIn("newestVersion=1.2", repaired)
            self.assertIn("nexusCategory=42", repaired)
            self.assertIn('category="42,"', repaired)

    def test_backs_up_meta_ini_before_repair(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            mod_dir = mods / "Example Mod"
            mod_dir.mkdir(parents=True)
            metadata = mod_dir / "meta.ini"
            original = (
                "[General]\n"
                "modid=123\n"
                "version=\n"
                "newestVersion=\n"
                "\n"
                "[installedFiles]\n"
                "size=1\n"
                "1\\modid=123\n"
                "1\\fileid=456\n"
            )
            metadata.write_text(original, encoding="utf-8")
            backup_dir = root / "backups"

            result = repairInstalledCollectionModMetadata(
                mods,
                {(123, 456): ["Example Mod"]},
                {
                    (123, 456): {
                        "file": {
                            "version": "1.2.3",
                            "mod": {"version": "1.2.3", "category": "Combat"},
                        }
                    }
                },
                backup_dir=backup_dir,
            )

            self.assertEqual(result["repaired"], 1)
            self.assertEqual(result["backed_up"], 1)
            self.assertEqual(
                (backup_dir / "Example Mod" / "meta.ini").read_text(encoding="utf-8"),
                original,
            )


class CollectionInstallPostconditionAuditTests(unittest.TestCase):
    def write_mod_metadata(
        self, mods, name, mod_id=123, file_id=456, valid_payload=True
    ):
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
        if valid_payload:
            plugin = mod_dir / "Example.esp"
            plugin.write_bytes(b"plugin")

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

    def test_fails_when_installed_metadata_points_to_invalid_payload(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            self.write_mod_metadata(mods, "Example Mod", valid_payload=False)
            self.write_download_metadata(downloads, installed="true")

            result = collectionInstallPostconditionAudit(
                downloads,
                mods,
                "# generated\n+Example Mod\n",
                expected_keys={(123, 456)},
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["invalid_payload_mods"], ["Example Mod"])
            self.assertEqual(result["missing_installs"], [(123, 456)])
            self.assertEqual(len(result["false_installed_download_metadata"]), 1)

    def test_invalid_payload_with_downloaded_only_metadata_still_needs_review(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            self.write_mod_metadata(mods, "Example Mod", valid_payload=False)
            self.write_download_metadata(downloads, installed="false")

            result = collectionInstallPostconditionAudit(
                downloads,
                mods,
                "# generated\n-Example Mod\n",
                expected_keys={(123, 456)},
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["invalid_payload_mods"], ["Example Mod"])
            self.assertEqual(result["false_installed_download_metadata"], [])

    def test_fails_when_missing_install_still_has_installed_download_metadata(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            self.write_download_metadata(downloads, installed="true")

            result = collectionInstallPostconditionAudit(
                downloads,
                mods,
                "# generated\n",
                expected_keys={(123, 456)},
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["missing_installs"], [(123, 456)])
            self.assertEqual(len(result["false_installed_download_metadata"]), 1)

    def test_handled_root_entry_allows_installed_download_metadata_without_container(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            downloads = root / "downloads"
            mods.mkdir()
            downloads.mkdir()
            self.write_download_metadata(downloads, installed="true")

            result = collectionInstallPostconditionAudit(
                downloads,
                mods,
                "# generated\n",
                expected_keys={(123, 456)},
                handled_keys={(123, 456)},
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["missing_installs"], [])
            self.assertEqual(result["false_installed_download_metadata"], [])

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
            (mod_dir / "Example.esp").write_bytes(b"plugin")
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
            (mod_dir / "Example.esp").write_bytes(b"plugin")

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
            (mod_dir / "FasterHDT.esp").write_bytes(b"plugin")

            self.assertEqual(
                installedModRecordsFromDirectory(
                    mods,
                    downloads,
                    expected_file_names={(57339, 550156): "Faster HDT-SMP"},
                ),
                {(57339, 550156): ["Faster HDT-SMP"]},
            )

    def test_flags_unqueried_download_metadata_for_installed_archive(self):
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
            (mod_dir / "FasterHDT.esp").write_bytes(b"plugin")
            archive = downloads / "Faster HDT-SMP-57339-2-5-1-1728377043.7z"
            archive.write_bytes(b"archive")
            metadata = downloads / "Faster HDT-SMP-57339-2-5-1-1728377043.7z.meta"
            metadata.write_text(
                "[General]\ninstalled=true\nuninstalled=false\n",
                encoding="utf-8",
            )

            result = collectionInstallPostconditionAudit(
                downloads,
                mods,
                "+Faster HDT-SMP\n",
                expected_keys={(57339, 550156)},
                expected_file_names={(57339, 550156): "Faster HDT-SMP"},
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["download_metadata_mismatches"], [str(metadata)])

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


class QuarantineInvalidPayloadModContainersTests(unittest.TestCase):
    def test_moves_invalid_payload_containers_out_of_active_mods(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            quarantine = root / "logs" / "invalid"
            bad_mod = mods / "Broken Mod"
            bad_mod.mkdir(parents=True)
            (bad_mod / "meta.ini").write_text("[General]\n", encoding="utf-8")

            result = quarantineInvalidPayloadModContainers(
                mods, ["Broken Mod"], quarantine
            )

            self.assertEqual(result["moved"], ["Broken Mod"])
            self.assertFalse(bad_mod.exists())
            self.assertTrue((quarantine / "Broken Mod" / "meta.ini").exists())

    def test_reports_missing_invalid_payload_containers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = quarantineInvalidPayloadModContainers(
                root / "mods", ["Missing Mod"], root / "invalid"
            )

            self.assertEqual(result["moved"], [])
            self.assertEqual(result["missing"], ["Missing Mod"])
            self.assertEqual(result["failed"], [])

    def test_uses_unique_quarantine_name_when_destination_exists(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods = root / "mods"
            quarantine = root / "invalid"
            (mods / "Broken Mod").mkdir(parents=True)
            (quarantine / "Broken Mod").mkdir(parents=True)

            result = quarantineInvalidPayloadModContainers(
                mods, ["Broken Mod"], quarantine
            )

            self.assertEqual(result["moved"], ["Broken Mod"])
            self.assertTrue((quarantine / "Broken Mod #2").exists())


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
                "Native archive worker unavailable after automatic launch; "
                "retry with the normal MO2 installer or review worker logs."
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


class MoveModlistEntriesToUiBottomTests(unittest.TestCase):
    def test_moves_entries_to_file_end_for_visible_ui_bottom(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            modlist = root / "modlist.txt"
            modlist.write_text(
                "# This file was automatically generated by Mod Organizer.\n"
                "+High Priority Existing\n"
                "+Middle Existing\n"
                "+New Mod A\n"
                "+Low Priority Existing\n"
                "+New Mod B\n",
                encoding="utf-8",
            )

            result = moveModlistEntriesToUiBottom(
                modlist, ["New Mod A", "New Mod B"]
            )

            self.assertEqual(result["moved"], 2)
            self.assertEqual(result["missing"], [])
            self.assertEqual(
                modlist.read_text(encoding="utf-8").splitlines(),
                [
                    "# This file was automatically generated by Mod Organizer.",
                    "+High Priority Existing",
                    "+Middle Existing",
                    "+Low Priority Existing",
                    "+New Mod A",
                    "+New Mod B",
                ],
            )

    def test_preserves_unmanaged_base_entries_before_moved_collection_entries(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            modlist = root / "modlist.txt"
            modlist.write_text(
                "# This file was automatically generated by Mod Organizer.\n"
                "+DLC: Dawnguard\n"
                "+DLC: HearthFires\n"
                "+DLC: Dragonborn\n"
                "+Creation Club: _ResourcePack\n"
                "+Creation Club: ccbgssse001-fish\n"
                "+Collection Mod A\n"
                "+Existing Mod\n"
                "+Collection Mod B\n",
                encoding="utf-8",
            )

            result = moveModlistEntriesToUiBottom(
                modlist, ["Collection Mod A", "Collection Mod B"]
            )

            self.assertEqual(result["moved"], 2)
            self.assertEqual(
                modlist.read_text(encoding="utf-8").splitlines(),
                [
                    "# This file was automatically generated by Mod Organizer.",
                    "+DLC: Dawnguard",
                    "+DLC: HearthFires",
                    "+DLC: Dragonborn",
                    "+Creation Club: _ResourcePack",
                    "+Creation Club: ccbgssse001-fish",
                    "+Existing Mod",
                    "+Collection Mod A",
                    "+Collection Mod B",
                ],
            )

    def test_restores_header_to_first_line_when_previous_edit_misplaced_it(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            modlist = root / "modlist.txt"
            modlist.write_text(
                "+New Mod A\n"
                "# This file was automatically generated by Mod Organizer.\n"
                "+Existing Mod\n",
                encoding="utf-8",
            )

            result = moveModlistEntriesToUiBottom(modlist, ["New Mod A"])

            self.assertEqual(result["moved"], 1)
            self.assertEqual(
                modlist.read_text(encoding="utf-8").splitlines(),
                [
                    "# This file was automatically generated by Mod Organizer.",
                    "+Existing Mod",
                    "+New Mod A",
                ],
            )

    def test_preserves_disabled_state_when_moving_to_bottom(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            modlist = root / "modlist.txt"
            modlist.write_text(
                "# This file was automatically generated by Mod Organizer.\n"
                "+Existing Mod\n"
                "-Rejected Collection Mod\n",
                encoding="utf-8",
            )

            result = moveModlistEntriesToUiBottom(
                modlist, ["Rejected Collection Mod"]
            )

            self.assertEqual(result["moved"], 1)
            self.assertEqual(
                modlist.read_text(encoding="utf-8").splitlines(),
                [
                    "# This file was automatically generated by Mod Organizer.",
                    "+Existing Mod",
                    "-Rejected Collection Mod",
                ],
            )

    def test_base_modlist_order_detects_managed_entries_before_unmanaged_base(self):
        modlist_text = (
            "# This file was automatically generated by Mod Organizer.\n"
            "+Managed Texture Pack\n"
            "+DLC: Dawnguard\n"
            "+DLC: HearthFires\n"
            "+DLC: Dragonborn\n"
            "+Creation Club: _ResourcePack\n"
        )

        self.assertTrue(mo2BaseModlistOrderNeedsRepair(modlist_text))

    def test_base_modlist_order_detects_inverted_dlc_entries(self):
        modlist_text = (
            "# This file was automatically generated by Mod Organizer.\n"
            "+DLC: Dragonborn\n"
            "+DLC: Dawnguard\n"
            "+Creation Club: _ResourcePack\n"
            "+Managed Texture Pack\n"
        )

        self.assertTrue(mo2BaseModlistOrderNeedsRepair(modlist_text))

    def test_base_modlist_order_accepts_dlc_then_creation_club_then_managed(self):
        modlist_text = (
            "# This file was automatically generated by Mod Organizer.\n"
            "+DLC: Dawnguard\n"
            "+DLC: HearthFires\n"
            "+DLC: Dragonborn\n"
            "+Creation Club: _ResourcePack\n"
            "+Creation Club: ccbgssse001-fish\n"
            "+Managed Texture Pack\n"
        )

        self.assertFalse(mo2BaseModlistOrderNeedsRepair(modlist_text))

    def test_repairs_base_modlist_order_before_managed_entries(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup = root / "backup"
            modlist = root / "modlist.txt"
            modlist.write_text(
                "# This file was automatically generated by Mod Organizer.\n"
                "+Managed Texture Pack\n"
                "+Creation Club: ccbgssse001-fish\n"
                "+DLC: Dragonborn\n"
                "+DLC: Dawnguard\n"
                "+Creation Club: _ResourcePack\n",
                encoding="utf-8",
            )

            result = repairMo2BaseModlistOrder(modlist, backup_dir=backup)

            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["moved"], 4)
            self.assertEqual(
                modlist.read_text(encoding="utf-8").splitlines(),
                [
                    "# This file was automatically generated by Mod Organizer.",
                    "+DLC: Dawnguard",
                    "+DLC: Dragonborn",
                    "+Creation Club: ccbgssse001-fish",
                    "+Creation Club: _ResourcePack",
                    "+Managed Texture Pack",
                ],
            )
            self.assertIn(
                "+Managed Texture Pack",
                (backup / "modlist.txt").read_text(encoding="utf-8"),
            )

    def test_base_modlist_order_repair_preserves_enabled_states(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            modlist = root / "modlist.txt"
            modlist.write_text(
                "# This file was automatically generated by Mod Organizer.\n"
                "+Managed Texture Pack\n"
                "-Creation Club: ccbgssse001-fish\n"
                "+DLC: Dragonborn\n",
                encoding="utf-8",
            )

            result = repairMo2BaseModlistOrder(modlist)

            self.assertEqual(result["failed"], 0)
            self.assertEqual(
                modlist.read_text(encoding="utf-8").splitlines(),
                [
                    "# This file was automatically generated by Mod Organizer.",
                    "+DLC: Dragonborn",
                    "-Creation Club: ccbgssse001-fish",
                    "+Managed Texture Pack",
                ],
            )

    def test_shared_modlist_backup_keeps_original_before_second_repair(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup = root / "backup"
            modlist = root / "modlist.txt"
            original = (
                "# This file was automatically generated by Mod Organizer.\n"
                "+Collection Mod A\n"
                "+DLC: Dawnguard\n"
                "+Creation Club: _ResourcePack\n"
                "+Existing Mod\n"
            )
            modlist.write_text(original, encoding="utf-8")

            repairMo2BaseModlistOrder(modlist, backup_dir=backup)
            moveModlistEntriesToUiBottom(modlist, ["Collection Mod A"], backup_dir=backup)

            self.assertEqual(
                (backup / "modlist.txt").read_text(encoding="utf-8"),
                original,
            )


class CollectionPriorityOrderNeedsRepairTests(unittest.TestCase):
    def test_sorted_priorities_before_base_priority_still_need_repair(self):
        self.assertTrue(collectionPriorityOrderNeedsRepair([0, 1, 2], 717))

    def test_single_priority_before_base_priority_still_needs_repair(self):
        self.assertTrue(collectionPriorityOrderNeedsRepair([0], 717))

    def test_sorted_priorities_at_collection_tail_are_valid(self):
        self.assertFalse(collectionPriorityOrderNeedsRepair([717, 718, 719], 717))

    def test_unsorted_priorities_need_repair(self):
        self.assertTrue(collectionPriorityOrderNeedsRepair([719, 717, 718], 717))


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

    def test_collection_recovery_targets_ignore_invalid_payloads(self):
        with TemporaryDirectory() as tmp:
            mods_dir = Path(tmp)
            invalid_mod = mods_dir / "Invalid Patch"
            invalid_mod.mkdir()
            (invalid_mod / "meta.ini").write_text("[General]\n", encoding="utf-8")

            result = collectionRecoveryTargets(
                {(123, 456): ["Invalid Patch"]},
                expected_keys={(123, 456)},
                mods_dir=mods_dir,
            )

            self.assertEqual(result["installed_keys"], set())
            self.assertEqual(result["missing_keys"], {(123, 456)})
            self.assertEqual(result["mod_names"], [])

    def test_collection_recovery_targets_accept_valid_payloads(self):
        with TemporaryDirectory() as tmp:
            mods_dir = Path(tmp)
            valid_mod = mods_dir / "Valid Patch"
            scripts_dir = valid_mod / "scripts"
            scripts_dir.mkdir(parents=True)
            (valid_mod / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (scripts_dir / "example.pex").write_text("script", encoding="utf-8")

            result = collectionRecoveryTargets(
                {(123, 456): ["Valid Patch"]},
                expected_keys={(123, 456)},
                mods_dir=mods_dir,
            )

            self.assertEqual(result["installed_keys"], {(123, 456)})
            self.assertEqual(result["missing_keys"], set())
            self.assertEqual(result["mod_names"], ["Valid Patch"])

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

    def test_unfinished_activity_fingerprint_ignores_empty_entries(self):
        metadata_entries = {
            (111, 222): [
                {
                    "archive": Path("Empty.7z.unfinished"),
                    "archive_size": 0,
                    "mtime": 100,
                }
            ]
        }
        orphan_entries = [
            {
                "archive": Path("Orphan.7z.unfinished"),
                "archive_size": 0,
                "mtime": 101,
                "mod_id": 333,
            }
        ]

        self.assertEqual(
            activeUnfinishedDownloadFingerprint(metadata_entries, orphan_entries),
            (),
        )

    def test_unfinished_activity_fingerprint_tracks_pending_metadata(self):
        metadata_entries = {
            (111, 222): [
                {
                    "archive": Path("One.7z.unfinished"),
                    "archive_size": 128,
                    "mtime": 100,
                }
            ],
            (333, 444): [
                {
                    "archive": Path("Two.7z.unfinished"),
                    "archive_size": 256,
                    "mtime": 101,
                }
            ],
        }

        self.assertEqual(
            activeUnfinishedDownloadFingerprint(
                metadata_entries,
                pending_keys={(333, 444)},
            ),
            (("metadata", 333, 444, 256, 101.0),),
        )

    def test_unfinished_activity_fingerprint_tracks_pending_orphans_by_mod(self):
        orphan_entries = [
            {
                "archive": Path("Wanted.7z.unfinished"),
                "archive_size": 128,
                "mtime": 100,
                "mod_id": 111,
            },
            {
                "archive": Path("Other.7z.unfinished"),
                "archive_size": 256,
                "mtime": 101,
                "mod_id": 333,
            },
            {
                "archive": Path("Unknown.7z.unfinished"),
                "archive_size": 512,
                "mtime": 102,
            },
        ]

        self.assertEqual(
            activeUnfinishedDownloadFingerprint(
                {},
                orphan_entries,
                pending_keys={(111, 222)},
            ),
            (("orphan", 111, 128, 100.0),),
        )

    def test_unfinished_activity_fingerprint_changes_with_size_or_mtime(self):
        first = activeUnfinishedDownloadFingerprint(
            {
                (111, 222): [
                    {
                        "archive": Path("One.7z.unfinished"),
                        "archive_size": 128,
                        "mtime": 100,
                    }
                ]
            }
        )
        second = activeUnfinishedDownloadFingerprint(
            {
                (111, 222): [
                    {
                        "archive": Path("One.7z.unfinished"),
                        "archive_size": 256,
                        "mtime": 101,
                    }
                ]
            }
        )

        self.assertNotEqual(first, second)

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

    def test_stops_small_collection_tail_after_adaptive_threshold(self):
        self.assertTrue(
            downloadTailBoundaryReached(18, 14, 0, 4, 16, 100, 160, 60)
        )

    def test_waits_below_small_collection_adaptive_tail_threshold(self):
        self.assertFalse(
            downloadTailBoundaryReached(18, 15, 0, 3, 16, 100, 200, 60)
        )

    def test_stops_single_laggard_when_queue_is_drained(self):
        self.assertTrue(
            downloadTailBoundaryReached(100, 99, 0, 1, 1, 100, 160, 60)
        )

    def test_waits_single_laggard_before_drained_queue_grace_expires(self):
        self.assertFalse(
            downloadTailBoundaryReached(100, 99, 0, 1, 1, 100, 159, 60)
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

    def test_tail_boundary_arm_time_uses_last_progress(self):
        self.assertEqual(downloadTailBoundaryArmTime(100, 150), 100)

    def test_tail_boundary_arm_time_uses_now_for_invalid_progress(self):
        self.assertEqual(downloadTailBoundaryArmTime(None, 150), 150)
        self.assertEqual(downloadTailBoundaryArmTime(0, 150), 150)

    def test_tail_boundary_arm_time_uses_now_for_future_progress(self):
        self.assertEqual(downloadTailBoundaryArmTime(200, 150), 150)


class DownloadTailLaggardPlanTests(unittest.TestCase):
    def test_retries_laggards_with_remaining_budget(self):
        plan = downloadTailLaggardPlan(
            {(1, 10), (2, 20)},
            {(1, 10): 0, (2, 20): 1},
            retry_budget=2,
        )

        self.assertEqual(plan["retry"], {(1, 10), (2, 20)})
        self.assertEqual(plan["restart_required"], set())

    def test_marks_exhausted_laggards_restart_required(self):
        plan = downloadTailLaggardPlan(
            {(1, 10), (2, 20)},
            {(1, 10): 2, (2, 20): 4},
            retry_budget=2,
        )

        self.assertEqual(plan["retry"], set())
        self.assertEqual(plan["restart_required"], {(1, 10), (2, 20)})

    def test_partitions_mixed_tail_by_attempt_budget(self):
        plan = downloadTailLaggardPlan(
            {(1, 10), (2, 20), (3, 30)},
            {(1, 10): 0, (2, 20): 2, (3, 30): "bad"},
            retry_budget=2,
        )

        self.assertEqual(plan["retry"], {(1, 10), (3, 30)})
        self.assertEqual(plan["restart_required"], {(2, 20)})

    def test_limits_retry_batch_to_lowest_progress_laggards(self):
        plan = downloadTailLaggardPlan(
            {(1, 10), (2, 20), (3, 30)},
            {(1, 10): 0, (2, 20): 0, (3, 30): 0},
            retry_budget=2,
            key_progress={(1, 10): 1000, (2, 20): 0, (3, 30): 20},
            max_retry_keys=2,
        )

        self.assertEqual(plan["retry"], {(2, 20), (3, 30)})
        self.assertEqual(plan["deferred"], {(1, 10)})
        self.assertEqual(plan["restart_required"], set())

    def test_batch_limit_does_not_defer_exhausted_laggards(self):
        plan = downloadTailLaggardPlan(
            {(1, 10), (2, 20), (3, 30)},
            {(1, 10): 2, (2, 20): 0, (3, 30): 0},
            retry_budget=2,
            key_progress={(1, 10): 0, (2, 20): 10, (3, 30): 20},
            max_retry_keys=1,
        )

        self.assertEqual(plan["retry"], {(2, 20)})
        self.assertEqual(plan["deferred"], {(3, 30)})
        self.assertEqual(plan["restart_required"], {(1, 10)})

    def test_invalid_retry_batch_limit_defers_retryable_laggards(self):
        plan = downloadTailLaggardPlan(
            {(1, 10), (2, 20)},
            {(1, 10): 0, (2, 20): 0},
            retry_budget=2,
            max_retry_keys="bad",
        )

        self.assertEqual(plan["retry"], set())
        self.assertEqual(plan["deferred"], {(1, 10), (2, 20)})
        self.assertEqual(plan["restart_required"], set())


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

    def test_prefers_safe_required_fallback_labels(self):
        self.assertEqual(
            preferredRequiredFomodFallbackOption(["Yes", "No"]),
            "No",
        )
        self.assertEqual(
            preferredRequiredFomodFallbackOption(["Fancy Patch", "None"]),
            "None",
        )

    def test_rejects_required_fallback_without_safe_label(self):
        self.assertIsNone(
            preferredRequiredFomodFallbackOption(["Red", "Blue", "Green"])
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

    def test_background_worker_redirects_handles_without_timeout(self):
        kwargs = backgroundWorkerSubprocessKwargs()

        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("timeout", kwargs)


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

    def test_skips_attribute_directory_records_without_trailing_slashes(self):
        listing = "\n".join(
            [
                "Path = Example.7z",
                "Type = 7z",
                "----------",
                "Path = Example",
                "Attributes = D",
                "Size = 0",
                "",
                "Path = Example/Data",
                "Attributes = D",
                "Size = 0",
                "",
                "Path = Example/Data/Example.esp",
                "Attributes = A",
                "Size = 4",
            ]
        )

        self.assertEqual(
            sevenZipArchiveMemberPaths(listing),
            ["Example/Data/Example.esp"],
        )

    def test_ignores_archive_header_path(self):
        self.assertEqual(
            sevenZipArchiveMemberPaths("Path = Archive.7z\nType = 7z\n"),
            [],
        )


class NativeArchiveWorkerTests(unittest.TestCase):
    def test_reports_missing_archive_path(self):
        from scripts.native_archive_worker import handle_request

        self.assertEqual(
            handle_request({"action": "list"}),
            {"ok": False, "error": "Request missing archive path."},
        )

    def test_reports_shutdown_request(self):
        from scripts.native_archive_worker import handle_request

        self.assertEqual(
            handle_request({"action": "shutdown"}),
            {"ok": True, "shutdown": True},
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

    def test_write_result_uses_temp_file_without_leaving_tmp_artifacts(self):
        from scripts.native_archive_worker import write_result

        with TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"

            write_result(result_path, {"ok": True, "value": 7})

            self.assertEqual(
                json.loads(result_path.read_text(encoding="utf-8")),
                {"ok": True, "value": 7},
            )
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

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
                cwd=Path(__file__).resolve().parent.parent,
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
                cwd=Path(__file__).resolve().parent.parent,
                check=True,
            )

            heartbeat = request_dir / "native-archive-worker.heartbeat.json"
            payload = json.loads(heartbeat.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])
            self.assertIn("time", payload)

    def test_shutdown_request_exits_running_worker(self):
        with TemporaryDirectory() as tmp:
            request_dir = Path(tmp) / "requests"
            request_dir.mkdir()
            process = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/native_archive_worker.py",
                    str(request_dir),
                    "--poll-ms",
                    "20",
                ],
                cwd=Path(__file__).resolve().parent.parent,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            try:
                heartbeat = request_dir / "native-archive-worker.heartbeat.json"
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and not heartbeat.exists():
                    time.sleep(0.02)
                self.assertTrue(heartbeat.exists())

                result_path = request_dir / "shutdown.result.json"
                request_path = request_dir / "shutdown.request.json"
                request_path.write_text(
                    json.dumps(
                        {
                            "action": "shutdown",
                            "result_path": str(result_path),
                        }
                    ),
                    encoding="utf-8",
                )

                process.communicate(timeout=5)
                result = json.loads(result_path.read_text(encoding="utf-8"))
                self.assertEqual(result, {"ok": True, "shutdown": True})
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)


class NativeArchiveWorkerHeartbeatStatusTests(unittest.TestCase):
    def test_accepts_current_worker_heartbeat(self):
        status = nativeArchiveWorkerHeartbeatStatus(
            {"ok": True, "pid": 123, "time": 100.0},
            now=101.0,
            max_age_seconds=30.0,
        )

        self.assertTrue(status["ok"])

    def test_rejects_stale_worker_heartbeat(self):
        status = nativeArchiveWorkerHeartbeatStatus(
            {"ok": True, "pid": 123, "time": 60.0},
            now=101.0,
            max_age_seconds=30.0,
        )

        self.assertFalse(status["ok"])
        self.assertIn("stale", status["reason"])

    def test_rejects_invalid_worker_heartbeat_time(self):
        status = nativeArchiveWorkerHeartbeatStatus(
            {"ok": True, "pid": 123, "time": "not-time"},
            now=101.0,
            max_age_seconds=30.0,
        )

        self.assertFalse(status["ok"])
        self.assertIn("invalid time", status["reason"])

    def test_rejects_tracked_worker_heartbeat_after_process_exit(self):
        status = nativeArchiveWorkerHeartbeatStatus(
            {"ok": True, "pid": 123, "time": 100.0},
            now=101.0,
            max_age_seconds=30.0,
            tracked_pid=123,
            tracked_exit_code=1,
        )

        self.assertFalse(status["ok"])
        self.assertIn("exited", status["reason"])

    def test_accepts_external_worker_heartbeat_with_different_pid(self):
        status = nativeArchiveWorkerHeartbeatStatus(
            {"ok": True, "pid": 456, "time": 100.0},
            now=101.0,
            max_age_seconds=30.0,
            tracked_pid=123,
            tracked_exit_code=1,
        )

        self.assertTrue(status["ok"])


class NativeArchiveWorkerTrackedProcessResetTests(unittest.TestCase):
    class FakeProcess:
        def __init__(self, poll_values, wait_raises_timeout=False):
            self.poll_values = list(poll_values)
            self.wait_raises_timeout = wait_raises_timeout
            self.terminated = False
            self.killed = False
            self.wait_calls = []

        def poll(self):
            if self.poll_values:
                return self.poll_values.pop(0)
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.wait_raises_timeout = False

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if self.wait_raises_timeout:
                raise subprocess.TimeoutExpired("worker", timeout)
            return 0

    def test_returns_clean_noop_for_missing_process(self):
        self.assertEqual(
            resetNativeArchiveWorkerTrackedProcess(None),
            {"terminated": False, "killed": False, "error": ""},
        )

    def test_leaves_already_exited_process_alone(self):
        process = self.FakeProcess([7])

        result = resetNativeArchiveWorkerTrackedProcess(process)

        self.assertFalse(result["terminated"])
        self.assertFalse(result["killed"])
        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)

    def test_terminates_running_process(self):
        process = self.FakeProcess([None])

        result = resetNativeArchiveWorkerTrackedProcess(process, timeout=0.1)

        self.assertTrue(result["terminated"])
        self.assertFalse(result["killed"])
        self.assertTrue(process.terminated)
        self.assertEqual(process.wait_calls, [0.1])

    def test_kills_process_when_terminate_wait_times_out(self):
        process = self.FakeProcess([None], wait_raises_timeout=True)

        result = resetNativeArchiveWorkerTrackedProcess(process, timeout=0.1)

        self.assertTrue(result["terminated"])
        self.assertTrue(result["killed"])
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(process.wait_calls, [0.1, 0.1])

    def test_reports_reset_errors(self):
        process = mock.Mock()
        process.poll.side_effect = RuntimeError("bad handle")

        result = resetNativeArchiveWorkerTrackedProcess(process)

        self.assertIn("bad handle", result["error"])


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


class FomodDependencyOptionGuideLinesTests(unittest.TestCase):
    def test_formats_skipped_dependency_options(self):
        lines = fomodDependencyOptionGuideLines(
            {
                "dependency_option_groups": [
                    {
                        "group": "Optional Patches",
                        "type": "SelectAny",
                        "options": [
                            {
                                "option": "Bruma Patch",
                                "plugin_type": "notusable",
                                "dependencies": [
                                    {"file": "BSHeartland.esm", "state": "Active"}
                                ],
                            }
                        ],
                    }
                ]
            }
        )

        self.assertEqual(
            lines,
            [
                "- FOMOD dependency group not auto-selected: "
                "`Optional Patches` (SelectAny)",
                "- Dependency options: "
                "`Bruma Patch` [notusable; BSHeartland.esm=Active]",
            ],
        )


class DependencyIssueGuideLinesTests(unittest.TestCase):
    def test_formats_structured_dependency_issues(self):
        lines = dependencyIssueGuideLines(
            {
                "dependency_issues": [
                    {"type": "missing_plugin", "name": "Missing.esp"},
                    {"type": "missing_master", "name": "Missing.esm"},
                    {"type": "inactive_master", "name": "Inactive.esm"},
                ]
            }
        )

        self.assertEqual(
            lines,
            [
                "- Dependency issues: `Missing.esp` (missing_plugin), "
                "`Missing.esm` (missing_master), "
                "`Inactive.esm` (inactive_master)"
            ],
        )


class HeadlessFomodDependencyInstallLayoutTests(unittest.TestCase):
    def test_reports_dependency_options_when_no_profile_evidence_matches(self):
        module_config = """\
<config>
  <installSteps>
    <installStep name="Patch">
      <optionalFileGroups>
        <group name="Optional Patches" type="SelectAny">
          <plugins>
            <plugin name="Missing Worldspace Patch">
              <files>
                <folder source="Missing Worldspace" destination="" />
              </files>
              <typeDescriptor>
                <dependencyType>
                  <defaultType name="NotUsable" />
                  <patterns>
                    <pattern>
                      <dependencies operator="And">
                        <fileDependency file="MissingWorldspace.esm" state="Active" />
                      </dependencies>
                      <type name="Optional" />
                    </pattern>
                  </patterns>
                </dependencyType>
              </typeDescriptor>
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
            ["Missing Worldspace/Missing Worldspace Patch.esp"],
            ["InstalledFollower.esp"],
        )

        self.assertFalse(plan["installable"])
        self.assertEqual(
            plan["reason"], "no FOMOD options matched installed profile evidence"
        )
        self.assertEqual(
            plan["dependency_option_groups"],
            [
                {
                    "group": "Optional Patches",
                    "type": "SelectAny",
                    "options": [
                        {
                            "option": "Missing Worldspace Patch",
                            "plugin_type": "notusable",
                            "dependencies": [
                                {
                                    "file": "MissingWorldspace.esm",
                                    "state": "Active",
                                }
                            ],
                            "payload_items": 1,
                            "matched_profile_evidence": False,
                        }
                    ],
                }
            ],
        )

    def test_reports_ambiguous_single_choice_candidates(self):
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
        self.assertEqual(
            [
                candidate["option"]
                for candidate in plan["ambiguous_dependency_groups"][0]["candidates"]
            ],
            [
                "Dear Diary Light Mode - Fixed Journal",
                "Dear Diary Dark Mode - Fixed Journal",
            ],
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


class KnownPostInstallErrorDialogMessageTests(unittest.TestCase):
    def test_extracts_plugin_not_found_names_from_message(self):
        self.assertEqual(
            pluginNotFoundNamesFromMessage(
                "Plugin not found: Missing.esp\nPlugin not found: missing.esp"
            ),
            ["Missing.esp"],
        )

    def test_captures_plugin_not_found_dialog_text(self):
        self.assertEqual(
            knownPostInstallErrorDialogMessage(
                ["Plugin not found: New Hagravens.esp", ""]
            ),
            "Plugin not found: New Hagravens.esp",
        )

    def test_captures_invalid_origin_dialog_text(self):
        self.assertEqual(
            knownPostInstallErrorDialogMessage(["invalid origin name: Example"]),
            "invalid origin name: Example",
        )

    def test_captures_secondary_process_error_dialog_text(self):
        self.assertEqual(
            knownPostInstallErrorDialogMessage(
                [
                    "failed to receive data from secondary process: Unknown error",
                    "",
                ]
            ),
            "failed to receive data from secondary process: Unknown error",
        )

    def test_ignores_unrelated_error_dialog_text(self):
        self.assertIsNone(
            knownPostInstallErrorDialogMessage(["unrelated transient warning"])
        )

    def test_suppressed_plugin_errors_become_review_entries(self):
        entries = suppressedPostInstallErrorReviewEntries(
            [
                {
                    "mod": "post-install activation",
                    "file": "",
                    "message": "Plugin not found: Missing.esp",
                    "category": "plugin_state_missing",
                    "source": "suppressed_dialog",
                }
            ]
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["mod"], "post-install activation")
        self.assertEqual(entries[0]["file"], "Missing.esp")
        self.assertEqual(entries[0]["missing_plugins"], ["Missing.esp"])
        self.assertEqual(
            entries[0]["dependency_issues"],
            [{"type": "missing_plugin", "name": "Missing.esp"}],
        )
        self.assertIn("install the mod", entries[0]["suggested_action"])
        self.assertIn("Plugin not found: Missing.esp", entries[0]["reason"])

    def test_interface_log_plugin_errors_become_review_entries(self):
        entries = suppressedPostInstallErrorReviewEntries(
            [
                {
                    "mod": "activating Example",
                    "file": "",
                    "message": "Plugin not found: MissingFromLog.esp",
                    "category": "plugin_state_missing",
                    "source": "interface_log",
                }
            ]
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["mod"], "activating Example")
        self.assertIn("Plugin not found: MissingFromLog.esp", entries[0]["reason"])

    def test_suppressed_secondary_process_errors_become_review_entries(self):
        entries = suppressedPostInstallErrorReviewEntries(
            [
                {
                    "mod": "post-install",
                    "file": "",
                    "message": (
                        "failed to receive data from secondary process: Unknown error"
                    ),
                    "category": "secondary_process_error",
                    "source": "suppressed_dialog",
                }
            ]
        )

        self.assertEqual(len(entries), 1)
        self.assertIn("secondary process", entries[0]["reason"])

    def test_suppressed_non_actionable_errors_do_not_block_summary(self):
        self.assertEqual(
            suppressedPostInstallErrorReviewEntries(
                [
                    {
                        "mod": "post-install",
                        "file": "",
                        "message": "failed to receive data",
                        "category": "other",
                        "source": "suppressed_dialog",
                    }
                ]
            ),
            [],
        )

    def test_clean_install_discard_preserves_suppressed_dialogs(self):
        warnings = [
            {
                "mod": "kept-before",
                "file": "",
                "message": "old warning",
                "normalized_message": "old warning",
                "category": "other",
                "occurrences": 1,
            },
            {
                "mod": "dropped-after",
                "file": "",
                "message": "transient warning",
                "normalized_message": "transient warning",
                "category": "other",
                "occurrences": 1,
            },
            {
                "mod": "plugin activation",
                "file": "",
                "message": "Plugin not found: BBLuxurySuite.esm",
                "normalized_message": "Plugin not found: BBLuxurySuite.esm",
                "category": "plugin_state_missing",
                "occurrences": 1,
                "source": "suppressed_dialog",
            },
        ]

        kept = warningsAfterCleanInstallDiscard(warnings, 1)

        self.assertEqual(
            [warning["message"] for warning in kept],
            ["old warning", "Plugin not found: BBLuxurySuite.esm"],
        )

    def test_clean_install_discard_preserves_interface_log_blockers(self):
        warnings = [
            {
                "mod": "kept-before",
                "file": "",
                "message": "old warning",
                "normalized_message": "old warning",
                "category": "other",
                "occurrences": 1,
            },
            {
                "mod": "dropped-after",
                "file": "",
                "message": "transient warning",
                "normalized_message": "transient warning",
                "category": "other",
                "occurrences": 1,
                "source": "interface_log",
            },
            {
                "mod": "activating Example",
                "file": "",
                "message": "Plugin not found: MissingFromLog.esp",
                "normalized_message": "Plugin not found: MissingFromLog.esp",
                "category": "plugin_state_missing",
                "occurrences": 1,
                "source": "interface_log",
            },
        ]

        kept = warningsAfterCleanInstallDiscard(warnings, 1)

        self.assertEqual(
            [warning["message"] for warning in kept],
            ["old warning", "Plugin not found: MissingFromLog.esp"],
        )


class PluginActivationReviewEntriesTests(unittest.TestCase):
    def test_missing_plugins_become_review_entries(self):
        entries = pluginActivationReviewEntries(["Missing.esp"])

        self.assertEqual(
            entries,
            [
                {
                    "mod": "plugin activation",
                    "file": "Missing.esp",
                    "mod_id": "unknown",
                    "file_id": "unknown",
                    "archive": "",
                    "missing_plugins": ["Missing.esp"],
                    "dependency_issues": [
                        {"type": "missing_plugin", "name": "Missing.esp"}
                    ],
                    "suggested_action": (
                        "install the mod or optional patch source that provides "
                        "Missing.esp; disable the dependent patch if that plugin "
                        "is not intended"
                    ),
                    "reason": (
                        "plugin not present in profile plugin list after refresh: "
                        "Missing.esp"
                    ),
                }
            ],
        )

    def test_deduplicates_missing_plugins_case_insensitively(self):
        entries = pluginActivationReviewEntries(
            ["Missing.esp", "missing.esp", "", None],
            source="post-install activation",
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["mod"], "post-install activation")
        self.assertEqual(entries[0]["file"], "Missing.esp")

    def test_ignores_empty_plugin_repair_failure_counts(self):
        self.assertEqual(pluginRepairFailureReviewEntries(0), [])
        self.assertEqual(pluginRepairFailureReviewEntries("not-a-count"), [])

    def test_plugin_repair_failures_become_review_entries(self):
        entries = pluginRepairFailureReviewEntries(
            "2", source="post-install activation"
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["mod"], "post-install activation")
        self.assertEqual(entries[0]["file"], "plugins.txt")
        self.assertIn("2 failure(s)", entries[0]["reason"])

    def test_master_dependency_audit_reports_missing_and_inactive_masters(self):
        problems = pluginMasterDependencyAudit(
            ["Patch.esp", "Other.esp"],
            ["Patch.esp", "Other.esp", "Inactive.esm", "Skyrim.esm"],
            ["Patch.esp", "Other.esp", "Skyrim.esm"],
            {
                "Patch.esp": ["Skyrim.esm", "Missing.esm", "Inactive.esm"],
                "Other.esp": ["Skyrim.esm"],
            },
        )

        self.assertEqual(
            problems,
            [
                {
                    "plugin": "Patch.esp",
                    "plugin_active": True,
                    "missing_masters": ["Missing.esm"],
                    "inactive_masters": ["Inactive.esm"],
                }
            ],
        )

    def test_master_dependency_audit_ignores_inactive_target_plugins(self):
        problems = pluginMasterDependencyAudit(
            ["Patch.esp"],
            ["Patch.esp"],
            [],
            {"Patch.esp": ["Missing.esm"]},
        )

        self.assertEqual(problems, [])

    def test_master_dependency_audit_can_report_inactive_target_plugins(self):
        problems = pluginMasterDependencyAudit(
            ["Patch.esp"],
            ["Patch.esp", "Inactive.esm"],
            [],
            {"Patch.esp": ["Missing.esm", "Inactive.esm"]},
            include_inactive_targets=True,
        )

        self.assertEqual(
            problems,
            [
                {
                    "plugin": "Patch.esp",
                    "plugin_active": False,
                    "missing_masters": ["Missing.esm"],
                    "inactive_masters": ["Inactive.esm"],
                }
            ],
        )

    def test_master_dependency_review_entries_deduplicate_plugins(self):
        entries = pluginMasterDependencyReviewEntries(
            [
                {
                    "plugin": "Patch.esp",
                    "plugin_active": False,
                    "missing_masters": ["Missing.esm"],
                    "inactive_masters": ["Inactive.esm"],
                },
                {
                    "plugin": "patch.esp",
                    "plugin_active": False,
                    "missing_masters": ["missing.esm"],
                    "inactive_masters": ["inactive.esm"],
                },
            ],
            source="post-install dependency audit",
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["mod"], "post-install dependency audit")
        self.assertEqual(entries[0]["file"], "Patch.esp")
        self.assertEqual(entries[0]["missing_masters"], ["Missing.esm"])
        self.assertEqual(entries[0]["inactive_masters"], ["Inactive.esm"])
        self.assertEqual(
            entries[0]["dependency_issues"],
            [
                {"type": "missing_master", "name": "Missing.esm"},
                {"type": "inactive_master", "name": "Inactive.esm"},
            ],
        )
        self.assertIn("enable Patch.esp", entries[0]["suggested_action"])
        self.assertIn("install the mod", entries[0]["suggested_action"])
        self.assertIn("enable Inactive.esm", entries[0]["suggested_action"])
        self.assertIn("disable Patch.esp", entries[0]["suggested_action"])
        self.assertIn("currently inactive", entries[0]["reason"])
        self.assertIn("missing Missing.esm", entries[0]["reason"])
        self.assertIn("inactive Inactive.esm", entries[0]["reason"])


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

    def test_keeps_recovery_success_summary_visible(self):
        self.assertFalse(shouldAutoCloseInstallSummary(True, False, 0, 1))

    def test_keeps_manual_install_summary_visible(self):
        self.assertFalse(shouldAutoCloseInstallSummary(False, False, 0))

    def test_keeps_failed_install_summary_visible_for_review(self):
        self.assertFalse(shouldAutoCloseInstallSummary(True, False, 1))

    def test_keeps_cancelled_install_summary_visible(self):
        self.assertFalse(shouldAutoCloseInstallSummary(True, True, 0))

    def test_keeps_no_applicable_fomod_summary_visible(self):
        self.assertFalse(shouldAutoCloseInstallSummary(True, False, 0, 0, 1))

    def test_keeps_dirty_download_metadata_summary_visible(self):
        self.assertFalse(shouldAutoCloseInstallSummary(True, False, 0, 0, 0, 1))


class CollectionInstallCompletedCountTests(unittest.TestCase):
    def test_no_applicable_entries_are_not_completed(self):
        self.assertEqual(
            collectionInstallCompletedCount(
                10,
                review_count=2,
                queued_recovery_count=1,
                no_applicable_count=3,
            ),
            4,
        )

    def test_completed_count_never_goes_negative(self):
        self.assertEqual(
            collectionInstallCompletedCount(
                2,
                review_count=2,
                queued_recovery_count=2,
                no_applicable_count=2,
            ),
            0,
        )


class CollectionTransientModDirNameTests(unittest.TestCase):
    def test_detects_plugin_owned_installing_and_extracting_dirs(self):
        self.assertTrue(
            isCollectionTransientModDirName(".nxm-collection-installing-Example")
        )
        self.assertTrue(
            isCollectionTransientModDirName(".nxm-collection-extracting-Example")
        )

    def test_does_not_match_normal_hidden_or_visible_mod_dirs(self):
        self.assertFalse(isCollectionTransientModDirName(".nxm-collection-cache"))
        self.assertFalse(isCollectionTransientModDirName("Example Collection Mod"))


class InstallPlanExecutionActionTests(unittest.TestCase):
    def test_already_complete_plan_fast_finishes_immediately(self):
        self.assertEqual(installPlanExecutionAction(True), "fast-finish")

    def test_plan_with_remaining_work_uses_normal_install_loop(self):
        self.assertEqual(installPlanExecutionAction(False), "install-next")


class FastFinishMetadataRepairKeysTests(unittest.TestCase):
    def test_excludes_installed_entries_because_final_sweep_validates_them(self):
        self.assertEqual(
            fastFinishMetadataRepairKeys(
                [{"status": "installed", "install_key": (123, 456)}]
            ),
            set(),
        )

    def test_returns_only_root_entries_without_failed_entries(self):
        self.assertEqual(
            fastFinishMetadataRepairKeys(
                [
                    {"status": "installed", "install_key": (123, 456)},
                    {"status": "root", "install_key": (321, 654)},
                    {"status": "failed", "install_key": (999, 111)},
                ]
            ),
            {(321, 654)},
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
                "install_visible": True,
                "install_label": "Install Available",
                "fomod_defaults_visible": True,
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


class InstallRuntimeSourceTests(unittest.TestCase):
    def install_source(self):
        return (Path(__file__).resolve().parents[1] / "install.py").read_text(
            encoding="utf-8"
        )

    def test_late_installer_recovery_records_suppressed_errors(self):
        source = self.install_source()

        self.assertIn("late-installer-recovery", source)
        self.assertIn("on_dismiss=", source)

    def test_install_trace_retry_receives_warning_context(self):
        source = self.install_source()
        method_source = source.split("def traceInstallModCall(", 1)[1].split(
            "\n    def callInstallMod(", 1
        )[0]

        self.assertIn("mod_name,", method_source)
        self.assertIn("file_name,", method_source)
        self.assertIn(
            "mod_name,\n        file_name,\n        allow_path_retry=True",
            method_source,
        )

    def test_invalid_plan_entries_are_downloaded_only_review_failures(self):
        source = self.install_source()
        plan_source = source.split("def prepareInstallPlan(", 1)[1].split(
            "\n    def fastFinishInstallPlan(", 1
        )[0]

        self.assertIn('installed_action == "review-invalid"', plan_source)
        self.assertIn("self.markDownloadedOnlyMetadata(context, install_key)", plan_source)
        self.assertIn("needs manual install", plan_source)
        self.assertNotIn("non-empty invalid kept installed", plan_source)

    def test_empty_invalid_plan_entries_are_not_forced_to_fomod(self):
        source = self.install_source()
        plan_source = source.split("def prepareInstallPlan(", 1)[1].split(
            "\n    def fastFinishInstallPlan(", 1
        )[0]

        self.assertNotIn("if invalid_installed_name:\n                fomod_state = True", plan_source)
        self.assertIn("self.archiveHasFomodInstaller(", plan_source)
        self.assertIn("shouldRetryInvalidInstalledCollectionArchive", plan_source)
        self.assertIn("needs manual install or content-tree review", plan_source)

    def test_invalid_payload_metadata_repairs_are_reported_separately(self):
        source = self.install_source()

        self.assertIn("invalid_payload_metadata_repair", source)
        self.assertIn('"invalid_payload_metadata_repaired"', source)
        self.assertIn('"invalid_payload_metadata_failed"', source)
        self.assertIn("Invalid payload metadata repairs", source)


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
                        "Native archive worker timed out after automatic launch; "
                        "retry with the normal MO2 installer or review worker logs."
                    ),
                }
            ),
            "manual",
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

    def test_ambiguous_layout_reports_diagnostics(self):
        plan = headlessZipInstallLayout(
            [
                "4k/Data/textures/example.dds",
                "2k/Data/textures/example.dds",
                "Preview/readme.txt",
            ]
        )

        self.assertFalse(plan["installable"])
        self.assertEqual(plan["reason"], "ambiguous archive layout")
        diagnostics = plan["diagnostics"]
        self.assertEqual(diagnostics["top_level_entries"], ["2k", "4k"])
        self.assertEqual(
            diagnostics["nested_data_candidates"],
            ["4k/Data/", "2k/Data/"],
        )
        self.assertIn("nested Data candidates", diagnostics["summary"])

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

    def test_strips_single_wrapper_data_folder_with_wrapper_docs(self):
        plan = headlessZipInstallLayout(
            [
                "CompanionArchive_Vanilla/Data/CompanionArchive.bsa",
                "CompanionArchive_Vanilla/Data/CompanionArchive.esp",
                "CompanionArchive_Vanilla/Data/Video/IntroScene.bik",
                "CompanionArchive_Vanilla/OutfitGuidePrintableImages.pdf",
                "CompanionArchive_Vanilla/Readme.txt",
                "CompanionArchive_Vanilla/WARDROBE MANUAL FOR BEGINNERS.doc",
            ]
        )
        self.assertTrue(plan["installable"])
        self.assertEqual(plan["reason"], "single wrapper Data folder")
        self.assertEqual(
            plan["strip_prefix"], "CompanionArchive_Vanilla/Data/"
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
        self.assertEqual(plan["reason"], "documentation-only archive")

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

    def test_moves_fomod_selected_file_by_unique_nested_basename(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            extract_root = tmp_path / "extract"
            target_dir = tmp_path / "mod"
            source = extract_root / "JK's Interiors Patch Collection" / "Elgrims"
            source.mkdir(parents=True)
            (source / "JKs Elgrims Elixirs - Bee and Barb Patch.esp").write_text(
                "plugin",
                encoding="utf-8",
            )

            count = moveHeadlessArchivePayload(
                extract_root,
                target_dir,
                {
                    "fomod_selection": True,
                    "mappings": [
                        {
                            "type": "file",
                            "source": "JKs Elgrims Elixirs - Bee and Barb Patch.esp",
                            "destination": "",
                        }
                    ],
                },
            )

            self.assertEqual(count, 1)
            self.assertTrue(
                (target_dir / "JKs Elgrims Elixirs - Bee and Barb Patch.esp").exists()
            )

    def test_refuses_ambiguous_fomod_nested_basename(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            extract_root = tmp_path / "extract"
            target_dir = tmp_path / "mod"
            for folder in ("Patch A", "Patch B"):
                source = extract_root / folder
                source.mkdir(parents=True)
                (source / "Example Patch.esp").write_text("plugin", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Ambiguous selected FOMOD"):
                moveHeadlessArchivePayload(
                    extract_root,
                    target_dir,
                    {
                        "fomod_selection": True,
                        "mappings": [
                            {
                                "type": "file",
                                "source": "Example Patch.esp",
                                "destination": "",
                            }
                        ],
                    },
                )


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


class InstalledPayloadCompletionTests(unittest.TestCase):
    def test_meta_only_mod_directory_is_empty_completion_payload(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Optional Patch Collection"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")

            self.assertEqual(installedPayloadFileCount(mod_dir), 0)
            self.assertFalse(installedModHasCompletionPayload(mod_dir))
            self.assertEqual(
                installedModCompletionIssueReason(mod_dir),
                "installer completed but produced an empty mod container",
            )

    def test_docs_only_mod_directory_is_invalid_completion_payload(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Patch Notes Only"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (mod_dir / "readme.txt").write_text("notes", encoding="utf-8")

            self.assertTrue(installedModHasCompletionPayload(mod_dir))
            self.assertEqual(
                installedModCompletionIssueReason(mod_dir),
                "installer completed but produced invalid MO2 game data",
            )

    def test_game_data_mod_directory_has_no_completion_issue(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Valid Mesh Mod"
            mesh_dir = mod_dir / "meshes"
            mesh_dir.mkdir(parents=True)
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (mesh_dir / "example.nif").write_text("mesh", encoding="utf-8")

            self.assertIsNone(installedModCompletionIssueReason(mod_dir))

    def test_invalid_installed_plan_action_accepts_valid_payload(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Valid Texture Mod"
            texture_dir = mod_dir / "textures"
            texture_dir.mkdir(parents=True)
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (texture_dir / "example.dds").write_text("texture", encoding="utf-8")

            self.assertEqual(
                invalidInstalledCollectionPlanAction(mod_dir, False),
                "installed",
            )

    def test_invalid_installed_plan_action_repairs_empty_with_archive(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Empty Installer Result"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")

            self.assertEqual(
                invalidInstalledCollectionPlanAction(mod_dir, True),
                "repair-empty",
            )

    def test_invalid_installed_plan_action_reviews_invalid_payload_with_archive(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Docs Only Installer Result"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")
            (mod_dir / "readme.txt").write_text("notes", encoding="utf-8")

            self.assertEqual(
                invalidInstalledCollectionPlanAction(mod_dir, True),
                "review-invalid",
            )

    def test_invalid_installed_plan_action_fails_without_archive(self):
        with TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "Empty Installer Result"
            mod_dir.mkdir()
            (mod_dir / "meta.ini").write_text("[General]\n", encoding="utf-8")

            self.assertEqual(
                invalidInstalledCollectionPlanAction(mod_dir, False),
                "fail-missing-archive",
            )

    def test_empty_fomod_without_required_choices_is_benign_noop(self):
        self.assertTrue(
            isBenignEmptyFomodInstallerResult(
                True,
                {
                    "module_config": "fomod/ModuleConfig.xml",
                    "manual_choices": [],
                    "safe_singleton_prompts": [],
                    "parse_error": None,
                    "error": None,
                },
            )
        )

    def test_empty_fomod_with_required_choices_is_not_benign(self):
        self.assertFalse(
            isBenignEmptyFomodInstallerResult(
                True,
                {
                    "module_config": "fomod/ModuleConfig.xml",
                    "manual_choices": [
                        {
                            "step": "Patches",
                            "group": "Do you use Example?",
                            "type": "SelectExactlyOne",
                            "options": ["Yes", "No"],
                        }
                    ],
                    "safe_singleton_prompts": [],
                    "parse_error": None,
                    "error": None,
                },
            )
        )

    def test_empty_fomod_with_unreadable_xml_is_not_benign(self):
        self.assertFalse(
            isBenignEmptyFomodInstallerResult(
                True,
                {
                    "module_config": None,
                    "manual_choices": [],
                    "safe_singleton_prompts": [],
                    "parse_error": "not well-formed",
                    "error": None,
                },
            )
        )


class FailedInstallReviewCategoryTests(unittest.TestCase):
    def test_classifies_missing_download_reasons(self):
        self.assertEqual(
            failedInstallReviewCategory("not found in downloads"),
            "missing_download",
        )
        self.assertEqual(
            failedInstallReviewCategory(
                "installed container has no valid game data and source archive is missing"
            ),
            "missing_download",
        )

    def test_classifies_manual_and_review_reasons(self):
        reasons = [
            "manual archive layout: ambiguous archive layout",
            "manual FOMOD choices required: pick one",
            "installed container has no usable payload; archive needs manual install or content-tree review",
            "installed container has no valid game data; source archive needs manual install",
        ]

        self.assertEqual(
            {failedInstallReviewCategory(reason) for reason in reasons},
            {"manual_or_review"},
        )

    def test_counts_review_categories(self):
        counts = failedInstallReviewCategoryCounts(
            [
                {"reason": "not found in downloads"},
                {"reason": "manual archive layout: ambiguous archive layout"},
                {"reason": "unexpected preflight failure"},
            ]
        )

        self.assertEqual(
            counts,
            {
                "missing_download": 1,
                "manual_or_review": 1,
                "other_failure": 1,
            },
        )

    def test_adds_review_category_without_mutating_original_entries(self):
        entries = [{"mod": "Example", "reason": "not found in downloads"}]

        categorized = failedInstallReviewEntriesWithCategories(entries)

        self.assertEqual(categorized[0]["review_category"], "missing_download")
        self.assertNotIn("review_category", entries[0])

    def test_returns_user_facing_category_labels(self):
        self.assertEqual(
            failedInstallReviewCategoryLabel("missing_download"),
            "Missing downloads",
        )
        self.assertEqual(
            failedInstallReviewCategoryLabel("manual_or_review"),
            "Manual install/review required",
        )
        self.assertEqual(
            failedInstallReviewCategoryLabel("unknown"),
            "Other failures",
        )


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


class WarningReportNeedsWriteTests(unittest.TestCase):
    def test_queued_fomod_recovery_entries_force_report(self):
        self.assertTrue(
            warningReportNeedsWrite(
                [],
                [],
                [],
                [],
                [
                    {
                        "archive": "JK's Windhelm Outskirts Patch Collection.rar",
                        "target": "JK's Windhelm Outskirts Patch Collection",
                        "observe": False,
                    }
                ],
                0,
            )
        )

    def test_dirty_download_metadata_audit_forces_report(self):
        self.assertTrue(
            warningReportNeedsWrite(
                [],
                [],
                [],
                [],
                [],
                0,
                {"checked": 1, "downloaded_only": ["archive.7z.meta"]},
            )
        )
        self.assertTrue(
            warningReportNeedsWrite(
                [],
                [],
                [],
                [],
                [],
                0,
                {"checked": 1, "missing_archive": ["archive.7z.meta"]},
            )
        )

    def test_empty_review_data_skips_report(self):
        self.assertFalse(warningReportNeedsWrite([], [], [], [], [], "0"))


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

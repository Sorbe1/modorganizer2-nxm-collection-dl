import json
import html
import os
import re
import shutil
import subprocess
import time
import traceback
import uuid
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import mobase
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QDialogButtonBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
)

from . import __meta__, var
from .collection_helpers import (
    AUTOMATED_INSTALL_CADENCE_DEFAULTS,
    FOMOD_ADVANCE_EXCLUDED_TITLES,
    INSTALLER_SETTING_DEFAULTS,
    allocateUniqueModName,
    archiveInspectionSubprocessKwargs,
    collectionMetadataFromFile,
    collectionInstallRoute,
    collectionPriorityOrderNeedsRepair,
    collectionPluginNamesFromModDirs,
    coerceBoolSetting,
    coerceIntSetting,
    collectionEntryNexusKey,
    collectionPluginActivationTargetModNames,
    collectionExpectedFileNames,
    collectionExpectedNexusKeys,
    contentTreeWarningDialogAction,
    detachedInstallCacheKeyFromPath,
    extractHeadlessZipArchive,
    fastFinishMetadataRepairKeys,
    EMPTY_INSTALLER_OUTPUT_REASON,
    EMPTY_OPTIONAL_FOMOD_OUTPUT_REASON,
    headlessArchiveInstallLayout,
    headlessFomodDependencyInstallLayout,
    gameRootFileEvidenceForCollectionEntry,
    nativeGameRootPathCandidate,
    fomodManualChoiceGuide,
    headlessInstallMetaIni,
    headlessPayloadRootValid,
    headlessArchivePreflightFallback,
    installedModCompletionIssueReason,
    installedModHasCompletionPayload,
    installedPayloadFileCount,
    invalidInstallContentDialogAction,
    installNoResultReason,
    installPlanExecutionAction,
    installerDefaultActionLabel,
    isBenignEmptyFomodInstallerResult,
    isCollectionTransientModDirName,
    knownPostInstallErrorDialogMessage,
    warningsAfterCleanInstallDiscard,
    isRequiredFomodGroupTitle,
    isSafeSingletonFomodOption,
    isTransientManualFomodPlanFailure,
    moveHeadlessArchivePayload,
    normalizedButtonLabel,
    nativeArchiveWorkerHeartbeatStatus,
    nativePathForArchiveInspection,
    pluginActivationReviewEntries,
    pluginMasterDependencyAudit,
    pluginMasterDependencyReviewEntries,
    pluginRepairFailureReviewEntries,
    preferredCanonicalDownloadArchive,
    quarantineInvalidPayloadModContainers,
    mo2CategoryNameMap,
    moveModlistEntriesToUiBottom,
    preferredRequiredFomodFallbackOption,
    repairDownloadMetadataInstalledFlags,
    repairInstalledCollectionModMetadata,
    repairSingleWrapperPayload,
    repairPluginEnabledStates,
    safeDisplayText,
    sevenZipArchiveMemberPaths,
    sevenZipModuleConfigPathFromListing,
    shouldAutoCloseInstallSummary,
    shouldPassTargetNameToInstallMod,
    shouldUseArchiveDefaultForFomodCompatibility,
    splitQueuedFomodRecoveryEntries,
    steamGameRootFromMo2BasePath,
    suppressedPostInstallErrorReviewEntries,
    zipArchiveMemberPaths,
    installedModRecordsFromDirectory,
)

qDebug = var.debug

MO2_WARNING_PATTERNS = (
    "Plugin not found:",
    "invalid origin name:",
    "failed to receive data from secondary process",
    "[fomodinstallerdialog.cpp:",
)

EXPECTED_INSTALL_EXCEPTION_PATTERNS = (
    "invalid origin name:",
    "Plugin not found:",
)


def resilientRmtree(path, attempts=5, delay_seconds=0.05):
    """Remove a tree across transient Windows/Proton directory-handle races."""
    path = Path(path)
    for attempt in range(max(1, int(attempts or 1))):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt >= max(1, int(attempts or 1)) - 1:
                raise
            QApplication.processEvents()
            time.sleep(max(0.0, float(delay_seconds or 0)))
    return not path.exists()


def removeStaleCollectionTransientDirs(mods_path):
    """Remove interrupted collection extraction/install directories from MO2 mods."""
    removed = []
    failed = []
    try:
        entries = list(Path(mods_path).iterdir())
    except OSError as e:
        return {"removed": removed, "failed": [f"<scan failed: {e}>"]}

    for entry in entries:
        if not entry.is_dir():
            continue
        if not isCollectionTransientModDirName(entry.name):
            continue
        try:
            resilientRmtree(entry)
            removed.append(entry.name)
        except OSError as e:
            failed.append(f"{entry.name}: {e}")
    return {"removed": removed, "failed": failed}


INSTALL_NEXT_DELAY_MS = AUTOMATED_INSTALL_CADENCE_DEFAULTS["next_mod_delay_ms"]
INSTALL_DIALOG_HANDLER_INITIAL_DELAY_MS = AUTOMATED_INSTALL_CADENCE_DEFAULTS[
    "dialog_poll_initial_delay_ms"
]
INSTALL_DIALOG_HANDLER_INTERVAL_MS = AUTOMATED_INSTALL_CADENCE_DEFAULTS[
    "dialog_poll_interval_ms"
]
FOMOD_ADVANCE_INTERVAL_MS = AUTOMATED_INSTALL_CADENCE_DEFAULTS[
    "fomod_advance_interval_ms"
]

_invalid_install_content_cancelled = False
_mod_exists_cancelled = False


def loadMetadataIntoVar(metadata):
    """Load saved collection metadata into the module state used by installers."""
    var.uri = metadata.get("uri")
    var.game = metadata.get("game")
    var.collection = metadata.get("collection")
    var.revision = metadata.get("revision")
    var.author = metadata.get("author", "Unknown Author")
    var.name = metadata.get("name", "Unknown Collection")
    var.summary = metadata.get("summary", "No description available.")
    var.thumbnail = metadata.get("thumbnail")
    var.essentialMods = metadata.get("essentialMods", [])
    var.chosenOptional = metadata.get("chosenOptional", [])
    var.externalMods = metadata.get("externalMods", [])


def currentCollectionMetadataFromOrganizer(organizer):
    if not organizer or not var.game or not var.collection or var.revision is None:
        return {}
    metadata_file = (
        Path(organizer.basePath())
        / "collections"
        / str(var.game)
        / f"{var.collection}_{var.revision}.json"
    )
    return collectionMetadataFromFile(metadata_file) or {}


def installCollectionMetadata(metadata, parent=None, auto_close_on_success=False):
    active_dialog = getattr(__meta__, "_active_install_dialog", None)
    if active_dialog is not None:
        try:
            if active_dialog.isVisible():
                if getattr(active_dialog, "install_finished", False) and getattr(
                    active_dialog, "install_successful", False
                ):
                    active_dialog.accept()
                else:
                    active_dialog.raise_()
                    active_dialog.activateWindow()
                    qDebug(
                        "[NXMColDL] Install handoff ignored; install dialog is active"
                    )
                    return
        except RuntimeError:
            pass

    loadMetadataIntoVar(metadata)
    qDebug(f"[NXMColDL] Loaded collection: {var.name}")
    qDebug(f"[NXMColDL] Essential mods: {len(var.essentialMods)}")
    qDebug(f"[NXMColDL] Optional mods: {len(var.chosenOptional)}")
    dialog = stepInstallMods(parent, auto_close_on_success=auto_close_on_success)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

    def release_dialog_reference(*_args):
        try:
            if getattr(__meta__, "_active_install_dialog", None) is dialog:
                __meta__._active_install_dialog = None
        except Exception:
            pass

    dialog.finished.connect(release_dialog_reference)
    dialog.destroyed.connect(release_dialog_reference)
    dialog.show()
    try:
        __meta__._active_install_dialog = dialog
    except Exception:
        pass


def runDeferredAutomaticFailedRetry(previous_context, parent=None):
    retry_dialog = stepInstallMods(parent, auto_start=False)
    retry_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

    def release_retry_reference(*_args):
        try:
            if (
                getattr(__meta__, "_deferred_install_retry_dialog", None)
                is retry_dialog
            ):
                __meta__._deferred_install_retry_dialog = None
        except Exception:
            pass

    retry_dialog.finished.connect(release_retry_reference)
    retry_dialog.destroyed.connect(release_retry_reference)
    retry_dialog.show()
    retry_dialog.startAutomaticFailedRetry(previous_context, deferred=True)
    try:
        __meta__._deferred_install_retry_dialog = retry_dialog
    except Exception:
        pass


def clickButtonByText(widget, texts):
    wanted = {text.lower() for text in texts}
    for button in widget.findChildren(QPushButton):
        if normalizedButtonLabel(button.text()) in wanted and button.isEnabled():
            suppressDialogAndClick(widget, button)
            return True
    return False


def suppressDialogAndClick(widget, button):
    suppressDialogWindowFocus(widget)
    button.click()


def suppressDialogWindowFocus(widget):
    """Best-effort non-activation flags for MO2 dialogs automated by the plugin."""
    try:
        widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    except Exception:
        pass

    try:
        widget.setAttribute(Qt.WidgetAttribute.WA_X11DoNotAcceptFocus, True)
    except Exception:
        pass

    try:
        widget.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    except Exception:
        pass

    try:
        handle = widget.windowHandle()
        if handle is not None:
            handle.setFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    except Exception:
        pass


def suppressFomodWindowFocus(widget):
    """Best-effort focus suppression for auto-advanced FOMOD pages."""
    if not widget.isVisible() or widget.windowTitle() in FOMOD_ADVANCE_EXCLUDED_TITLES:
        return False

    suppressDialogWindowFocus(widget)
    return True


def acceptQuickInstallDialog():
    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible() or widget.windowTitle() != "Quick Install":
            continue

        for button_box in widget.findChildren(QDialogButtonBox):
            button = button_box.button(QDialogButtonBox.StandardButton.Ok)
            if button and button.isEnabled():
                qDebug("[NXMColDL Install] Auto-accepting Quick Install dialog")
                suppressDialogAndClick(widget, button)
                return

        for button in widget.findChildren(QPushButton):
            if normalizedButtonLabel(button.text()) == "ok" and button.isEnabled():
                qDebug("[NXMColDL Install] Auto-accepting Quick Install dialog")
                suppressDialogAndClick(widget, button)
                return


def dismissKnownPostInstallErrorDialog(remaining=20, on_dismiss=None):
    if remaining <= 0:
        return False

    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible() or widget.windowTitle() != "Error":
            continue

        message = knownPostInstallErrorDialogMessage(
            [label.text() for label in widget.findChildren(QLabel)]
        )
        if not message:
            continue

        for button_box in widget.findChildren(QDialogButtonBox):
            button = button_box.button(QDialogButtonBox.StandardButton.Ok)
            if button and button.isEnabled():
                qDebug(
                    "[NXMColDL Install] Dismissing known post-install error dialog: "
                    f"{message}"
                )
                if on_dismiss is not None:
                    on_dismiss(message)
                suppressDialogAndClick(widget, button)
                return True

        for button in widget.findChildren(QPushButton):
            if normalizedButtonLabel(button.text()) == "ok" and button.isEnabled():
                qDebug(
                    "[NXMColDL Install] Dismissing known post-install error dialog: "
                    f"{message}"
                )
                if on_dismiss is not None:
                    on_dismiss(message)
                suppressDialogAndClick(widget, button)
                return True

    QTimer.singleShot(
        250,
        lambda: dismissKnownPostInstallErrorDialog(remaining - 1, on_dismiss),
    )
    return False


def handleModExistsDialog(action="merge"):
    global _mod_exists_cancelled

    action = normalizedButtonLabel(action)
    if action not in {"cancel", "merge", "replace", "rename"}:
        return False

    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible() or widget.windowTitle() != "Mod Exists":
            continue

        if clickButtonByText(widget, (action,)):
            if action == "cancel":
                _mod_exists_cancelled = True
            qDebug(f"[NXMColDL Install] Auto-{action} Mod Exists dialog")
            return True

    return False


def acceptModNameDialog(target_name=None):
    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible() or widget.windowTitle() != "Mod Name":
            continue

        if target_name:
            for line_edit in widget.findChildren(QLineEdit):
                if line_edit.isEnabled():
                    line_edit.setText(target_name)
                    qDebug(
                        "[NXMColDL Install] Setting Mod Name dialog target to "
                        f"{target_name}"
                    )
                    break

        if clickButtonByText(widget, ("ok",)):
            qDebug("[NXMColDL Install] Auto-accepting Mod Name dialog")
            return


def cancelInvalidInstallContentDialog():
    global _invalid_install_content_cancelled

    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible() or widget.windowTitle() != "Install Mods":
            continue

        action = invalidInstallContentDialogAction(
            widget.windowTitle(),
            (label.text() for label in widget.findChildren(QLabel)),
            (
                (button.text(), button.isEnabled())
                for button in widget.findChildren(QPushButton)
            ),
        )
        if action is None:
            continue

        if clickButtonByText(widget, (action,)):
            _invalid_install_content_cancelled = True
            qDebug(
                "[NXMColDL Install] Accepting invalid-content install dialog "
                "to match manual MO2 install flow"
            )
            return


def acceptContentTreeWarningDialog():
    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible() or widget.windowTitle() != "Continue?":
            continue

        action = contentTreeWarningDialogAction(
            widget.windowTitle(),
            (label.text() for label in widget.findChildren(QLabel)),
            (
                (button.text(), button.isEnabled())
                for button in widget.findChildren(QPushButton)
            ),
        )
        if action is None:
            continue

        if clickButtonByText(widget, (action,)):
            qDebug(
                "[NXMColDL Install] Accepting MO2 content-tree warning "
                "to match manual install flow"
            )
            return


def installerDefaultAction(widget):
    """Return the safest default-action button for a visible FOMOD installer."""
    if not widget.isVisible():
        return None

    buttons = widget.findChildren(QPushButton)
    action = installerDefaultActionLabel(
        widget.windowTitle(),
        ((button.text(), button.isEnabled()) for button in buttons),
    )
    if action is None:
        return None

    for button in buttons:
        if normalizedButtonLabel(button.text()) == action:
            return button


def fomodPageFingerprint(widget):
    """Return visible page text that distinguishes repeated same-title FOMOD pages."""
    parts = []
    for label in widget.findChildren(QLabel):
        if not label.isVisible():
            continue
        text = normalizedButtonLabel(label.text())
        if text:
            parts.append(text)
    return "|".join(parts)[:500]


def selectRequiredSingletonFomodOption(widget, selected_groups=None):
    """Select an obvious required/default FOMOD option when MO2 left it empty."""
    if not widget.isVisible() or widget.windowTitle() in FOMOD_ADVANCE_EXCLUDED_TITLES:
        return None

    suppressFomodWindowFocus(widget)

    selected_groups = selected_groups if selected_groups is not None else set()
    title = safeDisplayText(widget.windowTitle())
    for group in widget.findChildren(QGroupBox):
        candidates = []
        checked = []
        for button in group.findChildren(QAbstractButton):
            if isinstance(button, QPushButton):
                continue
            if button.isChecked():
                checked.append(button)
                continue
            if not button.isEnabled():
                continue
            if not normalizedButtonLabel(button.text()):
                continue
            candidates.append(button)

        if checked or not candidates:
            continue

        group_title = safeDisplayText(group.title())
        group_key = (title, group_title, fomodPageFingerprint(widget))
        if len(candidates) == 1:
            button = candidates[0]
            label = normalizedButtonLabel(button.text())
            if not isSafeSingletonFomodOption(group.title(), label):
                continue
        elif isRequiredFomodGroupTitle(group.title()):
            if group_key in selected_groups:
                continue
            preferred_label = preferredRequiredFomodFallbackOption(
                [button.text() for button in candidates]
            )
            if not preferred_label:
                continue
            button = None
            for candidate in candidates:
                if normalizedButtonLabel(candidate.text()) == normalizedButtonLabel(
                    preferred_label
                ):
                    button = candidate
                    break
            if button is None:
                continue
            label = normalizedButtonLabel(button.text())
        else:
            continue

        label = normalizedButtonLabel(button.text())
        qDebug(
            "[NXMColDL Install] Auto-selecting required FOMOD option "
            f"{label} in {group_title} for {title}"
        )
        button.click()
        QApplication.processEvents()
        return title, group_title, label, group_key

    return None


def advanceInstallerDialogDefaults(selected_groups=None):
    for widget in QApplication.topLevelWidgets():
        if widget.isVisible() and widget.windowTitle() == "Continue?":
            if clickButtonByText(widget, ("ignore",)):
                qDebug(
                    "[NXMColDL Install] Accepting MO2 Continue warning "
                    "during automated install"
                )
                QApplication.processEvents()
                return safeDisplayText(widget.windowTitle()), "ignore", None

        button = installerDefaultAction(widget)
        if button and normalizedButtonLabel(button.text()) == "install":
            selection = selectRequiredSingletonFomodOption(widget, selected_groups)
            if selection:
                title, group_title, label, group_key = selection
                return title, "select " + label, group_key
        if not button:
            selection = selectRequiredSingletonFomodOption(widget, selected_groups)
            if selection:
                title, group_title, label, group_key = selection
                return title, "select " + label, group_key
            continue

        title = safeDisplayText(widget.windowTitle())
        label = normalizedButtonLabel(button.text())
        qDebug(f"[NXMColDL Install] Auto-advancing installer defaults for {title}")
        button.click()
        QApplication.processEvents()
        return title, label, None

    return None


def raiseMo2MainWindow():
    """Best-effort restore of MO2's main window before opening native installers."""
    if not AUTOMATED_INSTALL_CADENCE_DEFAULTS["raise_mo2_for_native_install"]:
        return False

    for widget in QApplication.topLevelWidgets():
        try:
            class_name = widget.metaObject().className()
            title = widget.windowTitle() or ""
            if class_name != "MainWindow" and "Mod Organizer" not in title:
                continue
            widget.show()
            widget.raise_()
            widget.activateWindow()
            QApplication.processEvents()
            qDebug("[NXMColDL Install] Raised MO2 main window before native installer")
            return True
        except RuntimeError:
            continue
        except Exception as e:
            qDebug(f"[NXMColDL Install] Could not raise MO2 main window: {e}")
            return False
    qDebug("[NXMColDL Install] MO2 main window not found before native installer")
    return False


def scheduleInstallDialogHandlers(
    auto_accept_quick_install=INSTALLER_SETTING_DEFAULTS["auto_accept_quick_install"],
    auto_dismiss_known_post_install_errors=INSTALLER_SETTING_DEFAULTS[
        "auto_dismiss_known_post_install_errors"
    ],
    auto_cancel_invalid_install_content=INSTALLER_SETTING_DEFAULTS[
        "auto_cancel_invalid_install_content"
    ],
    existing_mod_action=None,
    existing_mod_target_name=None,
    should_run=None,
    remaining_ticks=1200,
    on_post_install_error=None,
):
    def run_tick(remaining):
        if should_run is not None and not should_run():
            return

        if auto_accept_quick_install:
            acceptQuickInstallDialog()
        if auto_dismiss_known_post_install_errors:
            dismissKnownPostInstallErrorDialog(
                remaining=1,
                on_dismiss=on_post_install_error,
            )
        acceptContentTreeWarningDialog()
        if auto_cancel_invalid_install_content:
            cancelInvalidInstallContentDialog()
        if existing_mod_action:
            handleModExistsDialog(existing_mod_action)
            acceptModNameDialog(existing_mod_target_name)

        if remaining > 0:
            QTimer.singleShot(
                INSTALL_DIALOG_HANDLER_INTERVAL_MS,
                lambda: run_tick(remaining - 1),
            )

    QTimer.singleShot(
        INSTALL_DIALOG_HANDLER_INITIAL_DELAY_MS,
        lambda: run_tick(remaining_ticks),
    )


def scheduleKnownPostInstallErrorDismissal(
    should_run=None,
    remaining_ticks=1200,
    on_post_install_error=None,
):
    """Keep known MO2 post-install error modals from blocking later phases."""

    def run_tick(remaining):
        if should_run is not None and not should_run():
            return

        dismissKnownPostInstallErrorDialog(
            remaining=1,
            on_dismiss=on_post_install_error,
        )

        if remaining > 0:
            QTimer.singleShot(
                INSTALL_DIALOG_HANDLER_INTERVAL_MS,
                lambda: run_tick(remaining - 1),
            )

    QTimer.singleShot(
        INSTALL_DIALOG_HANDLER_INITIAL_DELAY_MS,
        lambda: run_tick(remaining_ticks),
    )


class stepSelectCollection(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NXM Collection Installer - Select Collection")
        self.setMinimumWidth(400)

        self.collections_list = []
        self.current_metadata = None
        self.network_manager = None

        layout = QVBoxLayout()

        # Dropdown at the top
        self.label = QLabel("Select a downloaded collection to install:")
        layout.addWidget(self.label)

        self.dropdown = QComboBox()
        self.dropdown.currentIndexChanged.connect(self.on_selection_changed)
        layout.addWidget(self.dropdown)

        layout.addSpacing(10)

        # Info display section
        infoBox = QHBoxLayout()

        self.thumb_label = QLabel()
        self.thumb_label.setMaximumHeight(128)
        self.thumb_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        infoBox.addWidget(self.thumb_label)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        infoBox.addWidget(self.info)

        layout.addLayout(infoBox)

        layout.addSpacing(10)

        # Submit button
        self.submit_btn = QPushButton("Next")
        self.submit_btn.clicked.connect(self.submit)
        self.submit_btn.setEnabled(False)  # Disabled until a collection is loaded
        layout.addWidget(self.submit_btn)

        self.setLayout(layout)

    def showEvent(self, event):
        """Refresh collections list every time the dialog is shown"""
        super().showEvent(event)
        self.refreshCollections()

    def refreshCollections(self):
        """Reload and refresh the collections list"""
        # Clear current dropdown
        self.dropdown.blockSignals(True)
        self.dropdown.clear()
        self.collections_list = []
        self.current_metadata = None
        self.dropdown.blockSignals(False)

        # Load collections
        plugin_instance = getattr(__meta__, "_install_plugin", None)
        if not plugin_instance or not hasattr(plugin_instance, "_organizer"):
            QMessageBox.critical(self, "Error", "Failed to access Mod Organizer")
            qDebug("[NXMColDL] Failed to get plugin instance")
            self.close()
            return

        base_path = Path(plugin_instance._organizer.basePath())
        self.collections_list = var.listCollectionMetadata(base_path)
        qDebug(
            f"[NXMColDL] Found {len(self.collections_list)} collection(s) to install"
        )

        if not self.collections_list:
            QMessageBox.information(
                self,
                "No Collections Found",
                "No downloaded collections found.\n\nPlease download a collection first using the Download Collection tool.",
            )
            qDebug("[NXMColDL] No collections found")
            self.close()
            return

        # Populate dropdown with enough detail to distinguish concurrent
        # collection downloads for the same game.
        for game, collection_id, revision, metadata_file in self.collections_list:
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                    name = metadata.get("name", collection_id)
                    total_mods = metadata.get("totalMods", "?")
                    display_text = (
                        f"{name} [{collection_id} rev {revision}, "
                        f"{total_mods} mods] - {game}"
                    )
                    self.dropdown.addItem(display_text)
            except Exception as e:
                qDebug(f"[NXMColDL] Error loading metadata from {metadata_file}: {e}")
                self.dropdown.addItem(f"{collection_id} [rev {revision}] - {game}")

        # Trigger initial selection
        if self.dropdown.count() > 0:
            self.on_selection_changed(0)

    def on_selection_changed(self, index):
        """Handle dropdown selection change"""
        if index < 0 or index >= len(self.collections_list):
            return

        game, collection_id, revision, metadata_file = self.collections_list[index]

        # Load metadata
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                self.current_metadata = json.load(f)

            # Update display
            name = self.current_metadata.get("name", "Unknown Collection")
            author = self.current_metadata.get("author", "Unknown Author")
            summary = self.current_metadata.get("summary", "No description available.")
            thumbnail = self.current_metadata.get("thumbnail")
            total_mods = self.current_metadata.get("totalMods", 0)
            timestamp = self.current_metadata.get("timestamp", "Unknown")

            # Clean up summary for display
            summary = (
                var.cleanJson(summary, True) if summary else "No description available."
            )

            # Update info label
            self.info.setText(f"""
				<h2 style="margin:0;padding:0">{safeDisplayText(name)}</h2>
				<br>
				by <i>{safeDisplayText(author)}</i>
				<br>
				<br>
				{summary}
				<br>
				<br>
				<b>Total Mods:</b> {total_mods}
				<br>
				<b>Collection:</b> {collection_id} rev {revision}
				<br>
				<b>Downloaded:</b> {timestamp.split("T")[0] if "T" in timestamp else timestamp}
			""")

            # Update thumbnail
            if thumbnail:
                self.network_manager = var.loadThumbnail(
                    thumbnail, self.thumb_label, self.network_manager
                )
            else:
                self.thumb_label.clear()

            self.submit_btn.setEnabled(True)

            qDebug(f"[NXMColDL] Selected collection: {name} (Rev {revision})")

        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to load collection metadata: {e}"
            )
            qDebug(f"[NXMColDL] Error loading metadata: {e}")
            self.submit_btn.setEnabled(False)

    def submit(self):
        """Proceed to next step with selected collection"""
        if not self.current_metadata:
            QMessageBox.critical(self, "Error", "No collection selected")
            return

        self.close()
        installCollectionMetadata(self.current_metadata, self.parent())


class stepInstallMods(QDialog):
    def __init__(self, parent=None, auto_start=True, auto_close_on_success=False):
        super().__init__(parent)
        self.auto_close_on_success = bool(auto_close_on_success)
        self.install_finished = False
        self.install_successful = False
        self.setWindowTitle("NXM Collection Installer - Installing Mods")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)

        layout = QVBoxLayout()

        # Title
        title = QLabel(f"Installing: {safeDisplayText(var.name)}")
        title.setStyleSheet("font-weight: bold; font-size: 14pt;")
        layout.addWidget(title)

        # Progress info
        self.progress_label = QLabel("Preparing installation...")
        layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        layout.addWidget(self.progress_bar)

        # Log area
        log_label = QLabel("Installation Log:")
        layout.addWidget(log_label)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(200)
        try:
            self.log_text.document().setMaximumBlockCount(1200)
        except Exception:
            pass
        layout.addWidget(self.log_text)

        button_row = QHBoxLayout()

        self.cancel_btn = QPushButton("Cancel After Current")
        self.cancel_btn.clicked.connect(self.cancelInstallation)
        button_row.addWidget(self.cancel_btn)

        self.manual_install_btn = QPushButton("Retry Failed Manually")
        self.manual_install_btn.setEnabled(False)
        self.manual_install_btn.clicked.connect(self.startManualFailedInstall)
        button_row.addWidget(self.manual_install_btn)

        self.open_warning_report_btn = QPushButton("Open Warning Report")
        self.open_warning_report_btn.setEnabled(False)
        self.open_warning_report_btn.clicked.connect(self.openWarningReport)
        button_row.addWidget(self.open_warning_report_btn)

        self.open_fomod_guide_btn = QPushButton("Open FOMOD Guide")
        self.open_fomod_guide_btn.setEnabled(False)
        self.open_fomod_guide_btn.clicked.connect(self.openFomodGuide)
        button_row.addWidget(self.open_fomod_guide_btn)

        # Close button (initially disabled)
        self.close_btn = QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        button_row.addWidget(self.close_btn)
        layout.addLayout(button_row)

        self.setLayout(layout)
        self.install_warnings = []
        self.install_warning_index = {}
        self.install_warning_summary = Counter()
        self.install_context = None
        self.cancel_requested = False
        self.dialog_handler_generation = 0
        self.fomod_auto_advances = []
        self.fomod_auto_action_counts = {}
        self.fomod_auto_no_action_counts = {}
        self.fomod_auto_selected_groups = {}
        self.fomod_auto_visible_snapshots = {}
        self.fomod_auto_stalled_generations = set()
        self.archive_fomod_errors = {}
        self.archive_fomod_cache = None
        self.manual_fomod_plan_cache = None
        self.runtime_api_trace_logged = False
        self.last_failed_entries = []
        self.warning_report_path = None
        self.fomod_guide_path = None
        self._native_archive_worker_process = None
        self._native_archive_worker_launch_failed = False
        self._native_archive_worker_cleanup_done = False

        # Start installation after dialog is shown
        if auto_start:
            QTimer.singleShot(500, self.startInstallation)

    def accept(self):
        self.stopNativeArchiveWorker()
        super().accept()

    def reject(self):
        self.stopNativeArchiveWorker()
        super().reject()

    def closeEvent(self, event):
        self.stopNativeArchiveWorker()
        super().closeEvent(event)

    def openWarningReport(self):
        self.openGeneratedReport(self.warning_report_path)

    def openFomodGuide(self):
        self.openGeneratedReport(self.fomod_guide_path)

    def openGeneratedReport(self, path):
        if not path:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def installerBoolSetting(self, name):
        context = self.install_context or {}
        plugin_instance = context.get("plugin_instance")
        organizer = context.get("organizer")
        if not plugin_instance or not organizer:
            return INSTALLER_SETTING_DEFAULTS[name]
        if name == "headless_archive_installs":
            current = organizer.pluginSetting(plugin_instance.name(), name)
            if current is None:
                current = organizer.pluginSetting(
                    plugin_instance.name(), "headless_zip_installs"
                )
            return coerceBoolSetting(
                current,
                INSTALLER_SETTING_DEFAULTS[name],
            )
        return coerceBoolSetting(
            organizer.pluginSetting(plugin_instance.name(), name),
            INSTALLER_SETTING_DEFAULTS[name],
        )

    def installerIntSetting(self, name, minimum=0):
        context = self.install_context or {}
        plugin_instance = context.get("plugin_instance")
        organizer = context.get("organizer")
        if not plugin_instance or not organizer:
            return INSTALLER_SETTING_DEFAULTS[name]
        return coerceIntSetting(
            organizer.pluginSetting(plugin_instance.name(), name),
            default=INSTALLER_SETTING_DEFAULTS[name],
            minimum=minimum,
        )

    def log(self, message, level="info"):
        color = "black"
        if level == "error":
            color = "#b00020"
        elif level == "warning":
            color = "#9c6500"
        elif level == "note":
            color = "#555555"
        elif level == "success":
            color = "#207227"

        self.log_text.append(
            f'<span style="color: {color};">{html.escape(str(message))}</span>'
        )
        qDebug(f"[NXMColDL Install] {message}")
        if level in {"error", "warning"}:
            QApplication.processEvents()

    def logInstallIssue(self, message, expected=False):
        """Log install issues without making expected MO2 chatter look fatal."""
        prefix = "NOTE" if expected else "ERROR"
        level = "note" if expected else "error"
        self.log(f"  {prefix}: {message}", level)

    def recordRootLevelEntry(
        self, mod_name, file_name, mod_id, file_id, archive_path, reason
    ):
        """Record an archive that targets the game directory, not an MO2 mod."""
        if not self.install_context:
            return

        entry = {
            "mod": mod_name,
            "file": file_name,
            "mod_id": int(mod_id),
            "file_id": int(file_id),
            "archive": str(archive_path),
            "reason": reason,
        }
        self.install_context.setdefault("root_level_entries", []).append(entry)
        self.log(
            "  Root/game-directory archive skipped as externally handled: "
            f"{safeDisplayText(mod_name)}",
            "note",
        )

    def recordNoApplicableFomodEntry(
        self,
        context,
        mod_name,
        file_name,
        mod_id,
        file_id,
        archive_path,
        install_key,
        reason=EMPTY_OPTIONAL_FOMOD_OUTPUT_REASON,
        installed_name=None,
    ):
        """Record a FOMOD that produced no files for this active profile."""
        entry = {
            "mod": mod_name,
            "file": file_name,
            "mod_id": int(mod_id),
            "file_id": int(file_id),
            "archive": str(archive_path),
            "reason": reason,
        }
        if installed_name:
            entry["installed_name"] = installed_name
        context.setdefault("no_applicable_entries", []).append(entry)
        if installed_name:
            try:
                context["modlist"].setActive(installed_name, False)
            except Exception as e:
                self.logInstallIssue(
                    f"Could not disable no-op FOMOD container {installed_name}: {e}",
                    expected=True,
                )
            try:
                organizer = context["organizer"]
                downloads_path = Path(organizer.downloadsPath())
                quarantine_dir = (
                    downloads_path.parent
                    / "logs"
                    / "nxm-collection-no-applicable-payloads"
                    / datetime.now().strftime("%Y%m%d-%H%M%S")
                )
                quarantine_result = quarantineInvalidPayloadModContainers(
                    Path(organizer.modsPath()), [installed_name], quarantine_dir
                )
                if quarantine_result.get("moved"):
                    entry["quarantined_container"] = installed_name
                    self.log(
                        "  Moved no-op FOMOD container out of active mods: "
                        f"{installed_name}",
                        "note",
                    )
                for failure in quarantine_result.get("failed", []):
                    self.logInstallIssue(
                        f"Could not move no-op FOMOD container: {failure}",
                        expected=True,
                    )
            except Exception as e:
                self.logInstallIssue(
                    f"Could not quarantine no-op FOMOD container {installed_name}: {e}",
                    expected=True,
                )
        self.markDownloadedOnlyMetadata(context, install_key)

    def isExpectedInstallException(self, error):
        message = str(error)
        return any(
            pattern in message for pattern in EXPECTED_INSTALL_EXCEPTION_PATTERNS
        )

    def cancelInstallation(self):
        self.cancel_requested = True
        self.cancel_btn.setEnabled(False)
        self.manual_install_btn.setEnabled(False)
        self.progress_label.setText("Cancelling after current install...")
        self.log("Cancel requested; stopping after the current archive.", "warning")

    def interfaceLogPath(self, organizer):
        return Path(organizer.downloadsPath()).parent / "logs" / "mo_interface.log"

    def gameRootPath(self, organizer):
        """Return the game directory when MO2 exposes it, otherwise ``None``."""
        try:
            managed_game = organizer.managedGame()
        except Exception:
            managed_game = None

        if managed_game is not None:
            for attr_name in ("gameDirectory", "gamePath"):
                attr = getattr(managed_game, attr_name, None)
                if not attr:
                    continue
                try:
                    value = attr()
                except TypeError:
                    value = attr
                except Exception:
                    continue
                candidate = nativeGameRootPathCandidate(value)
                if candidate and (candidate / "SkyrimSE.exe").exists():
                    return candidate

        try:
            inferred = steamGameRootFromMo2BasePath(organizer.basePath())
        except Exception:
            inferred = None
        if inferred:
            return inferred
        return None

    def captureInterfaceLogPosition(self, organizer):
        log_path = self.interfaceLogPath(organizer)
        try:
            return log_path, log_path.stat().st_size
        except OSError:
            return log_path, 0

    def collectInterfaceLogWarnings(self, log_path, offset, mod_name, file_name):
        captured = 0
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
                log_file.seek(offset)
                for line in log_file:
                    line = line.rstrip()
                    if not any(pattern in line for pattern in MO2_WARNING_PATTERNS):
                        continue
                    normalized = self.normalizedInterfaceWarning(line)
                    category = self.interfaceWarningCategory(line)
                    self.install_warning_summary[category] += 1
                    key = (mod_name, file_name, normalized)
                    if key in self.install_warning_index:
                        self.install_warnings[self.install_warning_index[key]][
                            "occurrences"
                        ] += 1
                        captured += 1
                        continue
                    warning = {
                        "mod": mod_name,
                        "file": file_name,
                        "message": line,
                        "normalized_message": normalized,
                        "category": category,
                        "occurrences": 1,
                        "source": "interface_log",
                    }
                    self.install_warning_index[key] = len(self.install_warnings)
                    self.install_warnings.append(warning)
                    captured += 1
        except OSError as e:
            qDebug(f"[NXMColDL Install] Could not read MO2 interface log: {e}")
        return captured

    def recordSuppressedPostInstallError(self, message, mod_name, file_name):
        normalized = self.normalizedInterfaceWarning(message)
        category = self.interfaceWarningCategory(message)
        key = (mod_name or "post-install", file_name or "", normalized)
        if key in self.install_warning_index:
            self.install_warnings[self.install_warning_index[key]][
                "occurrences"
            ] += 1
        else:
            self.install_warning_index[key] = len(self.install_warnings)
            self.install_warnings.append(
                {
                    "mod": key[0],
                    "file": key[1],
                    "message": message,
                    "normalized_message": normalized,
                    "category": category,
                    "occurrences": 1,
                    "source": "suppressed_dialog",
                }
            )
        self.install_warning_summary[category] += 1

    def normalizedInterfaceWarning(self, message):
        return re.sub(r"^\[[^\]]+\]\s+[A-Z]\]\s+", "", str(message)).strip()

    def interfaceWarningCategory(self, message):
        text = str(message)
        if "Plugin not found:" in text:
            return "plugin_state_missing"
        if "invalid origin name:" in text:
            return "invalid_origin_name"
        if "failed to receive data from secondary process" in text:
            return "secondary_process_error"
        if "[fomodinstallerdialog.cpp:" in text:
            if "Missing requirement:" in text:
                return "fomod_missing_requirement"
            if "requires selection" in text:
                return "fomod_required_selection"
            if (
                "The value exists but was not matched." in text
                or "did not match" in text
            ):
                return "fomod_condition_mismatch"
            return "fomod_other"
        return "other"

    def warningSummaryForReport(self):
        return [
            {"category": category, "occurrences": count}
            for category, count in self.install_warning_summary.most_common()
        ]

    def totalInterfaceWarningOccurrences(self):
        return sum(self.install_warning_summary.values())

    def discardInterfaceWarningsFrom(self, warning_start):
        """Drop MO2 warning chatter for an entry that ultimately installed cleanly."""
        if warning_start >= len(self.install_warnings):
            return

        self.install_warnings = warningsAfterCleanInstallDiscard(
            self.install_warnings, warning_start
        )
        self.install_warning_index = {}
        self.install_warning_summary = Counter()
        for index, warning in enumerate(self.install_warnings):
            self.install_warning_index[
                (
                    warning["mod"],
                    warning["file"],
                    warning["normalized_message"],
                )
            ] = index
            self.install_warning_summary[warning["category"]] += warning["occurrences"]

    def writeWarningReport(self, organizer, timestamp=None):
        failed_entries = (
            self.install_context.get("failed_entries", [])
            if self.install_context
            else []
        )
        root_level_entries = (
            self.install_context.get("root_level_entries", [])
            if self.install_context
            else []
        )
        no_applicable_entries = (
            self.install_context.get("no_applicable_entries", [])
            if self.install_context
            else []
        )
        collection_metadata = (
            self.install_context.get("collection_metadata", {})
            if self.install_context
            else {}
        )
        recovery_count = collection_metadata.get("addCollectionRecoveryCount", 0)
        try:
            recovery_count = int(recovery_count)
        except (TypeError, ValueError):
            recovery_count = 0
        if (
            not self.install_warnings
            and not failed_entries
            and not root_level_entries
            and not no_applicable_entries
            and recovery_count <= 0
        ):
            return None

        if timestamp is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = (
            self.interfaceLogPath(organizer).parent
            / f"nxm-collection-install-warnings-{var.collection}-{var.revision}-{timestamp}.json"
        )
        report = {
            "collection": var.collection,
            "revision": var.revision,
            "name": var.name,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "warning_count": sum(self.install_warning_summary.values()),
            "unique_warning_count": len(self.install_warnings),
            "warning_summary": self.warningSummaryForReport(),
            "warnings": self.install_warnings,
            "failed_entries": failed_entries,
            "root_level_entries": root_level_entries,
            "no_applicable_entries": no_applicable_entries,
            "add_collection_launch_count": collection_metadata.get(
                "addCollectionLaunchCount", 0
            ),
            "add_collection_recovery_count": collection_metadata.get(
                "addCollectionRecoveryCount", 0
            ),
            "add_collection_launches": collection_metadata.get(
                "addCollectionLaunches", []
            ),
        }
        with open(report_path, "w", encoding="utf-8") as report_file:
            json.dump(report, report_file, indent=2)
        return report_path

    def writeFomodChoiceGuide(self, organizer, failed_entries, timestamp):
        if not failed_entries:
            return None

        guide_path = (
            self.interfaceLogPath(organizer).parent
            / f"nxm-collection-fomod-guide-{var.collection}-{var.revision}-{timestamp}.md"
        )
        entries = []
        summary = {
            "needs_user_choices": 0,
            "only_safe_singleton_prompts": 0,
            "no_unresolved_required_choices": 0,
            "unreadable_or_non_fomod": 0,
        }

        for entry in failed_entries:
            guide = self.inspectFailedEntryFomodGuide(entry)
            entries.append((entry, guide))
            if guide.get("manual_choices"):
                summary["needs_user_choices"] += 1
            elif guide.get("safe_singleton_prompts"):
                summary["only_safe_singleton_prompts"] += 1
            elif guide.get("module_config"):
                summary["no_unresolved_required_choices"] += 1
            else:
                summary["unreadable_or_non_fomod"] += 1

        lines = [
            f"# {safeDisplayText(var.name)} FOMOD/manual install guide",
            "",
            (
                "Generated from failed/skipped collection entries. This guide "
                "includes every failed entry, including entries where FOMOD XML "
                "has no unresolved required choices."
            ),
            "",
            "## Summary",
            "",
            f"- Failed/skipped entries: {len(failed_entries)}",
            f"- Need user choices: {summary['needs_user_choices']}",
            (
                "- Only safe singleton prompts detected: "
                f"{summary['only_safe_singleton_prompts']}"
            ),
            (
                "- No unresolved required choices detected in XML: "
                f"{summary['no_unresolved_required_choices']}"
            ),
            (f"- FOMOD XML not readable/found: {summary['unreadable_or_non_fomod']}"),
            "",
            "## Entries",
        ]

        for entry, guide in entries:
            lines.extend(self.formatFomodGuideEntry(entry, guide))

        try:
            with open(guide_path, "w", encoding="utf-8") as guide_file:
                guide_file.write("\n".join(lines) + "\n")
            return guide_path
        except OSError as e:
            qDebug(f"[NXMColDL Install] Could not write FOMOD guide: {e}")
            return None

    def inspectFailedEntryFomodGuide(self, entry):
        archive = entry.get("archive")
        if not archive:
            return {"error": "No archive path recorded for this entry."}

        archive_path = Path(archive)
        if not archive_path.exists():
            return {"error": f"Archive not found: {archive_path}"}

        organizer = (self.install_context or {}).get("organizer")
        module_config, module_path, error = self.readArchiveFomodModuleConfig(
            archive_path,
            organizer=organizer,
        )
        if module_config is None:
            return {"archive": str(archive_path), "error": error}

        guide = fomodManualChoiceGuide(module_config)
        guide["archive"] = str(archive_path)
        guide["module_config"] = module_path
        return guide

    def readArchiveFomodModuleConfig(self, archive_path, organizer=None):
        suffix = archive_path.suffix.lower()
        if suffix == ".zip":
            return self.readZipFomodModuleConfig(archive_path)
        if organizer is not None:
            return self.nativeArchiveWorkerFomod(organizer, archive_path)
        return self.readSevenZipFomodModuleConfig(archive_path)

    def archiveHasFomodInstaller(self, archive_path, organizer=None):
        cached = self.cachedArchiveFomodGuide(archive_path)
        if cached is not None:
            archive_key = str(archive_path)
            self.archive_fomod_errors.pop(archive_key, None)
            if cached.get("has_fomod"):
                qDebug(
                    "[NXMColDL Install] Archive FOMOD inspection result: "
                    f"archive={archive_path}, state=present, source=host-cache, "
                    f"module_config={cached.get('module_config')}"
                )
                return True
            qDebug(
                "[NXMColDL Install] Archive FOMOD inspection result: "
                f"archive={archive_path}, state=absent, source=host-cache"
            )
            return False

        module_config, module_path, error = self.readArchiveFomodModuleConfig(
            archive_path,
            organizer=organizer,
        )
        archive_key = str(archive_path)
        self.archive_fomod_errors.pop(archive_key, None)
        if module_config is not None:
            qDebug(
                "[NXMColDL Install] Archive FOMOD inspection result: "
                f"archive={archive_path}, state=present, module_config={module_path}"
            )
            return True
        if error and not str(error).startswith("No fomod/ModuleConfig.xml found"):
            self.archive_fomod_errors[archive_key] = error
            qDebug(
                "[NXMColDL Install] Archive FOMOD inspection result: "
                f"archive={archive_path}, state=unknown, error={error}"
            )
            return None
        qDebug(
            "[NXMColDL Install] Archive FOMOD inspection result: "
            f"archive={archive_path}, state=absent"
        )
        return False

    def archiveFomodGuide(self, archive_path, organizer=None):
        cached = self.cachedArchiveFomodGuide(archive_path)
        if cached is not None and cached.get("has_fomod"):
            guide = dict(cached)
            guide.setdefault("archive", str(archive_path))
            guide.setdefault("manual_choices", [])
            guide.setdefault("safe_singleton_prompts", [])
            guide.setdefault("parse_error", None)
            guide.setdefault("error", None)
            guide["source"] = "host-cache"
            return guide

        module_config, module_path, error = self.readArchiveFomodModuleConfig(
            archive_path,
            organizer=organizer,
        )
        if module_config is None:
            return {
                "archive": str(archive_path),
                "module_config": module_path,
                "error": error,
                "parse_error": None,
                "manual_choices": [],
                "safe_singleton_prompts": [],
            }

        guide = fomodManualChoiceGuide(module_config)
        guide["archive"] = str(archive_path)
        guide["module_config"] = module_path
        return guide

    def fomodCachePath(self):
        context = self.install_context or {}
        organizer = context.get("organizer")
        if organizer:
            return Path(organizer.basePath()) / "nxm-collection-dl-fomod-cache.json"
        return None

    def loadArchiveFomodCache(self):
        if self.archive_fomod_cache is not None:
            return self.archive_fomod_cache
        self.archive_fomod_cache = {}
        cache_path = self.fomodCachePath()
        if not cache_path:
            return self.archive_fomod_cache
        try:
            with open(cache_path, "r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
        except OSError as e:
            qDebug(
                "[NXMColDL Install] No host FOMOD cache available: "
                f"path={cache_path}, error={e}"
            )
            return self.archive_fomod_cache
        except ValueError as e:
            qDebug(
                "[NXMColDL Install] Could not parse host FOMOD cache: "
                f"path={cache_path}, error={e}"
            )
            return self.archive_fomod_cache

        entries = payload.get("archives", {}) if isinstance(payload, dict) else {}
        if isinstance(entries, dict):
            self.archive_fomod_cache = entries
        qDebug(
            "[NXMColDL Install] Loaded host FOMOD cache: "
            f"path={cache_path}, entries={len(self.archive_fomod_cache)}"
        )
        return self.archive_fomod_cache

    def cachedArchiveFomodGuide(self, archive_path):
        cache = self.loadArchiveFomodCache()
        if not cache:
            return None
        path_text = str(archive_path).replace("\\", "/")
        basename = Path(path_text).name
        unprefixed_basename = re.sub(r"^\d+-\d+-", "", basename)
        for key in (path_text, basename, unprefixed_basename):
            entry = cache.get(key)
            if isinstance(entry, dict):
                return entry
        return None

    def manualFomodPlanCachePath(self, organizer):
        return Path(organizer.basePath()) / "nxm-collection-dl-manual-fomod-cache.json"

    def loadManualFomodPlanCache(self, organizer):
        if self.manual_fomod_plan_cache is not None:
            return self.manual_fomod_plan_cache
        self.manual_fomod_plan_cache = {}
        try:
            with open(
                self.manualFomodPlanCachePath(organizer),
                "r",
                encoding="utf-8",
            ) as cache_file:
                payload = json.load(cache_file)
        except (OSError, ValueError):
            return self.manual_fomod_plan_cache
        entries = payload.get("archives", {}) if isinstance(payload, dict) else {}
        if isinstance(entries, dict):
            self.manual_fomod_plan_cache = entries
        return self.manual_fomod_plan_cache

    def archiveIdentityForManualFomodCache(self, archive_path):
        path = Path(str(archive_path))
        try:
            stat = path.stat()
        except OSError:
            return None
        return {
            "archive": path.name,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }

    def manualFomodPlanCacheKey(self, install_key):
        return f"{int(install_key[0])}:{int(install_key[1])}"

    def cachedManualFomodPlanFailure(self, organizer, install_key, archive_path):
        identity = self.archiveIdentityForManualFomodCache(archive_path)
        if identity is None:
            return None
        entry = self.loadManualFomodPlanCache(organizer).get(
            self.manualFomodPlanCacheKey(install_key)
        )
        if not isinstance(entry, dict):
            return None
        if entry.get("identity") != identity:
            return None
        reason = entry.get("reason")
        if not reason:
            return None
        if isTransientManualFomodPlanFailure(reason):
            return None
        return str(reason)

    def rememberManualFomodPlanFailure(
        self,
        organizer,
        install_key,
        archive_path,
        reason,
    ):
        identity = self.archiveIdentityForManualFomodCache(archive_path)
        if identity is None or not reason:
            return
        if isTransientManualFomodPlanFailure(reason):
            return
        cache = self.loadManualFomodPlanCache(organizer)
        cache[self.manualFomodPlanCacheKey(install_key)] = {
            "identity": identity,
            "reason": str(reason),
            "updated": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            with open(
                self.manualFomodPlanCachePath(organizer),
                "w",
                encoding="utf-8",
            ) as cache_file:
                json.dump({"archives": cache}, cache_file, indent=2, sort_keys=True)
        except OSError as e:
            qDebug(f"[NXMColDL Install] Could not write manual FOMOD plan cache: {e}")

    def sevenZipExecutable(self):
        executable = shutil.which("7z") or shutil.which("7zz")
        if executable:
            return executable

        for candidate in (
            "/usr/bin/7z",
            "/usr/local/bin/7z",
            "/bin/7z",
            "/usr/bin/7zz",
            "/usr/local/bin/7zz",
            "/bin/7zz",
        ):
            candidate_path = Path(candidate)
            if candidate_path.is_file() and os.access(candidate_path, os.X_OK):
                return str(candidate_path)
        return None

    def readZipFomodModuleConfig(self, archive_path):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for name in archive.namelist():
                    normalized = name.replace("\\", "/").lower()
                    if normalized.endswith("fomod/moduleconfig.xml"):
                        return archive.read(name), name.replace("\\", "/"), None
        except (OSError, zipfile.BadZipFile) as e:
            return None, None, f"Could not inspect zip archive: {e}"
        return None, None, "No fomod/ModuleConfig.xml found in archive."

    def headlessArchiveLayoutPlan(self, archive_path, organizer=None):
        listing = self.listArchiveMembers(archive_path, organizer=organizer)
        if not listing["ok"]:
            return {
                "installable": False,
                "reason": listing["error"],
                "strip_prefix": "",
            }
        return headlessArchiveInstallLayout(listing["members"])

    def headlessFomodDependencyLayoutPlan(
        self, archive_path, organizer, evidence_names
    ):
        if (
            Path(str(archive_path)).suffix.casefold() != ".zip"
            and not self.sevenZipExecutable()
        ):
            return {
                "installable": False,
                "reason": "non-ZIP FOMOD archive requires external 7z inspection",
                "mappings": [],
                "fomod_selection": True,
            }
        listing = self.listArchiveMembers(archive_path, organizer=organizer)
        if not listing["ok"]:
            return {
                "installable": False,
                "reason": listing["error"],
                "mappings": [],
                "fomod_selection": True,
            }
        module_config, module_path, error = self.readArchiveFomodModuleConfig(
            archive_path,
            organizer=organizer,
        )
        if error:
            return {
                "installable": False,
                "reason": error,
                "mappings": [],
                "fomod_selection": True,
            }
        return headlessFomodDependencyInstallLayout(
            module_config,
            module_path,
            listing["members"],
            evidence_names,
        )

    def profileInstallEvidenceNames(self, organizer):
        """Return active mod and plugin names for dependency-based FOMOD choices."""
        evidence = set()
        for file_name in ("modlist.txt", "plugins.txt"):
            profile_file = Path(organizer.profilePath()) / file_name
            try:
                lines = profile_file.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("+") or line.startswith("*"):
                    evidence.add(line[1:].strip())
        return sorted(name for name in evidence if name)

    def directHeadlessArchiveInstall(
        self,
        organizer,
        archive_path,
        target_mod_name,
        install_key,
        file_name,
        layout_plan,
        mod_info=None,
        replace_empty_target=False,
    ):
        if not target_mod_name:
            raise RuntimeError("Headless archive install requires a target mod name.")

        mods_path = Path(organizer.modsPath())
        target_dir = mods_path / target_mod_name
        temp_dir = mods_path / f".nxm-collection-installing-{target_mod_name}"
        extract_dir = mods_path / f".nxm-collection-extracting-{target_mod_name}"
        if target_dir.exists():
            if not replace_empty_target or installedModHasCompletionPayload(target_dir):
                raise RuntimeError(f"Target mod directory already exists: {target_dir}")
            resilientRmtree(target_dir)
        for transient_dir in (temp_dir, extract_dir):
            if transient_dir.exists():
                resilientRmtree(transient_dir)
        temp_dir.mkdir(parents=True)
        extract_dir.mkdir(parents=True)

        try:
            extraction = self.extractArchiveToDirectory(
                archive_path,
                extract_dir,
                organizer=organizer,
            )
            if not extraction["ok"]:
                raise RuntimeError(extraction["error"])
            moveHeadlessArchivePayload(extract_dir, temp_dir, layout_plan)
            if not headlessPayloadRootValid(temp_dir):
                raise RuntimeError(
                    "Headless archive extraction produced invalid MO2 game data"
                )
            self.writeHeadlessInstallMeta(
                temp_dir,
                archive_path,
                target_mod_name,
                install_key,
                file_name,
                mod_info=mod_info,
                organizer=organizer,
            )
            temp_dir.rename(target_dir)
            return target_mod_name
        except Exception:
            if temp_dir.exists():
                resilientRmtree(temp_dir)
            if extract_dir.exists():
                resilientRmtree(extract_dir)
            raise
        finally:
            if extract_dir.exists():
                resilientRmtree(extract_dir)

    def writeHeadlessInstallMeta(
        self,
        mod_dir,
        archive_path,
        target_mod_name,
        install_key,
        file_name,
        mod_info=None,
        organizer=None,
    ):
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        mod_id, file_id = install_key
        file_data = (mod_info or {}).get("file") or {}
        nexus_mod = file_data.get("mod") or {}
        category_map = (
            mo2CategoryNameMap(Path(organizer.basePath()) / "categories.dat")
            if organizer is not None
            else {}
        )
        nexus_category = nexus_mod.get("category", 0)
        if not isinstance(nexus_category, int):
            nexus_category = category_map.get(str(nexus_category).casefold(), 0)
        metadata = headlessInstallMetaIni(
            mod_id,
            file_id,
            Path(archive_path).name,
            target_mod_name,
            file_name,
            timestamp,
            file_version=file_data.get("version", ""),
            mod_version=nexus_mod.get("version", ""),
            nexus_category=nexus_category,
        )
        (Path(mod_dir) / "meta.ini").write_text(metadata, encoding="utf-8")

    def listArchiveMembers(self, archive_path, organizer=None):
        if Path(str(archive_path)).suffix.casefold() == ".zip":
            try:
                return {
                    "ok": True,
                    "members": zipArchiveMemberPaths(archive_path),
                    "error": None,
                }
            except (OSError, zipfile.BadZipFile) as e:
                return {
                    "ok": False,
                    "members": [],
                    "error": f"Could not inspect zip archive: {e}",
                }

        if organizer is not None:
            qDebug(
                "[NXMColDL Install] Listing non-ZIP archive through native worker: "
                f"archive={archive_path}"
            )
            return self.nativeArchiveWorkerList(
                organizer,
                archive_path,
                "Native archive worker is required to inspect non-ZIP archives from MO2.",
            )

        executable = self.sevenZipExecutable()
        if not executable:
            direct_error = "No 7z/7zz executable available to inspect this archive."
            return {"ok": False, "members": [], "error": direct_error}

        local_archive_path = nativePathForArchiveInspection(archive_path)
        listing = self.runArchiveInspectionSubprocess(
            [executable, "l", "-slt", local_archive_path],
            "list",
        )
        if not listing["ok"]:
            return {"ok": False, "members": [], "error": listing["error"]}
        members = sevenZipArchiveMemberPaths(listing["stdout"])
        return {"ok": True, "members": members, "error": None}

    def extractArchiveToDirectory(self, archive_path, target_dir, organizer=None):
        if Path(str(archive_path)).suffix.casefold() == ".zip":
            try:
                extractHeadlessZipArchive(archive_path, target_dir, {})
            except (OSError, zipfile.BadZipFile, RuntimeError) as e:
                return {"ok": False, "error": f"Could not extract zip archive: {e}"}
            return {"ok": True, "error": None}

        executable = self.sevenZipExecutable()
        if not executable:
            direct_error = "No 7z/7zz executable available to extract this archive."
            if organizer:
                return self.nativeArchiveWorkerExtract(
                    organizer,
                    archive_path,
                    target_dir,
                    direct_error,
                )
            return {"ok": False, "error": direct_error}

        local_archive_path = nativePathForArchiveInspection(archive_path)
        extraction = self.runArchiveInspectionSubprocess(
            [executable, "x", "-y", f"-o{target_dir}", local_archive_path],
            "extract",
            timeout=300,
        )
        if not extraction["ok"]:
            if organizer:
                return self.nativeArchiveWorkerExtract(
                    organizer,
                    archive_path,
                    target_dir,
                    extraction["error"],
                )
            return {"ok": False, "error": extraction["error"]}
        return {"ok": True, "error": None}

    def nativeArchiveWorkerDirectory(self, organizer):
        return Path(organizer.basePath()) / "collections" / "native-archive-requests"

    def nativeArchiveWorkerHeartbeatPath(self, organizer):
        return self.nativeArchiveWorkerDirectory(organizer) / (
            "native-archive-worker.heartbeat.json"
        )

    def nativeArchiveWorkerScriptPath(self):
        return Path(__file__).resolve().parent / "scripts" / "native_archive_worker.py"

    def nativeArchiveWorkerLaunchCommands(self, worker_path, request_dir):
        """Return candidate commands for launching the host-side archive worker."""
        worker_native = nativePathForArchiveInspection(worker_path)
        request_native = nativePathForArchiveInspection(request_dir)
        commands = []
        for executable in (
            os.environ.get("NXM_COLLECTION_NATIVE_PYTHON"),
            shutil.which("python3"),
            shutil.which("python"),
            "/usr/bin/python3",
            "/usr/bin/python",
        ):
            if executable:
                commands.append([executable, worker_native, request_native])

        # MO2 runs under Wine/Proton. When CreateProcess cannot execute a Unix
        # interpreter directly, Wine's start.exe can bridge to a host process.
        for start_exe in (
            r"C:\windows\command\start.exe",
            r"C:\windows\system32\start.exe",
            "start",
        ):
            commands.append(
                [start_exe, "/unix", "/usr/bin/python3", worker_native, request_native]
            )
        return commands

    def startNativeArchiveWorker(self, organizer):
        """Start the native archive worker used for non-ZIP headless installs."""
        request_dir = self.nativeArchiveWorkerDirectory(organizer)
        request_dir.mkdir(parents=True, exist_ok=True)
        worker_path = self.nativeArchiveWorkerScriptPath()
        if not worker_path.exists():
            self.log(
                f"Native archive worker script missing: {worker_path}",
                "debug",
            )
            return False

        process = getattr(self, "_native_archive_worker_process", None)
        if process is not None and process.poll() is None:
            return True
        if getattr(self, "_native_archive_worker_launch_failed", False):
            return False

        errors = []
        for command in self.nativeArchiveWorkerLaunchCommands(worker_path, request_dir):
            try:
                kwargs = {
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                if os.name != "nt":
                    kwargs["start_new_session"] = True
                self._native_archive_worker_process = subprocess.Popen(
                    command,
                    **kwargs,
                )
            except (OSError, subprocess.SubprocessError) as e:
                errors.append(f"{command[0]}: {e}")
                continue

            self.log(
                "Started native archive worker for headless archive installs: "
                + " ".join(str(part) for part in command),
                "debug",
            )
            return True

        self._native_archive_worker_launch_failed = True
        self.log(
            "Native archive worker launch failed for all candidates: "
            + "; ".join(errors),
            "debug",
        )
        return False

    def stopNativeArchiveWorker(self):
        """Ask the native archive worker to exit and reap the tracked process."""
        if getattr(self, "_native_archive_worker_cleanup_done", False):
            return
        self._native_archive_worker_cleanup_done = True

        context = self.install_context or {}
        organizer = context.get("organizer")
        process = getattr(self, "_native_archive_worker_process", None)

        if organizer is not None:
            try:
                request_dir = self.nativeArchiveWorkerDirectory(organizer)
                if request_dir.exists() and self.nativeArchiveWorkerAvailable(
                    organizer,
                    max_age_seconds=10.0,
                    attempts=1,
                    retry_delay_seconds=0,
                ):
                    request_id = f"shutdown-{int(time.time() * 1000)}-{uuid.uuid4().hex}"
                    request_path = request_dir / f"{request_id}.request.json"
                    result_path = request_dir / f"{request_id}.result.json"
                    tmp_path = request_dir / f"{request_id}.request.json.tmp"
                    payload = {
                        "id": request_id,
                        "action": "shutdown",
                        "result_path": nativePathForArchiveInspection(result_path),
                    }
                    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    tmp_path.rename(request_path)
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline:
                        if result_path.exists():
                            try:
                                result_path.unlink()
                            except OSError:
                                pass
                            break
                        if process is not None:
                            try:
                                if process.poll() is not None:
                                    break
                            except Exception:
                                break
                        time.sleep(0.05)
            except Exception as e:
                qDebug(f"[NXMColDL Install] Native archive worker cleanup failed: {e}")

        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            except Exception as e:
                qDebug(
                    "[NXMColDL Install] Native archive worker process cleanup failed: "
                    f"{e}"
                )
        self._native_archive_worker_process = None

    def nativeArchiveWorkerAvailable(
        self, organizer, max_age_seconds=30.0, attempts=5, retry_delay_seconds=0.05
    ):
        heartbeat = self.nativeArchiveWorkerHeartbeatPath(organizer)
        last_error = None
        for attempt in range(max(1, int(attempts))):
            try:
                payload = json.loads(heartbeat.read_text(encoding="utf-8"))
                tracked_pid = None
                tracked_exit_code = None
                process = getattr(self, "_native_archive_worker_process", None)
                if process is not None:
                    tracked_pid = getattr(process, "pid", None)
                    try:
                        tracked_exit_code = process.poll()
                    except Exception:
                        tracked_exit_code = None
                status = nativeArchiveWorkerHeartbeatStatus(
                    payload,
                    time.time(),
                    max_age_seconds=max_age_seconds,
                    tracked_pid=tracked_pid,
                    tracked_exit_code=tracked_exit_code,
                )
                if status["ok"]:
                    return True
                last_error = status["reason"]
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as e:
                last_error = e
            if attempt + 1 < attempts:
                time.sleep(retry_delay_seconds)
        if last_error is not None:
            self.log(
                f"Native archive worker heartbeat unavailable: {last_error}",
                "debug",
            )
        return False

    def nativeArchiveWorkerList(self, organizer, archive_path, direct_error):
        result = self.runNativeArchiveWorkerRequest(
            organizer,
            {
                "action": "list",
                "archive": nativePathForArchiveInspection(archive_path),
                "direct_error": direct_error,
            },
            timeout_seconds=60,
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "members": [],
                "error": result.get("error") or direct_error,
            }
        return {
            "ok": True,
            "members": result.get("members") or [],
            "error": None,
        }

    def nativeArchiveWorkerExtract(
        self, organizer, archive_path, target_dir, direct_error
    ):
        result = self.runNativeArchiveWorkerRequest(
            organizer,
            {
                "action": "extract",
                "archive": nativePathForArchiveInspection(archive_path),
                "target_dir": nativePathForArchiveInspection(target_dir),
                "direct_error": direct_error,
            },
            timeout_seconds=300,
        )
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or direct_error}
        return {"ok": True, "error": None}

    def nativeArchiveWorkerFomod(self, organizer, archive_path):
        result = self.runNativeArchiveWorkerRequest(
            organizer,
            {
                "action": "fomod",
                "archive": nativePathForArchiveInspection(archive_path),
            },
            timeout_seconds=10,
        )
        if not result.get("ok"):
            qDebug(
                "[NXMColDL Install] Native archive worker FOMOD inspection failed: "
                f"archive={archive_path}, error={result.get('error')}"
            )
            return None, None, result.get("error") or (
                "Native archive worker could not inspect FOMOD XML."
            )
        if not result.get("has_fomod"):
            return None, None, "No fomod/ModuleConfig.xml found in archive."
        module_xml = result.get("module_xml")
        if not module_xml:
            return None, None, "Native archive worker did not return FOMOD XML content."
        return module_xml, result.get("module_config"), None

    def runNativeArchiveWorkerRequest(self, organizer, payload, timeout_seconds=60):
        request_dir = self.nativeArchiveWorkerDirectory(organizer)
        request_dir.mkdir(parents=True, exist_ok=True)
        if not self.nativeArchiveWorkerAvailable(organizer):
            self.startNativeArchiveWorker(organizer)
        if not self.nativeArchiveWorkerAvailable(
            organizer, attempts=20, retry_delay_seconds=0.1
        ):
            process = getattr(self, "_native_archive_worker_process", None)
            exit_code = None
            if process is not None:
                try:
                    exit_code = process.poll()
                except Exception:
                    exit_code = None
            if exit_code is not None:
                return {
                    "ok": False,
                    "worker_unavailable": True,
                    "error": (
                        "Native archive worker exited before writing a heartbeat "
                        f"(exit {exit_code}); review worker launch command/logs."
                    ),
                }
            return {
                "ok": False,
                "worker_unavailable": True,
                "error": (
                    "Native archive worker is not running; start "
                    "scripts/native_archive_worker.py for headless archive installs."
                ),
            }
        request_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"
        request_path = request_dir / f"{request_id}.request.json"
        result_path = request_dir / f"{request_id}.result.json"
        tmp_path = request_dir / f"{request_id}.request.json.tmp"
        request_payload = dict(payload)
        request_payload["id"] = request_id
        request_payload["result_path"] = nativePathForArchiveInspection(result_path)
        tmp_path.write_text(json.dumps(request_payload, indent=2), encoding="utf-8")
        tmp_path.rename(request_path)

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if result_path.exists():
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except Exception as e:
                    return {
                        "ok": False,
                        "error": f"Native archive worker result unreadable: {e}",
                    }
                try:
                    result_path.unlink()
                except OSError:
                    pass
                return result
            time.sleep(0.05)

        for stale_path in (request_path, result_path, tmp_path):
            try:
                stale_path.unlink()
            except OSError:
                pass
        return {
            "ok": False,
            "error": (
                "Native archive worker timed out; start "
                "scripts/native_archive_worker.py for headless archive installs."
            ),
        }

    def readSevenZipFomodModuleConfig(self, archive_path):
        executable = self.sevenZipExecutable()
        if not executable:
            return (
                None,
                None,
                "No 7z/7zz executable available to inspect this archive.",
            )

        native_archive_path = nativePathForArchiveInspection(archive_path)
        listing = self.runArchiveInspectionSubprocess(
            [executable, "l", "-slt", native_archive_path],
            "list",
        )
        if not listing["ok"]:
            return None, None, listing["error"]

        module_path = sevenZipModuleConfigPathFromListing(listing["stdout"])
        if not module_path:
            return None, None, "No fomod/ModuleConfig.xml found in archive."

        extraction = self.runArchiveInspectionSubprocess(
            [executable, "x", "-so", native_archive_path, module_path],
            "extract FOMOD XML from",
            stderr_to_stdout=False,
        )
        if not extraction["ok"]:
            return None, None, extraction["error"]
        if not extraction["stdout"]:
            return None, None, "7z did not return FOMOD XML content."

        return extraction["stdout"], module_path, None

    def runArchiveInspectionSubprocess(
        self, args, action, timeout=30, stderr_to_stdout=True
    ):
        try:
            result = subprocess.run(
                args,
                **archiveInspectionSubprocessKwargs(
                    timeout=timeout,
                    stderr_to_stdout=stderr_to_stdout,
                ),
            )
        except (OSError, subprocess.SubprocessError) as e:
            return {
                "ok": False,
                "stdout": b"",
                "stderr": b"",
                "error": f"Could not {action} archive with subprocess 7z: {e}",
            }

        stdout = result.stdout or b""
        if result.returncode != 0:
            detail = stdout.decode("utf-8", errors="replace").strip()
            return {
                "ok": False,
                "stdout": stdout,
                "stderr": b"",
                "error": (
                    f"Could not {action} archive with subprocess 7z "
                    f"(exit {result.returncode}): {detail}"
                ),
            }

        return {"ok": True, "stdout": stdout, "stderr": b"", "error": None}

    def formatFomodGuideEntry(self, entry, guide):
        title = (
            f"### {safeDisplayText(entry.get('mod'))} - "
            f"{safeDisplayText(entry.get('file'))}"
        )
        lines = [
            "",
            title,
            (
                f"- Nexus: mod `{entry.get('mod_id', 'unknown')}`, "
                f"file `{entry.get('file_id', 'unknown')}`"
            ),
            f"- Reason: {safeDisplayText(entry.get('reason'))}",
        ]
        if entry.get("archive"):
            lines.append(f"- Archive: `{entry['archive']}`")
        if guide.get("module_config"):
            lines.append(f"- FOMOD XML: `{guide['module_config']}`")
        if guide.get("parse_error"):
            lines.append(
                f"- FOMOD XML parse error: {safeDisplayText(guide['parse_error'])}"
            )
        if guide.get("error"):
            lines.append(f"- Inspection note: {safeDisplayText(guide['error'])}")
        reason = str(entry.get("reason", "")).lower()
        if "root game-directory" in reason or "invalid install content" in reason:
            lines.append(
                "- Manual handling: this archive appears to target the game/base "
                "directory rather than a normal MO2 mod container."
            )

        safe_prompts = guide.get("safe_singleton_prompts") or []
        if safe_prompts:
            prompts = [
                f"{safeDisplayText(prompt.get('group'))} -> "
                f"{safeDisplayText(', '.join(prompt.get('options', [])))}"
                for prompt in safe_prompts
            ]
            lines.append(
                "- Safe prompts the plugin can auto-advance: " + "; ".join(prompts)
            )

        manual_choices = guide.get("manual_choices") or []
        if manual_choices:
            for idx, choice in enumerate(manual_choices, start=1):
                options = ", ".join(
                    f"`{safeDisplayText(option)}`"
                    for option in choice.get("options", [])
                )
                lines.append(
                    f"- Choice {idx}: step `{safeDisplayText(choice.get('step'))}`, "
                    f"group `{safeDisplayText(choice.get('group'))}` "
                    f"({safeDisplayText(choice.get('type'))}): {options}"
                )
        else:
            lines.append(
                "- Required choices needing user selection: none detected; "
                "open this entry manually in MO2 and complete the installer."
            )
        return lines

    def startInstallation(self):
        """Prepare the install pass and queue the first archive."""
        qDebug(f"[NXMColDL] Starting installation: {var.name}")
        try:
            plugin_instance = getattr(__meta__, "_install_plugin", None)
            if not plugin_instance or not hasattr(plugin_instance, "_organizer"):
                self.log("Failed to access Mod Organizer", "error")
                self.close_btn.setEnabled(True)
                return

            organizer = plugin_instance._organizer
            modlist = organizer.modList()
            fomod_default_override = var.autoAdvanceFomodDefaultsOverride
            var.autoAdvanceFomodDefaultsOverride = None
            if fomod_default_override is None:
                fomod_default_override = organizer.pluginSetting(
                    plugin_instance.name(), "auto_advance_fomod_defaults"
                )

            # Get all mods to install in order
            mods_to_install = var.essentialMods + var.chosenOptional
            expected_nexus_keys = collectionExpectedNexusKeys(mods_to_install)
            expected_file_names = collectionExpectedFileNames(mods_to_install)
            self.progress_bar.setMaximum(len(mods_to_install))

            self.log(f"Total mods to install: {len(mods_to_install)}")

            # Get initial mod count to determine starting priority
            # Collection mods will be placed at the end of the current mod list
            transient_cleanup = removeStaleCollectionTransientDirs(
                Path(organizer.modsPath())
            )
            if transient_cleanup["removed"]:
                self.log(
                    "Removed stale collection transient mod folder(s): "
                    f"{len(transient_cleanup['removed'])}",
                    "note",
                )
                try:
                    organizer.refresh(True)
                except Exception as e:
                    self.logInstallIssue(
                        f"Could not refresh MO2 after stale transient cleanup: {e}",
                        expected=True,
                    )
            for cleanup_failure in transient_cleanup["failed"]:
                self.logInstallIssue(
                    f"Could not remove stale collection transient folder: "
                    f"{cleanup_failure}",
                    expected=True,
                )

            initial_mod_count = len(modlist.allMods())
            base_priority = initial_mod_count
            self.log(f"Existing mods in list: {initial_mod_count}")
            self.log(f"Collection mods will start at priority: {base_priority}")
            self.log("")

            # Find downloaded files
            downloads_path = Path(organizer.downloadsPath())
            self.log(f"Searching for downloads in: {downloads_path}")

            # Build a mapping of (modId, fileId) -> download_path
            download_map = self.buildDownloadMap(downloads_path)
            self.log(f"Found {len(download_map)} downloaded files")

            installed_map = self.buildInstalledMap(
                Path(organizer.modsPath()),
                downloads_path,
                expected_file_names,
            )
            installed_stats = getattr(self, "_last_installed_map_stats", {})
            self.log(
                "Found "
                f"{len(installed_map)} installed Nexus file records "
                f"({installed_stats.get('metadata', 0)} metadata, "
                f"{installed_stats.get('detached_cache', 0)} detached-cache, "
                f"{installed_stats.get('correlated', 0)} correlated)"
            )
            self.log("")

            self.install_context = {
                "plugin_instance": plugin_instance,
                "organizer": organizer,
                "modlist": modlist,
                "game_root_path": self.gameRootPath(organizer),
                "collection_metadata": currentCollectionMetadataFromOrganizer(
                    organizer
                ),
                "mods_to_install": mods_to_install,
                "expected_nexus_keys": expected_nexus_keys,
                "expected_file_names": expected_file_names,
                "download_map": download_map,
                "installed_map": installed_map,
                "install_evidence_names": self.profileInstallEvidenceNames(organizer),
                "base_priority": base_priority,
                "installed_mods": [],
                "failed_entries": [],
                "root_level_entries": [],
                "no_applicable_entries": [],
                "mods_to_activate": [],
                "used_mod_names": set(modlist.allMods()),
                "mod_name_counts": {},
                "activation_enabled": coerceBoolSetting(
                    organizer.pluginSetting(
                        plugin_instance.name(), "activate_mods_after_install"
                    ),
                    INSTALLER_SETTING_DEFAULTS["activate_mods_after_install"],
                ),
                "activate_during_install": coerceBoolSetting(
                    organizer.pluginSetting(
                        plugin_instance.name(), "activate_mods_during_install"
                    ),
                    INSTALLER_SETTING_DEFAULTS["activate_mods_during_install"],
                ),
                "activated_mods": [],
                "activation_failures": [],
                "plugin_activation": {
                    "activated": 0,
                    "already_active": 0,
                    "blocked": 0,
                },
                "separate_file_installs": coerceBoolSetting(
                    organizer.pluginSetting(
                        plugin_instance.name(), "install_files_as_separate_mods"
                    ),
                    INSTALLER_SETTING_DEFAULTS["install_files_as_separate_mods"],
                ),
                "auto_advance_fomod_defaults": coerceBoolSetting(
                    fomod_default_override,
                    INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
                ),
                "fomod_archive_cache": {},
                "headless_installed_count": 0,
                "next_index": 0,
            }
            self.prepareInstallPlan(self.install_context)
            self.fomod_auto_advances = []
            if (
                installPlanExecutionAction(
                    self.install_context.get("install_plan_fast_finish")
                )
                == "fast-finish"
            ):
                self.fastFinishInstallPlan(self.install_context)
            else:
                QTimer.singleShot(INSTALL_NEXT_DELAY_MS, self.installNextMod)

        except Exception as e:
            self.log(f"Fatal error during installation: {e}", "error")
            self.close_btn.setEnabled(True)

    def failedEntryToCollectionMod(self, entry):
        return {
            "file": {
                "mod": {
                    "modId": int(entry["mod_id"]),
                    "name": entry["mod"],
                },
                "fileId": int(entry["file_id"]),
                "name": entry["file"],
            }
        }

    def prepareInstallPlan(self, context):
        """Resolve download, naming, and skip decisions before opening installers."""
        organizer = context["organizer"]
        mods_path = Path(organizer.modsPath())
        mods_to_install = context["mods_to_install"]
        download_map = context["download_map"]
        installed_map = context["installed_map"]
        used_mod_names = context["used_mod_names"]
        mod_name_counts = context["mod_name_counts"]
        game_root_path = context.get("game_root_path")
        manual_install_pass = context.get("manual_install_pass", False)
        normal_dialog_retry_pass = context.get("normal_dialog_retry_pass", False)

        self.log("Preparing install plan...", "note")
        plan = []
        counts = Counter()
        for index, mod_info in enumerate(mods_to_install, start=1):
            mod_id = int(mod_info["file"]["mod"]["modId"])
            file_id = int(mod_info["file"]["fileId"])
            mod_name = mod_info["file"]["mod"]["name"]
            file_name = mod_info["file"]["name"]
            install_key = (mod_id, file_id)
            entry = {
                "index": index,
                "install_key": install_key,
                "mod_id": mod_id,
                "file_id": file_id,
                "mod_name": mod_name,
                "file_name": file_name,
                "status": "install",
            }

            download_path = download_map.get(install_key)

            invalid_installed_name = None
            if install_key in installed_map:
                installed_name = installed_map[install_key]
                installed_dir = mods_path / installed_name
                invalid_installed = (
                    installed_dir / "meta.ini"
                ).exists() and not headlessPayloadRootValid(installed_dir)
                if invalid_installed and download_path:
                    invalid_payload_files = installedPayloadFileCount(installed_dir)
                    # Empty invalid containers are interrupted installer outputs and can be
                    # replayed from the archive. Non-empty invalid containers may be
                    # deliberate tool/root payloads, so keep them installed and let the
                    # final sweep disable them instead of forcing a lossy reinstall.
                    if invalid_payload_files == 0:
                        invalid_installed_name = installed_name
                        counts["repair_invalid_empty"] += 1
                    else:
                        counts["invalid_nonempty"] += 1

                if invalid_installed_name is None:
                    entry["status"] = "installed"
                    entry["installed_name"] = installed_name
                    counts["installed"] += 1
                    plan.append(entry)
                    continue

            root_evidence = (
                gameRootFileEvidenceForCollectionEntry(install_key, game_root_path)
                if game_root_path
                else []
            )
            if root_evidence:
                entry["status"] = "root"
                entry["reason"] = "game-root file already present: " + ", ".join(
                    root_evidence
                )
                counts["root"] += 1
                plan.append(entry)
                continue

            if not download_path:
                entry["status"] = "failed"
                entry["reason"] = "not found in downloads"
                counts["missing"] += 1
                plan.append(entry)
                continue

            existing_name_match = self.installedModMatchingDownload(
                mods_path, mod_name, download_path, mod_id
            )
            if existing_name_match:
                matched_dir = mods_path / existing_name_match
                if not installedModHasCompletionPayload(matched_dir):
                    invalid_installed_name = existing_name_match
                    counts["repair_invalid_empty"] += 1
                else:
                    entry["status"] = "installed"
                    entry["installed_name"] = existing_name_match
                    entry["matched_installation_archive"] = True
                    installed_map[install_key] = existing_name_match
                    counts["installed"] += 1
                    plan.append(entry)
                    continue

            self.repairStaleInstalledDownloadMetadata(
                download_path, install_key, installed_map
            )

            if manual_install_pass or normal_dialog_retry_pass:
                install_source_path = download_path
                source_note = "original"
            else:
                install_source_path = self.detachedInstallArchiveSource(
                    organizer, download_path, install_key
                )
                source_note = "detached"
            if invalid_installed_name:
                install_source_path = download_path
                source_note = "invalid-installed-repair"

            fomod_state = False
            auto_advance_fomod_defaults = context.get(
                "auto_advance_fomod_defaults",
                INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
            )
            if invalid_installed_name:
                fomod_state = True
            if (
                context["separate_file_installs"]
                and not manual_install_pass
                and not normal_dialog_retry_pass
                and auto_advance_fomod_defaults
                and not invalid_installed_name
            ):
                fomod_archive_cache = context.setdefault("fomod_archive_cache", {})
                archive_key = str(install_source_path)
                if archive_key not in fomod_archive_cache:
                    fomod_archive_cache[archive_key] = self.archiveHasFomodInstaller(
                        install_source_path,
                        organizer=organizer,
                    )
                fomod_state = fomod_archive_cache[archive_key]
                if fomod_state is True and install_source_path != download_path:
                    install_source_path = download_path
                    source_note = "original-fomod"

            use_archive_default_for_fomod = (
                shouldUseArchiveDefaultForFomodCompatibility(
                    context["separate_file_installs"],
                    manual_install_pass or normal_dialog_retry_pass,
                    fomod_state,
                )
            )

            existing_mod_action = None
            if invalid_installed_name:
                existing_mod_action = "replace"
            elif not manual_install_pass or normal_dialog_retry_pass:
                if context["separate_file_installs"]:
                    existing_mod_action = "rename"
                elif not context[
                    "separate_file_installs"
                ] and self.installerBoolSetting("auto_merge_existing_mods"):
                    existing_mod_action = "merge"

            use_target_mod_name = shouldPassTargetNameToInstallMod(
                context["separate_file_installs"],
                manual_install_pass,
                normal_dialog_retry_pass,
                use_archive_default_for_fomod,
            )
            target_mod_name = None
            if invalid_installed_name:
                target_mod_name = invalid_installed_name
                use_target_mod_name = True
                use_archive_default_for_fomod = False
            elif use_target_mod_name or existing_mod_action == "rename":
                target_mod_name = self.allocateCollectionModName(
                    mod_name, used_mod_names, mod_name_counts
                )

            install_route = collectionInstallRoute(
                install_source_path,
                fomod_state,
                separate_file_installs=context["separate_file_installs"],
                manual_install_pass=manual_install_pass,
                normal_dialog_retry_pass=normal_dialog_retry_pass,
                headless_archive_installs=self.installerBoolSetting(
                    "headless_archive_installs"
                ),
            )
            headless_archive_layout = None
            if (
                fomod_state is True
                and install_route == "mo2"
                and not manual_install_pass
                and not normal_dialog_retry_pass
                and self.installerBoolSetting("headless_archive_installs")
            ):
                headless_archive_layout = self.headlessFomodDependencyLayoutPlan(
                    install_source_path,
                    organizer=context["organizer"],
                    evidence_names=context.get("install_evidence_names", []),
                )
                if headless_archive_layout.get("installable"):
                    install_route = "headless-archive"
                    existing_mod_action = None
                    use_target_mod_name = True
                    use_archive_default_for_fomod = False
                    if invalid_installed_name:
                        counts["headless_fomod_repair"] += 1
                    else:
                        counts["headless_fomod"] += 1
                else:
                    if invalid_installed_name:
                        self.rememberManualFomodPlanFailure(
                            context["organizer"],
                            install_key,
                            install_source_path,
                            headless_archive_layout.get("reason"),
                        )
                        entry.update(
                            {
                                "status": "failed",
                                "download_path": download_path,
                                "install_source_path": install_source_path,
                                "source_note": source_note,
                                "reason": (
                                    "manual FOMOD choices required: "
                                    f"{headless_archive_layout.get('reason')}"
                                ),
                                "headless_archive_layout": headless_archive_layout,
                                "replacing_invalid_installed_name": invalid_installed_name,
                                "fomod_state": "true",
                            }
                        )
                        counts["missing"] += 1
                        plan.append(entry)
                        continue
                    headless_archive_layout = None
            if install_route == "headless-archive":
                headless_archive_layout = (
                    self.headlessArchiveLayoutPlan(
                        install_source_path,
                        organizer=context["organizer"],
                    )
                    if headless_archive_layout is None
                    else headless_archive_layout
                )
                if not headless_archive_layout.get("installable"):
                    fallback_route = headlessArchivePreflightFallback(
                        headless_archive_layout
                    )
                    if fallback_route == "mo2":
                        install_route = "mo2"
                    else:
                        # Do not use MO2's generic installer as recovery for
                        # archive preflight failures. It reintroduces Quick
                        # Install dialogs, focus loss, and slow per-mod UI.
                        entry.update(
                            {
                                "status": "failed",
                                "download_path": download_path,
                                "install_source_path": install_source_path,
                                "source_note": source_note,
                                "reason": (
                                    "manual archive layout: "
                                    f"{headless_archive_layout.get('reason')}"
                                ),
                                "headless_archive_layout": headless_archive_layout,
                            }
                        )
                        counts["missing"] += 1
                        plan.append(entry)
                        continue

            entry.update(
                {
                    "download_path": download_path,
                    "install_source_path": install_source_path,
                    "source_note": source_note,
                    "fomod_state": fomod_state,
                    "use_archive_default_for_fomod": use_archive_default_for_fomod,
                    "existing_mod_action": existing_mod_action,
                    "target_mod_name": target_mod_name,
                    "use_target_mod_name": use_target_mod_name,
                    "install_route": install_route,
                    "headless_archive_layout": headless_archive_layout,
                    "replacing_invalid_installed_name": invalid_installed_name,
                }
            )
            counts["install"] += 1
            if invalid_installed_name:
                counts["repair_invalid"] += 1
            if install_route == "headless-archive":
                counts["headless_archive"] += 1
            if fomod_state is True:
                counts["fomod"] += 1
            elif fomod_state is None:
                counts["fomod_unknown"] += 1
            if target_mod_name:
                counts["named"] += 1
            plan.append(entry)

        context["install_plan"] = plan
        context["install_plan_counts"] = dict(counts)
        context["install_plan_work_indexes"] = [
            index
            for index, entry in enumerate(plan)
            if entry.get("status") == "install"
        ]
        context["install_plan_fast_finish"] = counts["install"] == 0
        self.log(
            "Install plan ready: "
            f"{counts['installed']} already installed, "
            f"{counts['root']} root-handled, "
            f"{counts['install']} to install, "
            f"{counts['missing']} missing downloads, "
            f"{counts['fomod']} FOMOD, "
            f"{counts['fomod_unknown']} unknown FOMOD state, "
            f"{counts['headless_archive']} headless archive, "
            f"{counts['named']} reserved names, "
            f"{counts['repair_invalid']} invalid installed repair(s), "
            f"{counts['repair_invalid_empty']} meta-only invalid repair(s), "
            f"{counts['invalid_nonempty']} non-empty invalid kept installed.",
            "note",
        )
        self.log("")

    def fastFinishInstallPlan(self, context):
        """Populate postconditions for a no-op replay and finish immediately."""
        if context.get("install_plan_fast_finished"):
            self.finishInstallation()
            return

        activate_after_install = context["activation_enabled"]
        activate_during_install = context["activate_during_install"]
        installed_mods = context["installed_mods"]
        mods_to_activate = context["mods_to_activate"]
        installed_map = context["installed_map"]

        installed_count = 0
        root_count = 0
        failed_count = 0
        metadata_repair_keys = fastFinishMetadataRepairKeys(context.get("install_plan"))
        for entry in context.get("install_plan", []):
            status = entry.get("status")
            install_key = entry.get("install_key")
            if status == "installed":
                internal_name = entry["installed_name"]
                installed_mods.append(internal_name)
                installed_map[install_key] = internal_name
                if activate_after_install:
                    if activate_during_install:
                        self.activateModDuringInstall(internal_name)
                    else:
                        mods_to_activate.append(internal_name)
                installed_count += 1
            elif status == "root":
                context.setdefault("root_level_entries", []).append(
                    {
                        "mod": entry["mod_name"],
                        "file": entry["file_name"],
                        "mod_id": int(entry["mod_id"]),
                        "file_id": int(entry["file_id"]),
                        "archive": "",
                        "reason": entry.get("reason", "game-root file already present"),
                    }
                )
                root_count += 1
            elif status == "failed":
                context["failed_entries"].append(
                    {
                        "mod": entry["mod_name"],
                        "file": entry["file_name"],
                        "mod_id": int(entry["mod_id"]),
                        "file_id": int(entry["file_id"]),
                        "reason": entry.get("reason", "preflight failed"),
                    }
                )
                failed_count += 1

        if metadata_repair_keys:
            context["fast_finish_metadata_repair"] = self.markInstalledDownloadMetadataKeys(
                context, metadata_repair_keys
            )

        context["install_plan_fast_finished"] = True
        self.log(
            "No install work remains; preparing fast summary "
            f"for {installed_count} installed and {root_count} root-handled entries.",
            "note",
        )
        if failed_count:
            self.log(
                f"Install plan still has {failed_count} preflight failure(s).",
                "warning",
            )
        self.finishInstallation()

    def populateNonInstallPlanEntries(self, context):
        """Record preflight plan outcomes that do not need installer execution."""
        if context.get("install_plan_non_install_populated"):
            return

        for entry in context.get("install_plan", []):
            status = entry.get("status")
            if status == "failed":
                context["failed_entries"].append(
                    {
                        "mod": entry["mod_name"],
                        "file": entry["file_name"],
                        "mod_id": int(entry["mod_id"]),
                        "file_id": int(entry["file_id"]),
                        "reason": entry.get("reason", "preflight failed"),
                    }
                )
            elif status == "root":
                context.setdefault("root_level_entries", []).append(
                    {
                        "mod": entry["mod_name"],
                        "file": entry["file_name"],
                        "mod_id": int(entry["mod_id"]),
                        "file_id": int(entry["file_id"]),
                        "archive": "",
                        "reason": entry.get("reason", "game-root file already present"),
                    }
                )

        context["install_plan_non_install_populated"] = True

    def startManualFailedInstall(self):
        if not self.last_failed_entries:
            self.log("No failed entries are available for manual install.", "note")
            return

        try:
            plugin_instance = getattr(__meta__, "_install_plugin", None)
            if not plugin_instance or not hasattr(plugin_instance, "_organizer"):
                self.log("Failed to access Mod Organizer", "error")
                return

            organizer = plugin_instance._organizer
            modlist = organizer.modList()
            downloads_path = Path(organizer.downloadsPath())
            mods_to_install = [
                self.failedEntryToCollectionMod(entry)
                for entry in self.last_failed_entries
                if entry.get("mod_id") is not None and entry.get("file_id") is not None
            ]
            expected_nexus_keys = collectionExpectedNexusKeys(mods_to_install)
            expected_file_names = collectionExpectedFileNames(mods_to_install)

            self.cancel_requested = False
            self.close_btn.setEnabled(False)
            self.manual_install_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)
            self.install_warnings = []
            self.install_warning_index = {}
            self.install_warning_summary = Counter()
            self.fomod_auto_advances = []
            self.progress_bar.setMaximum(len(mods_to_install))
            self.progress_bar.setValue(0)
            self.progress_label.setText("Preparing manual install pass...")
            self.log("")
            self.log(
                f"Manual install pass: retrying {len(mods_to_install)} failed entries.",
                "note",
            )
            self.log(
                "Automation is disabled for this pass; complete or cancel each MO2 "
                "installer dialog manually.",
                "note",
            )
            self.log("")

            self.install_context = {
                "plugin_instance": plugin_instance,
                "organizer": organizer,
                "modlist": modlist,
                "game_root_path": self.gameRootPath(organizer),
                "collection_metadata": currentCollectionMetadataFromOrganizer(
                    organizer
                ),
                "mods_to_install": mods_to_install,
                "expected_nexus_keys": expected_nexus_keys,
                "expected_file_names": expected_file_names,
                "download_map": self.buildDownloadMap(downloads_path),
                "installed_map": self.buildInstalledMap(
                    Path(organizer.modsPath()),
                    downloads_path,
                    expected_file_names,
                ),
                "installed_mods": [],
                "failed_entries": [],
                "root_level_entries": [],
                "no_applicable_entries": [],
                "mods_to_activate": [],
                "used_mod_names": set(modlist.allMods()),
                "mod_name_counts": {},
                "activation_enabled": coerceBoolSetting(
                    organizer.pluginSetting(
                        plugin_instance.name(), "activate_mods_after_install"
                    ),
                    INSTALLER_SETTING_DEFAULTS["activate_mods_after_install"],
                ),
                "activate_during_install": coerceBoolSetting(
                    organizer.pluginSetting(
                        plugin_instance.name(), "activate_mods_during_install"
                    ),
                    INSTALLER_SETTING_DEFAULTS["activate_mods_during_install"],
                ),
                "activated_mods": [],
                "activation_failures": [],
                "plugin_activation": {
                    "activated": 0,
                    "already_active": 0,
                    "blocked": 0,
                },
                "separate_file_installs": coerceBoolSetting(
                    organizer.pluginSetting(
                        plugin_instance.name(), "install_files_as_separate_mods"
                    ),
                    INSTALLER_SETTING_DEFAULTS["install_files_as_separate_mods"],
                ),
                "auto_advance_fomod_defaults": False,
                "fomod_archive_cache": {},
                "manual_install_pass": True,
                "next_index": 0,
            }
            self.prepareInstallPlan(self.install_context)
            if (
                installPlanExecutionAction(
                    self.install_context.get("install_plan_fast_finish")
                )
                == "fast-finish"
            ):
                self.fastFinishInstallPlan(self.install_context)
            else:
                QTimer.singleShot(500, self.installNextMod)
        except Exception as e:
            self.log(f"Fatal error starting manual install pass: {e}", "error")
            self.close_btn.setEnabled(True)
            self.manual_install_btn.setEnabled(bool(self.last_failed_entries))

    def startAutomaticFailedRetry(self, previous_context, deferred=False):
        failed_entries = previous_context.get("failed_entries", [])
        if not failed_entries:
            return False

        organizer = previous_context["organizer"]
        downloads_path = Path(organizer.downloadsPath())
        mods_to_install = [
            self.failedEntryToCollectionMod(entry)
            for entry in failed_entries
            if entry.get("mod_id") is not None and entry.get("file_id") is not None
        ]
        if not mods_to_install:
            return False

        if not deferred:
            self.log("")
            self.log(
                "Automatic retry pass: retrying "
                f"{len(mods_to_install)} entries with normal MO2 installer dialogs "
                "after this progress dialog closes.",
                "note",
            )
            self.log("")
            retry_context = dict(previous_context)
            self.install_context = None
            self.accept()
            QTimer.singleShot(
                500,
                lambda context=retry_context, parent=self.parent(): (
                    runDeferredAutomaticFailedRetry(context, parent)
                ),
            )
            return True

        self.log("")
        self.log(
            "Automatic retry pass: retrying "
            f"{len(mods_to_install)} entries with normal MO2 installer dialogs.",
            "note",
        )
        self.log(
            "The retry progress window will hide while each MO2 installer dialog is opened.",
            "note",
        )
        self.log("")

        self.install_context = {
            "plugin_instance": previous_context["plugin_instance"],
            "organizer": organizer,
            "modlist": previous_context["modlist"],
            "game_root_path": previous_context.get("game_root_path")
            or self.gameRootPath(organizer),
            "collection_metadata": previous_context.get("collection_metadata", {}),
            "mods_to_install": mods_to_install,
            "collection_total_entries": len(previous_context["mods_to_install"]),
            "expected_nexus_keys": set(
                previous_context.get("expected_nexus_keys", set())
            ),
            "expected_file_names": dict(
                previous_context.get("expected_file_names", {})
            ),
            "download_map": self.buildDownloadMap(downloads_path),
            "installed_map": self.buildInstalledMap(
                Path(organizer.modsPath()),
                downloads_path,
                previous_context.get("expected_file_names", {}),
            ),
            "installed_mods": list(previous_context["installed_mods"]),
            "failed_entries": [],
            "root_level_entries": list(previous_context.get("root_level_entries", [])),
            "no_applicable_entries": list(
                previous_context.get("no_applicable_entries", [])
            ),
            "mods_to_activate": list(previous_context["mods_to_activate"]),
            "used_mod_names": set(previous_context["modlist"].allMods()),
            "mod_name_counts": {},
            "activation_enabled": previous_context["activation_enabled"],
            "activate_during_install": previous_context["activate_during_install"],
            "activated_mods": list(previous_context["activated_mods"]),
            "activation_failures": list(previous_context["activation_failures"]),
            "plugin_activation": dict(previous_context["plugin_activation"]),
            "separate_file_installs": previous_context["separate_file_installs"],
            "auto_advance_fomod_defaults": previous_context.get(
                "auto_advance_fomod_defaults",
                INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
            ),
            "fomod_archive_cache": {},
            "headless_installed_count": previous_context.get(
                "headless_installed_count", 0
            ),
            "normal_dialog_retry_pass": True,
            "next_index": 0,
        }
        self.prepareInstallPlan(self.install_context)
        self.progress_bar.setMaximum(len(mods_to_install))
        self.progress_bar.setValue(0)
        self.progress_label.setText("Retrying failed entries...")
        if (
            installPlanExecutionAction(
                self.install_context.get("install_plan_fast_finish")
            )
            == "fast-finish"
        ):
            self.fastFinishInstallPlan(self.install_context)
        else:
            QTimer.singleShot(500, self.installNextMod)
        return True

    def installNextMod(self):
        global _invalid_install_content_cancelled, _mod_exists_cancelled

        if not self.install_context:
            return

        context = self.install_context
        organizer = context["organizer"]
        mods_to_install = context["mods_to_install"]
        download_map = context["download_map"]
        installed_map = context["installed_map"]
        installed_mods = context["installed_mods"]
        failed_entries = context["failed_entries"]
        mods_to_activate = context["mods_to_activate"]
        used_mod_names = context["used_mod_names"]
        mod_name_counts = context["mod_name_counts"]
        activate_during_install = context["activate_during_install"]
        manual_install_pass = context.get("manual_install_pass", False)
        normal_dialog_retry_pass = context.get("normal_dialog_retry_pass", False)

        if self.cancel_requested:
            self.finishInstallation(cancelled=True)
            return

        if context.get("install_plan_fast_finish"):
            self.progress_label.setText("Verifying collection postconditions...")
            self.progress_bar.setValue(len(mods_to_install))
            self.fastFinishInstallPlan(context)
            return

        self.populateNonInstallPlanEntries(context)
        work_indexes = context.get("install_plan_work_indexes")
        if work_indexes is None:
            work_indexes = list(range(len(mods_to_install)))

        work_idx = context["next_index"] + 1
        if work_idx > len(work_indexes):
            self.finishInstallation()
            return

        context["next_index"] = work_idx
        plan_index = work_indexes[work_idx - 1]
        idx = plan_index + 1
        self.progress_label.setText(f"Installing mod {work_idx}/{len(work_indexes)}")
        self.progress_bar.setValue(work_idx - 1)

        mod_info = mods_to_install[plan_index]
        mod_id = mod_info["file"]["mod"]["modId"]
        file_id = mod_info["file"]["fileId"]
        mod_name = mod_info["file"]["mod"]["name"]
        file_name = mod_info["file"]["name"]
        install_key = (int(mod_id), int(file_id))

        self.log(f"[{idx}/{len(mods_to_install)}] Processing: {mod_name}")
        self.log(f"  File: {file_name} (ModID: {mod_id}, FileID: {file_id})")

        activate_after_install = context["activation_enabled"]
        plan = context.get("install_plan", [])
        plan_entry = plan[plan_index] if plan_index < len(plan) else None

        if plan_entry and plan_entry.get("status") == "installed":
            internal_name = plan_entry["installed_name"]
            suffix = (
                " (matched installation archive)"
                if plan_entry.get("matched_installation_archive")
                else ""
            )
            self.log(f"  Already installed as: {internal_name}{suffix}", "success")
            self.log("")
            installed_mods.append(internal_name)
            self.markInstalledDownloadMetadata(context, install_key)
            if activate_after_install:
                if activate_during_install:
                    self.activateModDuringInstall(internal_name)
                else:
                    mods_to_activate.append(internal_name)
            QTimer.singleShot(INSTALL_NEXT_DELAY_MS, self.installNextMod)
            return

        if plan_entry and plan_entry.get("status") == "failed":
            reason = plan_entry.get("reason", "preflight failed")
            self.logInstallIssue(f"{reason} - skipping")
            archive = plan_entry.get("download_path") or plan_entry.get(
                "install_source_path"
            )
            install_source = plan_entry.get("install_source_path")
            self.markDownloadedOnlyMetadata(context, install_key)
            failed_entries.append(
                {
                    "mod": mod_name,
                    "file": file_name,
                    "mod_id": int(mod_id),
                    "file_id": int(file_id),
                    "archive": str(archive) if archive else "",
                    "install_source": str(install_source) if install_source else "",
                    "reason": reason,
                }
            )
            self.log("")
            QTimer.singleShot(INSTALL_NEXT_DELAY_MS, self.installNextMod)
            return

        if plan_entry and plan_entry.get("status") == "root":
            reason = plan_entry.get("reason", "game-root file already present")
            self.log(
                f"  Root/game-directory entry already handled: {reason}",
                "success",
            )
            context.setdefault("root_level_entries", []).append(
                {
                    "mod": mod_name,
                    "file": file_name,
                    "mod_id": int(mod_id),
                    "file_id": int(file_id),
                    "archive": "",
                    "reason": reason,
                }
            )
            self.markInstalledDownloadMetadata(context, install_key)
            self.log("")
            QTimer.singleShot(INSTALL_NEXT_DELAY_MS, self.installNextMod)
            return

        if plan_entry:
            download_path = plan_entry["download_path"]
            install_source_path = plan_entry["install_source_path"]
            fomod_state = plan_entry["fomod_state"]
            unknown_fomod_state = fomod_state is None
            use_archive_default_for_fomod = plan_entry["use_archive_default_for_fomod"]
            existing_mod_action = plan_entry["existing_mod_action"]
            target_mod_name = plan_entry["target_mod_name"]
            use_target_mod_name = plan_entry["use_target_mod_name"]
            install_route = plan_entry.get("install_route", "mo2")
            headless_archive_layout = plan_entry.get("headless_archive_layout")
        else:
            if install_key not in download_map:
                self.logInstallIssue("Not found in downloads - skipping")
                failed_entries.append(
                    {
                        "mod": mod_name,
                        "file": file_name,
                        "mod_id": int(mod_id),
                        "file_id": int(file_id),
                        "reason": "not found in downloads",
                    }
                )
                self.log("")
                QTimer.singleShot(INSTALL_NEXT_DELAY_MS, self.installNextMod)
                return

            download_path = download_map[install_key]
            existing_name_match = self.installedModMatchingDownload(
                Path(organizer.modsPath()), mod_name, download_path, mod_id
            )
            if existing_name_match:
                self.log(
                    "  Already installed as: "
                    f"{existing_name_match} (matched installation archive)",
                    "success",
                )
                self.log("")
                installed_mods.append(existing_name_match)
                installed_map[install_key] = existing_name_match
                self.markInstalledDownloadMetadata(context, install_key)
                if activate_after_install:
                    if activate_during_install:
                        self.activateModDuringInstall(existing_name_match)
                    else:
                        mods_to_activate.append(existing_name_match)
                QTimer.singleShot(INSTALL_NEXT_DELAY_MS, self.installNextMod)
                return
            self.repairStaleInstalledDownloadMetadata(
                download_path, install_key, installed_map
            )
            if manual_install_pass or normal_dialog_retry_pass:
                install_source_path = download_path
            else:
                install_source_path = self.detachedInstallArchiveSource(
                    organizer, download_path, install_key
                )
            fomod_state = False
            unknown_fomod_state = False
            use_archive_default_for_fomod = False
            existing_mod_action = None
            target_mod_name = self.allocateCollectionModName(
                mod_name, used_mod_names, mod_name_counts
            )
            use_target_mod_name = True
            install_route = "mo2"
            headless_archive_layout = None

        self.log(f"  Found: {download_path.name}")
        if plan_entry and plan_entry.get("source_note") == "original":
            install_source_path = download_path
            self.log(
                f"  Using original MO2 download source: {install_source_path.name}",
                "note",
            )
        elif plan_entry and plan_entry.get("source_note") == "original-fomod":
            self.log(
                "  Using original MO2 download source for FOMOD installer automation",
                "note",
            )
        elif plan_entry and install_source_path != download_path:
            self.log(
                f"  Using detached install source: {install_source_path.name}",
                "note",
            )

        if install_route == "headless-archive":
            try:
                layout_reason = (headless_archive_layout or {}).get(
                    "reason", "safe archive layout"
                )
                self.log(
                    "  Install decision: route=headless-archive, "
                    f"layout={layout_reason}, "
                    f"target={target_mod_name}",
                    "note",
                )
                internal_name = self.directHeadlessArchiveInstall(
                    organizer,
                    install_source_path,
                    target_mod_name,
                    install_key,
                    file_name,
                    headless_archive_layout,
                    mod_info=mod_info,
                    replace_empty_target=bool(
                        plan_entry
                        and plan_entry.get("replacing_invalid_installed_name")
                    ),
                )
                self.log(f"  Installed headlessly as: {internal_name}", "success")
                self.log("  Priority will be checked after collection install")
                used_mod_names.add(internal_name)
                installed_mods.append(internal_name)
                installed_map[install_key] = internal_name
                self.markInstalledDownloadMetadata(context, install_key)
                context["headless_installed_count"] = (
                    context.get("headless_installed_count", 0) + 1
                )
                if activate_after_install:
                    if activate_during_install:
                        self.activateModDuringInstall(internal_name)
                    else:
                        mods_to_activate.append(internal_name)
            except Exception as e:
                error_text = str(e)
                empty_fomod_guide = None
                if (
                    fomod_state is True
                    and (headless_archive_layout or {}).get("fomod_selection")
                    and "invalid MO2 game data" in error_text
                ):
                    empty_fomod_guide = self.archiveFomodGuide(
                        install_source_path,
                        organizer=organizer,
                    )
                if isBenignEmptyFomodInstallerResult(
                    fomod_state,
                    empty_fomod_guide,
                ):
                    self.log(
                        "  "
                        f"{EMPTY_OPTIONAL_FOMOD_OUTPUT_REASON}; "
                        "leaving archive downloaded-only.",
                        "note",
                    )
                    self.recordNoApplicableFomodEntry(
                        context,
                        mod_name,
                        file_name,
                        mod_id,
                        file_id,
                        install_source_path,
                        install_key,
                    )
                else:
                    self.logInstallIssue(
                        f"Headless archive install failed: {e}; "
                        "retry with MO2 installer",
                        expected=True,
                    )
                    self.markDownloadedOnlyMetadata(context, install_key)
                    failed_entries.append(
                        {
                            "mod": mod_name,
                            "file": file_name,
                            "mod_id": int(mod_id),
                            "file_id": int(file_id),
                            "archive": str(install_source_path),
                            "reason": f"headless archive install failed: {e}",
                        }
                    )
            self.log("")
            QTimer.singleShot(INSTALL_NEXT_DELAY_MS, self.installNextMod)
            return

        log_path, log_offset = self.captureInterfaceLogPosition(organizer)
        try:
            _invalid_install_content_cancelled = False
            _mod_exists_cancelled = False
            self.dialog_handler_generation += 1
            dialog_handler_generation = self.dialog_handler_generation
            warning_start = len(self.install_warnings)
            scheduleInstallDialogHandlers(
                self.installerBoolSetting("auto_accept_quick_install"),
                self.installerBoolSetting("auto_dismiss_known_post_install_errors"),
                (
                    self.installerBoolSetting("auto_cancel_invalid_install_content")
                    and (not manual_install_pass or normal_dialog_retry_pass)
                ),
                existing_mod_action,
                target_mod_name if existing_mod_action == "rename" else None,
                lambda generation=dialog_handler_generation: (
                    generation == self.dialog_handler_generation
                ),
                on_post_install_error=(
                    lambda message, mod_name=mod_name, file_name=file_name: (
                        self.recordSuppressedPostInstallError(
                            message, mod_name, file_name
                        )
                    )
                ),
            )
            should_advance_fomod = (
                context.get(
                    "auto_advance_fomod_defaults",
                    INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
                )
                and not manual_install_pass
                and not normal_dialog_retry_pass
            )
            if should_advance_fomod:
                self.fomod_auto_action_counts[dialog_handler_generation] = {}
                self.fomod_auto_selected_groups[dialog_handler_generation] = set()
                max_steps = self.installerIntSetting("auto_advance_fomod_max_steps")
                if max_steps:
                    self.scheduleInstallerDefaultAdvancer(
                        dialog_handler_generation,
                        mod_name,
                        max_steps,
                    )
                else:
                    self.log(
                        "  FOMOD default auto-advance disabled by max-step limit",
                        "note",
                    )

            install_call_target_name = target_mod_name if use_target_mod_name else None
            install_call_phase = (
                "primary-target-name"
                if install_call_target_name
                else "primary-archive-default"
            )
            hide_window_for_install = bool(
                normal_dialog_retry_pass or should_advance_fomod
            )

            archive_inspection_error = self.archive_fomod_errors.get(
                str(install_source_path)
            )
            self.log(
                "  Install decision: "
                f"fomod_state={fomod_state}, "
                f"use_archive_default={use_archive_default_for_fomod and not install_call_target_name}, "
                f"use_target_name={bool(install_call_target_name)}, "
                f"existing_mod_action={existing_mod_action}, "
                f"normal_retry={normal_dialog_retry_pass}, "
                f"manual_pass={manual_install_pass}",
                "note",
            )
            if archive_inspection_error:
                self.log(
                    f"  Archive inspection unavailable: {archive_inspection_error}",
                    "note",
                )

            if install_call_target_name:
                if unknown_fomod_state:
                    self.log(
                        "  FOMOD status unknown; trying collection target name first",
                        "note",
                    )
                self.log(f"  Target MO2 name: {install_call_target_name}")
                installed_mod = self.runInstallModWithRetryWindowHidden(
                    hide_window_for_install,
                    lambda: self.traceInstallModCall(
                        organizer,
                        install_source_path,
                        install_call_target_name,
                        install_call_phase,
                        install_key,
                        dialog_handler_generation,
                        allow_path_retry=not use_archive_default_for_fomod,
                    ),
                )
            else:
                if use_archive_default_for_fomod:
                    if unknown_fomod_state:
                        self.log(
                            "  FOMOD status unknown; using MO2 native installer path",
                            "note",
                        )
                        if target_mod_name:
                            self.log(
                                f"  Rename target if needed: {target_mod_name}",
                                "note",
                            )
                    else:
                        self.log(
                            "  Target MO2 name: using archive default for FOMOD compatibility"
                        )
                        if target_mod_name:
                            self.log(
                                f"  Rename target if needed: {target_mod_name}",
                                "note",
                            )
                installed_mod = self.runInstallModWithRetryWindowHidden(
                    hide_window_for_install,
                    lambda: self.traceInstallModCall(
                        organizer,
                        install_source_path,
                        install_call_target_name,
                        install_call_phase,
                        install_key,
                        dialog_handler_generation,
                        allow_path_retry=not use_archive_default_for_fomod,
                    ),
                )
            if not installed_mod:
                recovered_mod_name = self.recoverLateInstallerResult(
                    organizer,
                    install_key,
                    dialog_handler_generation,
                    mod_name,
                    existing_mod_action,
                    target_mod_name if existing_mod_action == "rename" else None,
                )
                if recovered_mod_name:
                    installed_mod = organizer.modList().getMod(recovered_mod_name)
            warning_count = self.collectInterfaceLogWarnings(
                log_path, log_offset, mod_name, file_name
            )
            current_warnings = self.install_warnings[warning_start:]
            if installed_mod:
                internal_name = installed_mod.name()
                installed_dir = Path(organizer.modsPath()) / internal_name
                payload_issue_reason = installedModCompletionIssueReason(
                    installed_dir
                )
                if payload_issue_reason:
                    empty_fomod_guide = None
                    if (
                        payload_issue_reason
                        == EMPTY_INSTALLER_OUTPUT_REASON
                        and fomod_state is True
                    ):
                        empty_fomod_guide = self.archiveFomodGuide(
                            install_source_path,
                            organizer=organizer,
                        )
                    if isBenignEmptyFomodInstallerResult(
                        fomod_state,
                        empty_fomod_guide,
                    ):
                        self.log(
                            "  "
                            f"{EMPTY_OPTIONAL_FOMOD_OUTPUT_REASON}; "
                            "leaving archive downloaded-only and empty container "
                            "disabled.",
                            "note",
                        )
                        self.recordNoApplicableFomodEntry(
                            context,
                            mod_name,
                            file_name,
                            mod_id,
                            file_id,
                            install_source_path,
                            install_key,
                            installed_name=internal_name,
                        )
                        self.log("")
                        QTimer.singleShot(
                            INSTALL_NEXT_DELAY_MS, self.installNextMod
                        )
                        return
                    try:
                        context["modlist"].setActive(internal_name, False)
                    except Exception as e:
                        self.logInstallIssue(
                            "Could not disable invalid installer output "
                            f"{internal_name}: {e}",
                            expected=True,
                        )
                    reason = (
                        f"{payload_issue_reason}; review archive layout/manual "
                        "choices"
                    )
                    self.markDownloadedOnlyMetadata(context, install_key)
                    failed_entries.append(
                        {
                            "mod": mod_name,
                            "file": file_name,
                            "mod_id": int(mod_id),
                            "file_id": int(file_id),
                            "archive": str(install_source_path),
                            "reason": reason,
                            **self.fomodInstallDiagnostics(
                                fomod_state,
                                use_archive_default_for_fomod,
                                target_mod_name,
                                dialog_handler_generation,
                            ),
                        }
                    )
                    self.logInstallIssue(reason, expected=True)
                    self.log("")
                    QTimer.singleShot(INSTALL_NEXT_DELAY_MS, self.installNextMod)
                    return
                self.discardInterfaceWarningsFrom(warning_start)
                self.log(f"  Installed as: {internal_name}", "success")
                self.log(
                    "  Priority will be checked after collection install",
                )
                used_mod_names.add(internal_name)
                installed_mods.append(internal_name)
                installed_map[install_key] = internal_name
                self.markInstalledDownloadMetadata(context, install_key)
                if activate_after_install:
                    if activate_during_install:
                        self.activateModDuringInstall(internal_name)
                    else:
                        mods_to_activate.append(internal_name)
            else:
                reason = installNoResultReason(
                    _invalid_install_content_cancelled,
                    (warning["message"] for warning in current_warnings),
                )
                if _mod_exists_cancelled:
                    reason = (
                        "duplicate MO2 mod container for archive-default FOMOD "
                        "install; needs manual rename/merge choice"
                    )
                self.markDownloadedOnlyMetadata(context, install_key)
                failed_entries.append(
                    {
                        "mod": mod_name,
                        "file": file_name,
                        "mod_id": int(mod_id),
                        "file_id": int(file_id),
                        "archive": str(install_source_path),
                        "reason": reason,
                        **self.fomodInstallDiagnostics(
                            fomod_state,
                            use_archive_default_for_fomod,
                            target_mod_name,
                            dialog_handler_generation,
                        ),
                    }
                )
                self.logInstallIssue(reason, expected=True)
        except Exception as e:
            warning_count = self.collectInterfaceLogWarnings(
                log_path, log_offset, mod_name, file_name
            )
            if warning_count:
                self.log(
                    f"  Captured {warning_count} MO2 warning(s) for report",
                    "note",
                )
            self.logInstallIssue(
                f"Installation issue: {e}",
                expected=self.isExpectedInstallException(e),
            )
            self.markDownloadedOnlyMetadata(context, install_key)
            failed_entries.append(
                {
                    "mod": mod_name,
                    "file": file_name,
                    "mod_id": int(mod_id),
                    "file_id": int(file_id),
                    "archive": str(install_source_path),
                    "reason": str(e),
                }
            )

        self.log("")
        QTimer.singleShot(INSTALL_NEXT_DELAY_MS, self.installNextMod)

    def scheduleInstallerDefaultAdvancer(
        self, generation, mod_name, max_steps, remaining=800, advanced=0
    ):
        if remaining <= 0:
            return
        if advanced >= max_steps:
            self.fomod_auto_stalled_generations.add(generation)
            self.log(
                f"  FOMOD default auto-advance stopped after {advanced} step(s)",
                "note",
            )
            return
        if generation != self.dialog_handler_generation:
            return
        if not self.install_context:
            return

        selected_groups = self.fomod_auto_selected_groups.setdefault(generation, set())
        action = advanceInstallerDialogDefaults(selected_groups)
        if action:
            self.fomod_auto_no_action_counts[generation] = 0
            if not self.recordFomodAutoAction(generation, mod_name, action):
                return
            advanced += 1
        else:
            no_action_count = self.fomod_auto_no_action_counts.get(generation, 0) + 1
            self.fomod_auto_no_action_counts[generation] = no_action_count
            if no_action_count in {40, 160, 400, 760}:
                snapshots = self.visibleAutomationDialogSummaries()
                self.fomod_auto_visible_snapshots[generation] = snapshots
                message = "  FOMOD automation waiting; visible dialogs: " + (
                    "; ".join(snapshots) if snapshots else "none"
                )
                if no_action_count in {40, 160}:
                    self.log(message, "note")
                qDebug(f"[NXMColDL Install] {message}")
        QTimer.singleShot(
            FOMOD_ADVANCE_INTERVAL_MS,
            lambda: self.scheduleInstallerDefaultAdvancer(
                generation,
                mod_name,
                max_steps,
                remaining - 1,
                advanced,
            ),
        )

    def recordFomodAutoAction(self, generation, mod_name, action):
        title, button_label, selected_group_key = action
        selected_groups = self.fomod_auto_selected_groups.setdefault(generation, set())
        if selected_group_key:
            selected_groups.add(selected_group_key)
        action_key = (
            title,
            button_label,
            selected_group_key if button_label.startswith("select ") else None,
        )
        action_counts = self.fomod_auto_action_counts.setdefault(generation, {})
        action_counts[action_key] = action_counts.get(action_key, 0) + 1
        if button_label.startswith("select ") and action_counts[action_key] > 2:
            self.fomod_auto_stalled_generations.add(generation)
            self.log(
                "  FOMOD default auto-advance stopped on repeated action: "
                f"{button_label} on {title}",
                "note",
            )
            return False
        self.fomod_auto_advances.append(
            {
                "generation": generation,
                "mod": mod_name,
                "dialog": title,
                "action": button_label,
            }
        )
        self.log(
            f"  FOMOD default auto-advance: {button_label} on {title}",
            "note",
        )
        return True

    def traceInstallModCall(
        self,
        organizer,
        download_path,
        target_mod_name,
        phase,
        install_key,
        generation,
        allow_path_retry=True,
    ):
        archive_text = str(download_path)
        target_text = (
            target_mod_name if target_mod_name is not None else "<archive-default>"
        )
        trace_diagnostics = self.installerBoolSetting("trace_install_diagnostics")
        if trace_diagnostics:
            self.traceRuntimeInstallApi(organizer)
            visible_before = self.visibleAutomationDialogSummaries()
        else:
            visible_before = []
        exists_text, size_text = self.pathProbeSummary(archive_text)
        qDebug(
            "[NXMColDL InstallTrace] installMod start: "
            f"phase={phase}, key={install_key}, archive={archive_text}, "
            f"target={target_text}, generation={generation}, "
            f"path_probe={exists_text}, size={size_text}, visible_before={visible_before}"
        )
        started = time.monotonic()
        installed_mod = self.callInstallMod(
            organizer, archive_text, target_mod_name, phase, install_key, started
        )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        result_name = None
        result_type = type(installed_mod).__name__ if installed_mod else None
        if installed_mod:
            try:
                result_name = installed_mod.name()
            except Exception as e:
                result_name = f"<name failed: {e}>"

        installed_map_hit = None
        if trace_diagnostics or not installed_mod:
            try:
                installed_map = self.buildInstalledMap(Path(organizer.modsPath()))
                installed_map_hit = installed_map.get(install_key)
            except Exception as e:
                installed_map_hit = f"<installed-map failed: {e}>"

        visible_after = (
            self.visibleAutomationDialogSummaries() if trace_diagnostics else []
        )
        qDebug(
            "[NXMColDL InstallTrace] installMod return: "
            f"phase={phase}, key={install_key}, elapsed_ms={elapsed_ms}, "
            f"result_type={result_type}, result_name={result_name}, "
            f"installed_map_hit={installed_map_hit}, visible_after={visible_after}"
        )
        if not installed_mod:
            self.log(
                "  installMod returned no mod: "
                f"phase={phase}, elapsed_ms={elapsed_ms}, "
                f"installed_map_hit={installed_map_hit}",
                "note",
            )
            if not allow_path_retry:
                return installed_mod
            slash_archive_text = archive_text.replace("\\", "/")
            if slash_archive_text != archive_text:
                slash_exists, slash_size = self.pathProbeSummary(slash_archive_text)
                retry_existing_mod_action = "rename" if target_mod_name else None
                qDebug(
                    "[NXMColDL InstallTrace] installMod retry with slash path: "
                    f"phase={phase}, key={install_key}, archive={slash_archive_text}, "
                    f"path_probe={slash_exists}, size={slash_size}, "
                    f"retry_existing_mod_action={retry_existing_mod_action}"
                )
                if retry_existing_mod_action:
                    scheduleInstallDialogHandlers(
                        self.installerBoolSetting("auto_accept_quick_install"),
                        self.installerBoolSetting(
                            "auto_dismiss_known_post_install_errors"
                        ),
                        self.installerBoolSetting(
                            "auto_cancel_invalid_install_content"
                        ),
                        retry_existing_mod_action,
                        None,
                        lambda generation=generation: (
                            generation == self.dialog_handler_generation
                        ),
                        on_post_install_error=(
                            lambda message, mod_name=mod_name, file_name=file_name: (
                                self.recordSuppressedPostInstallError(
                                    message, mod_name, file_name
                                )
                            )
                        ),
                    )
                slash_started = time.monotonic()
                installed_mod = self.callInstallMod(
                    organizer,
                    slash_archive_text,
                    target_mod_name,
                    phase + "-slash-path",
                    install_key,
                    slash_started,
                )
                slash_elapsed_ms = int((time.monotonic() - slash_started) * 1000)
                slash_result_name = None
                slash_result_type = (
                    type(installed_mod).__name__ if installed_mod else None
                )
                if installed_mod:
                    try:
                        slash_result_name = installed_mod.name()
                    except Exception as e:
                        slash_result_name = f"<name failed: {e}>"
                try:
                    installed_map = self.buildInstalledMap(Path(organizer.modsPath()))
                    installed_map_hit = installed_map.get(install_key)
                except Exception as e:
                    installed_map_hit = f"<installed-map failed: {e}>"
                qDebug(
                    "[NXMColDL InstallTrace] installMod slash-path return: "
                    f"phase={phase}, key={install_key}, elapsed_ms={slash_elapsed_ms}, "
                    f"result_type={slash_result_type}, result_name={slash_result_name}, "
                    f"installed_map_hit={installed_map_hit}, "
                    f"visible_after={self.visibleAutomationDialogSummaries()}"
                )
        return installed_mod

    def callInstallMod(
        self, organizer, archive_text, target_mod_name, phase, install_key, started
    ):
        try:
            if target_mod_name is None:
                return organizer.installMod(archive_text)
            return organizer.installMod(archive_text, target_mod_name)
        except Exception as e:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            qDebug(
                "[NXMColDL InstallTrace] installMod exception: "
                f"phase={phase}, key={install_key}, elapsed_ms={elapsed_ms}, "
                f"archive={archive_text}, error={e}, traceback={traceback.format_exc()}, "
                f"visible_after={self.visibleAutomationDialogSummaries()}"
            )
            raise

    def pathProbeSummary(self, archive_text):
        try:
            exists = os.path.exists(archive_text)
        except Exception as e:
            return f"<exists failed: {e}>", None
        if not exists:
            return "missing", None
        try:
            return "exists", os.path.getsize(archive_text)
        except Exception as e:
            return "exists", f"<size failed: {e}>"

    def traceRuntimeInstallApi(self, organizer):
        if self.runtime_api_trace_logged:
            return
        self.runtime_api_trace_logged = True
        try:
            download_manager = organizer.downloadManager()
            methods = [
                name for name in dir(download_manager) if not name.startswith("_")
            ]
            qDebug(
                "[NXMColDL InstallTrace] Runtime downloadManager API: "
                + ", ".join(methods)
            )
        except Exception as e:
            qDebug(
                "[NXMColDL InstallTrace] Runtime downloadManager API introspection "
                f"failed: {e}"
            )
        try:
            install_mod_attr = getattr(organizer, "installMod", None)
            qDebug(
                "[NXMColDL InstallTrace] Runtime organizer.installMod attr: "
                f"type={type(install_mod_attr).__name__}, repr={install_mod_attr!r}"
            )
        except Exception as e:
            qDebug(
                "[NXMColDL InstallTrace] Runtime organizer.installMod introspection "
                f"failed: {e}"
            )
        try:
            organizer_methods = [
                name for name in dir(organizer) if not name.startswith("_")
            ]
            qDebug(
                "[NXMColDL InstallTrace] Runtime organizer API: "
                + ", ".join(organizer_methods)
            )
        except Exception as e:
            qDebug(
                "[NXMColDL InstallTrace] Runtime organizer API introspection "
                f"failed: {e}"
            )
        try:
            plugin_list = organizer.pluginList()
            plugin_list_methods = [
                name for name in dir(plugin_list) if not name.startswith("_")
            ]
            qDebug(
                "[NXMColDL InstallTrace] Runtime pluginList API: "
                + ", ".join(plugin_list_methods)
            )
            for accessor_name in ("plugins", "allPlugins"):
                accessor = getattr(plugin_list, accessor_name, None)
                if not callable(accessor):
                    continue
                plugins = accessor()
                plugin_summaries = []
                for plugin in plugins:
                    try:
                        plugin_name = plugin.name()
                    except Exception:
                        plugin_name = repr(plugin)
                    plugin_summaries.append(
                        f"{safeDisplayText(plugin_name)}<{type(plugin).__name__}>"
                    )
                qDebug(
                    "[NXMColDL InstallTrace] Runtime pluginList."
                    f"{accessor_name}: " + ", ".join(plugin_summaries)
                )
                break
        except Exception as e:
            qDebug(
                "[NXMColDL InstallTrace] Runtime pluginList introspection "
                f"failed: {e}, traceback={traceback.format_exc()}"
            )

    def recoverLateInstallerResult(
        self,
        organizer,
        install_key,
        generation,
        mod_name,
        existing_mod_action=None,
        existing_mod_target_name=None,
        timeout_seconds=30,
    ):
        """Drive installer dialogs that remain after MO2 returned no install result."""
        if not self.install_context:
            return None

        context = self.install_context
        auto_advance = context.get(
            "auto_advance_fomod_defaults",
            INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
        )
        max_steps = self.installerIntSetting("auto_advance_fomod_max_steps")
        auto_accept_quick_install = self.installerBoolSetting(
            "auto_accept_quick_install"
        )
        auto_dismiss_errors = self.installerBoolSetting(
            "auto_dismiss_known_post_install_errors"
        )
        auto_cancel_invalid = self.installerBoolSetting(
            "auto_cancel_invalid_install_content"
        )
        selected_groups = self.fomod_auto_selected_groups.setdefault(generation, set())
        advanced = len(
            [
                action
                for action in self.fomod_auto_advances
                if action.get("generation") == generation
            ]
        )
        deadline = time.monotonic() + timeout_seconds
        last_snapshots = []
        no_dialog_ticks = 0

        while time.monotonic() < deadline:
            QApplication.processEvents()
            if auto_accept_quick_install:
                acceptQuickInstallDialog()
            if auto_dismiss_errors:
                dismissKnownPostInstallErrorDialog(remaining=1)
            if auto_cancel_invalid:
                cancelInvalidInstallContentDialog()
                acceptContentTreeWarningDialog()
            if existing_mod_action:
                handleModExistsDialog(existing_mod_action)
                acceptModNameDialog(existing_mod_target_name)

            if auto_advance and max_steps and advanced < max_steps:
                action = advanceInstallerDialogDefaults(selected_groups)
                if action:
                    self.fomod_auto_no_action_counts[generation] = 0
                    if not self.recordFomodAutoAction(generation, mod_name, action):
                        return None
                    advanced += 1
                else:
                    self.fomod_auto_no_action_counts[generation] = (
                        self.fomod_auto_no_action_counts.get(generation, 0) + 1
                    )

            installed_map = self.buildInstalledMap(Path(organizer.modsPath()))
            if install_key in installed_map:
                return installed_map[install_key]

            last_snapshots = self.visibleAutomationDialogSummaries()
            if self.hasActionableAutomationDialog():
                self.fomod_auto_visible_snapshots[generation] = last_snapshots
                no_dialog_ticks = 0
            else:
                no_dialog_ticks += 1
                if no_dialog_ticks >= 10:
                    break
            time.sleep(0.05)

        if last_snapshots:
            self.log(
                "  Installer recovery timed out; visible dialogs: "
                + "; ".join(last_snapshots),
                "note",
            )
        else:
            self.log(
                "  Installer recovery timed out with no visible installer dialogs",
                "note",
            )
        return None

    def hasActionableAutomationDialog(self):
        actionable_titles = {
            "Quick Install",
            "Install Mods",
            "Mod Exists",
            "Mod Name",
            "Continue?",
        }
        for widget in QApplication.topLevelWidgets():
            if not widget.isVisible():
                continue
            if widget.windowTitle() in actionable_titles:
                return True
            if installerDefaultAction(widget):
                return True
        return False

    def visibleAutomationDialogSummaries(self):
        summaries = []
        for widget in QApplication.topLevelWidgets():
            if not widget.isVisible():
                continue

            title = safeDisplayText(widget.windowTitle()) or "<untitled>"
            class_name = widget.metaObject().className()
            if (
                title.startswith("NXM Collection Installer")
                and class_name == "stepInstallMods"
            ):
                continue
            buttons = []
            for button in widget.findChildren(QPushButton):
                label = normalizedButtonLabel(button.text())
                if label:
                    buttons.append(f"{label}:{'on' if button.isEnabled() else 'off'}")
            edits = []
            for line_edit in widget.findChildren(QLineEdit):
                if line_edit.isVisible():
                    edits.append(
                        f"{safeDisplayText(line_edit.text())}:"
                        f"{'on' if line_edit.isEnabled() else 'off'}"
                    )
            groups = []
            for group in widget.findChildren(QGroupBox):
                group_title = safeDisplayText(group.title()) or "<group>"
                states = []
                for button in group.findChildren(QAbstractButton):
                    if isinstance(button, QPushButton):
                        continue
                    label = normalizedButtonLabel(button.text())
                    if label:
                        states.append(
                            f"{label}:{'checked' if button.isChecked() else 'open'}:"
                            f"{'on' if button.isEnabled() else 'off'}"
                        )
                if states:
                    groups.append(f"{group_title}[{','.join(states[:5])}]")

            summaries.append(
                f"{title}<{class_name}> buttons={buttons[:8]} "
                f"edits={edits[:4]} groups={groups[:5]}"
            )

        return summaries[:6]

    def fomodInstallDiagnostics(
        self,
        fomod_state,
        use_archive_default_for_fomod,
        target_mod_name,
        initial_generation,
        fallback_generation=None,
    ):
        def state_label(value):
            if value is True:
                return "true"
            if value is False:
                return "false"
            return "unknown"

        def actions_for(generation):
            if generation is None:
                return []
            return [
                {
                    "dialog": action.get("dialog"),
                    "action": action.get("action"),
                }
                for action in self.fomod_auto_advances
                if action.get("generation") == generation
            ]

        return {
            "fomod_state": state_label(fomod_state),
            "used_archive_default_for_fomod": bool(use_archive_default_for_fomod),
            "target_mod_name": target_mod_name,
            "initial_auto_actions": actions_for(initial_generation),
            "initial_auto_stalled": (
                initial_generation in self.fomod_auto_stalled_generations
            ),
            "fallback_auto_actions": actions_for(fallback_generation),
            "fallback_auto_stalled": (
                fallback_generation in self.fomod_auto_stalled_generations
                if fallback_generation is not None
                else False
            ),
            "initial_no_action_count": self.fomod_auto_no_action_counts.get(
                initial_generation, 0
            ),
            "initial_visible_dialogs": self.fomod_auto_visible_snapshots.get(
                initial_generation, []
            ),
            "fallback_no_action_count": (
                self.fomod_auto_no_action_counts.get(fallback_generation, 0)
                if fallback_generation is not None
                else 0
            ),
            "fallback_visible_dialogs": (
                self.fomod_auto_visible_snapshots.get(fallback_generation, [])
                if fallback_generation is not None
                else []
            ),
        }

    def runInstallModWithRetryWindowHidden(self, hide_window, install_call):
        should_hide = bool(
            hide_window
            and self.isVisible()
            and AUTOMATED_INSTALL_CADENCE_DEFAULTS["hide_progress_for_native_install"]
        )
        if should_hide:
            self.hide()
            QApplication.processEvents()
            raiseMo2MainWindow()
        try:
            return install_call()
        finally:
            if should_hide:
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
                self.show()
                if AUTOMATED_INSTALL_CADENCE_DEFAULTS["restore_install_dialog_focus"]:
                    self.raise_()
                    self.activateWindow()
                QApplication.processEvents()

    def activateModDuringInstall(self, internal_name):
        if not self.install_context:
            return

        context = self.install_context
        if internal_name in context["activated_mods"]:
            return

        organizer = context["organizer"]
        modlist = context["modlist"]

        log_path, log_offset = self.captureInterfaceLogPosition(organizer)
        warning_start = len(self.install_warnings)
        self.dialog_handler_generation += 1
        dialog_handler_generation = self.dialog_handler_generation
        scheduleKnownPostInstallErrorDismissal(
            lambda generation=dialog_handler_generation: (
                generation == self.dialog_handler_generation
            ),
            on_post_install_error=(
                lambda message, internal_name=internal_name: (
                    self.recordSuppressedPostInstallError(
                        message, internal_name, "activation"
                    )
                )
            ),
        )
        try:
            modlist.setActive(internal_name, True)
            context["activated_mods"].append(internal_name)
            self.log(f"  Activated: {internal_name}", "success")
        except Exception as e:
            context["activation_failures"].append(
                {"mod": internal_name, "reason": str(e)}
            )
            self.logInstallIssue(f"Could not activate {internal_name}: {e}")
            return

        warning_count = self.collectInterfaceLogWarnings(
            log_path, log_offset, f"activating {internal_name}", ""
        )
        if warning_count:
            self.discardInterfaceWarningsFrom(warning_start)

        plugin_activation = self.activatePluginsForMods(
            organizer,
            [internal_name],
            heading=f"Activating plugins from {internal_name}...",
            failed_entries=context["failed_entries"],
        )
        for key in ("activated", "already_active", "blocked"):
            context["plugin_activation"][key] += plugin_activation[key]

    def markInstalledDownloadMetadata(self, context, install_key):
        """Set MO2's downloaded archive sidecar to installed=true for one key."""
        return self.markInstalledDownloadMetadataKeys(context, {install_key})

    def markDownloadedOnlyMetadata(self, context, install_key):
        """Set MO2's downloaded archive sidecar to installed=false for one key."""
        try:
            result = repairDownloadMetadataInstalledFlags(
                Path(context["organizer"].downloadsPath()),
                {install_key},
                desired_installed=False,
                expected_file_names=context.get("expected_file_names", {}),
            )
        except Exception as e:
            self.logInstallIssue(
                "Could not mark download metadata downloaded-only for "
                f"{install_key}: {e}",
                expected=True,
            )
            return {"checked": 0, "repaired": 0, "failed": 1}

        if result.get("repaired"):
            self.log(
                "  Marked MO2 download metadata downloaded-only for "
                f"{result['repaired']} Nexus file(s)",
                "note",
            )
        if result.get("failed"):
            self.logInstallIssue(
                f"Could not mark {result['failed']} download metadata file(s) "
                f"downloaded-only for {install_key}",
                expected=True,
            )
        return result

    def markInstalledDownloadMetadataKeys(self, context, install_keys):
        """Set MO2's downloaded archive sidecars to installed=true in one pass."""
        try:
            result = repairDownloadMetadataInstalledFlags(
                Path(context["organizer"].downloadsPath()),
                set(install_keys),
                desired_installed=True,
                expected_file_names=context.get("expected_file_names", {}),
            )
        except Exception as e:
            self.logInstallIssue(
                "Could not mark download metadata installed for "
                f"{sorted(install_keys)}: {e}",
                expected=True,
            )
            return {"checked": 0, "repaired": 0, "failed": 1}

        if result.get("repaired"):
            self.log(
                "  Marked MO2 download metadata installed for "
                f"{result['repaired']} Nexus file(s)",
                "note",
            )
        if result.get("failed"):
            self.logInstallIssue(
                f"Could not repair {result['failed']} download metadata file(s) "
                f"for {sorted(install_keys)}",
                expected=True,
            )
        return result

    def refreshInstalledCollectionState(self, context):
        """Discover installed collection mods, repair metadata, and queue activation."""
        organizer = context["organizer"]
        mods_path = Path(organizer.modsPath())
        downloads_path = Path(organizer.downloadsPath())
        expected_file_names = context.get("expected_file_names", {})
        expected_keys = set(context.get("expected_nexus_keys", set()))
        no_applicable_keys = {
            (int(entry["mod_id"]), int(entry["file_id"]))
            for entry in context.get("no_applicable_entries", [])
            if entry.get("mod_id") is not None and entry.get("file_id") is not None
        }
        installed_records = installedModRecordsFromDirectory(
            mods_path,
            downloads_path,
            expected_file_names=expected_file_names,
        )
        installed_keys = set(installed_records)
        expected_installed_keys = (
            expected_keys & installed_keys if expected_keys else installed_keys
        )
        expected_installed_keys -= no_applicable_keys

        installed_map = context["installed_map"]
        installed_mods = context["installed_mods"]
        mods_to_activate = context["mods_to_activate"]
        known_installed_mods = set(installed_mods)
        known_activation_targets = set(mods_to_activate) | set(
            context["activated_mods"]
        )

        ordered_keys = []
        seen_keys = set()
        for mod_info in context.get("mods_to_install", []):
            nexus_key = collectionEntryNexusKey(mod_info)
            if nexus_key in expected_installed_keys and nexus_key not in seen_keys:
                ordered_keys.append(nexus_key)
                seen_keys.add(nexus_key)
        for nexus_key in sorted(expected_installed_keys - seen_keys):
            ordered_keys.append(nexus_key)

        discovered_names = []
        invalid_payload_mods = []
        invalid_payload_keys = set()
        invalid_payload_quarantine = {"moved": [], "missing": [], "failed": []}
        valid_installed_keys = set()
        layout_repairs = 0
        for nexus_key in ordered_keys:
            mod_names = installed_records.get(nexus_key, [])
            valid_mod_names = []
            for mod_name in mod_names:
                mod_dir = mods_path / mod_name
                if repairSingleWrapperPayload(mod_dir):
                    layout_repairs += 1
                    self.log(
                        f"Repaired single-wrapper game data layout for {mod_name}.",
                        "note",
                    )
                if not headlessPayloadRootValid(mod_dir):
                    invalid_payload_mods.append(mod_name)
                    invalid_payload_keys.add(nexus_key)
                    continue
                valid_mod_names.append(mod_name)
                discovered_names.append(mod_name)
                if mod_name not in known_installed_mods:
                    installed_mods.append(mod_name)
                    known_installed_mods.add(mod_name)
                if (
                    context["activation_enabled"]
                    and mod_name not in known_activation_targets
                ):
                    mods_to_activate.append(mod_name)
                    known_activation_targets.add(mod_name)
            if valid_mod_names:
                installed_map[nexus_key] = valid_mod_names[0]
                valid_installed_keys.add(nexus_key)

        expected_installed_keys = valid_installed_keys
        if invalid_payload_mods:
            invalid_metadata_repair = repairDownloadMetadataInstalledFlags(
                downloads_path,
                invalid_payload_keys,
                desired_installed=False,
                expected_file_names=expected_file_names,
            )
            if invalid_metadata_repair.get("repaired"):
                self.log(
                    "Marked download metadata downloaded-only for "
                    f"{invalid_metadata_repair['repaired']} invalid collection "
                    "container(s).",
                    "note",
                )
            if invalid_metadata_repair.get("failed"):
                self.logInstallIssue(
                    "Could not mark "
                    f"{invalid_metadata_repair['failed']} invalid download metadata "
                    "file(s) downloaded-only",
                    expected=True,
                )
            quarantine_dir = (
                downloads_path.parent
                / "logs"
                / "nxm-collection-invalid-payloads"
                / datetime.now().strftime("%Y%m%d-%H%M%S")
            )
            invalid_payload_quarantine = quarantineInvalidPayloadModContainers(
                mods_path, invalid_payload_mods, quarantine_dir
            )
            if invalid_payload_quarantine.get("moved"):
                self.log(
                    "Moved invalid collection container(s) out of active mods: "
                    f"{len(invalid_payload_quarantine['moved'])}",
                    "note",
                )
            if invalid_payload_quarantine.get("failed"):
                for failure in invalid_payload_quarantine["failed"]:
                    self.logInstallIssue(
                        f"Could not move invalid collection container: {failure}",
                        expected=True,
                    )

        metadata_repair = repairDownloadMetadataInstalledFlags(
            downloads_path,
            expected_installed_keys,
            desired_installed=True,
            expected_file_names=expected_file_names,
        )
        if metadata_repair.get("repaired"):
            self.log(
                "Repaired MO2 download metadata for "
                f"{metadata_repair['repaired']} installed archive(s).",
                "note",
            )
        if metadata_repair.get("failed"):
            self.logInstallIssue(
                "Could not repair "
                f"{metadata_repair['failed']} installed download metadata file(s)",
                expected=True,
            )

        mods_by_key = {}
        for mod_info in context.get("mods_to_install", []):
            nexus_key = collectionEntryNexusKey(mod_info)
            if nexus_key is not None and nexus_key not in mods_by_key:
                mods_by_key[nexus_key] = mod_info
        mod_metadata_repair = repairInstalledCollectionModMetadata(
            mods_path,
            installed_records,
            mods_by_key,
            category_name_map=mo2CategoryNameMap(
                Path(organizer.basePath()) / "categories.dat"
            ),
        )
        if mod_metadata_repair.get("repaired"):
            self.log(
                "Repaired installed mod metadata for "
                f"{mod_metadata_repair['repaired']} collection container(s).",
                "note",
            )
        if mod_metadata_repair.get("failed"):
            self.logInstallIssue(
                "Could not repair "
                f"{mod_metadata_repair['failed']} installed mod metadata file(s)",
                expected=True,
            )

        if discovered_names:
            self.log(
                "Post-install state sweep: "
                f"{len(expected_installed_keys)} collection Nexus file(s), "
                f"{len(set(discovered_names))} MO2 mod container(s) installed.",
                "note",
            )
        if invalid_payload_mods:
            self.log(
                "Collection containers without valid game-data payload were "
                "moved out of active mods: "
                f"{len(invalid_payload_quarantine.get('moved', []))}/"
                f"{len(invalid_payload_mods)}",
                "note",
            )
        return {
            "installed_keys": len(installed_keys),
            "expected_installed_keys": len(expected_installed_keys),
            "discovered_mods": len(set(discovered_names)),
            "layout_repaired": layout_repairs,
            "metadata_repaired": metadata_repair.get("repaired", 0),
            "metadata_failed": metadata_repair.get("failed", 0),
            "mod_metadata_repaired": mod_metadata_repair.get("repaired", 0),
            "mod_metadata_failed": mod_metadata_repair.get("failed", 0),
            "invalid_payload_mods": invalid_payload_mods,
            "invalid_payload_keys": sorted(invalid_payload_keys),
            "invalid_payload_quarantine": invalid_payload_quarantine,
        }

    def finishInstallation(self, cancelled=False):
        if not self.install_context:
            return
        self.dialog_handler_generation += 1

        context = self.install_context
        organizer = context["organizer"]
        modlist = context["modlist"]
        mods_to_install = context["mods_to_install"]
        installed_mods = context["installed_mods"]
        failed_entries = context["failed_entries"]
        root_level_entries = context.get("root_level_entries", [])
        no_applicable_entries = context.get("no_applicable_entries", [])
        manual_install_pass = context.get("manual_install_pass", False)
        normal_dialog_retry_pass = context.get("normal_dialog_retry_pass", False)
        mods_to_activate = context["mods_to_activate"]
        activation_enabled = context["activation_enabled"]
        priority_order = None
        activated_count = len(context["activated_mods"])
        activation_failures = context["activation_failures"]
        plugin_activation = dict(context["plugin_activation"])
        fast_summary_only = context.get(
            "install_plan_fast_finished"
        ) and not context.get("headless_installed_count")

        if context.get("headless_installed_count"):
            try:
                organizer.refresh(True)
                self.log(
                    "Refreshed MO2 after "
                    f"{context['headless_installed_count']} headless install(s).",
                    "note",
                )
            except Exception as e:
                self.logInstallIssue(
                    f"Could not refresh MO2 after headless installs: {e}",
                    expected=True,
                )

        if fast_summary_only:
            self.log(
                "No install work remains; validating installed state before "
                "summary.",
                "note",
            )
        postcondition_state = self.refreshInstalledCollectionState(context)
        transient_cleanup = removeStaleCollectionTransientDirs(Path(organizer.modsPath()))
        if transient_cleanup["removed"]:
            self.log(
                "Removed stale collection transient mod folder(s): "
                f"{len(transient_cleanup['removed'])}",
                "note",
            )
            try:
                organizer.refresh(True)
            except Exception as e:
                self.logInstallIssue(
                    f"Could not refresh MO2 after stale transient cleanup: {e}",
                    expected=True,
                )
        for cleanup_failure in transient_cleanup["failed"]:
            self.logInstallIssue(
                f"Could not remove stale collection transient folder: "
                f"{cleanup_failure}",
                expected=True,
            )
        invalid_payload_mods = set(postcondition_state.get("invalid_payload_mods", []))
        if invalid_payload_mods:
            invalid_payload_keys = {
                tuple(key)
                for key in postcondition_state.get("invalid_payload_keys", [])
                if isinstance(key, (list, tuple)) and len(key) == 2
            }
            failed_keys = {
                (int(entry["mod_id"]), int(entry["file_id"]))
                for entry in failed_entries
                if entry.get("mod_id") is not None and entry.get("file_id") is not None
            }
            entries_by_key = {}
            for mod_info in mods_to_install:
                nexus_key = collectionEntryNexusKey(mod_info)
                if nexus_key is not None and nexus_key not in entries_by_key:
                    entries_by_key[nexus_key] = mod_info
            for nexus_key in sorted(invalid_payload_keys - failed_keys):
                mod_info = entries_by_key.get(nexus_key)
                if mod_info is None:
                    continue
                failed_entries.append(
                    {
                        "mod": mod_info["file"]["mod"]["name"],
                        "file": mod_info["file"]["name"],
                        "mod_id": int(nexus_key[0]),
                        "file_id": int(nexus_key[1]),
                        "reason": "installed container has no valid game data",
                    }
                )

            mods_to_activate = [
                name for name in mods_to_activate if name not in invalid_payload_mods
            ]
            context["mods_to_activate"] = mods_to_activate
            installed_mods[:] = [
                name for name in installed_mods if name not in invalid_payload_mods
            ]
            disabled_count = 0
            for internal_name in sorted(invalid_payload_mods):
                try:
                    modlist.setActive(internal_name, False)
                    disabled_count += 1
                except Exception as e:
                    self.logInstallIssue(
                        f"Could not disable invalid collection container "
                        f"{internal_name}: {e}",
                        expected=True,
                    )
            if disabled_count:
                self.log(
                    "Disabled "
                    f"{disabled_count} collection container(s) without valid game data.",
                    "note",
                )

        if installed_mods:
            priority_order = self.reconcileCollectionPriorityOrder(
                organizer,
                modlist,
                installed_mods,
                context.get("base_priority"),
            )
            self.log("")

        if mods_to_activate:
            self.progress_label.setText("Activating installed mods...")
            mods_to_activate = list(dict.fromkeys(mods_to_activate))
            self.log(f"Activating {len(mods_to_activate)} installed mods...")
            log_path, log_offset = self.captureInterfaceLogPosition(organizer)
            warning_start = len(self.install_warnings)
            activation_failure_start = len(activation_failures)
            self.dialog_handler_generation += 1
            dialog_handler_generation = self.dialog_handler_generation
            scheduleKnownPostInstallErrorDismissal(
                lambda generation=dialog_handler_generation: (
                    generation == self.dialog_handler_generation
                ),
                on_post_install_error=(
                    lambda message: self.recordSuppressedPostInstallError(
                        message, "post-install activation", ""
                    )
                ),
            )
            for internal_name in mods_to_activate:
                try:
                    modlist.setActive(internal_name, True)
                    activated_count += 1
                except Exception as e:
                    activation_failures.append({"mod": internal_name, "reason": str(e)})
                    self.logInstallIssue(f"Could not activate {internal_name}: {e}")
            warning_count = self.collectInterfaceLogWarnings(
                log_path, log_offset, "post-install activation", ""
            )
            if warning_count and len(activation_failures) == activation_failure_start:
                self.discardInterfaceWarningsFrom(warning_start)
            self.log("")

        plugin_activation_targets = collectionPluginActivationTargetModNames(
            installed_mods, mods_to_activate
        )
        if activation_enabled and plugin_activation_targets:
            self.dialog_handler_generation += 1
            dialog_handler_generation = self.dialog_handler_generation
            scheduleKnownPostInstallErrorDismissal(
                lambda generation=dialog_handler_generation: (
                    generation == self.dialog_handler_generation
                ),
                on_post_install_error=(
                    lambda message: self.recordSuppressedPostInstallError(
                        message, "plugin activation", ""
                    )
                ),
            )
            final_plugin_activation = self.activatePluginsForMods(
                organizer, plugin_activation_targets, failed_entries=failed_entries
            )
            for key in ("activated", "already_active", "blocked"):
                plugin_activation[key] += final_plugin_activation[key]
            self.log("")

        can_queue_fomod_recovery = (
            bool(failed_entries)
            and not cancelled
            and not manual_install_pass
            and not normal_dialog_retry_pass
            and context.get(
                "auto_advance_fomod_defaults",
                INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
            )
        )
        review_entries, queued_fomod_recovery_entries = (
            splitQueuedFomodRecoveryEntries(
                failed_entries, can_queue_fomod_recovery
            )
        )
        review_entries.extend(
            suppressedPostInstallErrorReviewEntries(self.install_warnings)
        )
        queued_recovery_count = len(queued_fomod_recovery_entries)

        self.progress_bar.setValue(len(mods_to_install))
        failed_count = len(review_entries)
        if cancelled:
            self.progress_label.setText("Installation cancelled.")
        elif failed_count:
            self.progress_label.setText("Installation completed; review needed.")
        elif queued_recovery_count:
            self.progress_label.setText("Installation completed; recovery queued.")
        else:
            self.progress_label.setText("Installation complete!")
        self.log("=" * 50)
        if cancelled:
            self.log("Installation Summary (cancelled):", "warning")
        elif manual_install_pass and failed_count:
            self.log("Manual Install Summary (review needed):", "note")
        elif manual_install_pass:
            self.log("Manual Install Summary:", "success")
        elif queued_recovery_count and failed_count:
            self.log("Installation Summary (queued recovery; review needed):", "note")
        elif queued_recovery_count:
            self.log("Installation Summary (queued recovery):", "note")
        elif failed_count:
            self.log("Installation Summary (review needed):", "note")
        else:
            self.log("Installation Summary:", "success")
        installed_containers = len(set(installed_mods))
        review_count = len(review_entries)
        root_level_count = len(root_level_entries)
        no_applicable_count = len(no_applicable_entries)
        total_entries = context.get("collection_total_entries", len(mods_to_install))
        collection_metadata = context.get("collection_metadata", {})
        recovery_count = 0
        launch_count = 0
        try:
            recovery_count = int(
                collection_metadata.get("addCollectionRecoveryCount", 0)
            )
            launch_count = int(collection_metadata.get("addCollectionLaunchCount", 0))
        except (TypeError, ValueError):
            recovery_count = 0
            launch_count = 0
        self.log(f"  Collection file entries: {total_entries}")
        if recovery_count > 0:
            self.log(
                "  Add Collection recovery launches: "
                f"{recovery_count} recovery after {launch_count} total launches",
                "warning",
            )
        completed_entries = (
            len(mods_to_install) - review_count - queued_recovery_count
        )
        self.log(f"  Entries completed/root-handled: {completed_entries}")
        if review_count:
            self.log(
                "  Entries downloaded but not installed: "
                f"{review_count} (use Retry Failed Manually)"
            )
        else:
            self.log("  Entries downloaded but not installed: 0")
        if queued_recovery_count:
            self.log(
                "  Entries queued for main-window FOMOD recovery: "
                f"{queued_recovery_count}",
                "note",
            )
        if root_level_count:
            self.log(
                f"  Root/game-directory entries noted: {root_level_count}",
                "note",
            )
        if no_applicable_count:
            self.log(
                "  Entries left downloaded-only/no applicable files: "
                f"{no_applicable_count}",
                "note",
            )
        self.log(f"  MO2 mod containers touched: {installed_containers}")
        self.log(
            "  Installed download metadata: "
            f"{postcondition_state['expected_installed_keys']} verified, "
            f"{postcondition_state['metadata_repaired']} repaired, "
            f"{postcondition_state['metadata_failed']} failed"
        )
        if priority_order:
            self.log(
                "  Collection priority order: "
                f"{priority_order['verified']} verified, "
                f"{priority_order['moved']} moved, "
                f"{priority_order['failed']} failed"
            )
        if activation_enabled:
            self.log(f"  Activated installed mods: {activated_count}")
            self.log(
                "  Plugins from activated mods: "
                f"{plugin_activation['activated']} activated, "
                f"{plugin_activation['already_active']} already active, "
                f"{plugin_activation['blocked']} blocked"
            )
            if activation_failures:
                self.log(
                    f"  Mod activation failures: {len(activation_failures)}",
                    "warning",
                )
        else:
            self.log("  Activated installed mods: 0 (activation disabled)")
        self.log(f"  Failed/skipped collection entries: {review_count}")
        if review_entries:
            self.log("  Entries needing review:", "note")
            for entry in review_entries[:20]:
                self.log(
                    "    "
                    f"{safeDisplayText(entry['mod'])} - "
                    f"{safeDisplayText(entry['file'])}: "
                    f"{safeDisplayText(entry['reason'])}",
                    "note",
                )
            if len(review_entries) > 20:
                self.log(
                    f"    ... {len(review_entries) - 20} more omitted from dialog",
                    "note",
                )
        if queued_fomod_recovery_entries:
            self.log("  Queued FOMOD recovery entries:", "note")
            recovery_by_key = {
                (entry.get("archive"), entry.get("target")): entry
                for entry in queued_fomod_recovery_entries
            }
            for entry in failed_entries[:20]:
                recovery_entry = recovery_by_key.get(
                    (
                        entry.get("archive"),
                        entry.get("target_mod_name") or entry.get("mod"),
                    )
                )
                if recovery_entry is None:
                    continue
                self.log(
                    "    "
                    f"{safeDisplayText(entry['mod'])} - "
                    f"{safeDisplayText(entry['file'])}: queued for main-window "
                    "Next/Install automation",
                    "note",
                )
        if root_level_entries:
            self.log("  Root/game-directory entries:", "note")
            for entry in root_level_entries[:10]:
                self.log(
                    "    "
                    f"{safeDisplayText(entry['mod'])} - "
                    f"{safeDisplayText(entry['file'])}: "
                    f"{safeDisplayText(entry['reason'])}",
                    "note",
                )
            if len(root_level_entries) > 10:
                self.log(
                    f"    ... {len(root_level_entries) - 10} more omitted from dialog",
                    "note",
                )
        if no_applicable_entries:
            self.log("  Downloaded-only/no applicable file entries:", "note")
            for entry in no_applicable_entries[:10]:
                self.log(
                    "    "
                    f"{safeDisplayText(entry['mod'])} - "
                    f"{safeDisplayText(entry['file'])}: "
                    f"{safeDisplayText(entry['reason'])}",
                    "note",
                )
            if len(no_applicable_entries) > 10:
                self.log(
                    f"    ... {len(no_applicable_entries) - 10} more omitted "
                    "from dialog",
                    "note",
                )
        warning_occurrences = self.totalInterfaceWarningOccurrences()
        self.log(
            "  MO2 warnings captured: "
            f"{warning_occurrences} occurrences, "
            f"{len(self.install_warnings)} unique"
        )
        if warning_occurrences:
            summary = ", ".join(
                f"{item['category']}={item['occurrences']}"
                for item in self.warningSummaryForReport()[:6]
            )
            self.log(f"  MO2 warning summary: {summary}", "note")
        self.log(f"  FOMOD default auto-advances: {len(self.fomod_auto_advances)}")
        report_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = self.writeWarningReport(organizer, report_timestamp)
        self.warning_report_path = report_path
        self.open_warning_report_btn.setEnabled(bool(report_path))
        if report_path:
            self.log(f"  Warning report: {report_path}", "note")
        guide_path = self.writeFomodChoiceGuide(
            organizer, review_entries, report_timestamp
        )
        self.fomod_guide_path = guide_path
        self.open_fomod_guide_btn.setEnabled(bool(guide_path))
        if guide_path:
            self.log(f"  FOMOD/manual install guide: {guide_path}", "note")
        qDebug(
            "[NXMColDL] Installation finished: "
            f"{completed_entries}/{len(mods_to_install)} completed/root-handled, "
            f"{failed_count} failed/skipped"
        )

        self.install_finished = True
        self.install_successful = (
            not cancelled and failed_count == 0 and queued_recovery_count == 0
        )

        if review_entries:
            self.log("", "note")
            self.log(
                "Some mods were not installed. Use Retry Failed Manually to retry "
                "the remaining entries with normal MO2 installer dialogs, or review "
                "the generated reports.",
                "note",
            )

        self.last_failed_entries = list(review_entries)
        self.close_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.manual_install_btn.setEnabled(bool(review_entries))
        if not queued_recovery_count and shouldAutoCloseInstallSummary(
            self.auto_close_on_success, cancelled, failed_count, recovery_count
        ):
            self.log(
                "Successful automatic install; closing summary dialog.",
                "success",
            )
            qDebug("[NXMColDL] Successful automatic install summary closing")
            QTimer.singleShot(250, self.accept)
            return
        if (
            self.auto_close_on_success
            and not cancelled
            and failed_count == 0
            and recovery_count > 0
        ):
            self.log(
                "Successful recovery install; summary left open for review.",
                "note",
            )
        if queued_fomod_recovery_entries:
            batch_entries = list(queued_fomod_recovery_entries)
            if batch_entries:
                batch_entries.append(
                    {
                        "command": "finalize-probe-installs",
                        "activate_completed": True,
                        "priority_completed": True,
                        "activate_plugins": True,
                    }
                )
                self.log(
                    "Queuing confirmed FOMOD entries for main-window "
                    f"Next/Install automation: {len(batch_entries) - 1}",
                    "note",
                )
                QTimer.singleShot(0, self.accept)
                QTimer.singleShot(
                    1000,
                    lambda entries=batch_entries: __meta__.queueInstallProbeBatch(
                        entries
                    ),
                )

    def reconcileCollectionPriorityOrder(
        self, organizer, modlist, installed_mods, base_priority=None
    ):
        ordered_mods = list(dict.fromkeys(installed_mods))
        result = {"verified": 0, "moved": 0, "failed": 0}
        if not ordered_mods:
            return result

        priority_by_mod = {}
        missing_mods = []
        for mod_name in ordered_mods:
            try:
                priority_by_mod[mod_name] = modlist.priority(mod_name)
            except Exception as e:
                missing_mods.append(mod_name)
                self.logInstallIssue(
                    f"Could not read priority for {mod_name}: {e}",
                    expected=True,
                )

        present_mods = [name for name in ordered_mods if name in priority_by_mod]
        if not present_mods:
            result["failed"] = len(missing_mods)
            return result

        current_priorities = [priority_by_mod[name] for name in present_mods]
        if not collectionPriorityOrderNeedsRepair(current_priorities, base_priority):
            result["verified"] = len(present_mods)
            result["failed"] = len(missing_mods)
            self.log(
                f"Collection priority order verified for {len(present_mods)} mods",
                "success",
            )
            return result

        target_priority = min(current_priorities)
        try:
            if base_priority is not None:
                target_priority = max(target_priority, int(base_priority))
        except (TypeError, ValueError):
            pass
        self.log(
            "Collection priority order differed from the manifest; repairing...",
            "note",
        )

        # Moving each touched mod to the block start in reverse manifest order
        # yields the requested ascending priority order while keeping the
        # collection as one contiguous block.
        for mod_name in reversed(present_mods):
            try:
                if modlist.setPriority(mod_name, target_priority):
                    result["moved"] += 1
                else:
                    result["failed"] += 1
                    self.logInstallIssue(
                        f"Could not set priority for {mod_name}",
                        expected=True,
                    )
            except Exception as e:
                result["failed"] += 1
                self.logInstallIssue(
                    f"Could not set priority for {mod_name}: {e}",
                    expected=True,
                )

        try:
            repaired_priorities = [modlist.priority(name) for name in present_mods]
        except Exception:
            repaired_priorities = []
        if not collectionPriorityOrderNeedsRepair(repaired_priorities, base_priority):
            result["verified"] = len(present_mods)
            self.log(
                f"Collection priority order repaired for {len(present_mods)} mods",
                "success",
            )
        else:
            fallback_result = self.repairCollectionModlistFileOrder(
                organizer, present_mods
            )
            if fallback_result["failed"]:
                result["failed"] += len(present_mods)
                self.logInstallIssue(
                    "Collection priority order still differs after repair",
                    expected=True,
                )
            else:
                result["moved"] += fallback_result["moved"]
                result["verified"] = len(present_mods)
                self.log(
                    "Collection priority order repaired on disk for "
                    f"{fallback_result['moved']} mod(s)",
                    "success",
                )

        result["failed"] += len(missing_mods)
        return result

    def repairCollectionModlistFileOrder(self, organizer, present_mods):
        if organizer is None:
            return {"moved": 0, "missing": list(present_mods), "failed": 1}
        profile_path = Path(organizer.profilePath())
        backup_dir = (
            profile_path
            / "nxm-collection-dl-backups"
            / datetime.now().strftime("priority-repair-%Y%m%d-%H%M%S")
        )
        repair_result = moveModlistEntriesToUiBottom(
            profile_path / "modlist.txt", present_mods, backup_dir=backup_dir
        )
        if repair_result.get("missing"):
            repair_result["failed"] = repair_result.get("failed", 0) + len(
                repair_result["missing"]
            )
            self.logInstallIssue(
                "Collection mod(s) missing from profile modlist during disk "
                "priority repair: "
                + ", ".join(
                    safeDisplayText(name) for name in repair_result["missing"][:10]
                ),
                expected=True,
            )
        try:
            organizer.refresh(True)
        except Exception as e:
            repair_result["failed"] = repair_result.get("failed", 0) + 1
            self.logInstallIssue(
                f"Could not refresh MO2 after profile priority repair: {e}",
                expected=True,
            )
        return repair_result

    def activatePluginsForMods(
        self, organizer, mod_names, heading=None, failed_entries=None
    ):
        plugin_names_from_dirs = collectionPluginNamesFromModDirs(
            Path(organizer.modsPath()), mod_names
        )
        blocked = 0

        self.log(heading or "Activating plugins from installed mods...")

        profile_plugins_path = Path(organizer.profilePath()) / "plugins.txt"
        file_repair = repairPluginEnabledStates(
            profile_plugins_path, plugin_names_from_dirs
        )
        activated = file_repair.get("enabled", 0)
        already_active = file_repair.get("already_enabled", 0)
        missing_plugins = file_repair.get("missing", [])
        if file_repair.get("enabled"):
            self.log(
                "  Profile plugin list repaired: "
                f"{file_repair['enabled']} plugin(s) enabled on disk",
                "success",
            )
        if missing_plugins:
            blocked += len(missing_plugins)
            self.logInstallIssue(
                "Plugin(s) not present in profile plugin list after refresh: "
                + ", ".join(safeDisplayText(name) for name in missing_plugins[:10]),
                expected=True,
            )
            if failed_entries is not None:
                seen_review_entries = {
                    (
                        str(entry.get("mod") or ""),
                        str(entry.get("file") or ""),
                        str(entry.get("reason") or ""),
                    )
                    for entry in failed_entries
                    if isinstance(entry, dict)
                }
                for entry in pluginActivationReviewEntries(missing_plugins):
                    key = (
                        str(entry.get("mod") or ""),
                        str(entry.get("file") or ""),
                        str(entry.get("reason") or ""),
                    )
                    if key in seen_review_entries:
                        continue
                    seen_review_entries.add(key)
                    failed_entries.append(entry)
        if file_repair.get("failed"):
            blocked += file_repair["failed"]
            self.logInstallIssue(
                "Could not repair profile plugin enabled state on disk",
                expected=True,
            )
            if failed_entries is not None:
                seen_review_entries = {
                    (
                        str(entry.get("mod") or ""),
                        str(entry.get("file") or ""),
                        str(entry.get("reason") or ""),
                    )
                    for entry in failed_entries
                    if isinstance(entry, dict)
                }
                for entry in pluginRepairFailureReviewEntries(file_repair["failed"]):
                    key = (
                        str(entry.get("mod") or ""),
                        str(entry.get("file") or ""),
                        str(entry.get("reason") or ""),
                    )
                    if key in seen_review_entries:
                        continue
                    seen_review_entries.add(key)
                    failed_entries.append(entry)
        blocked += self.auditPluginMasterDependencies(
            organizer, plugin_names_from_dirs, failed_entries=failed_entries
        )
        self.log(
            f"  Plugin activation: {activated} activated, "
            f"{already_active} already active, {blocked} blocked"
        )
        return {
            "activated": activated,
            "already_active": already_active,
            "blocked": blocked,
        }

    def auditPluginMasterDependencies(
        self, organizer, plugin_names, failed_entries=None
    ):
        target_plugins = [str(name or "").strip() for name in plugin_names or []]
        target_plugins = [name for name in target_plugins if name]
        if not target_plugins:
            return 0

        try:
            plugin_list = organizer.pluginList()
        except Exception as e:
            self.logInstallIssue(
                f"Could not inspect MO2 plugin master dependencies: {e}",
                expected=True,
            )
            return 0

        try:
            available_plugins = list(plugin_list.pluginNames())
        except Exception as e:
            self.logInstallIssue(
                f"Could not read MO2 plugin list for dependency audit: {e}",
                expected=True,
            )
            return 0

        active_plugins = []
        for plugin_name in available_plugins:
            try:
                if plugin_list.state(plugin_name) == mobase.PluginState.ACTIVE:
                    active_plugins.append(plugin_name)
            except Exception as e:
                self.logInstallIssue(
                    "Could not read plugin state during dependency audit: "
                    f"{safeDisplayText(plugin_name)} ({e})",
                    expected=True,
                )

        available_by_key = {
            str(plugin_name or "").casefold(): str(plugin_name or "")
            for plugin_name in available_plugins
            if str(plugin_name or "").strip()
        }
        masters_by_plugin = {}
        for plugin_name in target_plugins:
            canonical_name = available_by_key.get(plugin_name.casefold(), plugin_name)
            try:
                masters_by_plugin[plugin_name] = list(
                    plugin_list.masters(canonical_name)
                )
            except Exception as e:
                self.logInstallIssue(
                    "Could not read plugin masters during dependency audit: "
                    f"{safeDisplayText(plugin_name)} ({e})",
                    expected=True,
                )

        problems = pluginMasterDependencyAudit(
            target_plugins, available_plugins, active_plugins, masters_by_plugin
        )
        if not problems:
            return 0

        for problem in problems[:10]:
            details = []
            missing = problem.get("missing_masters") or []
            inactive = problem.get("inactive_masters") or []
            if missing:
                details.append(
                    "missing "
                    + ", ".join(safeDisplayText(name) for name in missing[:5])
                )
            if inactive:
                details.append(
                    "inactive "
                    + ", ".join(safeDisplayText(name) for name in inactive[:5])
                )
            self.logInstallIssue(
                "Plugin master dependency issue: "
                f"{safeDisplayText(problem.get('plugin'))} "
                + "; ".join(details),
                expected=True,
            )

        if failed_entries is not None:
            seen_review_entries = {
                (
                    str(entry.get("mod") or ""),
                    str(entry.get("file") or ""),
                    str(entry.get("reason") or ""),
                )
                for entry in failed_entries
                if isinstance(entry, dict)
            }
            for entry in pluginMasterDependencyReviewEntries(problems):
                key = (
                    str(entry.get("mod") or ""),
                    str(entry.get("file") or ""),
                    str(entry.get("reason") or ""),
                )
                if key in seen_review_entries:
                    continue
                seen_review_entries.add(key)
                failed_entries.append(entry)
        return len(problems)

    def buildDownloadMap(self, downloads_path: Path):
        download_map = {}

        if not downloads_path.exists():
            self.log(f"Downloads directory does not exist: {downloads_path}", "error")
            qDebug(f"[NXMColDL] Downloads path not found: {downloads_path}")
            return download_map

        qDebug(f"[NXMColDL] Building download map from: {downloads_path}")
        # Iterate through all .meta files in downloads directory
        for meta_file in downloads_path.glob("*.meta"):
            try:
                # The actual download file has the same name without .meta extension
                download_file = meta_file.with_suffix("")

                # Only consider files that exist and are not directories
                if not download_file.exists() or download_file.is_dir():
                    continue
                if download_file.name.endswith(".unfinished"):
                    qDebug(
                        f"[NXMColDL] Skipping unfinished download: {download_file.name}"
                    )
                    continue
                if download_file.stat().st_size == 0:
                    qDebug(f"[NXMColDL] Skipping empty download: {download_file.name}")
                    continue

                # Parse the .meta file (it's an INI-style file)
                mod_id = None
                file_id = None

                with open(meta_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("modID="):
                            mod_id = int(line.split("=", 1)[1])
                        elif line.startswith("fileID="):
                            file_id = int(line.split("=", 1)[1])

                        # Early exit if we found both
                        if mod_id is not None and file_id is not None:
                            break

                if mod_id is not None and file_id is not None:
                    preferred_file = preferredCanonicalDownloadArchive(download_file)
                    if preferred_file != download_file:
                        qDebug(
                            "[NXMColDL] Prefer canonical download archive "
                            f"{preferred_file.name} over {download_file.name}"
                        )
                    download_map[(mod_id, file_id)] = preferred_file
                else:
                    qDebug(
                        f"[NXMColDL] Incomplete metadata in {meta_file.name}: modID={mod_id}, fileID={file_id}"
                    )

            except (ValueError, IOError) as e:
                qDebug(f"[NXMColDL] Error parsing {meta_file.name}: {e}")
            except Exception as e:
                qDebug(f"[NXMColDL] Unexpected error parsing {meta_file.name}: {e}")

        cache_dir = downloads_path.parent / "nxm-collection-dl-install-cache"
        if cache_dir.exists():
            cache_candidates = {}
            cache_hits = 0
            for archive_file in cache_dir.iterdir():
                if not archive_file.is_file():
                    continue
                if archive_file.name.endswith(".unfinished"):
                    continue
                match = re.match(r"^(\d+)-(\d+)-.+", archive_file.name)
                if not match:
                    continue
                try:
                    key = (int(match.group(1)), int(match.group(2)))
                    if archive_file.stat().st_size > 0:
                        repeated_prefix = archive_file.name.startswith(
                            f"{key[0]}-{key[1]}-{key[0]}-{key[1]}-"
                        )
                        cache_candidates.setdefault(key, []).append(
                            (
                                1 if repeated_prefix else 0,
                                len(archive_file.name),
                                archive_file,
                            )
                        )
                except (OSError, ValueError):
                    continue
            for key, candidates in cache_candidates.items():
                if key in download_map:
                    continue
                candidates.sort(key=lambda item: (item[0], item[1], item[2].name))
                download_map[key] = candidates[0][2]
                cache_hits += 1
            if cache_hits:
                qDebug(
                    f"[NXMColDL] Added {cache_hits} cached install source(s) to download map"
                )

        return download_map

    def downloadMetadataPath(self, download_path):
        return Path(str(download_path) + ".meta")

    def repairStaleInstalledDownloadMetadata(
        self, download_path, install_key, installed_map
    ):
        if install_key in installed_map:
            return False

        metadata_path = self.downloadMetadataPath(download_path)
        try:
            lines = metadata_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines(keepends=True)
        except OSError as e:
            qDebug(
                "[NXMColDL InstallTrace] Could not read download metadata: "
                f"archive={download_path}, error={e}"
            )
            return False

        installed_index = None
        installed_value = None
        for index, line in enumerate(lines):
            if line.strip().lower().startswith("installed="):
                installed_index = index
                installed_value = line.split("=", 1)[1].strip().lower()
                break

        if installed_value != "true":
            qDebug(
                "[NXMColDL InstallTrace] Download metadata install flag clear: "
                f"key={install_key}, archive={download_path}, value={installed_value}"
            )
            return False

        newline = "\n" if lines[installed_index].endswith("\n") else ""
        lines[installed_index] = "installed=false" + newline
        try:
            metadata_path.write_text("".join(lines), encoding="utf-8")
        except OSError as e:
            qDebug(
                "[NXMColDL InstallTrace] Could not repair stale download metadata: "
                f"key={install_key}, archive={download_path}, metadata={metadata_path}, "
                f"error={e}"
            )
            return False

        self.log(
            "  Repaired stale download metadata: installed=true but exact "
            f"Nexus file {install_key} is not installed",
            "note",
        )
        qDebug(
            "[NXMColDL InstallTrace] Repaired stale download metadata: "
            f"key={install_key}, archive={download_path}, metadata={metadata_path}"
        )
        return True

    def detachedInstallArchiveSource(self, organizer, download_path, install_key):
        """Return an archive path outside downloads/ so MO2 avoids stale download state."""
        try:
            cache_dir = Path(organizer.basePath()) / "nxm-collection-dl-install-cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            if download_path.parent == cache_dir:
                qDebug(
                    "[NXMColDL InstallTrace] Using existing detached install source: "
                    f"key={install_key}, detached={download_path}"
                )
                self.log(
                    f"  Using detached install source: {download_path.name}",
                    "note",
                )
                return download_path
            target_path = cache_dir / (
                f"{install_key[0]}-{install_key[1]}-{download_path.name}"
            )
            metadata_path = self.downloadMetadataPath(target_path)
            if metadata_path.exists():
                metadata_path.unlink()

            source_stat = download_path.stat()
            if (
                target_path.exists()
                and target_path.stat().st_size == source_stat.st_size
            ):
                qDebug(
                    "[NXMColDL InstallTrace] Reusing detached install source: "
                    f"key={install_key}, source={download_path}, detached={target_path}"
                )
                self.log(
                    f"  Using detached install source: {target_path.name}",
                    "note",
                )
                return target_path

            if target_path.exists():
                target_path.unlink()

            try:
                os.link(download_path, target_path)
                method = "hardlink"
            except OSError:
                shutil.copy2(download_path, target_path)
                method = "copy"

            qDebug(
                "[NXMColDL InstallTrace] Created detached install source: "
                f"key={install_key}, method={method}, source={download_path}, "
                f"detached={target_path}, bytes={source_stat.st_size}"
            )
            self.log(
                f"  Using detached install source ({method}): {target_path.name}",
                "note",
            )
            return target_path
        except Exception as e:
            qDebug(
                "[NXMColDL InstallTrace] Could not create detached install source: "
                f"key={install_key}, source={download_path}, error={e}, "
                f"traceback={traceback.format_exc()}"
            )
            self.log(
                "  Could not detach archive from MO2 download metadata; "
                "using original download path",
                "note",
            )
            return download_path

    def allocateCollectionModName(self, mod_name, used_mod_names, mod_name_counts):
        return allocateUniqueModName(mod_name, used_mod_names, mod_name_counts)

    def installedModMatchingDownload(self, mods_path, mod_name, download_path, mod_id):
        meta_file = Path(mods_path) / mod_name / "meta.ini"
        if not meta_file.exists():
            return None

        meta_mod_id = None
        installation_file = None
        try:
            with open(meta_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("modid="):
                        meta_mod_id = int(line.split("=", 1)[1])
                    elif line.startswith("installationFile="):
                        installation_file = line.split("=", 1)[1]
        except (ValueError, IOError) as e:
            qDebug(f"[NXMColDL] Error checking installed mod metadata {meta_file}: {e}")
            return None

        if meta_mod_id != int(mod_id) or not installation_file:
            return None

        installed_archive = re.sub(
            r"^\d+_", "", Path(str(installation_file).replace("\\", "/")).name
        )
        download_archive = re.sub(r"^\d+_", "", Path(download_path).name)
        if installed_archive == download_archive:
            return mod_name
        return None

    def buildInstalledMap(
        self, mods_path: Path, downloads_path=None, expected_file_names=None
    ):
        installed_map = {}
        stats = {"metadata": 0, "detached_cache": 0, "correlated": 0}
        self._last_installed_map_stats = stats

        if not mods_path.exists():
            qDebug(f"[NXMColDL] Mods path not found: {mods_path}")
            return installed_map

        for meta_file in mods_path.glob("*/meta.ini"):
            mod_id = None
            file_ids = []
            installation_file = None
            try:
                with open(meta_file, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("modid="):
                            mod_id = int(line.split("=", 1)[1])
                        elif line.startswith("installationFile="):
                            installation_file = line.split("=", 1)[1]
                        elif "\\fileid=" in line:
                            file_ids.append(int(line.split("=", 1)[1]))

                if mod_id is not None:
                    for file_id in file_ids:
                        installed_map[(mod_id, file_id)] = meta_file.parent.name
                        stats["metadata"] += 1

                detached_key = detachedInstallCacheKeyFromPath(installation_file or "")
                if detached_key and detached_key not in installed_map:
                    installed_map[detached_key] = meta_file.parent.name
                    stats["detached_cache"] += 1

            except (ValueError, IOError) as e:
                qDebug(f"[NXMColDL] Error parsing installed metadata {meta_file}: {e}")

        correlated_records = installedModRecordsFromDirectory(
            mods_path,
            downloads_path,
            expected_file_names=expected_file_names,
        )
        for nexus_key, mod_names in correlated_records.items():
            if not mod_names:
                continue
            if nexus_key not in installed_map:
                stats["correlated"] += 1
            installed_map[nexus_key] = mod_names[0]

        return installed_map

    (moveHeadlessArchivePayload,)
    (sevenZipArchiveMemberPaths,)

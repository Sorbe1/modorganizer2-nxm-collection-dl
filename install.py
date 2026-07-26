import json
import html
import os
import re
import shutil
import subprocess
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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
)

from . import __meta__, var
from .collection_helpers import (
    FOMOD_ADVANCE_EXCLUDED_TITLES,
    INSTALLER_SETTING_DEFAULTS,
    allocateUniqueModName,
    archiveInspectionSubprocessKwargs,
    coerceBoolSetting,
    coerceIntSetting,
    contentTreeWarningDialogAction,
    fomodManualChoiceGuide,
    invalidInstallContentDialogAction,
    installNoResultReason,
    installerDefaultActionLabel,
    isRequiredFomodGroupTitle,
    isSafeSingletonFomodOption,
    normalizedButtonLabel,
    safeDisplayText,
    shouldUseArchiveDefaultForFomodCompatibility,
    shouldUseCollectionTargetModName,
)

qDebug = var.debug

MO2_WARNING_PATTERNS = (
    "Plugin not found:",
    "invalid origin name:",
    "[fomodinstallerdialog.cpp:",
)

EXPECTED_INSTALL_EXCEPTION_PATTERNS = (
    "invalid origin name:",
    "Plugin not found:",
)

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


def installCollectionMetadata(metadata, parent=None):
    loadMetadataIntoVar(metadata)
    qDebug(f"[NXMColDL] Loaded collection: {var.name}")
    qDebug(f"[NXMColDL] Essential mods: {len(var.essentialMods)}")
    qDebug(f"[NXMColDL] Optional mods: {len(var.chosenOptional)}")
    stepInstallMods(parent).exec()


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


def dismissKnownPostInstallErrorDialog(remaining=20):
    if remaining <= 0:
        return

    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible() or widget.windowTitle() != "Error":
            continue

        labels = widget.findChildren(QLabel)
        if not any(
            "invalid origin name:" in label.text()
            or "Plugin not found:" in label.text()
            for label in labels
        ):
            continue

        for button_box in widget.findChildren(QDialogButtonBox):
            button = button_box.button(QDialogButtonBox.StandardButton.Ok)
            if button and button.isEnabled():
                qDebug("[NXMColDL Install] Dismissing known post-install error dialog")
                suppressDialogAndClick(widget, button)
                return

        for button in widget.findChildren(QPushButton):
            if normalizedButtonLabel(button.text()) == "ok" and button.isEnabled():
                qDebug("[NXMColDL Install] Dismissing known post-install error dialog")
                suppressDialogAndClick(widget, button)
                return

    QTimer.singleShot(250, lambda: dismissKnownPostInstallErrorDialog(remaining - 1))


def handleModExistsDialog(action="merge"):
    global _mod_exists_cancelled

    action = normalizedButtonLabel(action)
    if action not in {"cancel", "merge", "rename"}:
        return

    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible() or widget.windowTitle() != "Mod Exists":
            continue

        if clickButtonByText(widget, (action,)):
            if action == "cancel":
                _mod_exists_cancelled = True
            qDebug(f"[NXMColDL Install] Auto-{action} Mod Exists dialog")
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
            button = candidates[0]
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
        suppressFomodWindowFocus(widget)
        selected = selectRequiredSingletonFomodOption(widget, selected_groups)
        if selected:
            title, group_title, label, group_key = selected
            return title, f"select {label} in {group_title}", group_key

        button = installerDefaultAction(widget)
        if not button:
            continue

        title = safeDisplayText(widget.windowTitle())
        label = normalizedButtonLabel(button.text())
        qDebug(f"[NXMColDL Install] Auto-advancing installer defaults for {title}")
        suppressDialogAndClick(widget, button)
        return title, label, None

    return None


def scheduleInstallDialogHandlers(
    auto_accept_quick_install=INSTALLER_SETTING_DEFAULTS["auto_accept_quick_install"],
    auto_dismiss_known_post_install_errors=INSTALLER_SETTING_DEFAULTS[
        "auto_dismiss_known_post_install_errors"
    ],
    auto_cancel_invalid_install_content=INSTALLER_SETTING_DEFAULTS[
        "auto_cancel_invalid_install_content"
    ],
    existing_mod_action=None,
    should_run=None,
):
    def run_if_current(handler):
        if should_run is None or should_run():
            handler()

    for delay in (250, 750, 1500, 3000, 5000, 8000, 12000, 20000):
        if auto_accept_quick_install:
            QTimer.singleShot(
                delay,
                lambda handler=acceptQuickInstallDialog: run_if_current(handler),
            )
        if auto_dismiss_known_post_install_errors:
            QTimer.singleShot(
                delay,
                lambda handler=dismissKnownPostInstallErrorDialog: run_if_current(
                    handler
                ),
            )
        if auto_cancel_invalid_install_content:
            QTimer.singleShot(
                delay,
                lambda handler=cancelInvalidInstallContentDialog: run_if_current(
                    handler
                ),
            )
            QTimer.singleShot(
                delay,
                lambda handler=acceptContentTreeWarningDialog: run_if_current(handler),
            )
        if existing_mod_action:
            QTimer.singleShot(
                delay,
                lambda action=existing_mod_action: run_if_current(
                    lambda: handleModExistsDialog(action)
                ),
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
    def __init__(self, parent=None):
        super().__init__(parent)
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
        self.fomod_auto_selected_groups = {}
        self.fomod_auto_stalled_generations = set()
        self.last_failed_entries = []
        self.warning_report_path = None
        self.fomod_guide_path = None

        # Start installation after dialog is shown
        QTimer.singleShot(500, self.startInstallation)

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
                        self.install_warnings[
                            self.install_warning_index[key]
                        ]["occurrences"] += 1
                        captured += 1
                        continue
                    warning = {
                        "mod": mod_name,
                        "file": file_name,
                        "message": line,
                        "normalized_message": normalized,
                        "category": category,
                        "occurrences": 1,
                    }
                    self.install_warning_index[key] = len(self.install_warnings)
                    self.install_warnings.append(warning)
                    captured += 1
        except OSError as e:
            qDebug(f"[NXMColDL Install] Could not read MO2 interface log: {e}")
        return captured

    def normalizedInterfaceWarning(self, message):
        return re.sub(r"^\[[^\]]+\]\s+[A-Z]\]\s+", "", str(message)).strip()

    def interfaceWarningCategory(self, message):
        text = str(message)
        if "Plugin not found:" in text:
            return "plugin_state_missing"
        if "invalid origin name:" in text:
            return "invalid_origin_name"
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

        self.install_warnings = self.install_warnings[:warning_start]
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
            self.install_warning_summary[warning["category"]] += warning[
                "occurrences"
            ]

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
        if not self.install_warnings and not failed_entries and not root_level_entries:
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
            (
                "- FOMOD XML not readable/found: "
                f"{summary['unreadable_or_non_fomod']}"
            ),
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

        module_config, module_path, error = self.readArchiveFomodModuleConfig(
            archive_path
        )
        if module_config is None:
            return {"archive": str(archive_path), "error": error}

        guide = fomodManualChoiceGuide(module_config)
        guide["archive"] = str(archive_path)
        guide["module_config"] = module_path
        return guide

    def readArchiveFomodModuleConfig(self, archive_path):
        suffix = archive_path.suffix.lower()
        if suffix == ".zip":
            return self.readZipFomodModuleConfig(archive_path)
        return self.readSevenZipFomodModuleConfig(archive_path)

    def archiveHasFomodInstaller(self, archive_path):
        module_config, _module_path, _error = self.readArchiveFomodModuleConfig(
            archive_path
        )
        if module_config is not None:
            return True
        if archive_path.suffix.lower() != ".zip" and _error and (
            "No 7z/7zz executable" in _error
            or "Could not list archive with 7z" in _error
        ):
            return None
        return False

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

    def readSevenZipFomodModuleConfig(self, archive_path):
        executable = self.sevenZipExecutable()
        if not executable:
            return (
                None,
                None,
                "No 7z/7zz executable available to inspect this archive.",
            )

        try:
            listing = subprocess.run(
                [executable, "l", "-slt", str(archive_path)],
                **archiveInspectionSubprocessKwargs(timeout=30),
            )
        except (OSError, subprocess.SubprocessError) as e:
            return None, None, f"Could not list archive with 7z: {e}"

        module_path = None
        for raw_line in listing.stdout.decode("utf-8", errors="replace").splitlines():
            if not raw_line.startswith("Path = "):
                continue
            candidate = raw_line[7:].replace("\\", "/")
            if candidate.lower().endswith("fomod/moduleconfig.xml"):
                module_path = candidate
                break
        if not module_path:
            return None, None, "No fomod/ModuleConfig.xml found in archive."

        try:
            extraction = subprocess.run(
                [executable, "x", "-so", str(archive_path), module_path],
                **archiveInspectionSubprocessKwargs(
                    timeout=30, stderr_to_stdout=False
                ),
            )
        except (OSError, subprocess.SubprocessError) as e:
            return None, None, f"Could not extract FOMOD XML with 7z: {e}"

        if extraction.returncode != 0 or not extraction.stdout:
            return None, None, "7z did not return FOMOD XML content."
        return extraction.stdout, module_path, None

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
            lines.append(f"- FOMOD XML parse error: {safeDisplayText(guide['parse_error'])}")
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
            self.progress_bar.setMaximum(len(mods_to_install))

            self.log(f"Total mods to install: {len(mods_to_install)}")

            # Get initial mod count to determine starting priority
            # Collection mods will be placed at the end of the current mod list
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

            installed_map = self.buildInstalledMap(Path(organizer.modsPath()))
            self.log(f"Found {len(installed_map)} installed Nexus file records")
            self.log("")

            self.install_context = {
                "plugin_instance": plugin_instance,
                "organizer": organizer,
                "modlist": modlist,
                "mods_to_install": mods_to_install,
                "download_map": download_map,
                "installed_map": installed_map,
                "installed_mods": [],
                "failed_entries": [],
                "root_level_entries": [],
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
                "next_index": 0,
            }
            self.fomod_auto_advances = []
            QTimer.singleShot(1500, self.installNextMod)

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
                "mods_to_install": mods_to_install,
                "download_map": self.buildDownloadMap(downloads_path),
                "installed_map": self.buildInstalledMap(Path(organizer.modsPath())),
                "installed_mods": [],
                "failed_entries": [],
                "root_level_entries": [],
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
            QTimer.singleShot(500, self.installNextMod)
        except Exception as e:
            self.log(f"Fatal error starting manual install pass: {e}", "error")
            self.close_btn.setEnabled(True)
            self.manual_install_btn.setEnabled(bool(self.last_failed_entries))

    def installNextMod(self):
        global _invalid_install_content_cancelled, _mod_exists_cancelled

        if not self.install_context:
            return

        context = self.install_context
        plugin_instance = context["plugin_instance"]
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

        idx = context["next_index"] + 1
        if self.cancel_requested:
            self.finishInstallation(cancelled=True)
            return

        if idx > len(mods_to_install):
            self.finishInstallation()
            return

        context["next_index"] = idx
        self.progress_label.setText(f"Installing mod {idx}/{len(mods_to_install)}")
        self.progress_bar.setValue(idx - 1)

        mod_info = mods_to_install[idx - 1]
        mod_id = mod_info["file"]["mod"]["modId"]
        file_id = mod_info["file"]["fileId"]
        mod_name = mod_info["file"]["mod"]["name"]
        file_name = mod_info["file"]["name"]
        install_key = (int(mod_id), int(file_id))

        self.log(f"[{idx}/{len(mods_to_install)}] Processing: {mod_name}")
        self.log(f"  File: {file_name} (ModID: {mod_id}, FileID: {file_id})")

        activate_after_install = context["activation_enabled"]

        if install_key in installed_map:
            internal_name = installed_map[install_key]
            self.log(f"  Already installed as: {internal_name}", "success")
            self.log("")
            installed_mods.append(internal_name)
            if activate_after_install:
                if activate_during_install:
                    self.activateModDuringInstall(internal_name)
                else:
                    mods_to_activate.append(internal_name)
            QTimer.singleShot(100, self.installNextMod)
            return

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
            QTimer.singleShot(100, self.installNextMod)
            return

        download_path = download_map[install_key]
        self.log(f"  Found: {download_path.name}")

        log_path, log_offset = self.captureInterfaceLogPosition(organizer)
        try:
            _invalid_install_content_cancelled = False
            _mod_exists_cancelled = False
            self.dialog_handler_generation += 1
            dialog_handler_generation = self.dialog_handler_generation
            warning_start = len(self.install_warnings)
            fomod_state = False
            if (
                context["separate_file_installs"]
                and not manual_install_pass
                and context.get(
                    "auto_advance_fomod_defaults",
                    INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
                )
            ):
                fomod_archive_cache = context.setdefault("fomod_archive_cache", {})
                archive_key = str(download_path)
                if archive_key not in fomod_archive_cache:
                    fomod_archive_cache[archive_key] = self.archiveHasFomodInstaller(
                        download_path
                    )
                fomod_state = fomod_archive_cache[archive_key]
            unknown_fomod_state = fomod_state is None
            use_archive_default_for_fomod = (
                shouldUseArchiveDefaultForFomodCompatibility(
                    context["separate_file_installs"],
                    manual_install_pass,
                    fomod_state,
                )
            )

            existing_mod_action = None
            if not manual_install_pass:
                if use_archive_default_for_fomod:
                    existing_mod_action = "cancel"
                elif (
                    not context["separate_file_installs"]
                    and self.installerBoolSetting("auto_merge_existing_mods")
                ):
                    existing_mod_action = "merge"
            scheduleInstallDialogHandlers(
                self.installerBoolSetting("auto_accept_quick_install"),
                self.installerBoolSetting("auto_dismiss_known_post_install_errors"),
                (
                    self.installerBoolSetting("auto_cancel_invalid_install_content")
                    and not manual_install_pass
                ),
                existing_mod_action,
                lambda generation=dialog_handler_generation: (
                    generation == self.dialog_handler_generation
                ),
            )
            if context.get(
                "auto_advance_fomod_defaults",
                INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
            ):
                self.fomod_auto_action_counts[dialog_handler_generation] = {}
                self.fomod_auto_selected_groups[dialog_handler_generation] = set()
                max_steps = self.installerIntSetting(
                    "auto_advance_fomod_max_steps"
                )
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

            target_mod_name = None
            use_target_mod_name = shouldUseCollectionTargetModName(
                context["separate_file_installs"], manual_install_pass
            )
            if use_target_mod_name and not use_archive_default_for_fomod:
                target_mod_name = self.allocateCollectionModName(
                    mod_name, used_mod_names, mod_name_counts
                )
                if unknown_fomod_state:
                    self.log(
                        "  FOMOD status unknown; trying collection target name first",
                        "note",
                    )
                self.log(f"  Target MO2 name: {target_mod_name}")
                installed_mod = organizer.installMod(
                    str(download_path), target_mod_name
                )
            else:
                if use_archive_default_for_fomod:
                    self.log(
                        "  Target MO2 name: using archive default for FOMOD compatibility"
                    )
                installed_mod = organizer.installMod(str(download_path))
            warning_count = self.collectInterfaceLogWarnings(
                log_path, log_offset, mod_name, file_name
            )
            current_warnings = self.install_warnings[warning_start:]
            if installed_mod:
                self.discardInterfaceWarningsFrom(warning_start)
                internal_name = installed_mod.name()
                self.log(f"  Installed as: {internal_name}", "success")
                self.log(
                    "  Priority will be checked after collection install",
                )
                used_mod_names.add(internal_name)
                installed_mods.append(internal_name)
                installed_map[install_key] = internal_name
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
                if not manual_install_pass:
                    self.logInstallIssue(reason, expected=True)
                    if _mod_exists_cancelled or (
                        dialog_handler_generation
                        in self.fomod_auto_stalled_generations
                    ):
                        if _mod_exists_cancelled:
                            self.log(
                                "  Skipping immediate fallback because duplicate "
                                "FOMOD target needs a manual rename/merge choice.",
                                "note",
                            )
                        else:
                            self.log(
                                "  Skipping immediate manual fallback because FOMOD "
                                "automation stalled; retry from Manual Install "
                                "Failed.",
                                "note",
                            )
                        failed_entries.append(
                            {
                                "mod": mod_name,
                                "file": file_name,
                                "mod_id": int(mod_id),
                                "file_id": int(file_id),
                                "archive": str(download_path),
                                "reason": reason,
                                **self.fomodInstallDiagnostics(
                                    fomod_state,
                                    use_archive_default_for_fomod,
                                    target_mod_name,
                                    dialog_handler_generation,
                                ),
                            }
                        )
                        self.log("")
                        QTimer.singleShot(1500, self.installNextMod)
                        return
                    fallback_auto_advance = context.get(
                        "auto_advance_fomod_defaults",
                        INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
                    )
                    if fallback_auto_advance:
                        self.log(
                            "  Retrying installer with FOMOD default automation...",
                            "note",
                        )
                    else:
                        self.log(
                            "  Opening manual installer for this failed entry...",
                            "note",
                        )
                    self.dialog_handler_generation += 1
                    fallback_generation = self.dialog_handler_generation
                    manual_log_path, manual_log_offset = (
                        self.captureInterfaceLogPosition(organizer)
                    )
                    fallback_existing_mod_action = None
                    if context["separate_file_installs"]:
                        fallback_existing_mod_action = "cancel"
                    elif self.installerBoolSetting("auto_merge_existing_mods"):
                        fallback_existing_mod_action = "merge"
                    scheduleInstallDialogHandlers(
                        self.installerBoolSetting("auto_accept_quick_install"),
                        self.installerBoolSetting(
                            "auto_dismiss_known_post_install_errors"
                        ),
                        self.installerBoolSetting("auto_cancel_invalid_install_content"),
                        fallback_existing_mod_action,
                        lambda generation=fallback_generation: (
                            generation == self.dialog_handler_generation
                        ),
                    )
                    if fallback_auto_advance:
                        self.fomod_auto_action_counts[fallback_generation] = {}
                        self.fomod_auto_selected_groups[fallback_generation] = set()
                        max_steps = self.installerIntSetting(
                            "auto_advance_fomod_max_steps"
                        )
                        if max_steps:
                            self.scheduleInstallerDefaultAdvancer(
                                fallback_generation,
                                mod_name,
                                max_steps,
                            )
                        else:
                            self.log(
                                "  FOMOD default auto-advance disabled by "
                                "max-step limit",
                                "note",
                            )
                    try:
                        installed_mod = organizer.installMod(str(download_path))
                    finally:
                        manual_warning_count = self.collectInterfaceLogWarnings(
                            manual_log_path, manual_log_offset, mod_name, file_name
                        )

                    if installed_mod:
                        self.discardInterfaceWarningsFrom(warning_start)
                        internal_name = installed_mod.name()
                        if fallback_auto_advance:
                            self.log(
                                f"  Automated retry completed as: {internal_name}",
                                "success",
                            )
                        else:
                            self.log(
                                f"  Manual install completed as: {internal_name}",
                                "success",
                            )
                        self.log(
                            "  Priority will be checked after collection install",
                        )
                        used_mod_names.add(internal_name)
                        installed_mods.append(internal_name)
                        installed_map[install_key] = internal_name
                        if activate_after_install:
                            if activate_during_install:
                                self.activateModDuringInstall(internal_name)
                            else:
                                mods_to_activate.append(internal_name)
                    else:
                        failed_entries.append(
                            {
                                "mod": mod_name,
                                "file": file_name,
                                "mod_id": int(mod_id),
                                "file_id": int(file_id),
                                "archive": str(download_path),
                                "reason": (
                                    "fallback installer returned no installed mod "
                                    f"after automatic failure: {reason}"
                                ),
                                **self.fomodInstallDiagnostics(
                                    fomod_state,
                                    use_archive_default_for_fomod,
                                    target_mod_name,
                                    dialog_handler_generation,
                                    fallback_generation,
                                ),
                            }
                        )
                        self.logInstallIssue(
                            "Fallback installer returned no installed mod",
                            expected=True,
                        )
                    self.dialog_handler_generation += 1
                    self.log("")
                    QTimer.singleShot(1500, self.installNextMod)
                    return

                failed_entries.append(
                    {
                        "mod": mod_name,
                        "file": file_name,
                        "mod_id": int(mod_id),
                        "file_id": int(file_id),
                        "archive": str(download_path),
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
            failed_entries.append(
                {
                    "mod": mod_name,
                    "file": file_name,
                    "mod_id": int(mod_id),
                    "file_id": int(file_id),
                    "archive": str(download_path),
                    "reason": str(e),
                }
            )

        self.log("")
        QTimer.singleShot(1500, self.installNextMod)

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
            title, button_label, selected_group_key = action
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
                return
            advanced += 1
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
        QTimer.singleShot(
            75,
            lambda: self.scheduleInstallerDefaultAdvancer(
                generation,
                mod_name,
                max_steps,
                remaining - 1,
                advanced,
            ),
        )

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
        }

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
        )
        for key in ("activated", "already_active", "blocked"):
            context["plugin_activation"][key] += plugin_activation[key]

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
        manual_install_pass = context.get("manual_install_pass", False)
        mods_to_activate = context["mods_to_activate"]
        activation_enabled = context["activation_enabled"]
        priority_order = None
        activated_count = len(context["activated_mods"])
        activation_failures = context["activation_failures"]
        plugin_activation = dict(context["plugin_activation"])

        if installed_mods:
            priority_order = self.reconcileCollectionPriorityOrder(
                modlist, installed_mods
            )
            self.log("")

        if mods_to_activate:
            self.progress_label.setText("Activating installed mods...")
            mods_to_activate = list(dict.fromkeys(mods_to_activate))
            self.log(f"Activating {len(mods_to_activate)} installed mods...")
            log_path, log_offset = self.captureInterfaceLogPosition(organizer)
            warning_start = len(self.install_warnings)
            activation_failure_start = len(activation_failures)
            for internal_name in mods_to_activate:
                try:
                    modlist.setActive(internal_name, True)
                    activated_count += 1
                except Exception as e:
                    activation_failures.append(
                        {"mod": internal_name, "reason": str(e)}
                    )
                    self.logInstallIssue(f"Could not activate {internal_name}: {e}")
            warning_count = self.collectInterfaceLogWarnings(
                log_path, log_offset, "post-install activation", ""
            )
            if warning_count and len(activation_failures) == activation_failure_start:
                self.discardInterfaceWarningsFrom(warning_start)
            if activation_enabled:
                final_plugin_activation = self.activatePluginsForMods(
                    organizer, mods_to_activate
                )
                for key in ("activated", "already_active", "blocked"):
                    plugin_activation[key] += final_plugin_activation[key]
            self.log("")

        self.progress_bar.setValue(len(mods_to_install))
        failed_count = len(failed_entries)
        if cancelled:
            self.progress_label.setText("Installation cancelled.")
        elif failed_count:
            self.progress_label.setText("Installation completed; review needed.")
        else:
            self.progress_label.setText("Installation complete!")
        self.log("=" * 50)
        if cancelled:
            self.log("Installation Summary (cancelled):", "warning")
        elif manual_install_pass and failed_count:
            self.log("Manual Install Summary (review needed):", "note")
        elif manual_install_pass:
            self.log("Manual Install Summary:", "success")
        elif failed_count:
            self.log("Installation Summary (review needed):", "note")
        else:
            self.log("Installation Summary:", "success")
        installed_containers = len(set(installed_mods))
        review_count = len(failed_entries)
        root_level_count = len(root_level_entries)
        self.log(f"  Collection file entries: {len(mods_to_install)}")
        self.log(
            "  Entries installed/already present/root-handled: "
            f"{len(installed_mods) + root_level_count}"
        )
        self.log(
            "  Entries downloaded but not installed: "
            f"{review_count} (use Retry Failed Manually)"
        )
        if root_level_count:
            self.log(
                f"  Root/game-directory entries noted: {root_level_count}",
                "note",
            )
        self.log(f"  MO2 mod containers touched: {installed_containers}")
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
        if failed_entries:
            self.log("  Entries needing review:", "note")
            for entry in failed_entries[:20]:
                self.log(
                    "    "
                    f"{safeDisplayText(entry['mod'])} - "
                    f"{safeDisplayText(entry['file'])}: "
                    f"{safeDisplayText(entry['reason'])}",
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
            if len(failed_entries) > 20:
                self.log(
                    f"    ... {len(failed_entries) - 20} more omitted from dialog",
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
            organizer, failed_entries, report_timestamp
        )
        self.fomod_guide_path = guide_path
        self.open_fomod_guide_btn.setEnabled(bool(guide_path))
        if guide_path:
            self.log(f"  FOMOD/manual install guide: {guide_path}", "note")
        qDebug(
            "[NXMColDL] Installation finished: "
            f"{len(installed_mods) + root_level_count}/{len(mods_to_install)} "
            "installed/root-handled, "
            f"{failed_count} failed/skipped"
        )

        if failed_entries:
            self.log("", "note")
            self.log(
                "Some mods were not installed. Use Retry Failed Manually to retry "
                "the remaining entries with normal MO2 installer dialogs, or review "
                "the generated reports.",
                "note",
            )

        self.last_failed_entries = list(failed_entries)
        self.close_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.manual_install_btn.setEnabled(bool(failed_entries))

    def reconcileCollectionPriorityOrder(self, modlist, installed_mods):
        ordered_mods = list(dict.fromkeys(installed_mods))
        result = {"verified": 0, "moved": 0, "failed": 0}
        if len(ordered_mods) < 2:
            result["verified"] = len(ordered_mods)
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
        if current_priorities == sorted(current_priorities):
            result["verified"] = len(present_mods)
            result["failed"] = len(missing_mods)
            self.log(
                f"Collection priority order verified for {len(present_mods)} mods",
                "success",
            )
            return result

        target_priority = min(current_priorities)
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
        if repaired_priorities == sorted(repaired_priorities):
            result["verified"] = len(present_mods)
            self.log(
                f"Collection priority order repaired for {len(present_mods)} mods",
                "success",
            )
        else:
            result["failed"] += len(present_mods)
            self.logInstallIssue(
                "Collection priority order still differs after repair",
                expected=True,
            )

        result["failed"] += len(missing_mods)
        return result

    def activatePluginsForMods(self, organizer, mod_names, heading=None):
        plugin_list = organizer.pluginList()
        mod_name_set = set(mod_names)
        activated = 0
        already_active = 0
        blocked = 0

        try:
            organizer.refresh(True)
        except Exception as e:
            self.logInstallIssue(f"Could not refresh plugin list: {e}", expected=True)

        self.log(heading or "Activating plugins from installed mods...")
        for plugin_name in plugin_list.pluginNames():
            try:
                if plugin_list.origin(plugin_name) not in mod_name_set:
                    continue

                if plugin_list.state(plugin_name) == mobase.PluginState.ACTIVE:
                    already_active += 1
                    continue

                plugin_list.setState(plugin_name, mobase.PluginState.ACTIVE)
                if plugin_list.state(plugin_name) == mobase.PluginState.ACTIVE:
                    activated += 1
                else:
                    blocked += 1
                    masters = ", ".join(plugin_list.masters(plugin_name))
                    detail = f"; masters: {masters}" if masters else ""
                    self.log(
                        f"  Plugin not activated by MO2: {plugin_name}{detail}",
                        "note",
                    )
            except Exception as e:
                blocked += 1
                self.logInstallIssue(
                    f"Could not activate plugin {plugin_name}: {e}",
                    expected=True,
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
                    download_map[(mod_id, file_id)] = download_file
                else:
                    qDebug(
                        f"[NXMColDL] Incomplete metadata in {meta_file.name}: modID={mod_id}, fileID={file_id}"
                    )

            except (ValueError, IOError) as e:
                qDebug(f"[NXMColDL] Error parsing {meta_file.name}: {e}")
            except Exception as e:
                qDebug(f"[NXMColDL] Unexpected error parsing {meta_file.name}: {e}")

        return download_map

    def allocateCollectionModName(self, mod_name, used_mod_names, mod_name_counts):
        return allocateUniqueModName(mod_name, used_mod_names, mod_name_counts)

    def buildInstalledMap(self, mods_path: Path):
        installed_map = {}

        if not mods_path.exists():
            qDebug(f"[NXMColDL] Mods path not found: {mods_path}")
            return installed_map

        for meta_file in mods_path.glob("*/meta.ini"):
            mod_id = None
            file_ids = []
            try:
                with open(meta_file, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("modid="):
                            mod_id = int(line.split("=", 1)[1])
                        elif "\\fileid=" in line:
                            file_ids.append(int(line.split("=", 1)[1]))

                if mod_id is None:
                    continue

                for file_id in file_ids:
                    installed_map[(mod_id, file_id)] = meta_file.parent.name

            except (ValueError, IOError) as e:
                qDebug(f"[NXMColDL] Error parsing installed metadata {meta_file}: {e}")

        return installed_map

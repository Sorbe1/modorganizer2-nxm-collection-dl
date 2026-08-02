import json
import re
import time
from datetime import datetime
from pathlib import Path
from PyQt6.QtCore import QObject, QSize, QThread, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFontMetrics
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from .api import fetchRevisions, fetchInfo, fetchModInfo
from . import __meta__
from . import var
from .collection_helpers import (
    INSTALLER_SETTING_DEFAULTS,
    activeDownloadPromptKey,
    activeUnfinishedDownloadFingerprint,
    adaptiveDownloadTailGraceSeconds,
    coerceBoolSetting,
    coerceDownloadId,
    coerceIntSetting,
    cleanupZeroByteUnfinishedDownloads,
    collectionDownloadExpectedSizes,
    collectionExpectedFileNames,
    collectionExpectedNexusKeys,
    collectionFlowFailureReport,
    collectionLinkCompletionPolicy,
    collectionRecoveryTargets,
    downloadedArchiveNameKeys,
    downloadCompletionChoices,
    downloadCompletionPlan,
    downloadProgressIsStalled,
    downloadTailLaggardPlan,
    downloadTailBoundaryArmTime,
    downloadTailBoundaryReached,
    downloadProgressCanClose,
    downloadProgressFormat,
    downloadProgressState,
    downloadedFileKeys,
    duplicateDownloadPromptArchiveAction,
    downloadPromptKeyFromArchiveLabels,
    downloadPromptKeyFromLabels,
    duplicateDownloadPromptActionLabel,
    hasPartialUnfinishedEntries,
    installedModRecordsFromDirectory,
    isQuotaLimitText,
    matchingPartialOrphanUnfinishedEntries,
    normalizedButtonLabel,
    orphanUnfinishedDownloadEntries,
    parseCollectionAddress,
    readDownloadMetaKey,
    recordCollectionLinkLaunch,
    repairDownloadMetadataInstalledFlags,
    removeOrphanUnfinishedEntries,
    removeOrphanUnfinishedDownloadsForKeys,
    removeUnfinishedEntries,
    safeDisplayText,
    shouldDelayTerminalDownloadFailure,
    staleAlreadyStartedAction,
    staleDownloadStartAction,
    staleOrphanUnfinishedDownloadEntries,
    staleUnfinishedEntries,
    unfinishedDownloadEntries,
    zeroByteDownloadStartIsStalled,
    zeroByteUnfinishedEntries,
)

qDebug = var.debug
_active_download_progress = None


def activeDownloadProgressIsVisible():
    global _active_download_progress

    if not _active_download_progress:
        return False
    try:
        return _active_download_progress.isVisible()
    except RuntimeError:
        _active_download_progress = None
        return False


def raiseActiveDownloadProgress():
    if not activeDownloadProgressIsVisible():
        return False
    try:
        _active_download_progress.raise_()
        _active_download_progress.activateWindow()
    except RuntimeError:
        return False
    return True


def registerActiveDownloadProgress(dialog):
    global _active_download_progress

    if activeDownloadProgressIsVisible():
        raiseActiveDownloadProgress()
        return False
    _active_download_progress = dialog
    return True


def releaseActiveDownloadProgress(dialog):
    global _active_download_progress

    if _active_download_progress is dialog:
        _active_download_progress = None


def downloadDirectory():
    plugin_instance = getattr(__meta__, "_download_plugin", None)
    organizer = getattr(plugin_instance, "_organizer", None)
    if not organizer:
        return None
    return Path(organizer.basePath()) / "downloads"


def installerFomodDefaultSetting():
    plugin_instance = getattr(__meta__, "_install_plugin", None)
    organizer = getattr(plugin_instance, "_organizer", None)
    if not plugin_instance or not organizer:
        return INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"]
    return coerceBoolSetting(
        organizer.pluginSetting(plugin_instance.name(), "auto_advance_fomod_defaults"),
        INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
    )


def linkAutoInstallDefaultSetting():
    plugin_instance = getattr(__meta__, "_download_plugin", None)
    organizer = getattr(plugin_instance, "_organizer", None)
    if not plugin_instance or not organizer:
        return collectionLinkCompletionPolicy()["auto_install_after_download_default"]
    value = organizer.pluginSetting(
        "NXM Collection Link Handler", "auto_install_after_download"
    )
    result = coerceBoolSetting(
        value,
        collectionLinkCompletionPolicy()["auto_install_after_download_default"],
    )
    qDebug(
        "[NXMColDL] Auto-install after download default from link handler "
        f"setting: {result} (raw={value!r})"
    )
    return result


def selectLatestRevision():
    revisions = fetchRevisions(var.uri)
    revision_list = (
        revisions.get("collection", {}).get("revisions", []) if revisions else []
    )
    revision_numbers = [
        revision.get("revisionNumber")
        for revision in revision_list
        if revision.get("revisionNumber") is not None
    ]
    return max(revision_numbers) if revision_numbers else None


def applyCollectionAddress(address):
    parsed = parseCollectionAddress(address)
    if not parsed:
        return None
    var.uri = parsed["uri"]
    var.game = parsed["game"]
    var.collection = parsed["collection"]
    var.revision = parsed["revision"]
    qDebug(
        "[NXMColDL] Collection address parsed: "
        f"{var.game}/{var.collection} rev {var.revision or 'latest'}"
    )
    return parsed


def populateCollectionInfo():
    collection_data = fetchInfo(var.uri)
    qDebug(f"[NXMColDL] Collection Info: {var.cleanJson(collection_data)}")
    if not collection_data:
        return False

    collection = collection_data["collection"]
    var.author = collection["user"]["name"]
    var.name = collection["name"]
    var.summary = var.cleanJson(collection["summary"], True)
    var.thumbnail = collection.get("tileImage", {}).get("thumbnailUrl")
    qDebug(f"[NXMColDL] Collection Name: {var.name}")
    qDebug(f"[NXMColDL] Collection Author: {var.author}")
    qDebug(f"[NXMColDL] Collection Summary: {var.summary}")
    qDebug(f"[NXMColDL] Collection Thumbnail: {var.thumbnail}")
    return True


def populateCollectionMods(mods):
    var.essentialMods.clear()
    var.optionalMods.clear()
    var.chosenOptional.clear()
    var.externalMods.clear()
    var.bundledMods.clear()

    for mod in mods.get("collectionRevision", {}).get("modFiles", []):
        mod_info = mod.get("file", {}).get("mod", {})
        mod_domain = mod_info.get("game", {}).get("domainName")
        if mod_domain and mod_domain != var.game:
            mod_id = mod_info.get("modId")
            var.externalMods.append(
                {
                    "id": mod_id,
                    "name": mod_info.get("name", "External Nexus resource"),
                    "resourceType": f"Nexus {mod_domain}",
                    "resourceUrl": f"https://www.nexusmods.com/{mod_domain}/mods/{mod_id}",
                }
            )
            qDebug(
                "[NXMColDL] Cross-domain mod added as external resource: "
                f"{mod_info.get('name')} ({mod_domain})"
            )
            continue

        if not mod.get("optional"):
            var.essentialMods.append(mod)
            qDebug(f"[NXMColDL] Essential mod added: {mod['file']['mod']['name']}")
        else:
            var.optionalMods.append(mod)
            qDebug(f"[NXMColDL] Optional mod added: {mod['file']['mod']['name']}")

    for mod in mods.get("collectionRevision", {}).get("externalResources", []):
        if mod.get("resourceUrl"):
            var.externalMods.append(mod)
            qDebug(f"[NXMColDL] External resource added: {mod.get('name')}")
        else:
            var.bundledMods.append(mod)
            qDebug(f"[NXMColDL] Bundled resource added: {mod.get('name')}")


class ModInfoWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, uri):
        super().__init__()
        self.uri = uri

    def run(self):
        try:
            mods = fetchModInfo(self.uri)
            if mods is None:
                self.error.emit("Failed to fetch mod info")
            else:
                self.finished.emit(mods)
        except Exception as e:
            qDebug(f"[NXMColDL] Error fetching mod info: {str(e)}")
            self.error.emit(str(e))


class stepURL(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        qDebug("[NXMColDL] Initializing stepURL dialog")
        self.setWindowTitle("NXM Collection Downloader - Enter URL")
        self.setMinimumWidth(400)

        layout = QVBoxLayout()

        self.label = QLabel("Enter Nexus Collection URL:")
        layout.addWidget(self.label)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "https://nexusmods.com/games/.../collections/.../"
        )
        layout.addWidget(self.url_input)

        self.submit_btn = QPushButton("Submit")
        self.submit_btn.clicked.connect(self.submit)
        layout.addWidget(self.submit_btn)

        self.setLayout(layout)

    def get_url(self):
        input_url = self.url_input.text().strip()
        qDebug(f"[NXMColDL] URL entered: {input_url}")
        return applyCollectionAddress(input_url)

    def check_valid(self, url):
        valid = parseCollectionAddress(url)
        if valid:
            qDebug("[NXMColDL] URL is valid")
        return valid

    def submit(self):
        matched = self.get_url()
        if not matched:
            qDebug("[NXMColDL] stepURL: URL validation failed")
            QMessageBox.critical(
                self,
                "Error",
                "The URL you entered is not a valid Nexus Collection URL.",
            )
            return
        self.close()
        if var.revision:
            stepModCount(self.parent()).exec()
        else:
            stepVersion(self.parent()).exec()


class stepVersion(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        qDebug("[NXMColDL] Initializing stepVersion dialog")
        self.setWindowTitle("NXM Collection Downloader - Select Revision")
        self.setMinimumWidth(300)
        self.network_manager = None

        layout = QVBoxLayout()

        qDebug("[NXMColDL] Fetching collection info...")
        populateCollectionInfo()

        infoBox = QHBoxLayout()

        self.thumb_label = QLabel()
        self.thumb_label.setMaximumHeight(128)
        self.thumb_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if var.thumbnail:
            self.network_manager = var.loadThumbnail(
                var.thumbnail, self.thumb_label, self.network_manager
            )
            infoBox.addWidget(self.thumb_label)

        self.info = QLabel(f"""
							<h2 style="margin:0;padding:0">{safeDisplayText(var.name)}</h2>
							<br>
							by <i>{safeDisplayText(var.author)}</i>
							<br>
							<br>
							{var.summary}
							""")
        self.info.setWordWrap(True)
        infoBox.addWidget(self.info)

        layout.addLayout(infoBox)

        layout.addSpacing(10)

        self.label = QLabel("Select Revision:")
        layout.addWidget(self.label)

        self.dropdown = QComboBox()
        self.getList()
        layout.addWidget(self.dropdown)

        self.submit_btn = QPushButton("Submit")
        self.submit_btn.clicked.connect(self.submit)
        layout.addWidget(self.submit_btn)

        self.setLayout(layout)

    def getList(self):
        qDebug("[NXMColDL] stepVersion: Fetching revisions list")
        revisions = fetchRevisions(var.uri)
        if revisions:
            revision_list = revisions.get("collection", {}).get("revisions", [])
            qDebug(
                f"[NXMColDL] stepVersion: Adding {len(revision_list)} revisions to dropdown"
            )
            for data in revision_list:
                created = (
                    data.get("createdAt", "").split("T")[0]
                    if data.get("createdAt")
                    else ""
                )
                revision_num = data.get("revisionNumber", "?")
                self.dropdown.addItem(f"Revision {revision_num} ({created})")
                qDebug(
                    f"[NXMColDL] stepVersion: Added revision {revision_num} created on {created}"
                )
        else:
            qDebug("[NXMColDL] stepVersion: No revisions found or fetch failed")

    def submit(self):
        revision_text = self.dropdown.currentText()
        if not revision_text:
            qDebug("[NXMColDL] No revision selected")
            return

        try:
            revision_str = revision_text.replace("Revision ", "").split(" (")[0].strip()
            if not revision_str or revision_str == "?":
                qDebug(f"[NXMColDL] Invalid revision text: {revision_text}")
                return
            var.revision = int(revision_str)
            qDebug(f"[NXMColDL] Selected Revision: {var.revision}")
            self.close()
            stepModCount(self.parent()).exec()
        except (ValueError, IndexError) as e:
            qDebug(f"[NXMColDL] Failed to parse revision from '{revision_text}': {e}")
            return


class stepModCount(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        qDebug("[NXMColDL] Initializing stepModCount dialog")
        self.setWindowTitle("NXM Collection Downloader - Mod Count")
        self.setMinimumWidth(300)

        layout = QVBoxLayout()
        label = QLabel("This collection contains the following:")
        layout.addWidget(label)

        self.essentialLabel = QLabel("Loading essential mods...")
        self.optionalLabel = QLabel("Loading optional mods...")
        self.externalLabel = QLabel("Loading external resources...")
        self.bundledLabel = QLabel("Loading bundled resources...")

        layout.addWidget(self.essentialLabel)
        layout.addWidget(self.optionalLabel)
        layout.addWidget(self.externalLabel)
        layout.addWidget(self.bundledLabel)

        self.submit_btn = QPushButton("Next")
        self.submit_btn.setEnabled(False)  # disabled until data is loaded
        self.submit_btn.clicked.connect(self.submit)
        layout.addWidget(self.submit_btn)

        self.setLayout(layout)

        self.getMods()

    def getMods(self):
        # background process for loading modlist
        self._thread = QThread(self)
        self._worker = ModInfoWorker(var.uri)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_mods_fetched)
        self._worker.error.connect(self._on_mods_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        qDebug("[NXMColDL] stepModCount: Worker thread started")

    def _on_mods_error(self, err):
        message = getattr(var, "lastQuotaLimitMessage", None) or (
            f"Failed to load mod information: {err}"
        )
        qDebug(f"[NXMColDL] Error fetching mods: {err}; displayed={message}")
        QMessageBox.critical(self, "Error", message)
        self.essentialLabel.setText("Error loading essential mods")
        self.optionalLabel.setText("Error loading optional mods")
        self.externalLabel.setText("Error loading external resources")
        self.bundledLabel.setText("Error loading bundled resources")

    def _on_mods_fetched(self, mods):
        qDebug("[NXMColDL] stepModCount: Processing fetched mod data")
        qDebug(f"[NXMColDL] Mods Info: {var.cleanJson(mods)}")
        populateCollectionMods(mods)

        essentialCount = len(var.essentialMods)
        optionalCount = len(var.optionalMods)
        externalCount = len(var.externalMods)
        bundledCount = len(var.bundledMods)

        qDebug(
            f"[NXMColDL] stepModCount: Totals - Essential: {essentialCount}, Optional: {optionalCount}, External: {externalCount}, Bundled: {bundledCount}"
        )

        self.essentialLabel.setText(f"{essentialCount} essential mods")
        self.optionalLabel.setText(f"{optionalCount} optional mods")
        self.externalLabel.setText(f"{externalCount} external resources")
        self.bundledLabel.setText(f"{bundledCount} bundled resources")

        self.submit_btn.setEnabled(True)

    def submit(self):
        qDebug(
            "[NXMColDL] stepModCount: Proceeding to next dialog based on available mod types"
        )
        self.close()
        if var.essentialMods:
            qDebug("[NXMColDL] stepModCount: Opening stepEssential")
            stepEssential(self.parent()).exec()
        elif var.optionalMods:
            qDebug("[NXMColDL] stepModCount: Opening stepOptional")
            stepOptional(self.parent()).exec()
        elif var.externalMods:
            qDebug("[NXMColDL] stepModCount: Opening stepExternal")
            stepExternal(self.parent()).exec()
        elif var.bundledMods:
            qDebug("[NXMColDL] stepModCount: Opening stepBundled")
            stepBundled(self.parent()).exec()
        else:
            qDebug("[NXMColDL] stepModCount: No mods found, opening stepSummary")
            stepSummary(self.parent()).exec()


class stepEssential(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("NXM Collection Downloader - Essential Mods")
        self.setMinimumWidth(300)

        layout = QVBoxLayout()
        label = QLabel("Included 'Essential' mods:")
        layout.addWidget(label)

        self.modlist = QListWidget()
        self.modlist.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.modlist.setAlternatingRowColors(True)
        for mod in var.essentialMods:
            item_text = f"Mod: {mod['file']['mod']['name']}\nFile: {mod['file']['name']} - {mod['file']['version']}\nby {mod['file']['mod']['author']}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, mod)
            self.modlist.addItem(item)
        self.modlist.setMinimumHeight(200)
        layout.addWidget(self.modlist)

        self.submit_btn = QPushButton("Next")
        self.submit_btn.clicked.connect(self.submit)
        layout.addWidget(self.submit_btn)

        self.setLayout(layout)

    def submit(self):
        self.close()
        if var.optionalMods:
            stepOptional(self.parent()).exec()
        elif var.externalMods:
            stepExternal(self.parent()).exec()
        elif var.bundledMods:
            stepBundled(self.parent()).exec()
        else:
            stepSummary(self.parent()).exec()


class stepOptional(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("NXM Collection Downloader - Optional Mods")
        self.setMinimumWidth(300)

        layout = QVBoxLayout()
        label = QLabel("Select 'Optional' mods:")
        layout.addWidget(label)

        self.modlist = QListWidget()
        self.modlist.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.modlist.setAlternatingRowColors(True)
        for mod in var.optionalMods:
            item_text = f"Mod: {mod['file']['mod']['name']}\nFile: {mod['file']['name']} - {mod['file']['version']}\nby {mod['file']['mod']['author']}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, mod)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setSizeHint(
                QSize(0, QFontMetrics(self.modlist.font()).lineSpacing() * 3 + 8)
            )
            self.modlist.addItem(item)
        self.modlist.setMinimumHeight(200)
        layout.addWidget(self.modlist)

        self.submit_btn = QPushButton("Next")
        self.submit_btn.clicked.connect(self.submit)
        layout.addWidget(self.submit_btn)

        self.setLayout(layout)

    def submit(self):
        var.chosenOptional = []
        for i in range(self.modlist.count()):
            item = self.modlist.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                mod_data = item.data(Qt.ItemDataRole.UserRole)
                var.chosenOptional.append(mod_data)
                qDebug(
                    f"[NXMColDL] Optional mod selected: {mod_data['file']['mod']['name']}"
                )

        qDebug(f"[NXMColDL] Total optional mods selected: {len(var.chosenOptional)}")

        self.close()
        if var.externalMods:
            stepExternal(self.parent()).exec()
        elif var.bundledMods:
            stepBundled(self.parent()).exec()
        else:
            stepSummary(self.parent()).exec()


class stepExternal(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("NXM Collection Downloader - External Mods")
        self.setMinimumWidth(300)

        layout = QVBoxLayout()
        label = QLabel("Included 'External' mods:")
        layout.addWidget(label)

        self.modlist = QListWidget()
        self.modlist.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.modlist.setAlternatingRowColors(True)
        for mod in var.externalMods:
            item = QListWidgetItem(f"{mod['name']}")
            item.setData(Qt.ItemDataRole.UserRole, mod)
            self.modlist.addItem(item)
        self.modlist.setMinimumHeight(200)
        layout.addWidget(self.modlist)

        plugin_instance = getattr(__meta__, "_download_plugin", None)
        if plugin_instance and plugin_instance._organizer:
            var.chosenExternal = coerceBoolSetting(
                plugin_instance._organizer.pluginSetting(
                    plugin_instance.name(), "externalmods_browser_default"
                )
            )

        self.urlCheck = QCheckBox("Open URLs in Browser")
        self.urlCheck.setChecked(var.chosenExternal)
        self.urlCheck.stateChanged.connect(
            lambda s: setattr(var, "chosenExternal", bool(s))
        )
        layout.addWidget(self.urlCheck)

        self.submit_btn = QPushButton("Next")
        self.submit_btn.clicked.connect(self.submit)
        layout.addWidget(self.submit_btn)

        self.setLayout(layout)

    def submit(self):
        self.close()
        if not var.chosenExternal:
            var.externalMods.clear()  # clear if not chosen
        if var.bundledMods:
            stepBundled(self.parent()).exec()
        else:
            stepSummary(self.parent()).exec()


class stepBundled(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("NXM Collection Downloader - Bundled Mods Warning")
        self.setMinimumWidth(300)

        layout = QVBoxLayout()
        explanation = QLabel(
            """
Bundled assets are currently unsupported as there are no public APIs for retreiving them.
They can only be retreived with the Vortex client, and are listed here for your information.
"""
        )
        label = QLabel("The following bundled assets will NOT be installed:")
        label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(explanation)
        layout.addWidget(label)
        self.modlist = QListWidget()
        self.modlist.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.modlist.setAlternatingRowColors(True)
        for mod in var.bundledMods:
            item = QListWidgetItem(f"{mod['name']}")
            item.setData(Qt.ItemDataRole.UserRole, mod)
            self.modlist.addItem(item)
        self.modlist.setMinimumHeight(200)
        layout.addWidget(self.modlist)

        self.submit_btn = QPushButton("Acknowledge")
        self.submit_btn.clicked.connect(self.submit)
        layout.addWidget(self.submit_btn)

        self.setLayout(layout)

    def submit(self):
        self.close()
        stepSummary(self.parent()).exec()


class stepSummary(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NXM Collection Downloader - Summary")
        self.setMinimumWidth(300)

        layout = QVBoxLayout()
        label = QLabel("Summary of selected mods:")
        layout.addWidget(label)

        essentialCount = len(var.essentialMods)
        optionalCount = len(var.chosenOptional)
        externalCount = len(var.externalMods)
        bundledCount = len(var.bundledMods)

        labelEssential = QLabel(f"{essentialCount} essential mods")
        labelOptional = QLabel(f"{optionalCount} optional mods")
        labelExternal = QLabel(f"{externalCount} external resources")
        labelBundled = QLabel(f"{bundledCount} bundled resources")

        layout.addWidget(labelEssential)
        layout.addWidget(labelOptional)
        layout.addWidget(labelExternal)
        layout.addWidget(labelBundled)

        plugin_instance = getattr(__meta__, "_download_plugin", None)
        if plugin_instance and plugin_instance._organizer:
            var.openModWebsites = coerceBoolSetting(
                plugin_instance._organizer.pluginSetting(
                    plugin_instance.name(), "modpage_browser_default"
                )
            )

        self.urlCheck = QCheckBox(
            "Open Mod Websites in Browser (Required for non-Premium users)"
        )
        self.urlCheck.setChecked(var.openModWebsites)
        self.urlCheck.stateChanged.connect(
            lambda s: setattr(var, "openModWebsites", bool(s))
        )
        layout.addWidget(self.urlCheck)

        self.submit_btn = QPushButton("Finish")
        self.submit_btn.clicked.connect(self.submit)
        layout.addWidget(self.submit_btn)

        self.setLayout(layout)

    def submit(self):
        self.close()
        stepDownload(self.parent()).exec()


class stepDownloadProgress(QDialog):
    """Progress dialog that tracks download completion"""

    def __init__(
        self,
        parent=None,
        mods_to_download=None,
        on_complete=None,
        max_retries=2,
        retry_delay_ms=500,
        stale_unfinished_seconds=600,
        close_on_success=False,
        success_close_delay_ms=0,
        decline_duplicate_prompts=True,
        prompt_after_download=False,
        fomod_defaults_default=INSTALLER_SETTING_DEFAULTS[
            "auto_advance_fomod_defaults"
        ],
        auto_install_after_download_default=True,
    ):
        super().__init__(parent)
        self.setWindowTitle("NXM Collection Downloader - Download Progress")
        self.setMinimumWidth(350)
        self.blocked_by_active_progress = False
        if not registerActiveDownloadProgress(self):
            self.blocked_by_active_progress = True
            self.mods_to_download = []
            self.total_mods = 0
            self.on_complete = None
            self.is_tracking = False

            layout = QVBoxLayout()
            label = QLabel(
                "Another collection download is already running. "
                "Finish or close that tracker before starting another collection."
            )
            label.setWordWrap(True)
            layout.addWidget(label)
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.accept)
            layout.addWidget(close_btn)
            self.setLayout(layout)
            QTimer.singleShot(0, raiseActiveDownloadProgress)
            qDebug("[NXMColDL Progress] Blocked concurrent collection download tracker")
            return

        self.mods_to_download = mods_to_download or []
        self.total_mods = len(self.mods_to_download)
        self.on_complete = on_complete
        self.max_retries = max(0, int(max_retries or 0))
        self.retry_delay_ms = retry_delay_ms
        self.stale_unfinished_seconds = max(0, int(stale_unfinished_seconds or 0))
        self.close_on_success = close_on_success
        self.success_close_delay_ms = max(0, int(success_close_delay_ms or 0))
        self.decline_duplicate_prompts = bool(decline_duplicate_prompts)
        self.prompt_after_download = bool(prompt_after_download)
        self.fomod_defaults_default = bool(fomod_defaults_default)
        self.auto_install_after_download_default = bool(
            auto_install_after_download_default
        )
        self.runtime_download_api_trace_logged = False
        self.completion_callback_started = False
        self.completed_count = 0
        self.failed_count = 0
        self.retry_count = 0
        self.duplicate_prompt_count = 0
        self.prequeue_cleanup_count = 0
        self.resume_report_path = None
        self.is_tracking = True
        self.active_queue_key = None
        self.prompt_context_key = None
        self.prompt_context_expires_at = 0
        self.download_ids = {}
        self.ambiguous_download_ids = set()
        self.ambiguous_download_keys = {}
        self.completed_keys = set()
        self.failed_keys = set()
        self.failed_key_reasons = {}
        self.restart_required_keys = set()
        self.restart_required_reasons = {}
        self.duplicate_declined_keys = set()
        self.duplicate_declined_archive_complete_keys = set()
        self.already_started_keys = set()
        self.already_started_at = {}
        self.completed_callback_at = {}
        self.paused_keys = {}
        self.paused_download_stall_seconds = 30
        self.already_started_cleanup_attempts = set()
        self.terminal_cleanup_attempts = set()
        self.terminal_failure_grace_attempts = 0
        self.terminal_failure_grace_max_attempts = 10
        self.terminal_failure_grace_delay_ms = 250
        self.final_readiness_attempts = 0
        self.final_readiness_max_attempts = 80
        self.final_readiness_delay_ms = 250
        self.retry_attempts = {}
        self.key_counts = {}
        self.queued_keys = set()
        self.queued_at = {}
        self.zero_byte_seen_at = {}
        self.waiting_partial_keys = set()
        self.queue_interval_ms = 50
        self.queue_start_timeout_seconds = 10
        self.zero_byte_start_timeout_seconds = 15
        self.zero_byte_start_max_retries = 20
        self.zero_byte_restart_threshold = 999
        self.zero_byte_orphan_stale_seconds = 3
        self.max_unresolved_queue_submissions = 16
        self.queue_pending_mods = []
        self.queue_pending_filter = None
        self.queue_pump_active = False
        self.queue_backoff_until = 0
        self.queue_throttle_log_at = 0
        self.last_download_progress_at = time.time()
        self.last_download_progress_count = 0
        self.last_download_activity_fingerprint = None
        self.tail_boundary_started_at = None
        self.tail_boundary_completion_ratio = 0.75
        self.tail_boundary_retry_budget = max(1, min(3, self.max_retries + 1))
        self.tail_boundary_grace_seconds = adaptiveDownloadTailGraceSeconds(
            self.total_mods,
            self.max_unresolved_queue_submissions,
            self.max_retries,
            self.stale_unfinished_seconds,
        )
        self.quota_stop_message = None
        self.reconcile_timer = QTimer(self)
        self.reconcile_timer.setInterval(1000)
        self.reconcile_timer.timeout.connect(self.reconcile_completed_downloads)
        self.duplicate_prompt_timer = QTimer(self)
        self.duplicate_prompt_timer.setInterval(50)
        self.duplicate_prompt_timer.timeout.connect(
            self.dismiss_duplicate_download_prompt
        )
        for mod in self.mods_to_download:
            key = self.mod_key(mod)
            self.key_counts[key] = self.key_counts.get(key, 0) + 1
        self.preflight_cleanup_zero_byte_unfinished()
        self.expected_file_sizes = collectionDownloadExpectedSizes(
            self.mods_to_download
        )
        self.already_downloaded_keys = downloadedFileKeys(
            downloadDirectory(),
            self.expected_file_sizes,
        )
        self.already_downloaded_keys.update(
            downloadedArchiveNameKeys(downloadDirectory(), self.mods_to_download)
        )

        layout = QVBoxLayout()

        self.label = QLabel(f"Downloading mods: 0/{self.total_mods} completed")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(self.total_mods)
        self.progress.setFormat("%p%")
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.detail_label = QLabel("Downloads have been queued...")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.detail_label)

        self.auto_install_after_download_check = QCheckBox(
            "Install collection after downloads complete"
        )
        self.auto_install_after_download_check.setChecked(
            self.auto_install_after_download_default
        )
        self.auto_install_after_download_check.setVisible(self.on_complete is not None)
        layout.addWidget(self.auto_install_after_download_check)

        self.fomod_defaults_check = QCheckBox("Use default FOMOD installer choices")
        self.fomod_defaults_check.setChecked(self.fomod_defaults_default)
        self.fomod_defaults_check.setVisible(self.on_complete is not None)
        layout.addWidget(self.fomod_defaults_check)

        button_row = QHBoxLayout()
        self.retry_failed_btn = QPushButton("Retry Failed")
        self.retry_failed_btn.clicked.connect(self.retry_failed_downloads)
        self.retry_failed_btn.setVisible(False)
        button_row.addWidget(self.retry_failed_btn)

        self.install_available_btn = QPushButton("Install Available")
        self.install_available_btn.clicked.connect(
            self.run_on_complete_from_user_choice
        )
        self.install_available_btn.setVisible(False)
        button_row.addWidget(self.install_available_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.request_close)
        button_row.addWidget(self.close_btn)
        layout.addLayout(button_row)

        self.setLayout(layout)

        # Register callbacks with download manager
        plugin_instance = getattr(__meta__, "_download_plugin", None)
        if plugin_instance and hasattr(plugin_instance, "_organizer"):
            self.download_manager = plugin_instance._organizer.downloadManager()
            self.trace_runtime_download_api()
            self.download_manager.onDownloadComplete(self.on_download_complete)
            self.download_manager.onDownloadFailed(self.on_download_failed)
            self.download_manager.onDownloadPaused(self.on_download_paused)
            self.download_manager.onDownloadRemoved(self.on_download_removed)
        else:
            self.download_manager = None

        QTimer.singleShot(0, self.queue_downloads)
        self.reconcile_timer.start()
        if self.decline_duplicate_prompts:
            self.duplicate_prompt_timer.start()

        self.destroyed.connect(lambda *_args: releaseActiveDownloadProgress(self))

    def mod_key(self, mod):
        return (int(mod["file"]["mod"]["modId"]), int(mod["file"]["fileId"]))

    def mod_label(self, mod):
        return mod["file"]["mod"].get("name") or mod["file"].get("name") or "mod"

    def trace_runtime_download_api(self):
        """Log MO2's download manager methods once for stale-queue recovery."""
        if self.runtime_download_api_trace_logged:
            return
        self.runtime_download_api_trace_logged = True
        try:
            methods = [
                name for name in dir(self.download_manager) if not name.startswith("_")
            ]
            qDebug(
                "[NXMColDL Progress] Runtime downloadManager API: " + ", ".join(methods)
            )
        except Exception as e:
            qDebug(
                "[NXMColDL Progress] Runtime downloadManager API introspection "
                f"failed: {e}"
            )

    def queue_downloads(self, only_keys=None):
        plugin_instance = getattr(__meta__, "_download_plugin", None)
        if not plugin_instance or not getattr(plugin_instance, "_organizer", None):
            self.detail_label.setText(
                "Failed to access Mod Organizer download manager."
            )
            self.detail_label.setStyleSheet("color: red;")
            self.is_tracking = False
            return

        retry_filter = set(only_keys or []) if only_keys else None
        if self.reconcile_completed_downloads_from_disk():
            self.update_progress()
            self.finish_if_complete()
            if not self.is_tracking:
                return

        pending = []
        for mod in self.mods_to_download:
            key = self.mod_key(mod)
            if retry_filter is not None and key not in retry_filter:
                continue
            if key in self.completed_keys or key in self.queued_keys:
                continue
            pending.append(mod)

        if not pending:
            state = self.refresh_progress_counts()
            if self.total_mods == 0 or state["is_terminal"]:
                self.finish_if_complete()
            return

        pending_existing = {self.mod_key(mod) for mod in self.queue_pending_mods}
        deduped_pending = [
            mod for mod in pending if self.mod_key(mod) not in pending_existing
        ]
        if not deduped_pending:
            if not self.queue_pump_active:
                QTimer.singleShot(self.queue_interval_ms, self.queue_downloads)
            return

        self.queue_pending_mods.extend(deduped_pending)
        qDebug(
            "[NXMColDL Progress] Queued download pump work: "
            f"{len(deduped_pending)} file(s), "
            f"pending_total={len(self.queue_pending_mods)}, "
            f"interval_ms={self.queue_interval_ms}"
        )
        if not self.queue_pump_active:
            self.queue_pump_active = True
            QTimer.singleShot(0, self.pump_next_download)

    def enqueue_retry_key(self, key, delay_ms=None):
        """Append one retry for key to the normal pump instead of bypassing it."""
        self.enqueue_retry_keys({key}, delay_ms=delay_ms)

    def enqueue_retry_keys(self, keys, delay_ms=None):
        """Append retry keys to the normal pump as one scheduled batch."""
        if not self.is_tracking:
            return

        retry_keys = {
            key
            for key in set(keys or [])
            if key not in self.completed_keys and key not in self.failed_keys
        }
        if not retry_keys:
            return
        pending_keys = {self.mod_key(mod) for mod in self.queue_pending_mods}
        retry_keys.difference_update(pending_keys)
        if not retry_keys:
            return

        delay = self.retry_delay_ms if delay_ms is None else max(0, int(delay_ms))
        QTimer.singleShot(
            delay, lambda keys=retry_keys: self.queue_downloads(only_keys=keys)
        )

    def pump_next_download(self):
        if not self.is_tracking:
            self.queue_pending_mods = []
            self.queue_pump_active = False
            return

        if self.stop_if_quota_limited():
            return

        self.reconcile_completed_downloads_from_disk()

        if not self.queue_pending_mods:
            self.queue_pump_active = False
            state = self.refresh_progress_counts()
            if self.total_mods == 0 or state["is_terminal"]:
                self.finish_if_complete()
            elif self.retry_missing_after_queue_drained():
                return
            return

        now = time.time()
        if now < self.queue_backoff_until:
            delay_ms = max(1, int((self.queue_backoff_until - now) * 1000))
            QTimer.singleShot(delay_ms, self.pump_next_download)
            return

        self.cleanup_stalled_zero_byte_queue_starts()
        unresolved = self.unresolved_queue_count()
        if unresolved >= self.max_unresolved_queue_submissions:
            if self.apply_download_tail_boundary(unresolved, now):
                return
            if now >= self.queue_throttle_log_at:
                qDebug(
                    "[NXMColDL Progress] Pausing download pump: "
                    f"{unresolved} unresolved MO2 queue item(s), "
                    f"limit={self.max_unresolved_queue_submissions}"
                )
                self.queue_throttle_log_at = now + 5
            QTimer.singleShot(self.queue_interval_ms, self.pump_next_download)
            return

        self.tail_boundary_started_at = None
        mod = self.queue_pending_mods.pop(0)
        key = self.mod_key(mod)
        skipped = 0
        try:
            if self.is_already_downloaded(key):
                if self.mark_key_completed(key, "Skipped already-downloaded archive"):
                    skipped += self.key_counts.get(key, 1)
            elif key in self.completed_keys or key in self.queued_keys:
                pass
            elif self.wait_for_existing_partial_download(key):
                pass
            else:
                self.cleanup_stale_unfinished_before_queue(key)
                self.queued_keys.add(key)
                self.queued_at[key] = time.time()
                if not self.queue_mod(mod):
                    self.handle_queue_start_failed(mod, key)

            if skipped:
                qDebug(
                    "[NXMColDL Progress] Skipped "
                    f"{skipped} already-downloaded archive(s)"
                )
                self.update_progress()
        finally:
            QTimer.singleShot(self.queue_interval_ms, self.pump_next_download)

    def retry_missing_after_queue_drained(self):
        """Retry keys that never produced archives after the main queue drained."""
        pending_keys = set(self.key_counts) - self.completed_keys - self.failed_keys
        if not pending_keys:
            return False

        unresolved = self.unresolved_queue_count()
        if unresolved > 0:
            qDebug(
                "[NXMColDL Progress] Waiting for MO2 queue to drain before "
                f"retrying missing downloads: {unresolved} unresolved item(s)"
            )
            if self.apply_download_tail_boundary(
                unresolved,
                time.time(),
                unresolved_limit=1,
                boundary_context="drained queue",
            ):
                return True
            QTimer.singleShot(self.queue_interval_ms, self.pump_next_download)
            return True

        retry_keys = set()
        failed_keys = set()
        downloads_dir = downloadDirectory()
        entries_by_key = unfinishedDownloadEntries(downloads_dir)
        removed_files = 0

        for key in sorted(pending_keys):
            attempts = self.retry_attempts.get(key, 0)
            if attempts >= self.max_retries:
                failed_keys.add(key)
                continue

            removed_files += removeUnfinishedEntries(entries_by_key.get(key))
            removed_files += removeOrphanUnfinishedDownloadsForKeys(
                downloads_dir, {key}
            )
            self.retry_attempts[key] = attempts + 1
            self.retry_count += 1
            self.clear_pending_state_for_key(key)
            retry_keys.add(key)

        for key in failed_keys:
            self.clear_pending_state_for_key(key)
            self.mark_key_failed(key, "Download did not produce archive after retries")

        if retry_keys:
            self.detail_label.setText(
                f"Retrying {len(retry_keys)} missing download(s) after queue drained..."
            )
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Retrying missing downloads after queue "
                f"drained: {len(retry_keys)} key(s), {removed_files} file(s) removed"
            )
            QTimer.singleShot(
                self.retry_delay_ms,
                lambda keys=retry_keys: self.queue_downloads(only_keys=keys),
            )
            return True

        if failed_keys:
            self.update_progress()
            self.finish_if_complete()
            return True

        return False

    def progress_state(self):
        return downloadProgressState(
            self.total_mods,
            self.completed_keys,
            self.failed_keys,
            self.key_counts,
        )

    def can_close_dialog(self):
        """Allow closing once this progress dialog is no longer tracking work."""
        return downloadProgressCanClose(
            self.is_tracking,
            self.progress_state(),
            restart_required=bool(self.restart_required_keys),
            quota_limited=bool(self.quota_stop_message),
        )

    def accept(self):
        releaseActiveDownloadProgress(self)
        super().accept()

    def reject(self):
        if self.can_close_dialog():
            releaseActiveDownloadProgress(self)
            super().reject()
            return

        self.request_close()

    def request_close(self):
        """Keep the tracker alive while MO2 still owns active downloads."""
        if self.can_close_dialog():
            self.accept()
            return

        self.detail_label.setText(
            "Downloads are still running in MO2; wait for completion or retry "
            "when available."
        )
        self.detail_label.setStyleSheet("color: orange;")
        qDebug("[NXMColDL Progress] Ignored close request while downloads are active")

    def closeEvent(self, event):
        if self.can_close_dialog():
            releaseActiveDownloadProgress(self)
            event.accept()
            return

        self.request_close()
        event.ignore()

    def refresh_progress_counts(self):
        state = self.progress_state()
        self.completed_count = state["successful"]
        self.failed_count = state["failed"]
        return state

    def note_download_progress(self):
        """Record that the collection made terminal progress."""
        state = self.refresh_progress_counts()
        progress_count = state["successful"] + state["failed"]
        if progress_count > self.last_download_progress_count:
            self.last_download_progress_count = progress_count
            self.last_download_progress_at = time.time()
            self.tail_boundary_started_at = None
        return state

    def note_download_activity_from_disk(self):
        """Record byte-level activity for in-flight MO2 downloads."""
        downloads_dir = downloadDirectory()
        pending_keys = set(self.key_counts) - self.completed_keys - self.failed_keys
        fingerprint = activeUnfinishedDownloadFingerprint(
            unfinishedDownloadEntries(downloads_dir),
            orphanUnfinishedDownloadEntries(downloads_dir),
            pending_keys,
        )
        if fingerprint == self.last_download_activity_fingerprint:
            return False

        self.last_download_activity_fingerprint = fingerprint
        if not fingerprint:
            return False

        self.last_download_progress_at = time.time()
        self.tail_boundary_started_at = None
        return True

    def mark_key_completed(self, key, reason):
        """Count a collection entry as complete after its archive is on disk."""
        if key in self.completed_keys:
            return False

        self.failed_keys.discard(key)
        self.failed_key_reasons.pop(key, None)
        self.restart_required_keys.discard(key)
        self.restart_required_reasons.pop(key, None)
        self.completed_keys.add(key)
        self.prune_pending_queue_keys({key})
        self.note_download_progress()
        qDebug(f"[NXMColDL Progress] {reason}: ModID {key[0]}, FileID {key[1]}")
        return True

    def prune_pending_queue_keys(self, keys=None):
        """Drop queued pump work that no longer needs a MO2 request."""
        if not self.queue_pending_mods:
            return 0

        remove_keys = set(keys or self.completed_keys)
        if not remove_keys:
            return 0

        before = len(self.queue_pending_mods)
        self.queue_pending_mods = [
            mod
            for mod in self.queue_pending_mods
            if self.mod_key(mod) not in remove_keys
        ]
        return before - len(self.queue_pending_mods)

    def mark_key_failed(self, key, reason):
        """Count a collection entry as failed without counting it as downloaded."""
        if key in self.completed_keys or key in self.failed_keys:
            return False

        self.failed_keys.add(key)
        self.failed_key_reasons[key] = reason
        self.note_download_progress()
        qDebug(f"[NXMColDL Progress] {reason}: ModID {key[0]}, FileID {key[1]}")
        return True

    def mark_key_restart_required(self, key, reason):
        """Fail a key in a restartable state when MO2 keeps stale queue state."""
        downloads_dir = downloadDirectory()
        removed = removeOrphanUnfinishedDownloadsForKeys(downloads_dir, {key})
        entries = unfinishedDownloadEntries(downloads_dir).get(key)
        removed += removeUnfinishedEntries(entries)
        self.clear_pending_state_for_key(key)
        self.restart_required_keys.add(key)
        self.restart_required_reasons[key] = reason
        if self.mark_key_failed(key, reason):
            qDebug(
                "[NXMColDL Progress] Marked download restart-required "
                f"for ModID {key[0]}, FileID {key[1]}; "
                f"{removed} stale file(s) removed"
            )
            return True
        return False

    def mark_keys_restart_required(self, keys, reason):
        """Fail multiple keys in a restartable state without hiding the count."""
        marked = 0
        for key in sorted(set(keys or [])):
            if key in self.completed_keys or key in self.failed_keys:
                continue
            if self.mark_key_restart_required(key, reason):
                marked += 1
        return marked

    def stop_queueing_for_resume_boundary(self):
        """Stop issuing MO2 requests after stale in-memory queue state appears."""
        self.queue_pending_mods = []
        self.queue_pump_active = False
        self.queue_pending_filter = None
        self.active_queue_key = None
        self.queue_backoff_until = 0

    def unresolved_queue_count(self):
        """Return collection entries that are still plausibly active in MO2."""
        now = time.time()
        entries_by_key = unfinishedDownloadEntries(downloadDirectory())
        active_partial_keys = {
            key
            for key, entries in entries_by_key.items()
            if hasPartialUnfinishedEntries(entries)
        }
        orphan_entries = orphanUnfinishedDownloadEntries(downloadDirectory())
        pending_keys = set(self.key_counts) - self.completed_keys - self.failed_keys
        active_orphan_keys = set()
        active_orphan_count = 0
        for entry in orphan_entries:
            if entry.get("archive_size", 0) <= 0:
                continue

            if now - entry.get("mtime", 0) < self.stale_unfinished_seconds:
                candidates = [
                    key
                    for key in self.orphan_candidate_keys(entry)
                    if key in pending_keys
                ]
                if len(candidates) == 1:
                    active_orphan_keys.add(candidates[0])
                active_orphan_count += 1

        recent_queue_keys = {
            key
            for key, queued_at in list(self.queued_at.items())
            if now - queued_at < self.queue_start_timeout_seconds
        }
        tracked_download_keys = set(self.download_ids.values())
        ambiguous_recent_keys = self.recent_ambiguous_download_keys(now)
        validation_pending_keys = self.recent_completed_callback_keys(now)
        stalled_zero_keys = self.stalled_zero_byte_keys(
            entries_by_key,
            (
                tracked_download_keys
                | set(self.queued_at)
            )
            - ambiguous_recent_keys
            - validation_pending_keys,
            now,
            self.queue_start_timeout_seconds,
        )
        tracked_download_keys.difference_update(stalled_zero_keys)
        recent_queue_keys.difference_update(stalled_zero_keys)
        stale_queue_keys = (
            set(self.queued_at)
            - recent_queue_keys
            - active_partial_keys
            - active_orphan_keys
            - tracked_download_keys
            - ambiguous_recent_keys
            - validation_pending_keys
        )
        for key in stale_queue_keys:
            self.queued_at.pop(key, None)
            self.queued_keys.discard(key)
            self.remove_download_ids_for_key(key)
            qDebug(
                "[NXMColDL Progress] Dropped stale queued download state "
                f"for ModID {key[0]}, FileID {key[1]}; no active partial appeared"
            )

        unresolved_keys = (
            active_partial_keys
            | active_orphan_keys
            | recent_queue_keys
            | tracked_download_keys
            | ambiguous_recent_keys
            | validation_pending_keys
        )
        unresolved_keys.difference_update(self.completed_keys)
        unresolved_keys.difference_update(self.failed_keys)
        extra_orphans = max(0, active_orphan_count - len(active_orphan_keys))
        return len(unresolved_keys) + extra_orphans

    def stalled_queue_keys(self):
        """Return pending keys with stale MO2 queue/download evidence."""
        entries_by_key = unfinishedDownloadEntries(downloadDirectory())
        pending_keys = set(self.key_counts) - self.completed_keys - self.failed_keys
        keys = set()
        keys.update(key for key in entries_by_key if key in pending_keys)
        keys.update(key for key in self.download_ids.values() if key in pending_keys)
        keys.update(key for key in self.queued_keys if key in pending_keys)
        keys.update(key for key in self.queued_at if key in pending_keys)
        keys.update(key for key in self.waiting_partial_keys if key in pending_keys)
        keys.update(key for key in self.already_started_keys if key in pending_keys)
        return keys

    def stalled_queue_key_progress(self, keys):
        """Return the best observed unfinished archive byte count for each key."""
        entries_by_key = unfinishedDownloadEntries(downloadDirectory())
        progress = {}
        for key in set(keys or []):
            max_size = 0
            for entry in entries_by_key.get(key, []) or []:
                try:
                    max_size = max(max_size, int(entry.get("archive_size", 0) or 0))
                except (TypeError, ValueError, AttributeError):
                    continue
            progress[key] = max_size
        return progress

    def apply_download_tail_boundary(
        self, unresolved, now, unresolved_limit=None, boundary_context="active queue"
    ):
        """Stop a mostly complete run from waiting forever on MO2 queue laggards."""
        state = self.refresh_progress_counts()
        if self.note_download_activity_from_disk():
            now = time.time()
        effective_unresolved_limit = (
            self.max_unresolved_queue_submissions
            if unresolved_limit is None
            else max(1, int(unresolved_limit or 1))
        )
        if not downloadProgressIsStalled(
            self.last_download_progress_at,
            now,
            self.tail_boundary_grace_seconds,
        ):
            self.tail_boundary_started_at = None
            return False

        if not self.tail_boundary_started_at:
            if downloadTailBoundaryReached(
                self.total_mods,
                state["successful"],
                state["failed"],
                unresolved,
                effective_unresolved_limit,
                now,
                now,
                0,
                self.tail_boundary_completion_ratio,
            ):
                self.tail_boundary_started_at = downloadTailBoundaryArmTime(
                    self.last_download_progress_at,
                    now,
                )
                qDebug(
                    "[NXMColDL Progress] Download tail boundary armed: "
                    f"successful={state['successful']}, failed={state['failed']}, "
                    f"total={self.total_mods}, unresolved={unresolved}, "
                    f"limit={effective_unresolved_limit}, "
                    f"context={boundary_context}, "
                    f"started_at={self.tail_boundary_started_at}"
                )
            return False

        if not downloadTailBoundaryReached(
            self.total_mods,
            state["successful"],
            state["failed"],
            unresolved,
            effective_unresolved_limit,
            self.tail_boundary_started_at,
            now,
            self.tail_boundary_grace_seconds,
            self.tail_boundary_completion_ratio,
        ):
            return False

        tail_keys = self.stalled_queue_keys()
        if not tail_keys:
            self.tail_boundary_started_at = None
            return False

        retry_batch_limit = max(1, min(len(tail_keys), effective_unresolved_limit))
        plan = downloadTailLaggardPlan(
            tail_keys,
            self.retry_attempts,
            self.tail_boundary_retry_budget,
            key_progress=self.stalled_queue_key_progress(tail_keys),
            max_retry_keys=retry_batch_limit,
        )
        retry_keys = plan["retry"]
        restart_required_keys = plan["restart_required"]
        deferred_keys = plan.get("deferred", set())
        for key in sorted(retry_keys):
            attempts = self.retry_attempts.get(key, 0)
            self.retry_attempts[key] = attempts + 1
            self.retry_count += 1
            self.clear_pending_state_for_key(key)
        marked = self.mark_keys_restart_required(
            restart_required_keys,
            "Download tail exceeded retry/grace budget; rerun collection to resume laggards",
        )
        if retry_keys:
            qDebug(
                "[NXMColDL Progress] Download tail boundary retrying laggards: "
                f"{len(retry_keys)}/{len(tail_keys)} key(s), "
                f"deferred={len(deferred_keys)}, "
                f"batch_limit={retry_batch_limit}, "
                f"budget={self.tail_boundary_retry_budget}, "
                f"context={boundary_context}"
            )
            self.detail_label.setText(
                f"Retrying {len(retry_keys)}/{len(tail_keys)} lagging download(s); "
                "completed files remain eligible for install."
            )
            self.detail_label.setStyleSheet("color: orange;")
            self.tail_boundary_started_at = None
            self.enqueue_retry_keys(retry_keys, delay_ms=0)
            self.update_progress()
            return True

        qDebug(
            "[NXMColDL Progress] Download tail boundary applied: "
            f"{marked} key(s) moved to restart/manual review after "
            f"{self.tail_boundary_grace_seconds}s grace, "
            f"context={boundary_context}"
        )
        self.detail_label.setText(
            f"{marked} lagging download(s) moved to review; "
            "rerun the collection to resume only missing files."
        )
        self.detail_label.setStyleSheet("color: orange;")
        self.update_progress()
        self.finish_if_complete()
        return True

    def stalled_zero_byte_keys(self, entries_by_key, keys, now, timeout_seconds):
        """Return keys whose MO2 placeholder never started receiving bytes."""
        stalled = set()
        live_zero_keys = set()
        for key in keys:
            if not zeroByteUnfinishedEntries(entries_by_key.get(key)):
                continue

            live_zero_keys.add(key)
            first_seen = self.zero_byte_seen_at.setdefault(
                key,
                self.queued_at.get(key, now),
            )
            if zeroByteDownloadStartIsStalled(first_seen, now, timeout_seconds):
                stalled.add(key)

        for key in list(self.zero_byte_seen_at):
            if key not in live_zero_keys:
                self.zero_byte_seen_at.pop(key, None)

        return stalled

    def cleanup_stalled_zero_byte_queue_starts(self):
        """Recycle MO2 queue placeholders that never became real downloads."""
        downloads_dir = downloadDirectory()
        entries_by_key = unfinishedDownloadEntries(downloads_dir)
        pending_keys = set(self.key_counts) - self.completed_keys - self.failed_keys
        candidate_keys = pending_keys & (
            set(self.queued_at) | self.queued_keys | set(self.download_ids.values())
        )
        if not candidate_keys:
            return 0

        now = time.time()
        ambiguous_recent_keys = self.recent_ambiguous_download_keys(now)
        validation_pending_keys = self.recent_completed_callback_keys(now)
        candidate_keys.difference_update(ambiguous_recent_keys)
        candidate_keys.difference_update(validation_pending_keys)
        if not candidate_keys:
            return 0

        stalled_keys = self.stalled_zero_byte_keys(
            entries_by_key,
            candidate_keys,
            now,
            self.zero_byte_start_timeout_seconds,
        )
        if not stalled_keys:
            return 0

        pending_queue_keys = {self.mod_key(mod) for mod in self.queue_pending_mods}
        recycled = 0
        removed_files = 0
        for key in sorted(stalled_keys):
            if key in self.completed_keys or key in self.failed_keys:
                continue

            attempts = self.retry_attempts.get(key, 0)
            if (
                staleDownloadStartAction(attempts, self.zero_byte_start_max_retries)
                == "restart_required"
            ):
                self.mark_key_restart_required(
                    key,
                    "Zero-byte unfinished download needs MO2 restart",
                )
                continue

            zero_entries = zeroByteUnfinishedEntries(entries_by_key.get(key))
            if not zero_entries:
                continue

            removed = removeUnfinishedEntries(zero_entries)
            removed_files += removed
            self.retry_attempts[key] = attempts + 1
            self.retry_count += 1
            self.clear_pending_state_for_key(key)

            if key not in pending_queue_keys:
                mod = self.mod_by_key(key)
                if mod:
                    self.queue_pending_mods.insert(0, mod)
                    pending_queue_keys.add(key)
                    recycled += 1

            qDebug(
                "[NXMColDL Progress] Recycled zero-byte queued download "
                f"ModID {key[0]}, FileID {key[1]} "
                f"({attempts + 1}/{self.zero_byte_start_max_retries}); "
                f"{removed} file(s) removed"
            )

        if recycled:
            self.detail_label.setText(
                f"Retrying {recycled} zero-byte queued download(s)..."
            )
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Requeued zero-byte queued downloads from pump: "
                f"{recycled} key(s), {removed_files} file(s) removed"
            )

        return recycled

    def stop_if_quota_limited(self):
        if self.quota_stop_message:
            return False
        message = getattr(var, "lastQuotaLimitMessage", None)
        if not message:
            message = self.dismiss_quota_limit_dialog()
        if not message:
            return False

        self.stop_for_quota(message)
        return True

    def dismiss_quota_limit_dialog(self):
        """Close visible quota/rate-limit dialogs and return their text."""
        for window in QApplication.topLevelWidgets():
            if not window.isVisible():
                continue
            if not isinstance(window, (QDialog, QMessageBox)):
                continue
            title = str(window.windowTitle() or "")
            if title in {
                "NXM Collection Downloader",
                "NXM Collection D...ownload Progress",
                "NXM Collection Download Progress",
                "NXM Collection Installer - Select Collection",
                "NXM Collection Installer - Installing Mods",
            }:
                continue
            labels = self.dialog_labels(window)
            text = "\n".join([title] + labels)
            if not isQuotaLimitText(text):
                continue

            for button in window.findChildren(QPushButton):
                if (
                    normalizedButtonLabel(button.text()) in {"ok", "close"}
                    and button.isEnabled()
                ):
                    button.click()
                    break
            message = "Nexus quota/rate limit detected from MO2 dialog."
            qDebug(
                "[NXMColDL Progress] Quota/rate-limit dialog detected: "
                f"title={title!r}, text={text[:500]!r}"
            )
            return message
        return None

    def stop_for_quota(self, message):
        """Pause queueing in a retryable terminal state when Nexus is limited."""
        if self.quota_stop_message:
            return

        self.quota_stop_message = str(message)
        self.queue_pending_mods = []
        self.queue_pump_active = False
        self.reconcile_completed_downloads_from_disk()

        pending_keys = set(self.key_counts) - self.completed_keys - self.failed_keys
        self.failed_keys.update(pending_keys)
        for key in sorted(pending_keys):
            self.failed_key_reasons[key] = self.quota_stop_message
            self.queued_keys.discard(key)
            self.queued_at.pop(key, None)
            self.waiting_partial_keys.discard(key)
            self.already_started_keys.discard(key)
            self.already_started_at.pop(key, None)
            self.paused_keys.pop(key, None)
            self.already_started_cleanup_attempts.discard(key)
            self.terminal_cleanup_attempts.discard(key)
            self.remove_download_ids_for_key(key)

        self.detail_label.setText(self.quota_stop_message)
        self.detail_label.setStyleSheet("color: orange;")
        self.refresh_progress_counts()
        self.update_progress()
        self.finish_if_complete()
        qDebug(
            "[NXMColDL Progress] Download queue paused for quota/rate limit: "
            f"{self.quota_stop_message}; completed={len(self.completed_keys)}, "
            f"waiting_for_retry={len(pending_keys)}"
        )

    def is_already_downloaded(self, key):
        """Refresh disk state and report whether this Nexus file is complete."""
        if key in self.already_downloaded_keys:
            return True

        self.already_downloaded_keys = downloadedFileKeys(
            downloadDirectory(),
            self.expected_file_sizes,
        )
        self.already_downloaded_keys.update(
            downloadedArchiveNameKeys(downloadDirectory(), self.mods_to_download)
        )
        return key in self.already_downloaded_keys

    def record_waiting_for_existing_download(self, key, message):
        """Track a file MO2 is already downloading instead of prompting again."""
        now = time.time()
        self.queued_keys.add(key)
        self.queued_at[key] = now
        self.zero_byte_seen_at.pop(key, None)
        self.waiting_partial_keys.add(key)
        self.already_started_at[key] = now
        self.detail_label.setText(message)
        self.detail_label.setStyleSheet("color: orange;")

    def handle_duplicate_declined_key(self, key):
        """Handle duplicate prompts without crediting partial archives."""
        if (
            key in self.duplicate_declined_archive_complete_keys
            or self.is_already_downloaded(key)
        ):
            self.duplicate_declined_archive_complete_keys.discard(key)
            self.clear_pending_state_for_key(key)
            if self.mark_key_completed(key, "Skipped duplicate existing archive"):
                self.update_progress()
                self.finish_if_complete()
            return True

        self.record_waiting_for_existing_download(
            key,
            "Waiting for an existing MO2 download/archive to complete...",
        )
        qDebug(
            "[NXMColDL Progress] Duplicate prompt declined for incomplete archive "
            f"ModID {key[0]}, FileID {key[1]}; waiting for MO2"
        )
        return True

    def remove_download_ids_for_key(self, key):
        download_ids = [
            download_id
            for download_id, download_key in self.download_ids.items()
            if download_key == key
        ]
        for download_id in download_ids:
            self.download_ids.pop(download_id, None)
            self.ambiguous_download_ids.discard(download_id)

    def mark_ambiguous_download_key(self, key, now=None):
        now = time.time() if now is None else now
        self.ambiguous_download_keys[key] = now
        self.queued_keys.add(key)
        self.queued_at[key] = now
        self.zero_byte_seen_at.pop(key, None)

    def recent_ambiguous_download_keys(self, now=None):
        now = time.time() if now is None else now
        grace_seconds = max(
            self.stale_unfinished_seconds,
            self.queue_start_timeout_seconds,
            self.zero_byte_start_timeout_seconds,
        )
        recent = set()
        for key, marked_at in list(self.ambiguous_download_keys.items()):
            if key in self.completed_keys or key in self.failed_keys:
                self.ambiguous_download_keys.pop(key, None)
                continue
            if now - marked_at < grace_seconds:
                recent.add(key)
            else:
                self.ambiguous_download_keys.pop(key, None)
        return recent

    def mark_completed_callback_pending(self, key, now=None):
        now = time.time() if now is None else now
        self.completed_callback_at[key] = now
        self.queued_keys.add(key)
        self.queued_at[key] = now
        self.zero_byte_seen_at.pop(key, None)
        self.waiting_partial_keys.add(key)

    def recent_completed_callback_keys(self, now=None):
        now = time.time() if now is None else now
        grace_seconds = max(
            self.stale_unfinished_seconds,
            self.queue_start_timeout_seconds,
            self.zero_byte_start_timeout_seconds,
        )
        recent = set()
        for key, marked_at in list(self.completed_callback_at.items()):
            if key in self.completed_keys or key in self.failed_keys:
                self.completed_callback_at.pop(key, None)
                continue
            if now - marked_at < grace_seconds:
                recent.add(key)
            else:
                self.completed_callback_at.pop(key, None)
        return recent

    def track_download_id(self, download_id, key):
        now = time.time()
        existing_key = self.download_ids.get(download_id)
        if existing_key is not None and existing_key != key:
            self.ambiguous_download_ids.add(download_id)
            self.download_ids.pop(download_id, None)
            self.mark_ambiguous_download_key(existing_key, now)
            self.mark_ambiguous_download_key(key, now)
            qDebug(
                "[NXMColDL Progress] Ignoring non-unique MO2 download ID "
                f"{download_id}; existing ModID {existing_key[0]}, "
                f"FileID {existing_key[1]}, new ModID {key[0]}, FileID {key[1]}"
            )
            return False
        if download_id in self.ambiguous_download_ids:
            self.mark_ambiguous_download_key(key, now)
            qDebug(
                "[NXMColDL Progress] Ignoring reused MO2 download ID "
                f"{download_id} for ModID {key[0]}, FileID {key[1]}"
            )
            return False
        self.download_ids[download_id] = key
        return True

    def pop_tracked_download_key(self, download_id, event_name):
        coerced_download_id = coerceDownloadId(download_id)
        if coerced_download_id is None:
            return None
        if coerced_download_id in self.ambiguous_download_ids:
            qDebug(
                "[NXMColDL Progress] Ignoring "
                f"{event_name} callback for non-unique MO2 download ID "
                f"{download_id}; disk reconciliation will settle it"
            )
            return None
        return self.download_ids.pop(coerced_download_id, None)

    def clear_pending_state_for_key(self, key):
        self.queued_keys.discard(key)
        self.queued_at.pop(key, None)
        self.zero_byte_seen_at.pop(key, None)
        self.waiting_partial_keys.discard(key)
        self.ambiguous_download_keys.pop(key, None)
        self.completed_callback_at.pop(key, None)
        self.already_started_keys.discard(key)
        self.already_started_at.pop(key, None)
        self.paused_keys.pop(key, None)
        self.already_started_cleanup_attempts.discard(key)
        if self.active_queue_key == key:
            self.active_queue_key = None
        self.clear_prompt_context(key)
        self.remove_download_ids_for_key(key)

    def orphan_candidate_keys(
        self, entry, completed_on_disk=None, include_completed=False
    ):
        mod_id = entry.get("mod_id")
        if mod_id is None:
            return []

        completed_on_disk = completed_on_disk or set()
        candidates = []
        for key in self.key_counts:
            if key[0] != mod_id or key in self.failed_keys:
                continue
            if key in completed_on_disk:
                continue
            if not include_completed and key in self.completed_keys:
                continue
            candidates.append(key)
        return candidates

    def tracked_orphan_candidate_keys(self, entry):
        """Return tracked MO2 keys that plausibly own an orphan placeholder."""
        mod_id = entry.get("mod_id")
        if mod_id is None:
            return []

        candidates = [
            key
            for key in set(self.download_ids.values())
            if key[0] == mod_id
            and key not in self.completed_keys
            and key not in self.failed_keys
        ]
        return candidates

    def preflight_cleanup_zero_byte_unfinished(self):
        """Clear dead MO2 placeholders before queueing the collection batch."""
        downloads_dir = downloadDirectory()
        pending_keys = set(self.key_counts)
        cleanup = cleanupZeroByteUnfinishedDownloads(downloads_dir, pending_keys)
        orphan_removed = removeOrphanUnfinishedDownloadsForKeys(
            downloads_dir, pending_keys
        )
        if not cleanup["cleaned_keys"] and not orphan_removed:
            return

        cleaned_count = len(cleanup["cleaned_keys"]) + orphan_removed
        self.prequeue_cleanup_count += cleaned_count
        qDebug(
            "[NXMColDL Progress] Removed zero-byte unfinished downloads "
            f"before queueing {cleaned_count} collection file(s); "
            f"{cleanup['removed_files'] + orphan_removed} file(s) removed "
            f"({orphan_removed} orphan)"
        )

    def cleanup_stale_unfinished_before_queue(self, key):
        """Remove empty leftovers before MO2 sees a duplicate file."""
        downloads_dir = downloadDirectory()
        entries = unfinishedDownloadEntries(downloads_dir).get(key)
        cleanup_entries = zeroByteUnfinishedEntries(entries)
        orphan_removed = removeOrphanUnfinishedDownloadsForKeys(downloads_dir, {key})
        if not cleanup_entries and not orphan_removed:
            return 0

        removed = removeUnfinishedEntries(cleanup_entries) + orphan_removed

        self.prequeue_cleanup_count += removed
        qDebug(
            "[NXMColDL Progress] Removed zero-byte unfinished download "
            f"before queueing ModID {key[0]}, FileID {key[1]}; "
            f"{removed} file(s) removed"
        )
        return removed

    def cleanup_failed_unfinished_before_retry(self, key):
        """Remove MO2 leftovers after a failed/removed download callback."""
        downloads_dir = downloadDirectory()
        entries = unfinishedDownloadEntries(downloads_dir).get(key)
        removed = removeUnfinishedEntries(entries)
        removed += removeOrphanUnfinishedDownloadsForKeys(
            downloads_dir, {key}, include_nonzero=True
        )
        if removed:
            self.prequeue_cleanup_count += removed
            qDebug(
                "[NXMColDL Progress] Removed unfinished download leftovers "
                f"before retrying ModID {key[0]}, FileID {key[1]}; "
                f"{removed} file(s) removed"
            )
        return removed

    def wait_for_existing_partial_download(self, key):
        """Avoid duplicate prompts when MO2 already has a resumable partial file."""
        entries = self.partial_orphan_aware_unfinished_entries(key)
        if not entries:
            return False

        self.queued_keys.add(key)
        self.queued_at.setdefault(key, time.time())
        self.waiting_partial_keys.add(key)
        qDebug(
            "[NXMColDL Progress] Waiting for existing partial download "
            f"for ModID {key[0]}, FileID {key[1]}"
        )
        self.detail_label.setText(
            "Waiting for an existing partial download in MO2 to complete..."
        )
        self.detail_label.setStyleSheet("color: orange;")
        return True

    def partial_orphan_aware_unfinished_entries(self, key):
        """Return metadata-backed or uniquely matched orphan partial entries."""
        downloads_dir = downloadDirectory()
        entries = unfinishedDownloadEntries(downloads_dir).get(key)
        if hasPartialUnfinishedEntries(entries):
            qDebug(
                "[NXMColDL Progress] Found metadata-backed partial download "
                f"for ModID {key[0]}, FileID {key[1]}: "
                f"{', '.join(entry['archive'].name for entry in entries)}"
            )
            return entries

        completed_on_disk = downloadedFileKeys(
            downloads_dir,
            self.expected_file_sizes,
        )
        entries = matchingPartialOrphanUnfinishedEntries(
            downloads_dir,
            key,
            self.key_counts,
            completed_on_disk=completed_on_disk,
            completed_keys=self.completed_keys,
            failed_keys=set(self.failed_keys) - {key},
        )
        if entries:
            qDebug(
                "[NXMColDL Progress] Found uniquely matched orphan partial "
                f"download for ModID {key[0]}, FileID {key[1]}: "
                f"{', '.join(entry['archive'].name for entry in entries)}"
            )
        return entries

    def queue_mod(self, mod):
        plugin_instance = getattr(__meta__, "_download_plugin", None)
        key = self.mod_key(mod)
        mod_id, file_id = key
        mod_name = self.mod_label(mod)
        qDebug(
            "[NXMColDL] Queueing download - "
            f"ModID: {mod_id}, FileID: {file_id}, Name: {mod_name}"
        )
        self.active_queue_key = key
        self.set_prompt_context(key)
        try:
            download_id = plugin_instance.downloadMod(mod)
        finally:
            # MO2 sometimes posts duplicate/already-started prompts just after
            # startDownloadNexusFile returns. Pumping one event pass keeps the
            # prompt tied to this collection entry instead of leaving it modal.
            QApplication.processEvents()
            self.dismiss_duplicate_download_prompt()
            self.active_queue_key = None

        if key in self.duplicate_declined_keys:
            return self.handle_duplicate_declined_key(key)

        if key in self.already_started_keys:
            self.record_waiting_for_existing_download(
                key,
                "Waiting for an already-started MO2 download to complete...",
            )
            qDebug(
                "[NXMColDL Progress] Waiting for already-started download "
                f"for ModID {mod_id}, FileID {file_id}"
            )
            return True

        coerced_download_id = coerceDownloadId(download_id)
        if coerced_download_id is None:
            qDebug(
                "[NXMColDL Progress] MO2 did not return a usable download id "
                f"for ModID {mod_id}, FileID {file_id}: {download_id}"
            )
            return False

        tracked = self.track_download_id(coerced_download_id, key)
        qDebug(
            "[NXMColDL Progress] Tracking download "
            f"ID {download_id} for ModID {mod_id}, FileID {file_id}, "
            f"callback_tracked={tracked}"
        )
        return True

    def dialog_buttons(self, window):
        return [
            (button.text(), button.isEnabled())
            for button in window.findChildren(QPushButton)
        ]

    def dialog_labels(self, window):
        return [label.text() for label in window.findChildren(QLabel)]

    def set_prompt_context(self, key):
        self.prompt_context_key = key
        self.prompt_context_expires_at = time.time() + 5

    def current_prompt_key(self):
        key = activeDownloadPromptKey(
            self.active_queue_key,
            self.prompt_context_key,
            self.prompt_context_expires_at,
            time.time(),
        )
        if key is None:
            self.prompt_context_key = None
            self.prompt_context_expires_at = 0
        return key

    def clear_prompt_context(self, key):
        if self.prompt_context_key == key:
            self.prompt_context_key = None
            self.prompt_context_expires_at = 0

    def dismiss_duplicate_download_prompt(self):
        """Answer MO2 duplicate/already-started prompts during collection queueing."""
        if not self.is_tracking:
            self.duplicate_prompt_timer.stop()
            return False

        if self.dismiss_completed_rename_error_dialog():
            return True

        if self.stop_if_quota_limited():
            return True

        for window in QApplication.topLevelWidgets():
            if not window.isVisible():
                continue
            action = duplicateDownloadPromptActionLabel(
                window.windowTitle(), self.dialog_buttons(window)
            )
            if action not in {"no", "ok"}:
                continue

            labels = self.dialog_labels(window)
            archive_action = None
            restart_required_after_click = False
            is_download_again = str(window.windowTitle() or "") == "Download again?"
            if is_download_again:
                archive_action = duplicateDownloadPromptArchiveAction(
                    labels,
                    downloadDirectory(),
                )
                if archive_action == "no":
                    action = "no"
                else:
                    # In collection mode, downloading again creates numbered
                    # duplicate archives and makes reconciliation ambiguous.
                    # Treat this as stale MO2 state and resume after restart.
                    action = "no"
                    restart_required_after_click = True

            prompt_key = self.current_prompt_key()
            if prompt_key is None:
                prompt_key = downloadPromptKeyFromLabels(
                    labels,
                    self.key_counts,
                )
            if prompt_key is None and is_download_again:
                prompt_key = downloadPromptKeyFromArchiveLabels(
                    labels,
                    downloadDirectory(),
                    self.key_counts,
                )
            if prompt_key is None:
                if is_download_again:
                    action = "no"
                elif archive_action not in {"yes", "no"}:
                    continue
                else:
                    action = archive_action

            for button in window.findChildren(QPushButton):
                if (
                    normalizedButtonLabel(button.text()) == action
                    and button.isEnabled()
                ):
                    self.duplicate_prompt_count += 1
                    if action == "no":
                        if prompt_key is not None:
                            self.duplicate_declined_keys.add(prompt_key)
                            if archive_action == "no":
                                self.duplicate_declined_archive_complete_keys.add(
                                    prompt_key
                                )
                        qDebug(
                            "[NXMColDL Progress] Declined duplicate download prompt "
                            + (
                                f"for ModID {prompt_key[0]}, FileID {prompt_key[1]}"
                                if prompt_key is not None
                                else "for completed archive"
                            )
                        )
                    elif action == "ok":
                        if prompt_key is None:
                            continue
                        self.already_started_keys.add(prompt_key)
                        self.already_started_at[prompt_key] = time.time()
                        qDebug(
                            "[NXMColDL Progress] Acknowledged already-started "
                            "download prompt for "
                            f"ModID {prompt_key[0]}, "
                            f"FileID {prompt_key[1]}"
                        )
                    else:
                        if prompt_key is not None:
                            self.queued_at[prompt_key] = time.time()
                            self.waiting_partial_keys.discard(prompt_key)
                        qDebug(
                            "[NXMColDL Progress] Accepted duplicate download prompt "
                            "because no complete archive exists for "
                            + (
                                f"ModID {prompt_key[0]}, FileID {prompt_key[1]}"
                                if prompt_key is not None
                                else "the named archive"
                            )
                        )
                    self.queue_backoff_until = max(
                        self.queue_backoff_until,
                        time.time() + 1,
                    )
                    if prompt_key is not None:
                        self.clear_prompt_context(prompt_key)
                    button.click()
                    if restart_required_after_click and prompt_key is not None:
                        self.mark_key_restart_required(
                            prompt_key,
                            "Duplicate download prompt without complete archive needs MO2 restart",
                        )
                        self.update_progress()
                        self.finish_if_complete()
                    elif action == "no" and prompt_key is not None:
                        self.handle_duplicate_declined_key(prompt_key)
                    return True

        return False

    def download_dialog_path(self, path_text):
        """Resolve an MO2/Wine downloads path from a dialog label."""
        name = Path(str(path_text).replace("\\", "/")).name
        if not name:
            return None
        return downloadDirectory() / name

    def dismiss_completed_rename_error_dialog(self):
        """Recover MO2 rename errors when the completed destination exists."""
        for window in QApplication.topLevelWidgets():
            if not window.isVisible() or str(window.windowTitle() or "") != "Error":
                continue

            labels = self.dialog_labels(window)
            text = "\n".join(labels)
            if "failed to rename" not in text.casefold():
                continue

            match = re.search(
                r"failed to rename ['\"]([^'\"]+\.unfinished)['\"] to ['\"]([^'\"]+)['\"]",
                text,
                re.I,
            )
            if not match:
                continue

            source = self.download_dialog_path(match.group(1))
            destination = self.download_dialog_path(match.group(2))
            if not source or not destination:
                continue

            metadata = Path(str(source) + ".meta")
            key = readDownloadMetaKey(metadata)
            if key is None:
                continue
            if key not in self.key_counts:
                continue

            try:
                destination_size = destination.stat().st_size
            except OSError:
                destination_size = 0
            expected_size = self.expected_file_sizes.get(key, 0)
            if destination_size <= 0:
                continue
            if expected_size and destination_size < expected_size:
                continue

            removed = 0
            for path in (source, metadata):
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass

            self.clear_pending_state_for_key(key)
            self.duplicate_declined_keys.discard(key)
            self.duplicate_declined_archive_complete_keys.discard(key)
            if self.mark_key_completed(key, "Recovered completed rename target"):
                self.update_progress()
                self.finish_if_complete()

            for button in window.findChildren(QPushButton):
                if (
                    normalizedButtonLabel(button.text()) in {"ok", "close"}
                    and button.isEnabled()
                ):
                    qDebug(
                        "[NXMColDL Progress] Dismissed completed rename error "
                        f"for ModID {key[0]}, FileID {key[1]}; "
                        f"removed={removed}, destination={destination.name}"
                    )
                    button.click()
                    return True

        return False

    def handle_queue_start_failed(self, mod, key):
        """Retry or fail a download that MO2 refused to queue."""
        self.queued_keys.discard(key)
        self.queued_at.pop(key, None)
        if key in self.already_started_keys:
            self.record_waiting_for_existing_download(
                key,
                "Waiting for an already-started MO2 download to complete...",
            )
            qDebug(
                "[NXMColDL Progress] Queue start returned no id because MO2 "
                f"already had ModID {key[0]}, FileID {key[1]} started"
            )
            return
        if key in self.duplicate_declined_keys:
            self.handle_duplicate_declined_key(key)
            return
        if self.is_already_downloaded(key):
            if self.mark_key_completed(key, "Skipped already-downloaded archive"):
                self.update_progress()
                self.finish_if_complete()
            return

        attempts = self.retry_attempts.get(key, 0)
        if attempts < self.max_retries:
            self.retry_attempts[key] = attempts + 1
            self.retry_count += 1
            mod_name = self.mod_label(mod) if mod else f"ModID {key[0]}"
            self.detail_label.setText(
                f"Retrying queue start for {mod_name} "
                f"({attempts + 1}/{self.max_retries})..."
            )
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Requeueing after failed queue start "
                f"for ModID {key[0]}, FileID {key[1]} "
                f"({attempts + 1}/{self.max_retries})"
            )
            self.enqueue_retry_key(key)
            return

        if self.mark_key_failed(key, "Failed to queue download after retries"):
            self.update_progress()
            self.finish_if_complete()

    def retry_after_already_started_cleanup(self, mod, key, count_retry=False):
        """Requeue once after removing a stale placeholder that blocked MO2."""
        if key in self.already_started_cleanup_attempts:
            return False

        if count_retry:
            attempts = self.retry_attempts.get(key, 0)
            if attempts >= self.max_retries:
                return False

        removed = self.cleanup_stale_unfinished_before_queue(key)
        if not removed:
            return False

        self.already_started_cleanup_attempts.add(key)
        retry_suffix = ""
        if count_retry:
            self.retry_attempts[key] = attempts + 1
            self.retry_count += 1
            retry_suffix = f" ({attempts + 1}/{self.max_retries})"

        self.queued_keys.discard(key)
        self.queued_at.pop(key, None)
        self.waiting_partial_keys.discard(key)
        self.already_started_keys.discard(key)
        self.already_started_at.pop(key, None)
        self.paused_keys.pop(key, None)
        self.failed_keys.discard(key)
        self.failed_key_reasons.pop(key, None)
        self.remove_download_ids_for_key(key)

        mod_name = self.mod_label(mod) if mod else f"ModID {key[0]}"
        self.detail_label.setText(
            f"Retrying already-started {mod_name}{retry_suffix}..."
        )
        self.detail_label.setStyleSheet("color: orange;")
        qDebug(
            "[NXMColDL Progress] Requeueing after stale already-started "
            f"placeholder for ModID {key[0]}, FileID {key[1]}; "
            f"{removed} file(s) removed"
        )
        self.enqueue_retry_key(key)
        return True

    def retry_failed_leftovers_before_terminal(self):
        """Recover failed keys that still have removable MO2 placeholders."""
        retry_keys = set(self.failed_keys) - set(self.completed_keys)
        recovered = False
        for key in sorted(retry_keys):
            if key in self.terminal_cleanup_attempts:
                continue

            mod = self.mod_by_key(key)
            if not mod:
                continue

            removed = self.cleanup_failed_unfinished_before_retry(key)
            if not removed:
                continue

            self.terminal_cleanup_attempts.add(key)
            self.failed_keys.discard(key)
            self.failed_key_reasons.pop(key, None)
            self.queued_keys.discard(key)
            self.queued_at.pop(key, None)
            self.waiting_partial_keys.discard(key)
            self.already_started_keys.discard(key)
            self.already_started_at.pop(key, None)
            self.paused_keys.pop(key, None)
            self.already_started_cleanup_attempts.discard(key)
            self.remove_download_ids_for_key(key)

            mod_name = self.mod_label(mod)
            self.detail_label.setText(f"Retrying blocked {mod_name}...")
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Requeueing failed terminal download after "
                "removing MO2 leftovers for "
                f"ModID {key[0]}, FileID {key[1]}; {removed} file(s) removed"
            )
            self.enqueue_retry_key(key)
            recovered = True

        if recovered:
            self.refresh_progress_counts()
            self.update_progress()
        return recovered

    def recover_failed_partial_orphans_before_terminal(self):
        """Keep waiting when a failed key still has a fresh MO2 partial archive."""
        retry_keys = set(self.failed_keys) - set(self.completed_keys)
        recovered = False
        for key in sorted(retry_keys):
            entries = self.partial_orphan_aware_unfinished_entries(key)
            if not entries:
                continue

            self.failed_keys.discard(key)
            self.failed_key_reasons.pop(key, None)
            self.queued_keys.add(key)
            self.queued_at.setdefault(key, time.time())
            self.waiting_partial_keys.add(key)
            self.already_started_keys.add(key)
            self.already_started_at.setdefault(key, time.time())
            self.remove_download_ids_for_key(key)
            recovered = True
            qDebug(
                "[NXMColDL Progress] Recovered failed queue state as active "
                "partial download for "
                f"ModID {key[0]}, FileID {key[1]} using "
                f"{', '.join(entry['archive'].name for entry in entries)}"
            )

        if recovered:
            self.detail_label.setText(
                "Waiting for MO2 partial downloads to complete..."
            )
            self.detail_label.setStyleSheet("color: orange;")
            self.refresh_progress_counts()
            self.update_progress()
        return recovered

    def requeue_mod(self, mod, key):
        """Requeue a download after clearing empty placeholders for the same file."""
        if not self.is_tracking:
            return
        if key in self.completed_keys or key in self.failed_keys:
            return
        if self.is_already_downloaded(key):
            if self.mark_key_completed(key, "Skipped already-downloaded archive"):
                self.update_progress()
                self.finish_if_complete()
            return

        if self.wait_for_existing_partial_download(key):
            return
        self.cleanup_stale_unfinished_before_queue(key)
        self.queued_keys.add(key)
        self.queued_at[key] = time.time()
        if not self.queue_mod(mod):
            self.handle_queue_start_failed(mod, key)

    def mod_by_key(self, key):
        for mod in self.mods_to_download:
            if self.mod_key(mod) == key:
                return mod
        return None

    def on_download_complete(self, download_id):
        """Called when a download completes successfully"""
        if not self.is_tracking:
            return

        key = self.pop_tracked_download_key(download_id, "complete")
        if key is None or key in self.completed_keys:
            return

        if self.is_already_downloaded(key):
            if self.mark_key_completed(key, f"Download completed: ID {download_id}"):
                self.update_progress()
                self.finish_if_complete()
            return

        self.record_waiting_for_existing_download(
            key,
            "Waiting for MO2 to finish writing the completed archive...",
        )
        self.mark_completed_callback_pending(key)
        qDebug(
            "[NXMColDL Progress] Download-complete callback fired before archive "
            f"validated for ModID {key[0]}, FileID {key[1]}"
        )

    def on_download_failed(self, download_id):
        """Called when a download fails"""
        if not self.is_tracking:
            return

        key = self.pop_tracked_download_key(download_id, "failed")
        if key is None or key in self.completed_keys or key in self.failed_keys:
            return

        qDebug(f"[NXMColDL Progress] Download failed: ID {download_id}")
        if self.is_already_downloaded(key):
            if self.mark_key_completed(
                key,
                f"Recovered completed archive: ID {download_id}",
            ):
                self.update_progress()
                self.finish_if_complete()
            return

        attempts = self.retry_attempts.get(key, 0)
        if attempts < self.max_retries:
            self.cleanup_failed_unfinished_before_retry(key)
            self.retry_attempts[key] = attempts + 1
            self.retry_count += 1
            self.queued_keys.discard(key)
            self.queued_at.pop(key, None)
            mod = self.mod_by_key(key)
            mod_name = self.mod_label(mod) if mod else f"ModID {key[0]}"
            self.detail_label.setText(
                f"Retrying {mod_name} ({attempts + 1}/{self.max_retries})..."
            )
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Requeueing failed download "
                f"ModID {key[0]}, FileID {key[1]} "
                f"({attempts + 1}/{self.max_retries})"
            )
            if mod:
                self.enqueue_retry_key(key)
            return

        if self.mark_key_failed(
            key, f"Download failed after retries: ID {download_id}"
        ):
            self.update_progress()
            self.finish_if_complete()

    def on_download_paused(self, download_id):
        if not self.is_tracking:
            return

        coerced_download_id = coerceDownloadId(download_id)
        if coerced_download_id is None:
            return
        if coerced_download_id in self.ambiguous_download_ids:
            qDebug(
                "[NXMColDL Progress] Ignoring paused callback for non-unique "
                f"MO2 download ID {download_id}"
            )
            return
        if coerced_download_id not in self.download_ids:
            return
        key = self.download_ids.get(coerced_download_id)
        if key:
            self.paused_keys[key] = time.time()
        qDebug(f"[NXMColDL Progress] Download paused: ID {download_id}")
        self.detail_label.setText("A tracked download is paused in MO2.")
        self.detail_label.setStyleSheet("color: orange;")

    def retry_paused_downloads_stalled(self):
        """Retry paused downloads that MO2 left idle without progress."""
        if self.quota_stop_message or not self.paused_keys:
            return False

        now = time.time()
        entries_by_key = unfinishedDownloadEntries(downloadDirectory())
        handled = False
        for key, paused_at in list(self.paused_keys.items()):
            if key in self.completed_keys or key in self.failed_keys:
                self.paused_keys.pop(key, None)
                continue
            entries = entries_by_key.get(key)
            if not hasPartialUnfinishedEntries(entries):
                continue
            newest_mtime = max(entry["mtime"] for entry in entries)
            if (
                now - paused_at >= self.paused_download_stall_seconds
                and now - newest_mtime >= self.paused_download_stall_seconds
            ):
                attempts = self.retry_attempts.get(key, 0)
                self.clear_pending_state_for_key(key)
                if attempts >= self.max_retries:
                    self.mark_key_failed(key, "Paused download exhausted retries")
                    handled = True
                    continue

                mod = self.mod_by_key(key)
                if not mod:
                    self.mark_key_failed(key, "Paused download has no collection entry")
                    handled = True
                    continue

                removed = self.cleanup_failed_unfinished_before_retry(key)
                self.retry_attempts[key] = attempts + 1
                self.retry_count += 1
                mod_name = self.mod_label(mod)
                self.detail_label.setText(
                    f"Retrying paused {mod_name} ({attempts + 1}/{self.max_retries})..."
                )
                self.detail_label.setStyleSheet("color: orange;")
                qDebug(
                    "[NXMColDL Progress] Requeueing paused download "
                    f"ModID {key[0]}, FileID {key[1]} "
                    f"({attempts + 1}/{self.max_retries}); {removed} file(s) removed"
                )
                self.enqueue_retry_key(key)
                handled = True

        if handled:
            self.update_progress()
            self.finish_if_complete()
        return handled

    def on_download_removed(self, download_id):
        if not self.is_tracking:
            return

        key = self.pop_tracked_download_key(download_id, "removed")
        if key is None or key in self.completed_keys or key in self.failed_keys:
            return

        removed = self.cleanup_failed_unfinished_before_retry(key)
        attempts = self.retry_attempts.get(key, 0)
        mod = self.mod_by_key(key)
        if removed and attempts < self.max_retries and mod:
            self.retry_attempts[key] = attempts + 1
            self.retry_count += 1
            self.queued_keys.discard(key)
            self.queued_at.pop(key, None)
            self.detail_label.setText(
                f"Retrying removed download {self.mod_label(mod)} "
                f"({attempts + 1}/{self.max_retries})..."
            )
            self.detail_label.setStyleSheet("color: orange;")
            self.enqueue_retry_key(key)
            return

        if self.mark_key_failed(key, f"Download removed: ID {download_id}"):
            self.update_progress()
            self.finish_if_complete()

    def reconcile_completed_downloads(self):
        """Credit downloads that MO2 completed without emitting a tracked callback."""
        if not self.is_tracking:
            self.reconcile_timer.stop()
            return

        if self.reconcile_completed_downloads_from_disk():
            self.update_progress()
            self.finish_if_complete()

        if self.is_tracking:
            self.retry_stale_unfinished_downloads()
            self.retry_paused_downloads_stalled()

    def reconcile_completed_downloads_from_disk(self):
        """Credit all collection files that now have completed archives on disk."""
        completed_on_disk = downloadedFileKeys(
            downloadDirectory(),
            self.expected_file_sizes,
        )
        completed_on_disk.update(
            downloadedArchiveNameKeys(downloadDirectory(), self.mods_to_download)
        )
        newly_completed = (
            completed_on_disk & set(self.key_counts)
        ) - self.completed_keys
        for key in newly_completed:
            self.mark_key_completed(key, "Reconciled completed download from disk")
            self.clear_pending_state_for_key(key)

        pruned = self.prune_pending_queue_keys(completed_on_disk & set(self.key_counts))
        if pruned:
            qDebug(
                "[NXMColDL Progress] Pruned completed entries from download pump: "
                f"{pruned}"
            )

        return bool(newly_completed)

    def downgrade_stale_orphan_completed_downloads(self):
        """Reopen completed keys when MO2 has a stale orphan placeholder."""
        if not self.stale_unfinished_seconds:
            return False

        downloads_dir = downloadDirectory()
        completed_on_disk = downloadedFileKeys(downloads_dir, self.expected_file_sizes)
        orphan_entries = staleOrphanUnfinishedDownloadEntries(
            orphanUnfinishedDownloadEntries(downloads_dir),
            time.time(),
            self.stale_unfinished_seconds,
        )
        changed = False

        for entry in orphan_entries:
            candidates = [
                key
                for key in self.orphan_candidate_keys(
                    entry,
                    completed_on_disk=completed_on_disk,
                    include_completed=True,
                )
                if key in self.completed_keys
            ]
            if len(candidates) != 1:
                if entry.get("mod_id") is not None and len(candidates) > 1:
                    qDebug(
                        "[NXMColDL Progress] Orphan unfinished download has "
                        "ambiguous collection match; leaving it for MO2: "
                        f"{entry['archive'].name}"
                    )
                continue

            key = candidates[0]
            self.completed_keys.discard(key)
            self.clear_pending_state_for_key(key)
            changed = True
            qDebug(
                "[NXMColDL Progress] Reopened stalled orphan download "
                "previously marked complete: "
                f"ModID {key[0]}, FileID {key[1]}, file {entry['archive'].name}"
            )

        if changed:
            self.refresh_progress_counts()
        return changed

    def retry_stale_orphan_unfinished_downloads(self, pending_keys, now):
        """Requeue stale orphan placeholders that MO2 never gave metadata for."""
        downloads_dir = downloadDirectory()
        orphan_entries = orphanUnfinishedDownloadEntries(downloads_dir)

        for entry in orphan_entries:
            if entry.get("archive_size", 0) <= 0:
                continue

            if now - entry["mtime"] < self.stale_unfinished_seconds:
                continue

            candidates = [
                key for key in self.orphan_candidate_keys(entry) if key in pending_keys
            ]
            if len(candidates) != 1:
                if entry.get("mod_id") is not None and len(candidates) > 1:
                    qDebug(
                        "[NXMColDL Progress] Stale orphan unfinished download "
                        "has ambiguous collection match; leaving it for MO2: "
                        f"{entry['archive'].name}"
                    )
                continue

            key = candidates[0]
            attempts = self.retry_attempts.get(key, 0)
            if attempts >= self.max_retries:
                self.clear_pending_state_for_key(key)
                self.mark_key_failed(
                    key,
                    "Stale orphan unfinished download exhausted retries",
                )
                continue

            mod = self.mod_by_key(key)
            if not mod:
                continue

            removed = removeOrphanUnfinishedEntries([entry])
            if not removed:
                qDebug(
                    "[NXMColDL Progress] Skipping stale orphan retry because "
                    "cleanup removed no files for "
                    f"ModID {key[0]}, FileID {key[1]}"
                )
                continue

            self.retry_attempts[key] = attempts + 1
            self.retry_count += 1
            self.clear_pending_state_for_key(key)
            mod_name = self.mod_label(mod)
            self.detail_label.setText(
                f"Retrying stalled {mod_name} ({attempts + 1}/{self.max_retries})..."
            )
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Requeueing stale orphan unfinished "
                f"download for ModID {key[0]}, FileID {key[1]}; "
                f"{removed} file(s) removed"
            )
            QTimer.singleShot(
                self.retry_delay_ms,
                lambda keys={key}: self.queue_downloads(only_keys=keys),
            )

    def retry_stale_unfinished_downloads(self):
        """Requeue unfinished files that MO2 left idle without a callback."""
        if not self.stale_unfinished_seconds:
            return

        downloads_dir = downloadDirectory()
        pending_keys = set(self.key_counts) - self.completed_keys - self.failed_keys
        entries_by_key = unfinishedDownloadEntries(downloads_dir)
        now = time.time()
        handled_keys = set()
        retry_keys = set()
        tracked_download_keys = set(self.download_ids.values())
        ambiguous_recent_keys = self.recent_ambiguous_download_keys(now)
        validation_pending_keys = self.recent_completed_callback_keys(now)
        stalled_zero_keys = self.stalled_zero_byte_keys(
            entries_by_key,
            pending_keys - ambiguous_recent_keys - validation_pending_keys,
            now,
            self.zero_byte_start_timeout_seconds,
        )
        if stalled_zero_keys:
            qDebug(
                "[NXMColDL Progress] Found stale zero-byte unfinished downloads "
                f"eligible for retry: {len(stalled_zero_keys)}"
            )
            if len(stalled_zero_keys) >= self.zero_byte_restart_threshold:
                for key in sorted(stalled_zero_keys):
                    self.mark_key_restart_required(
                        key,
                        "Stale MO2 zero-byte queue batch needs MO2 restart/resume",
                    )
                    handled_keys.add(key)
                self.detail_label.setText(
                    f"{len(stalled_zero_keys)} stale queued download(s) need "
                    "MO2 restart/resume."
                )
                self.detail_label.setStyleSheet("color: orange;")
                self.update_progress()
                self.finish_if_complete()
                return

        for key in pending_keys:
            if key in ambiguous_recent_keys or key in validation_pending_keys:
                continue

            started_at = self.already_started_at.get(key)
            if (
                key in self.already_started_keys
                and not entries_by_key.get(key)
                and started_at
                and now - started_at >= self.queue_start_timeout_seconds
            ):
                if staleAlreadyStartedAction(False) == "restart_required":
                    self.mark_key_restart_required(
                        key,
                        "Already-started download needs MO2 restart to clear stale queue state",
                    )
                    continue

                continue

            entries = entries_by_key.get(key)
            stale_entries = None
            zero_start_retry = key in stalled_zero_keys
            if zeroByteUnfinishedEntries(entries) and not zero_start_retry:
                continue
            if key in tracked_download_keys and entries and not zero_start_retry:
                zero_entries = zeroByteUnfinishedEntries(entries)
                if zero_entries:
                    continue
            if zero_start_retry:
                stale_entries = zeroByteUnfinishedEntries(entries)
            if stale_entries is None:
                stale_entries = staleUnfinishedEntries(
                    entries, now, self.stale_unfinished_seconds
                )
            if not stale_entries:
                continue

            attempts = self.retry_attempts.get(key, 0)
            max_attempts = (
                max(self.max_retries, self.zero_byte_start_max_retries)
                if zero_start_retry
                else self.max_retries
            )
            if attempts >= max_attempts:
                mark_failed = (
                    self.mark_key_restart_required
                    if zero_start_retry
                    else self.mark_key_failed
                )
                mark_failed(
                    key,
                    (
                        "Zero-byte unfinished download needs MO2 restart"
                        if zero_start_retry
                        else "Stale unfinished download exhausted retries"
                    ),
                )
                continue

            mod = self.mod_by_key(key)
            if not mod:
                continue

            if zero_start_retry:
                self.retry_attempts[key] = attempts + 1
                self.retry_count += 1
                self.clear_pending_state_for_key(key)
                removed = removeUnfinishedEntries(stale_entries)
                mod_name = self.mod_label(mod)
                self.detail_label.setText(
                    f"Retrying zero-byte start {mod_name} "
                    f"({attempts + 1}/{max_attempts})..."
                )
                self.detail_label.setStyleSheet("color: orange;")
                qDebug(
                    "[NXMColDL Progress] Requeueing zero-byte unfinished download "
                    f"ModID {key[0]}, FileID {key[1]} "
                    f"({attempts + 1}/{max_attempts}); {removed} file(s) removed"
                )
                retry_keys.add(key)
                handled_keys.add(key)
                continue

            self.retry_attempts[key] = attempts + 1
            self.retry_count += 1
            self.clear_pending_state_for_key(key)
            removed = removeUnfinishedEntries(stale_entries)

            mod_name = self.mod_label(mod)
            self.detail_label.setText(
                f"Retrying stalled {mod_name} ({attempts + 1}/{max_attempts})..."
            )
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Requeueing stale unfinished download "
                f"ModID {key[0]}, FileID {key[1]} "
                f"({attempts + 1}/{max_attempts}); {removed} file(s) removed"
            )
            retry_keys.add(key)
            handled_keys.add(key)

        if retry_keys:
            qDebug(
                "[NXMColDL Progress] Queueing stale unfinished retry batch: "
                f"{len(retry_keys)} key(s)"
            )
            self.enqueue_retry_keys(retry_keys)

        if not handled_keys:
            self.retry_stale_orphan_unfinished_downloads(pending_keys, now)
        self.update_progress()
        self.finish_if_complete()

    def show_completion_choices(self, state):
        """Offer an explicit recovery or install decision when tracking ends."""
        self.is_tracking = False
        self.reconcile_timer.stop()
        self.duplicate_prompt_timer.stop()
        restart_required = bool(self.restart_required_keys)
        quota_limited = bool(self.quota_stop_message)
        choices = downloadCompletionChoices(
            state,
            self.on_complete is not None,
            restart_required=restart_required,
            quota_limited=quota_limited,
        )
        self.progress.setFormat(downloadProgressFormat(state))
        self.progress.setValue(state["progress"])
        self.label.setText(
            f"Downloading mods: {state['successful']}/{state['total']} completed"
        )
        if state["has_failures"]:
            if self.quota_stop_message:
                self.detail_label.setText(
                    f"{self.quota_stop_message} "
                    f"{state['successful']} completed; "
                    f"{state['failed']} waiting for resume."
                )
            elif self.restart_required_keys:
                report_suffix = (
                    f" Report: {self.resume_report_path}"
                    if self.resume_report_path
                    else ""
                )
                self.detail_label.setText(
                    f"{state['failed']} download(s) need an MO2 restart/resume; "
                    f"{state['successful']} completed. Install available files now, "
                    "or close MO2, relaunch, and rerun the collection to fetch "
                    "only missing files."
                    f"{report_suffix}"
                )
            else:
                self.detail_label.setText(
                    f"{state['failed']} download(s) failed; "
                    f"{state['successful']} completed. Retry failed downloads or "
                    "install the available files."
                )
            self.detail_label.setStyleSheet("color: orange;")
        else:
            self.detail_label.setText(
                "Downloads complete. Install collection now or close."
            )
            self.detail_label.setStyleSheet("color: green;")
        self.retry_failed_btn.setVisible(choices["retry_visible"])
        self.install_available_btn.setVisible(
            choices["install_visible"] and not self.quota_stop_message
        )
        self.install_available_btn.setText(choices["install_label"])
        self.fomod_defaults_check.setVisible(
            choices["fomod_defaults_visible"] and not self.quota_stop_message
        )
        self.auto_install_after_download_check.setVisible(
            choices["install_visible"]
            and not state["has_failures"]
            and not self.quota_stop_message
        )
        self.close_btn.setText("Close")
        self.close_btn.setVisible(True)

    def run_on_complete_from_user_choice(self):
        """Start installation after the user accepts the download result."""
        if self.completion_callback_started:
            return
        self.completion_callback_started = True
        var.autoAdvanceFomodDefaultsOverride = self.fomod_defaults_check.isChecked()
        self.accept()
        if self.on_complete:
            QTimer.singleShot(0, self.on_complete)

    def retry_failed_downloads(self):
        """Remove stale failed entries and try only the failed collection files."""
        if self.restart_required_keys:
            self.detail_label.setText(
                "MO2 restart/resume is required before retrying failed downloads."
            )
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Ignored in-process retry because "
                "restart-required keys are present"
            )
            return
        if self.quota_stop_message:
            self.detail_label.setText(
                f"{self.quota_stop_message} Relaunch/retry after the limit resets."
            )
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Ignored in-process retry because "
                "quota/rate limit is active"
            )
            return

        retry_keys = set(self.failed_keys) - set(self.completed_keys)
        if not retry_keys:
            return

        downloads_dir = downloadDirectory()
        entries_by_key = unfinishedDownloadEntries(downloads_dir)
        removed_files = 0
        for key in retry_keys:
            entries = entries_by_key.get(key)
            if entries:
                removed_files += removeUnfinishedEntries(entries)
        removed_files += removeOrphanUnfinishedDownloadsForKeys(
            downloads_dir, retry_keys
        )
        if removed_files:
            qDebug(
                "[NXMColDL Progress] Removed unfinished downloads before retrying "
                f"{len(retry_keys)} failed file(s); {removed_files} file(s) removed"
            )

        self.failed_keys.difference_update(retry_keys)
        for key in retry_keys:
            self.failed_key_reasons.pop(key, None)
        self.queued_keys.difference_update(retry_keys)
        self.waiting_partial_keys.difference_update(retry_keys)
        for key in retry_keys:
            self.queued_at.pop(key, None)
            self.already_started_keys.discard(key)
            self.already_started_at.pop(key, None)
            self.already_started_cleanup_attempts.discard(key)
            self.terminal_cleanup_attempts.discard(key)
            self.restart_required_keys.discard(key)
            self.restart_required_reasons.pop(key, None)
            self.retry_attempts[key] = 0

        self.terminal_failure_grace_attempts = 0
        self.completion_callback_started = False
        self.quota_stop_message = None
        var.lastQuotaLimitMessage = None
        self.refresh_progress_counts()
        self.retry_failed_btn.setVisible(False)
        self.install_available_btn.setVisible(False)
        self.fomod_defaults_check.setVisible(False)
        self.auto_install_after_download_check.setVisible(self.on_complete is not None)
        self.is_tracking = True
        self.detail_label.setText("Retrying failed downloads...")
        self.detail_label.setStyleSheet("color: orange;")
        self.reconcile_timer.start()
        if self.decline_duplicate_prompts:
            self.duplicate_prompt_timer.start()
        QTimer.singleShot(
            0, lambda keys=retry_keys: self.queue_downloads(only_keys=keys)
        )

    def finish_if_complete(self):
        if self.reconcile_completed_downloads_from_disk():
            self.update_progress()

        state = self.refresh_progress_counts()
        if not state["is_terminal"]:
            return

        if self.quota_stop_message:
            self.show_completion_choices(state)
            return

        if state["has_failures"] and self.restart_required_keys:
            self.write_resume_boundary_report()
            self.show_completion_choices(state)
            return

        if self.downgrade_stale_orphan_completed_downloads():
            self.update_progress()
            self.retry_stale_unfinished_downloads()
            return

        if state["has_failures"] and self.dismiss_duplicate_download_prompt():
            return

        if (
            state["has_failures"]
            and self.recover_failed_partial_orphans_before_terminal()
        ):
            return

        if state["has_failures"] and self.retry_failed_leftovers_before_terminal():
            return

        state = self.refresh_progress_counts()
        if not state["is_terminal"]:
            return

        if shouldDelayTerminalDownloadFailure(
            state["has_failures"],
            self.terminal_failure_grace_attempts,
            self.terminal_failure_grace_max_attempts,
        ):
            self.terminal_failure_grace_attempts += 1
            self.detail_label.setText(
                "Waiting for late MO2 download prompts to settle..."
            )
            self.detail_label.setStyleSheet("color: orange;")
            qDebug(
                "[NXMColDL Progress] Delaying terminal download failure "
                "for late MO2 prompt cleanup "
                f"({self.terminal_failure_grace_attempts}/"
                f"{self.terminal_failure_grace_max_attempts})"
            )
            QTimer.singleShot(
                self.terminal_failure_grace_delay_ms,
                self.finish_if_complete,
            )
            return

        readiness = self.final_download_readiness()
        if not readiness["ready"]:
            if (
                not state["has_failures"]
                and self.final_readiness_attempts < self.final_readiness_max_attempts
            ):
                self.final_readiness_attempts += 1
                self.detail_label.setText(
                    "Finalizing downloads before install: "
                    + "; ".join(readiness["blockers"][:3])
                )
                self.detail_label.setStyleSheet("color: orange;")
                qDebug(
                    "[NXMColDL Progress] Waiting for final download readiness "
                    f"({self.final_readiness_attempts}/"
                    f"{self.final_readiness_max_attempts}): "
                    + "; ".join(readiness["blockers"])
                )
                QTimer.singleShot(
                    self.final_readiness_delay_ms,
                    self.finish_if_complete,
                )
                return

            qDebug(
                "[NXMColDL Progress] Final download readiness failed: "
                + "; ".join(readiness["blockers"])
            )
            missing_keys = readiness.get("missing_keys", set())
            self.failed_keys.update(missing_keys)
            for key in missing_keys:
                self.failed_key_reasons[key] = (
                    "Final download readiness failed: "
                    + "; ".join(readiness["blockers"])
                )
            state = self.refresh_progress_counts()
            self.show_completion_choices(state)
            return

        qDebug(
            "[NXMColDL Progress] Download tracking complete. "
            f"{state['successful']} successful, {state['failed']} failed."
        )
        self.is_tracking = False
        self.reconcile_timer.stop()
        self.duplicate_prompt_timer.stop()
        should_prompt_for_install = (
            self.on_complete
            and self.prompt_after_download
            and not self.auto_install_after_download_check.isChecked()
        )
        if state["has_failures"] or should_prompt_for_install:
            self.show_completion_choices(state)
            return

        if self.on_complete:
            var.autoAdvanceFomodDefaultsOverride = self.fomod_defaults_check.isChecked()
        else:
            var.autoAdvanceFomodDefaultsOverride = None
        plan = downloadCompletionPlan(
            state["failed"],
            self.on_complete is not None,
            self.close_on_success,
            self.success_close_delay_ms,
        )
        if plan["close_immediately"]:
            self.accept()
        if plan["run_complete"]:
            self.completion_callback_started = True
            QTimer.singleShot(0, self.on_complete)
        if plan["close_delay_ms"]:
            QTimer.singleShot(plan["close_delay_ms"], self.accept)

    def final_download_readiness(self):
        """Return whether the completed collection is safe to hand to install."""
        downloads_dir = downloadDirectory()
        expected_keys = set(self.key_counts.keys())
        blockers = []
        removed_files = 0

        entries_by_key = unfinishedDownloadEntries(downloads_dir)
        for key in expected_keys:
            entries = entries_by_key.get(key)
            if entries:
                removed_files += removeUnfinishedEntries(entries)
        removed_files += removeOrphanUnfinishedDownloadsForKeys(
            downloads_dir, expected_keys, include_nonzero=True
        )
        if removed_files:
            qDebug(
                "[NXMColDL Progress] Final cleanup removed unfinished leftovers: "
                f"{removed_files} file(s)"
            )

        completed_on_disk = downloadedFileKeys(
            downloads_dir,
            self.expected_file_sizes,
        )
        completed_on_disk.update(
            downloadedArchiveNameKeys(downloads_dir, self.mods_to_download)
        )
        missing_keys = expected_keys - completed_on_disk
        if missing_keys:
            blockers.append(f"{len(missing_keys)} expected archive(s) missing on disk")

        remaining_entries = unfinishedDownloadEntries(downloads_dir)
        remaining_unfinished_keys = set(remaining_entries.keys()) & expected_keys
        if remaining_unfinished_keys:
            blockers.append(
                f"{len(remaining_unfinished_keys)} unfinished download(s) remain"
            )

        orphan_mod_ids = {
            entry.get("mod_id")
            for entry in orphanUnfinishedDownloadEntries(downloads_dir)
        }
        expected_mod_ids = {int(key[0]) for key in expected_keys}
        remaining_orphan_count = len(orphan_mod_ids & expected_mod_ids)
        if remaining_orphan_count:
            blockers.append(
                f"{remaining_orphan_count} orphan unfinished download(s) remain"
            )

        if self.dismiss_duplicate_download_prompt():
            blockers.append("late duplicate download prompt dismissed")

        if not blockers:
            self.completed_keys.update(completed_on_disk & expected_keys)
            self.failed_keys.difference_update(expected_keys)
            for key in expected_keys:
                self.failed_key_reasons.pop(key, None)
            self.restart_required_keys.difference_update(expected_keys)
            self.restart_required_reasons = {
                key: value
                for key, value in self.restart_required_reasons.items()
                if key not in expected_keys
            }
            self.final_readiness_attempts = 0
            qDebug(
                "[NXMColDL Progress] Final download readiness verified: "
                f"{len(expected_keys)} archive(s) ready for install"
            )

        return {
            "ready": not blockers,
            "blockers": blockers,
            "missing_keys": missing_keys,
        }

    def write_resume_boundary_report(self):
        """Persist missing/restart-required download state for restart diagnosis."""
        if self.resume_report_path:
            return self.resume_report_path

        downloads_dir = downloadDirectory()
        log_dir = downloads_dir.parent / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        completed_on_disk = downloadedFileKeys(
            downloads_dir,
            self.expected_file_sizes,
        )
        completed_on_disk.update(
            downloadedArchiveNameKeys(downloads_dir, self.mods_to_download)
        )
        expected_keys = set(self.key_counts)
        missing_keys = expected_keys - completed_on_disk
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = (
            log_dir
            / f"nxm-collection-download-resume-{var.collection}-{var.revision}-{timestamp}.json"
        )

        def key_record(key):
            mod = self.mod_by_key(key) or {}
            file_info = mod.get("file", {}) if isinstance(mod, dict) else {}
            mod_info = file_info.get("mod", {}) if isinstance(file_info, dict) else {}
            return {
                "mod_id": key[0],
                "file_id": key[1],
                "mod": mod_info.get("name"),
                "file": file_info.get("name"),
                "reason": (
                    self.restart_required_reasons.get(key)
                    or self.failed_key_reasons.get(key)
                    or "Download did not reach a completed archive"
                ),
            }

        report = {
            "collection": var.collection,
            "revision": var.revision,
            "name": var.name,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "total": self.total_mods,
            "completed": len(expected_keys & completed_on_disk),
            "missing": len(missing_keys),
            "restart_required": [
                key_record(key) for key in sorted(self.restart_required_keys)
            ],
            "missing_entries": [key_record(key) for key in sorted(missing_keys)],
        }
        try:
            with open(report_path, "w", encoding="utf-8") as report_file:
                json.dump(report, report_file, indent=2)
        except OSError:
            return None

        self.resume_report_path = report_path
        qDebug(f"[NXMColDL Progress] Download resume report: {report_path}")
        return report_path

    def update_progress(self):
        """Update the progress display."""
        state = self.refresh_progress_counts()
        self.progress.setFormat(downloadProgressFormat(state))
        self.progress.setValue(state["progress"])
        self.label.setText(
            f"Downloading mods: {state['successful']}/{state['total']} completed"
        )

        if state["has_failures"]:
            self.detail_label.setText(
                f"{state['failed']} download(s) failed; {state['remaining']} remaining"
            )
            self.detail_label.setStyleSheet("color: orange;")
        elif state["is_terminal"]:
            if self.retry_count:
                self.detail_label.setText(
                    f"All downloads completed after {self.retry_count} retry attempt(s)!"
                )
            else:
                self.detail_label.setText("All downloads completed!")
            self.detail_label.setStyleSheet("color: green;")
        else:
            self.detail_label.setText(f"Downloading... {state['remaining']} remaining")
            self.detail_label.setStyleSheet("")


class stepDownload(QDialog):
    def __init__(self, parent=None, on_complete=None):
        super().__init__(parent)
        self.setWindowTitle("NXM Collection Downloader - Downloading...")
        self.setMinimumWidth(150)
        self.on_complete = on_complete

        self.layout = QVBoxLayout()
        self.label = QLabel("Preparing to download selected mods...")

        self.mod_urls_to_open = []
        self.current_batch = 0
        self.batch_btn = None
        self.progress_dialog = None

        plugin_instance = getattr(__meta__, "_download_plugin", None)
        if plugin_instance is not None:
            try:
                base_path = Path(plugin_instance._organizer.basePath())
                metadata_file = var.saveCollectionMetadata(base_path)
                qDebug(f"[NXMColDL] Collection metadata saved to: {metadata_file}")
            except (ValueError, IOError) as e:
                qDebug(f"[NXMColDL] Failed to save collection metadata: {e}")
                QMessageBox.warning(
                    self,
                    "Warning",
                    f"Failed to save collection metadata:\n\n{e}\n\n"
                    "You will not be able to install this collection automatically later.",
                )

            # Always open external resources if user chose to
            if var.chosenExternal and var.externalMods:
                for mod in var.externalMods:
                    QDesktopServices.openUrl(QUrl(mod["resourceUrl"]))

            if var.openModWebsites:
                # User chose to open websites instead of downloading
                qDebug(
                    "[NXMColDL] stepDownload: Opening mod websites mode (user is non-Premium or chose this option)"
                )
                for mod in var.essentialMods + var.chosenOptional:
                    mod_id = mod["file"]["mod"]["modId"]
                    mod_url = f"https://www.nexusmods.com/{var.game}/mods/{mod_id}"
                    self.mod_urls_to_open.append((mod["file"]["mod"]["name"], mod_url))
                self.label.setText(
                    f"Ready to open {len(self.mod_urls_to_open)} mod website(s) in batches."
                )
            else:
                # Queue downloads and show progress tracker
                qDebug(
                    "[NXMColDL] stepDownload: Queueing downloads via MO2 download manager"
                )
                mods_to_download = var.essentialMods + var.chosenOptional
                qDebug(f"[NXMColDL] Queueing {len(mods_to_download)} downloads")
                max_retries = coerceIntSetting(
                    plugin_instance._organizer.pluginSetting(
                        plugin_instance.name(), "download_retry_count"
                    ),
                    default=0,
                    minimum=0,
                )
                success_close_delay_ms = 1000 * coerceIntSetting(
                    plugin_instance._organizer.pluginSetting(
                        plugin_instance.name(), "download_success_close_delay_seconds"
                    ),
                    default=0,
                    minimum=0,
                )
                stale_unfinished_seconds = coerceIntSetting(
                    plugin_instance._organizer.pluginSetting(
                        plugin_instance.name(), "stale_unfinished_retry_seconds"
                    ),
                    default=600,
                    minimum=0,
                )
                if 0 < stale_unfinished_seconds < 30:
                    stale_unfinished_seconds = 30
                decline_duplicate_prompts = coerceBoolSetting(
                    plugin_instance._organizer.pluginSetting(
                        plugin_instance.name(),
                        "auto_decline_duplicate_download_prompts",
                    )
                )
                self.progress_dialog = stepDownloadProgress(
                    self.parent(),
                    mods_to_download,
                    on_complete=self.on_complete,
                    max_retries=max_retries,
                    stale_unfinished_seconds=stale_unfinished_seconds,
                    close_on_success=self.on_complete is not None,
                    success_close_delay_ms=success_close_delay_ms,
                    decline_duplicate_prompts=decline_duplicate_prompts,
                    prompt_after_download=self.on_complete is not None,
                    fomod_defaults_default=installerFomodDefaultSetting(),
                    auto_install_after_download_default=linkAutoInstallDefaultSetting(),
                )

                self.label.setText(f"Queued {len(mods_to_download)} downloads in MO2.")
        else:
            qDebug(
                "[NXMColDL] downloadMod called without active plugin instance; Aborting"
            )
            self.close()
            return

        self.layout.addWidget(self.label)

        # Add button for opening mod websites in batches if needed
        if self.mod_urls_to_open:
            self.batch_btn = QPushButton()
            self.batch_btn.clicked.connect(self.open_next_batch)
            self.layout.addWidget(self.batch_btn)
            self.update_batch_button()

        self.submit_btn = QPushButton("Finish")
        self.submit_btn.clicked.connect(self.submit)
        self.layout.addWidget(self.submit_btn)
        self.setLayout(self.layout)

        if self.progress_dialog is not None:
            QTimer.singleShot(0, self.show_progress_dialog)

    def show_progress_dialog(self):
        """Run the progress dialog after this step's modal event loop starts."""
        self.hide()
        self.progress_dialog.exec()
        self.accept()

    def update_batch_button(self):
        plugin_instance = getattr(__meta__, "_download_plugin", None)
        if plugin_instance and hasattr(plugin_instance, "_organizer"):
            batch_size = coerceIntSetting(
                plugin_instance._organizer.pluginSetting(
                    plugin_instance.name(), "modpage_batch_size"
                ),
                default=5,
                minimum=1,
            )
        else:
            batch_size = 5
        remaining = len(self.mod_urls_to_open) - (self.current_batch * batch_size)
        if remaining > 0:
            next_batch_count = min(batch_size, remaining)
            self.batch_btn.setText(
                f"Open Next {next_batch_count} Mod Website(s) ({remaining} remaining)"
            )
            self.batch_btn.setEnabled(True)
        else:
            self.batch_btn.setText("All mod websites opened")
            self.batch_btn.setEnabled(False)

    def open_next_batch(self):
        plugin_instance = getattr(__meta__, "_download_plugin", None)
        if plugin_instance and hasattr(plugin_instance, "_organizer"):
            batch_size = coerceIntSetting(
                plugin_instance._organizer.pluginSetting(
                    plugin_instance.name(), "modpage_batch_size"
                ),
                default=5,
                minimum=1,
            )
        else:
            batch_size = 5
        start_idx = self.current_batch * batch_size
        end_idx = min(start_idx + batch_size, len(self.mod_urls_to_open))

        if start_idx >= len(self.mod_urls_to_open):
            return  # All done

        for i in range(start_idx, end_idx):
            mod_name, mod_url = self.mod_urls_to_open[i]
            QDesktopServices.openUrl(QUrl(mod_url))
            qDebug(f"[NXMColDL] Opening mod website: {mod_url}")

        self.current_batch += 1
        self.update_batch_button()

    def submit(self):
        self.close()


class stepCollectionLinkFlow(QDialog):
    """Fetch, download, and optionally install a collection from a direct link."""

    def __init__(self, collection_url, parent=None, auto_install=True):
        super().__init__(parent)
        self.collection_url = collection_url
        self.auto_install = auto_install
        self.metadata = None
        self.setWindowTitle("NXM Collection Downloader")
        self.setMinimumWidth(420)

        layout = QVBoxLayout()
        self.label = QLabel("Preparing collection download...")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        self.setLayout(layout)
        QTimer.singleShot(0, self.start)

    def write_failure_report(self, message, stage):
        plugin_instance = getattr(__meta__, "_download_plugin", None)
        organizer = getattr(plugin_instance, "_organizer", None)
        if not organizer:
            return None

        try:
            log_dir = Path(organizer.basePath()) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            report_path = (
                log_dir
                / (
                    "nxm-collection-download-failure-"
                    f"{var.collection}-{var.revision}-{timestamp}.json"
                )
            )
            report = collectionFlowFailureReport(
                var.collection,
                var.revision,
                var.name,
                self.collection_url,
                message,
                stage,
            )
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return report_path
        except OSError as e:
            qDebug(
                "[NXMColDL] Failed to write direct collection failure report: "
                f"{e}"
            )
            return None

    def fail(self, message, stage="unknown"):
        report_path = self.write_failure_report(message, stage)
        qDebug(
            "[NXMColDL] Direct collection flow failed: "
            f"stage={stage}, message={message}, report={report_path}"
        )
        display_message = message
        if report_path:
            display_message = f"{message}\n\nFailure report:\n{report_path}"
        self.label.setText(display_message)
        QMessageBox.critical(self, "Collection Download Failed", display_message)
        self.reject()

    def start(self):
        if not applyCollectionAddress(self.collection_url):
            self.fail("The Nexus collection link was not recognized.", "parse_link")
            return
        qDebug(
            "[NXMColDL] Direct collection flow started: "
            f"game={var.game}, collection={var.collection}, revision={var.revision}"
        )

        if not populateCollectionInfo():
            self.fail(
                "Failed to fetch collection information from Nexus Mods.",
                "fetch_collection_info",
            )
            return

        if var.revision is None:
            var.revision = selectLatestRevision()
            if var.revision is None:
                self.fail(
                    "Failed to determine the latest collection revision.",
                    "select_revision",
                )
                return
            qDebug(
                f"[NXMColDL] Direct collection selected latest revision: {var.revision}"
            )

        self.label.setText(
            f"Fetching collection manifest for {safeDisplayText(var.name)}..."
        )
        mods = fetchModInfo(var.uri)
        if mods is None:
            self.fail(
                "Failed to fetch collection mod information from Nexus Mods.",
                "fetch_mod_info",
            )
            return

        populateCollectionMods(mods)
        var.chosenOptional = list(var.optionalMods)
        qDebug(
            "[NXMColDL] Direct collection manifest loaded: "
            f"essential={len(var.essentialMods)}, optional={len(var.optionalMods)}, "
            f"external={len(var.externalMods)}, bundled={len(var.bundledMods)}"
        )

        plugin_instance = getattr(__meta__, "_download_plugin", None)
        if not plugin_instance or not getattr(plugin_instance, "_organizer", None):
            self.fail("Failed to access Mod Organizer.", "access_organizer")
            return

        try:
            base_path = Path(plugin_instance._organizer.basePath())
            metadata_file = var.saveCollectionMetadata(base_path)
            self.metadata = recordCollectionLinkLaunch(metadata_file)
            launch_count = self.metadata.get("addCollectionLaunchCount", 1)
            recovery_count = self.metadata.get("addCollectionRecoveryCount", 0)
            qDebug(
                "[NXMColDL] Add Collection launch recorded: "
                f"collection={var.collection}, revision={var.revision}, "
                f"launch_count={launch_count}, recovery_count={recovery_count}, "
                f"mode={self.metadata.get('addCollectionLaunchMode')}"
            )
            qDebug(f"[NXMColDL] Collection metadata saved to: {metadata_file}")
        except (ValueError, IOError) as e:
            self.fail(f"Failed to save collection metadata:\n\n{e}", "save_metadata")
            return

        if var.chosenExternal and var.externalMods:
            for mod in var.externalMods:
                QDesktopServices.openUrl(QUrl(mod["resourceUrl"]))

        if var.bundledMods:
            qDebug(
                "[NXMColDL] Direct collection flow found unsupported bundled "
                f"resources: {len(var.bundledMods)}"
            )

        mods_to_download = var.essentialMods + var.chosenOptional
        self.recover_local_collection_state(plugin_instance, mods_to_download)
        max_retries = coerceIntSetting(
            plugin_instance._organizer.pluginSetting(
                plugin_instance.name(), "download_retry_count"
            ),
            default=0,
            minimum=0,
        )
        success_close_delay_ms = 1000 * coerceIntSetting(
            plugin_instance._organizer.pluginSetting(
                plugin_instance.name(), "download_success_close_delay_seconds"
            ),
            default=0,
            minimum=0,
        )
        stale_unfinished_seconds = coerceIntSetting(
            plugin_instance._organizer.pluginSetting(
                plugin_instance.name(), "stale_unfinished_retry_seconds"
            ),
            default=600,
            minimum=0,
        )
        if 0 < stale_unfinished_seconds < 30:
            stale_unfinished_seconds = 30
        decline_duplicate_prompts = coerceBoolSetting(
            plugin_instance._organizer.pluginSetting(
                plugin_instance.name(), "auto_decline_duplicate_download_prompts"
            )
        )
        completion_policy = collectionLinkCompletionPolicy()
        qDebug(
            "[NXMColDL] Direct collection download policy: "
            f"mods={len(mods_to_download)}, max_retries={max_retries}, "
            f"stale_unfinished_seconds={stale_unfinished_seconds}, "
            f"decline_duplicate_prompts={decline_duplicate_prompts}, "
            f"completion_policy={completion_policy}"
        )
        self.progress_dialog = stepDownloadProgress(
            self.parent(),
            mods_to_download,
            on_complete=(
                self.install_after_download
                if completion_policy["attach_install_callback"]
                else None
            ),
            max_retries=max_retries,
            stale_unfinished_seconds=stale_unfinished_seconds,
            close_on_success=completion_policy["close_on_success"],
            success_close_delay_ms=success_close_delay_ms,
            decline_duplicate_prompts=decline_duplicate_prompts,
            prompt_after_download=completion_policy["prompt_after_download"],
            fomod_defaults_default=installerFomodDefaultSetting(),
            auto_install_after_download_default=coerceBoolSetting(
                self.auto_install,
                completion_policy["auto_install_after_download_default"],
            ),
        )
        self.hide()
        self.progress_dialog.exec()
        self.accept()

    def recover_local_collection_state(self, plugin_instance, mods_to_download):
        """Repair local MO2 state before replaying Add Collection.

        This makes Add Collection idempotent for interrupted or older runs:
        exact Nexus files already installed locally get their download metadata
        marked installed, and their MO2 mod containers are enabled. Missing
        files remain missing so the normal download/install flow can resume.
        """
        organizer = plugin_instance._organizer
        expected_keys = collectionExpectedNexusKeys(mods_to_download)
        if not expected_keys:
            return

        downloads_path = Path(organizer.downloadsPath())
        mods_path = Path(organizer.modsPath())
        expected_file_names = collectionExpectedFileNames(mods_to_download)
        installed_records = installedModRecordsFromDirectory(
            mods_path,
            downloads_path,
            expected_file_names=expected_file_names,
        )
        recovery = collectionRecoveryTargets(
            installed_records, expected_keys, mods_dir=mods_path
        )
        installed_keys = recovery["installed_keys"]
        mod_names = recovery["mod_names"]
        if not installed_keys and not mod_names:
            qDebug(
                "[NXMColDL] Add Collection local recovery: "
                f"0 installed, {len(recovery['missing_keys'])} missing"
            )
            return

        metadata_repair = repairDownloadMetadataInstalledFlags(
            downloads_path,
            installed_keys,
            desired_installed=True,
            backup_dir=(
                downloads_path.parent
                / "logs"
                / "nxm-collection-repair-backups"
                / datetime.now().strftime("%Y%m%d-%H%M%S")
                / "download-metadata"
            ),
        )

        enabled = 0
        enable_failed = 0
        modlist = organizer.modList()
        for mod_name in mod_names:
            try:
                modlist.setActive(mod_name, True)
                enabled += 1
            except Exception as e:
                enable_failed += 1
                qDebug(
                    "[NXMColDL] Add Collection local recovery activation failed: "
                    f"{mod_name}: {e}"
                )

        qDebug(
            "[NXMColDL] Add Collection local recovery: "
            f"installed_keys={len(installed_keys)}, "
            f"download_metadata_repaired={metadata_repair.get('repaired', 0)}, "
            f"download_metadata_failed={metadata_repair.get('failed', 0)}, "
            f"mods_enabled={enabled}, enable_failed={enable_failed}, "
            f"missing_keys={len(recovery['missing_keys'])}"
        )
        if metadata_repair.get("repaired") or enabled:
            self.label.setText(
                "Recovered local collection state; preparing remaining downloads..."
            )

    def install_after_download(self):
        if not self.metadata:
            qDebug("[NXMColDL] Install handoff skipped: no collection metadata")
            return
        from .install import installCollectionMetadata

        qDebug(
            "[NXMColDL] Install handoff starting: "
            f"collection={self.metadata.get('collection')}, "
            f"revision={self.metadata.get('revision')}, "
            f"totalMods={self.metadata.get('totalMods')}"
        )
        __meta__.releaseActiveCollectionLinkFlow(self)
        installCollectionMetadata(
            self.metadata,
            self.parent(),
            auto_close_on_success=True,
        )

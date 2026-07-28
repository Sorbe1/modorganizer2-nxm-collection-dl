import json
import mobase  # type: ignore
import time
from PyQt6.QtCore import Qt, QTimer, QUrl, qDebug
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QGroupBox,
    QLineEdit,
    QMainWindow,
    QPushButton,
)
from . import var
from .collection_helpers import (
    FOMOD_ADVANCE_EXCLUDED_TITLES,
    INSTALLER_SETTING_DEFAULTS,
    coerceBoolSetting,
    collectionLinkCompletionPolicy,
    installerDefaultActionLabel,
    normalizedButtonLabel,
    parseCollectionAddress,
    safeDisplayText,
)
from .download import stepCollectionLinkFlow, stepURL
from .install import stepInstallMods, stepSelectCollection
from pathlib import Path

PLUGIN_VERSION = mobase.VersionInfo(1, 0, 0, mobase.ReleaseType.ALPHA)
_active_collection_link_flow = None
_active_install_probe = False
_install_probe_generation = 0
_pending_install_probe_queue = []
_completed_install_probe_names = []
INSTALL_PROBE_CLICK_DELAY_MS = 50
INSTALL_PROBE_IDLE_DELAY_MS = 250


def activeCollectionLinkFlowIsVisible():
    global _active_collection_link_flow

    if not _active_collection_link_flow:
        return False
    try:
        return _active_collection_link_flow.isVisible()
    except RuntimeError:
        _active_collection_link_flow = None
        return False


def raiseActiveCollectionLinkFlow():
    if not activeCollectionLinkFlowIsVisible():
        return False
    try:
        _active_collection_link_flow.raise_()
        _active_collection_link_flow.activateWindow()
    except RuntimeError:
        return False
    return True


def releaseActiveCollectionLinkFlow(flow):
    global _active_collection_link_flow

    if _active_collection_link_flow is flow:
        _active_collection_link_flow = None


def configurePluginDebugLog(organizer: mobase.IOrganizer):
    try:
        log_path = (
            Path(organizer.downloadsPath()).parent
            / "logs"
            / "nxm-collection-dl-debug.log"
        )
        var.setDebugLogPath(log_path)
        var.debug(f"[NXMColDL] Plugin debug log path: {log_path}")
    except Exception as e:
        qDebug(f"[NXMColDL] Failed to configure plugin debug log: {e}")


def icon(icon_name: str) -> QIcon:
    return QIcon(str(Path(__file__).parent / "icons" / icon_name))


def pendingLinkFiles(organizer: mobase.IOrganizer):
    """Return rendezvous paths for external nxm:// collection handlers.

    The MO2 data directory is the preferred path. The plugin directory is a
    fallback because it maps cleanly as both /home/... on Linux and Z:/home/...
    inside Wine/Proton.
    """
    return [
        Path(organizer.basePath()) / "collections" / "pending-nxm-link.txt",
        Path(__file__).parent / "pending-nxm-link.txt",
    ]


def pendingInstallProbeFiles(organizer: mobase.IOrganizer):
    """Return rendezvous paths for one-shot installMod isolation probes."""
    return [
        Path(organizer.basePath()) / "collections" / "pending-install-probe.txt",
        Path(__file__).parent / "pending-install-probe.txt",
    ]


def queueInstallProbeBatch(entries):
    """Queue archive installs to run from MO2's normal event-loop watcher."""
    queued = 0
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command") or "").strip()
        if command:
            queued_entry = dict(entry)
            queued_entry["command"] = command
            _pending_install_probe_queue.append(queued_entry)
            queued += 1
            continue
        archive_path = str(entry.get("archive") or "").strip()
        if not archive_path:
            continue
        _pending_install_probe_queue.append(
            {
                "archive": archive_path,
                "target": (
                    str(entry.get("target")).strip()
                    if entry.get("target") is not None
                    else None
                ),
                "observe": bool(entry.get("observe", False)),
            }
        )
        queued += 1
    var.debug(f"[NXMColDL Probe] Queued batch install probes: {queued}")
    return queued


def summarizeVisibleDialogs():
    summaries = []
    try:
        widgets = QApplication.topLevelWidgets()
    except Exception as e:
        return [f"<topLevelWidgets failed: {e}>"]

    for widget in widgets:
        try:
            if not widget.isVisible():
                continue
            title = widget.windowTitle() or "<untitled>"
            class_name = widget.metaObject().className()
            buttons = []
            edits = []
            groups = []

            for button in widget.findChildren(QAbstractButton):
                label = button.text().strip().replace("&", "").lower()
                if label:
                    buttons.append(f"{label}:{'on' if button.isEnabled() else 'off'}")

            for line_edit in widget.findChildren(QLineEdit):
                text = line_edit.text().strip()
                if text:
                    edits.append(text[:80])

            for group in widget.findChildren(QGroupBox):
                label = group.title().strip()
                if label:
                    groups.append(label[:80])

            summaries.append(
                f"{title}<{class_name}> buttons={buttons[:8]} "
                f"edits={edits[:4]} groups={groups[:5]}"
            )
        except RuntimeError:
            continue
        except Exception as e:
            summaries.append(f"<dialog summary failed: {e}>")

    return summaries[:8]


def runInstallProbeFinalize(organizer: mobase.IOrganizer, payload):
    global _completed_install_probe_names
    mod_names = []
    for name in payload.get("activate_mods", []) if isinstance(payload, dict) else []:
        name = str(name).strip()
        if name:
            mod_names.append(name)
    if isinstance(payload, dict) and payload.get("activate_completed", False):
        mod_names.extend(_completed_install_probe_names)

    priority_order = []
    for name in payload.get("priority_order", []) if isinstance(payload, dict) else []:
        name = str(name).strip()
        if name:
            priority_order.append(name)
    if isinstance(payload, dict) and payload.get("priority_completed", False):
        priority_order.extend(_completed_install_probe_names)

    activate_plugins = bool(payload.get("activate_plugins", True))
    var.debug(
        "[NXMColDL Probe] finalize start: "
        f"activate_mods={len(mod_names)}, priority_order={len(priority_order)}, "
        f"activate_plugins={activate_plugins}"
    )

    try:
        modlist = organizer.modList()
    except Exception as e:
        var.debug(f"[NXMColDL Probe] finalize failed: modList unavailable: {e}")
        return

    activated = 0
    activation_failed = 0
    for name in dict.fromkeys(mod_names):
        try:
            modlist.setActive(name, True)
            activated += 1
        except Exception as e:
            activation_failed += 1
            var.debug(f"[NXMColDL Probe] finalize mod activation failed: {name}: {e}")

    priority_moved = 0
    priority_failed = 0
    ordered_mods = [name for name in dict.fromkeys(priority_order) if name]
    if ordered_mods:
        try:
            current_priorities = []
            present_mods = []
            for name in ordered_mods:
                try:
                    current_priorities.append(modlist.priority(name))
                    present_mods.append(name)
                except Exception:
                    priority_failed += 1
            if present_mods:
                target_priority = min(current_priorities)
                for name in reversed(present_mods):
                    try:
                        if modlist.setPriority(name, target_priority):
                            priority_moved += 1
                    except Exception as e:
                        priority_failed += 1
                        var.debug(
                            f"[NXMColDL Probe] finalize priority failed: {name}: {e}"
                        )
        except Exception as e:
            var.debug(f"[NXMColDL Probe] finalize priority repair failed: {e}")

    plugin_activated = 0
    plugin_already_active = 0
    plugin_blocked = 0
    if activate_plugins and mod_names:
        try:
            organizer.refresh(True)
        except Exception as e:
            var.debug(f"[NXMColDL Probe] finalize refresh failed: {e}")
        try:
            plugin_list = organizer.pluginList()
            mod_name_set = set(mod_names)
            for plugin_name in plugin_list.pluginNames():
                try:
                    if plugin_list.origin(plugin_name) not in mod_name_set:
                        continue
                    if plugin_list.state(plugin_name) == mobase.PluginState.ACTIVE:
                        plugin_already_active += 1
                        continue
                    plugin_list.setState(plugin_name, mobase.PluginState.ACTIVE)
                    if plugin_list.state(plugin_name) == mobase.PluginState.ACTIVE:
                        plugin_activated += 1
                    else:
                        plugin_blocked += 1
                except Exception as e:
                    plugin_blocked += 1
                    var.debug(
                        "[NXMColDL Probe] finalize plugin activation failed: "
                        f"{plugin_name}: {e}"
                    )
        except Exception as e:
            var.debug(f"[NXMColDL Probe] finalize failed: pluginList unavailable: {e}")

    var.debug(
        "[NXMColDL Probe] finalize done: "
        f"mods_activated={activated}, mod_failures={activation_failed}, "
        f"priority_moved={priority_moved}, priority_failures={priority_failed}, "
        f"plugins_activated={plugin_activated}, "
        f"plugins_already_active={plugin_already_active}, "
        f"plugins_blocked={plugin_blocked}"
    )
    if payload.get("clear_completed", True):
        _completed_install_probe_names = []


def runHeadlessArchiveProbe(organizer: mobase.IOrganizer, payload):
    global _completed_install_probe_names

    archive_path = str(payload.get("archive") or "").strip()
    target_name = str(payload.get("target") or "").strip()
    file_name = str(payload.get("file_name") or Path(archive_path).name).strip()
    try:
        mod_id = int(payload.get("mod_id"))
        file_id = int(payload.get("file_id"))
    except Exception:
        var.debug(
            "[NXMColDL Probe] headless archive probe failed: "
            "mod_id and file_id are required"
        )
        return
    if not archive_path or not target_name:
        var.debug(
            "[NXMColDL Probe] headless archive probe failed: "
            "archive and target are required"
        )
        return

    start = time.monotonic()
    dialog = stepInstallMods(auto_start=False)
    dialog.install_context = {"organizer": organizer}
    try:
        layout = dialog.headlessArchiveLayoutPlan(
            Path(archive_path), organizer=organizer
        )
        if not layout.get("installable"):
            var.debug(
                "[NXMColDL Probe] headless archive probe rejected: "
                f"archive={archive_path}, target={target_name}, layout={layout}"
            )
            return
        installed_name = dialog.directHeadlessArchiveInstall(
            organizer,
            Path(archive_path),
            target_name,
            (mod_id, file_id),
            file_name,
            layout,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _completed_install_probe_names.append(installed_name)
        var.debug(
            "[NXMColDL Probe] headless archive probe installed: "
            f"elapsed_ms={elapsed_ms}, archive={archive_path}, "
            f"target={target_name}, layout={layout}"
        )
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        var.debug(
            "[NXMColDL Probe] headless archive probe exception: "
            f"elapsed_ms={elapsed_ms}, archive={archive_path}, "
            f"target={target_name}, error={e}"
        )
    finally:
        dialog.deleteLater()


def advanceInstallProbeDefaults(generation, remaining_steps=80, advanced=0):
    if not _active_install_probe or generation != _install_probe_generation:
        return
    if advanced >= remaining_steps:
        var.debug(
            "[NXMColDL Probe] default automation stopped: "
            f"advanced={advanced}, visible={summarizeVisibleDialogs()}"
        )
        return

    for widget in QApplication.topLevelWidgets():
        try:
            if (
                not widget.isVisible()
                or widget.windowTitle() in FOMOD_ADVANCE_EXCLUDED_TITLES
            ):
                continue
            buttons = widget.findChildren(QPushButton)
            action = installerDefaultActionLabel(
                widget.windowTitle(),
                ((button.text(), button.isEnabled()) for button in buttons),
            )
            if action is None:
                continue
            for button in buttons:
                if normalizedButtonLabel(button.text()) != action:
                    continue
                if not button.isEnabled():
                    continue
                title = safeDisplayText(widget.windowTitle())
                var.debug(
                    f"[NXMColDL Probe] default automation click: {action} on {title}"
                )
                button.click()
                QApplication.processEvents()
                QTimer.singleShot(
                    INSTALL_PROBE_CLICK_DELAY_MS,
                    lambda: advanceInstallProbeDefaults(
                        generation, remaining_steps, advanced + 1
                    ),
                )
                return
        except RuntimeError:
            continue
        except Exception as e:
            var.debug(f"[NXMColDL Probe] default automation error: {e}")
            continue

    QTimer.singleShot(
        INSTALL_PROBE_IDLE_DELAY_MS,
        lambda: advanceInstallProbeDefaults(generation, remaining_steps, advanced),
    )


def consumePendingInstallProbe(organizer: mobase.IOrganizer):
    global _active_install_probe, _install_probe_generation
    if _active_install_probe:
        return

    archive_path = None
    target_name = None
    consumed_from = None
    observe = True
    if _pending_install_probe_queue:
        probe_payload = _pending_install_probe_queue.pop(0)
        command = str(probe_payload.get("command") or "").strip()
        if command == "finalize-probe-installs":
            runInstallProbeFinalize(organizer, probe_payload)
            QTimer.singleShot(250, lambda: consumePendingInstallProbe(organizer))
            return
        if command == "headless-archive-probe":
            runHeadlessArchiveProbe(organizer, probe_payload)
            QTimer.singleShot(250, lambda: consumePendingInstallProbe(organizer))
            return
        archive_path = probe_payload.get("archive")
        target_name = probe_payload.get("target")
        observe = bool(probe_payload.get("observe", False))
        consumed_from = "<internal-batch>"
    else:
        for pending_file in pendingInstallProbeFiles(organizer):
            try:
                if not pending_file.exists():
                    continue
                probe_text = pending_file.read_text(encoding="utf-8").strip()
                pending_file.unlink()
                consumed_from = pending_file
                try:
                    probe_payload = json.loads(probe_text)
                except Exception:
                    archive_path = probe_text
                else:
                    if isinstance(probe_payload, list):
                        pending_entries = probe_payload
                    elif isinstance(probe_payload, dict) and isinstance(
                        probe_payload.get("entries"), list
                    ):
                        pending_entries = probe_payload.get("entries")
                    else:
                        pending_entries = None

                    if pending_entries is not None:
                        queued = queueInstallProbeBatch(pending_entries)
                        var.debug(
                            "[NXMColDL Probe] Consumed pending install probe batch: "
                            f"file={consumed_from}, queued={queued}"
                        )
                        QTimer.singleShot(
                            250, lambda: consumePendingInstallProbe(organizer)
                        )
                        return

                    if isinstance(probe_payload, dict):
                        command = str(probe_payload.get("command") or "").strip()
                        if command == "finalize-probe-installs":
                            runInstallProbeFinalize(organizer, probe_payload)
                            QTimer.singleShot(
                                250, lambda: consumePendingInstallProbe(organizer)
                            )
                            return
                        if command == "headless-archive-probe":
                            runHeadlessArchiveProbe(organizer, probe_payload)
                            QTimer.singleShot(
                                250, lambda: consumePendingInstallProbe(organizer)
                            )
                            return
                        archive_path = str(probe_payload.get("archive") or "").strip()
                        target_name = probe_payload.get("target")
                        if target_name is not None:
                            target_name = str(target_name).strip() or None
                        observe = bool(probe_payload.get("observe", True))
                    else:
                        archive_path = probe_text
                break
            except OSError as e:
                var.debug(f"[NXMColDL Probe] Failed to consume install probe: {e}")
                return

    if not archive_path:
        return

    _active_install_probe = True
    _install_probe_generation += 1
    generation = _install_probe_generation
    var.debug(
        "[NXMColDL Probe] Consumed pending install probe: "
        f"file={consumed_from}, archive={archive_path}, target={target_name}"
    )
    QTimer.singleShot(250, lambda: advanceInstallProbeDefaults(generation))
    QTimer.singleShot(
        0,
        lambda: runInstallProbe(organizer, archive_path, target_name, observe),
    )


def runInstallProbe(
    organizer: mobase.IOrganizer, archive_path: str, target_name=None, observe=True
):
    global _active_install_probe
    start = time.monotonic()
    try:
        var.debug(
            "[NXMColDL Probe] installMod start: "
            f"archive={archive_path}, target={target_name}, "
            f"visible_before={summarizeVisibleDialogs()}"
        )
        if target_name:
            result = organizer.installMod(archive_path, target_name)
        else:
            result = organizer.installMod(archive_path)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result_name = None
        if result is not None:
            try:
                result_name = result.name()
            except Exception as e:
                result_name = f"<name failed: {e}>"
        if result_name:
            _completed_install_probe_names.append(result_name)
        var.debug(
            "[NXMColDL Probe] installMod return: "
            f"elapsed_ms={elapsed_ms}, result_type={type(result).__name__ if result is not None else None}, "
            f"result_name={result_name}, visible_after={summarizeVisibleDialogs()}"
        )
        if observe:
            logProbeDialogs(30)
        else:
            _active_install_probe = False
            QTimer.singleShot(250, lambda: consumePendingInstallProbe(organizer))
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        var.debug(
            "[NXMColDL Probe] installMod exception: "
            f"elapsed_ms={elapsed_ms}, error={e}, visible_after={summarizeVisibleDialogs()}"
        )
        _active_install_probe = False
        QTimer.singleShot(250, lambda: consumePendingInstallProbe(organizer))


def logProbeDialogs(remaining_seconds):
    global _active_install_probe
    var.debug(
        "[NXMColDL Probe] visible dialogs: "
        f"remaining_seconds={remaining_seconds}, dialogs={summarizeVisibleDialogs()}"
    )
    if remaining_seconds <= 0:
        _active_install_probe = False
        var.debug("[NXMColDL Probe] observation complete")
        return
    QTimer.singleShot(1000, lambda: logProbeDialogs(remaining_seconds - 1))


def consumePendingCollectionLink(organizer: mobase.IOrganizer, parent):
    global _active_collection_link_flow
    if activeCollectionLinkFlowIsVisible():
        raiseActiveCollectionLinkFlow()
        return

    url = None
    for pending_file in pendingLinkFiles(organizer):
        try:
            if not pending_file.exists():
                continue
            url = pending_file.read_text(encoding="utf-8").strip()
            pending_file.unlink()
            break
        except OSError as e:
            qDebug(f"[NXMColDL] Failed to consume pending collection link: {e}")
            return

    if not url:
        return

    if not parseCollectionAddress(url):
        qDebug(f"[NXMColDL] Ignoring invalid pending collection link: {url}")
        return

    qDebug(f"[NXMColDL] Consuming pending collection link: {url}")
    flow = stepCollectionLinkFlow(
        url,
        parent,
        auto_install=coerceBoolSetting(
            organizer.pluginSetting(
                "NXM Collection Link Handler", "auto_install_after_download"
            ),
            collectionLinkCompletionPolicy()["auto_install_after_download_default"],
        ),
    )
    _active_collection_link_flow = flow
    flow.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    flow.destroyed.connect(lambda *_args: releaseActiveCollectionLinkFlow(flow))
    flow.finished.connect(lambda *_args: releaseActiveCollectionLinkFlow(flow))
    flow.show()
    flow.raise_()
    flow.activateWindow()


def startPendingLinkWatcher(plugin):
    if getattr(plugin, "_pending_link_timer", None):
        return

    def pollPendingFiles():
        try:
            if any(
                path.exists() for path in pendingInstallProbeFiles(plugin._organizer)
            ):
                var.debug("[NXMColDL Probe] Pending install probe file detected")
        except Exception as e:
            var.debug(f"[NXMColDL Probe] Pending probe check failed: {e}")
        consumePendingInstallProbe(plugin._organizer)
        consumePendingCollectionLink(plugin._organizer, plugin._parent)

    plugin._pending_link_timer = QTimer()
    plugin._pending_link_timer.timeout.connect(pollPendingFiles)
    plugin._pending_link_timer.start(1000)
    QTimer.singleShot(0, pollPendingFiles)
    QTimer.singleShot(2500, pollPendingFiles)
    paths = ", ".join(str(path) for path in pendingLinkFiles(plugin._organizer))
    probe_paths = ", ".join(
        str(path) for path in pendingInstallProbeFiles(plugin._organizer)
    )
    qDebug(f"[NXMColDL] Watching for collection links at {paths}")
    var.debug(f"[NXMColDL Probe] Watching for install probes at {probe_paths}")


class DownloadCollectionTool(mobase.IPluginTool):
    _organizer: mobase.IOrganizer

    def __init__(self):
        super().__init__()
        self._organizer = None
        self._parent = None
        self._pending_link_timer = None
        self._pending_link_timer = None

    def init(self, organizer: mobase.IOrganizer):
        self._organizer = organizer
        configurePluginDebugLog(organizer)
        qDebug("[NXMColDL] Initializing Download Collection plugin")
        try:
            import sys as _sys

            _sys.modules[__name__]._download_plugin = self
        except Exception:
            pass
        self._organizer.onUserInterfaceInitialized(
            self.onUserInterfaceInitializedCallback
        )
        return True

    def name(self) -> str:
        return "NXM Collections Downloader"

    def displayName(self):
        return "NXM Collections/Download Collection"

    def author(self) -> str:
        return "Furglitch"

    def description(self) -> str:
        return "Allows downloading NXM collections directly in MO2"

    def version(self) -> mobase.VersionInfo:
        return PLUGIN_VERSION

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.name(), "enabled")

    def icon(self):
        return icon("download.ico")

    def tooltip(self):
        return "Download a Nexus Mods collection"

    def setParentWidget(self, widget):
        self._parent = widget

    def display(self) -> None:
        dlg = getattr(self, "_stepURL", None) or stepURL()
        dlg.exec()

    def settings(self):
        return [
            mobase.PluginSetting("enabled", "Enable", True),
            mobase.PluginSetting(
                "modpage_browser_default",
                "Open mod download sites in browser by default (set True if non-Premium user)",
                False,
            ),
            mobase.PluginSetting(
                "modpage_batch_size", "Number of mod websites to open at once", 5
            ),
            mobase.PluginSetting(
                "externalmods_browser_default",
                "Open external mod URLs in browser by default",
                True,
            ),
            mobase.PluginSetting(
                "download_retry_count",
                "Retry failed collection downloads this many times",
                2,
            ),
            mobase.PluginSetting(
                "download_success_close_delay_seconds",
                "Close successful download progress dialogs after this many seconds (0 disables)",
                5,
            ),
            mobase.PluginSetting(
                "stale_unfinished_retry_seconds",
                "Retry stale unfinished downloads after this many idle seconds (0 disables)",
                600,
            ),
            mobase.PluginSetting(
                "auto_decline_duplicate_download_prompts",
                "Automatically decline duplicate archive prompts during collection downloads",
                True,
            ),
        ]

    def onUserInterfaceInitializedCallback(self, main_window: "QMainWindow"):
        self._parent = main_window
        startPendingLinkWatcher(self)
        self._stepURL = stepURL(main_window)
        startPendingLinkWatcher(self)

    def downloadMod(self, modInfo: dict):
        modID = int(modInfo["file"]["mod"]["modId"])
        fileID = int(modInfo["file"]["fileId"])
        qDebug(f"[NXMColDL] Downloading mod {modID} file {fileID}")
        return self._organizer.downloadManager().startDownloadNexusFile(modID, fileID)


class InstallCollectionTool(mobase.IPluginTool):
    _organizer: mobase.IOrganizer

    def __init__(self):
        super().__init__()
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer):
        self._organizer = organizer
        configurePluginDebugLog(organizer)
        qDebug("[NXMColDL] Initializing Install Collection plugin")
        try:
            import sys

            sys.modules[__name__]._install_plugin = self
        except Exception:
            pass
        self._organizer.onUserInterfaceInitialized(
            self.onUserInterfaceInitializedCallback
        )
        return True

    def name(self) -> str:
        return "NXM Collections Installer"

    def displayName(self):
        return "NXM Collections/Install Downloaded Collection"

    def author(self) -> str:
        return "Furglitch"

    def description(self) -> str:
        return self.tooltip()

    def version(self) -> mobase.VersionInfo:
        return PLUGIN_VERSION

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.name(), "enabled")

    def icon(self):
        return icon("install.ico")

    def tooltip(self):
        return "Installs mods from an already downloaded Nexus Mods collection"

    def setParentWidget(self, widget):
        self._parent = widget

    def display(self) -> None:
        dlg = getattr(self, "_stepSelectCollection", None) or stepSelectCollection()
        dlg.exec()

    def settings(self):
        return [
            mobase.PluginSetting("enabled", "Enable", True),
            mobase.PluginSetting(
                "auto_accept_quick_install",
                "Automatically accept MO2 Quick Install dialogs",
                INSTALLER_SETTING_DEFAULTS["auto_accept_quick_install"],
            ),
            mobase.PluginSetting(
                "auto_dismiss_known_post_install_errors",
                "Automatically dismiss known MO2 post-install error dialogs",
                INSTALLER_SETTING_DEFAULTS["auto_dismiss_known_post_install_errors"],
            ),
            mobase.PluginSetting(
                "auto_cancel_invalid_install_content",
                "Automatically accept invalid-content install warnings",
                INSTALLER_SETTING_DEFAULTS["auto_cancel_invalid_install_content"],
            ),
            mobase.PluginSetting(
                "headless_archive_installs",
                "Install safe non-FOMOD archives without opening MO2 installer dialogs",
                INSTALLER_SETTING_DEFAULTS["headless_archive_installs"],
            ),
            mobase.PluginSetting(
                "auto_merge_existing_mods",
                "Automatically merge duplicate MO2 mod names instead of keeping # suffixes",
                INSTALLER_SETTING_DEFAULTS["auto_merge_existing_mods"],
            ),
            mobase.PluginSetting(
                "auto_advance_fomod_defaults",
                "Automatically accept default FOMOD installer choices",
                INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_defaults"],
            ),
            mobase.PluginSetting(
                "auto_advance_fomod_max_steps",
                "Maximum FOMOD default choices to accept for one archive",
                INSTALLER_SETTING_DEFAULTS["auto_advance_fomod_max_steps"],
            ),
            mobase.PluginSetting(
                "install_files_as_separate_mods",
                "Install each collection file as a separate named MO2 mod",
                INSTALLER_SETTING_DEFAULTS["install_files_as_separate_mods"],
            ),
            mobase.PluginSetting(
                "activate_mods_after_install",
                "Activate installed mods after the collection install pass completes",
                INSTALLER_SETTING_DEFAULTS["activate_mods_after_install"],
            ),
            mobase.PluginSetting(
                "activate_mods_during_install",
                "Activate each installed mod before processing the next archive",
                INSTALLER_SETTING_DEFAULTS["activate_mods_during_install"],
            ),
            mobase.PluginSetting(
                "trace_install_diagnostics",
                "Write expensive per-archive install diagnostics",
                INSTALLER_SETTING_DEFAULTS["trace_install_diagnostics"],
            ),
        ]

    def onUserInterfaceInitializedCallback(self, main_window: "QMainWindow"):
        self._stepSelectCollection = stepSelectCollection(main_window)


class CollectionModPage(mobase.IPluginModPage):
    _organizer: mobase.IOrganizer

    def __init__(self):
        super().__init__()
        self._organizer = None
        self._parent = None

    def init(self, organizer: mobase.IOrganizer):
        self._organizer = organizer
        configurePluginDebugLog(organizer)
        qDebug("[NXMColDL] Initializing Nexus collection link handler")
        self._organizer.onUserInterfaceInitialized(
            self.onUserInterfaceInitializedCallback
        )
        return True

    def name(self) -> str:
        return "NXM Collection Link Handler"

    def displayName(self):
        return "Nexus Mods Collections"

    def author(self) -> str:
        return "Furglitch"

    def description(self) -> str:
        return "Handles Nexus Mods Add Collection links"

    def version(self) -> mobase.VersionInfo:
        return PLUGIN_VERSION

    def isActive(self) -> bool:
        return self._organizer.pluginSetting(self.name(), "enabled")

    def icon(self):
        return icon("download.ico")

    def pageURL(self):
        return QUrl("https://www.nexusmods.com")

    def useIntegratedBrowser(self):
        return False

    def setParentWidget(self, widget):
        self._parent = widget

    def onUserInterfaceInitializedCallback(self, main_window: "QMainWindow"):
        self._parent = main_window

    def settings(self):
        return [
            mobase.PluginSetting("enabled", "Enable", True),
            mobase.PluginSetting(
                "auto_install_after_download",
                "Automatically install a collection after Add Collection downloads finish",
                True,
            ),
        ]

    def handlesDownload(self, page_url, download_url, fileinfo):
        url = next(
            (
                candidate
                for candidate in (download_url.toString(), page_url.toString())
                if parseCollectionAddress(candidate)
            ),
            None,
        )
        if not url:
            return False

        qDebug(f"[NXMColDL] Handling collection link from Nexus: {url}")
        auto_install = coerceBoolSetting(
            self._organizer.pluginSetting(self.name(), "auto_install_after_download"),
            collectionLinkCompletionPolicy()["auto_install_after_download_default"],
        )
        QTimer.singleShot(0, lambda: self.startCollectionFlow(url, auto_install))
        return True

    def startCollectionFlow(self, url, auto_install):
        global _active_collection_link_flow
        if activeCollectionLinkFlowIsVisible():
            qDebug("[NXMColDL] Collection link ignored; another flow is already active")
            raiseActiveCollectionLinkFlow()
            return

        flow = stepCollectionLinkFlow(url, self._parent, auto_install=auto_install)
        _active_collection_link_flow = flow
        flow.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        flow.destroyed.connect(lambda *_args: releaseActiveCollectionLinkFlow(flow))
        flow.finished.connect(lambda *_args: releaseActiveCollectionLinkFlow(flow))
        flow.show()
        flow.raise_()
        flow.activateWindow()

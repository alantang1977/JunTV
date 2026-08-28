"""Shared launcher for URLs opened from the desktop application."""

import os
import shlex
import shutil
import sys

from PySide6.QtCore import QProcess, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSizePolicy, QVBoxLayout
from qfluentwidgets import CheckBox, ComboBox, FluentIcon, PushButton

from desktop_ui.widgets import apply_dialog_theme, localize_dialog_buttons
from utils.i18n import t


MODE_BROWSER = "browser"
MODE_EXTERNAL = "external"
MODE_ASK = "ask"
_MODES = {MODE_BROWSER, MODE_EXTERNAL, MODE_ASK}


def discover_players() -> list[tuple[str, str]]:
    """Return installed, known media players without probing arbitrary files."""
    if sys.platform == "darwin":
        app_roots = ("/Applications", os.path.expanduser("~/Applications"))
        candidates = [
            (name, os.path.join(root, bundle, "Contents", "MacOS", binary))
            for root in app_roots
            for name, bundle, binary in (
                ("VLC", "VLC.app", "VLC"),
                ("IINA", "IINA.app", "IINA"),
                ("mpv", "mpv.app", "mpv"),
            )
        ]
    elif sys.platform == "win32":
        roots = tuple(filter(None, (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        )))
        candidates = [
            (name, os.path.join(root, *parts))
            for root in roots
            for name, parts in (
                ("VLC", ("VideoLAN", "VLC", "vlc.exe")),
                ("mpv", ("mpv", "mpv.exe")),
                ("PotPlayer", ("DAUM", "PotPlayer", "PotPlayerMini64.exe")),
            )
        ]
    else:
        candidates = [
            (name, path)
            for name in ("VLC", "mpv", "SMPlayer", "Celluloid", "Totem")
            if (path := shutil.which(name.lower()))
        ]

    players = []
    seen = set()
    for name, path in candidates:
        normalized = os.path.normcase(os.path.realpath(path))
        if normalized in seen or not os.path.isfile(path):
            continue
        seen.add(normalized)
        players.append((name, path))
    return players


def playback_mode() -> str:
    mode = str(QSettings().value("playback/mode", MODE_BROWSER))
    return mode if mode in _MODES else MODE_BROWSER


def external_player() -> tuple[str, str]:
    settings = QSettings()
    return (
        str(settings.value("playback/executable", "")).strip(),
        str(settings.value("playback/arguments", "{url}")).strip(),
    )


def build_player_command(executable: str, arguments: str, url: str) -> tuple[str, list[str]]:
    """Build a detached-player command without passing user values to a shell."""
    args = shlex.split(arguments, posix=os.name != "nt") if arguments.strip() else []
    has_url = any("{url}" in arg for arg in args)
    args = [arg.replace("{url}", url) for arg in args]
    if not has_url:
        args.append(url)
    return executable, args


def _open_external(url: str, parent=None) -> bool:
    executable, arguments = external_player()
    if not executable:
        QMessageBox.warning(parent, t("name.error"), t("desktop.player_not_configured"))
        return False
    program, args = build_player_command(executable, arguments, url)
    if QProcess.startDetached(program, args):
        return True
    QMessageBox.warning(
        parent,
        t("name.error"),
        t("desktop.player_start_failed").format(player=program),
    )
    return False


def play_url(url: str, parent=None) -> bool:
    """Open a media URL according to the saved GUI playback preference."""
    if not url:
        return False
    mode = playback_mode()
    if mode == MODE_ASK:
        choice = QDialog(parent)
        apply_dialog_theme(choice)
        choice.setWindowTitle(t("desktop.choose_playback_method"))
        choice.setMinimumWidth(420)
        layout = QVBoxLayout(choice)
        layout.addWidget(QLabel(t("desktop.choose_playback_method_hint"), choice))
        buttons = QHBoxLayout()
        cancel = PushButton(t("desktop.cancel"), choice)
        browser = PushButton(t("desktop.play_in_browser"), choice)
        player = PushButton(t("desktop.play_in_external_player"), choice)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(browser)
        buttons.addWidget(player)
        layout.addLayout(buttons)
        selected = {"mode": None}
        cancel.clicked.connect(choice.reject)
        browser.clicked.connect(lambda: (selected.update(mode="browser"), choice.accept()))
        player.clicked.connect(lambda: (selected.update(mode="external"), choice.accept()))
        if choice.exec() != QDialog.DialogCode.Accepted:
            return False
        if selected["mode"] == "external":
            return _open_external(url, parent)
    if mode == MODE_EXTERNAL:
        return _open_external(url, parent)
    return QDesktopServices.openUrl(QUrl(url))


class PlaybackPreferencesDialog(QDialog):
    """Small, immediate-effect UI for preferences not belonging to service config."""

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_dialog_theme(self)
        self.setWindowTitle(t("desktop.playback_preferences"))
        self.setMinimumWidth(560)
        settings = QSettings()

        self.mode = ComboBox(self)
        self._mode_values = (MODE_BROWSER, MODE_EXTERNAL, MODE_ASK)
        self.mode.addItems([
            t("desktop.play_in_browser"),
            t("desktop.play_in_external_player"),
            t("desktop.ask_playback_method"),
        ])
        current = playback_mode()
        self.mode.setCurrentIndex(self._mode_values.index(current))

        self.executable = QLineEdit(str(settings.value("playback/executable", "")), self)
        self.executable.setPlaceholderText(t("desktop.player_executable_placeholder"))
        self.executable.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.player_picker = ComboBox(self)
        self._player_choices = []
        self._reload_player_choices()
        self.player_picker.currentIndexChanged.connect(self._choose_detected_player)
        self.refresh_players = PushButton(FluentIcon.SYNC, t("desktop.scan_players"), self)
        self.refresh_players.clicked.connect(self._reload_player_choices)
        self.browse_player = PushButton(FluentIcon.FOLDER, t("desktop.select_file"), self)
        self.browse_player.clicked.connect(self._browse_executable)
        self.player_actions = QHBoxLayout()
        self.player_actions.setContentsMargins(0, 0, 0, 0)
        self.player_actions.addWidget(self.player_picker, 1)
        self.player_actions.addWidget(self.refresh_players)
        self.player_actions.addWidget(self.browse_player)

        arguments_value = str(settings.value("playback/arguments", "{url}"))
        self.advanced_toggle = CheckBox(t("desktop.advanced_playback_options"), self)
        show_advanced = settings.value("playback/show_advanced", None)
        self.advanced_toggle.setChecked(
            bool(show_advanced) if show_advanced is not None else arguments_value.strip() != "{url}"
        )
        self.arguments = QLineEdit(arguments_value, self)
        self.arguments.setPlaceholderText("{url}")
        self.hint = QLabel(t("desktop.player_arguments_hint"), self)
        self.hint.setWordWrap(True)

        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.addRow(t("desktop.playback_default"), self.mode)
        self.form.addRow(t("desktop.player_selection"), self.player_actions)
        self.form.addRow(t("desktop.player_executable"), self.executable)
        self.form.addRow("", self.advanced_toggle)
        self.form.addRow(t("desktop.player_arguments"), self.arguments)
        self.arguments_label = self.form.labelForField(self.arguments)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        localize_dialog_buttons(self.buttons)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addWidget(self.hint)
        layout.addWidget(self.buttons)
        self.mode.currentIndexChanged.connect(self._update_enabled)
        self.advanced_toggle.toggled.connect(self._update_advanced_visibility)
        self._update_enabled()

    def retranslate(self):
        """Refresh every user-visible string after an application language change."""
        selected_mode = self._selected_mode()
        self.setWindowTitle(t("desktop.playback_preferences"))
        self.mode.clear()
        self.mode.addItems([
            t("desktop.play_in_browser"),
            t("desktop.play_in_external_player"),
            t("desktop.ask_playback_method"),
        ])
        self.mode.setCurrentIndex(self._mode_values.index(selected_mode))
        self.executable.setPlaceholderText(t("desktop.player_executable_placeholder"))
        self.refresh_players.setText(t("desktop.scan_players"))
        self.browse_player.setText(t("desktop.select_file"))
        self.advanced_toggle.setText(t("desktop.advanced_playback_options"))
        self.hint.setText(t("desktop.player_arguments_hint"))
        self.form.labelForField(self.mode).setText(t("desktop.playback_default"))
        self.form.labelForField(self.player_actions).setText(t("desktop.player_selection"))
        self.form.labelForField(self.executable).setText(t("desktop.player_executable"))
        self.arguments_label.setText(t("desktop.player_arguments"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(t("desktop.save"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("desktop.cancel"))
        self._reload_player_choices()
        self._update_enabled()

    def _browse_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, t("desktop.select_player_executable"))
        if path:
            self.executable.setText(path)

    def _reload_player_choices(self):
        selected = self.executable.text().strip()
        self._player_choices = [(t("desktop.manual_player"), ""), *discover_players()]
        self.player_picker.clear()
        self.player_picker.addItems([name for name, _path in self._player_choices])
        index = next(
            (i for i, (_name, path) in enumerate(self._player_choices) if path == selected),
            0,
        )
        self.player_picker.setCurrentIndex(index)

    def _choose_detected_player(self, index):
        if 0 <= index < len(self._player_choices):
            path = self._player_choices[index][1]
            if path:
                self.executable.setText(path)

    def _update_enabled(self):
        enabled = self._selected_mode() == MODE_EXTERNAL
        self.executable.setEnabled(enabled)
        self.player_picker.setEnabled(enabled)
        self.refresh_players.setEnabled(enabled)
        self.browse_player.setEnabled(enabled)
        self.advanced_toggle.setEnabled(enabled)
        self._update_advanced_visibility()

    def _update_advanced_visibility(self):
        visible = self._selected_mode() == MODE_EXTERNAL and self.advanced_toggle.isChecked()
        self.arguments_label.setVisible(visible)
        self.arguments.setVisible(visible)
        self.arguments.setEnabled(visible)
        self.hint.setVisible(visible)

    def _save(self):
        mode = self._selected_mode()
        executable = self.executable.text().strip()
        if mode == MODE_EXTERNAL and not executable:
            QMessageBox.warning(self, t("name.error"), t("desktop.player_not_configured"))
            return
        settings = QSettings()
        settings.setValue("playback/mode", mode)
        settings.setValue("playback/executable", executable)
        settings.setValue("playback/arguments", self.arguments.text().strip() or "{url}")
        settings.setValue("playback/show_advanced", self.advanced_toggle.isChecked())
        settings.sync()
        self.accept()

    def _selected_mode(self) -> str:
        return self._mode_values[self.mode.currentIndex()]

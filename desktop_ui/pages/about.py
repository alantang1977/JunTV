import os

from PySide6.QtCore import QSettings, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, FluentIcon, HyperlinkButton, InfoBar, InfoBarPosition, ProgressBar, PushButton, StrongBodyLabel, SubtitleLabel, SwitchButton, isDarkTheme, qconfig

from desktop_ui.update_manager import REPOSITORY_URL, UpdateManager
from desktop_ui.update_installer import UpdateInstallError, launch_update
from desktop_ui.changelog_dialog import ChangelogDialog
from desktop_ui.widgets import AccentPushButton, apply_dialog_theme, localize_dialog_buttons
from utils.config import resource_path
from utils.i18n import t
from utils.tools import get_version_info


class AboutPage(QWidget):
    status_changed = Signal(str, object)
    update_notification_requested = Signal(dict)
    AUTO_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1_000
    AUTO_CHECK_SETTING = "updates/auto_check_enabled"
    READ_UPDATE_SETTING = "updates/read_version"
    IGNORED_UPDATE_SETTING = "updates/ignored_version"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("aboutPage")
        info = get_version_info()
        self.release_url = REPOSITORY_URL + "/releases/latest"
        self.asset_url = ""
        self.asset_name = ""
        self.asset_sha256 = ""
        self.downloaded_path = ""
        self.info = info
        self._update_state = "not_checked"
        self._update_result = None
        self._automatic_check = False
        self.manager = UpdateManager(
            str(info.get("version") or "0"),
            self,
            current_revision=info.get("build_revision") or 0,
        )

        logo = QLabel(self)
        logo.setFixedSize(88, 88)
        logo.setPixmap(QPixmap(resource_path("favicon.ico")).scaled(88, 88))
        identity = QVBoxLayout()
        identity.addWidget(SubtitleLabel(str(info.get("name") or "IPTV-API"), self))
        self.version_label = BodyLabel(t("desktop.version_value").format(version=info.get("version") or "--"), self)
        self.author_label = BodyLabel(t("desktop.author_value").format(author="Guovin"), self)
        identity.addWidget(self.version_label)
        identity.addWidget(self.author_label)
        identity.addStretch(1)
        hero = QHBoxLayout()
        hero.addWidget(logo)
        hero.addSpacing(16)
        hero.addLayout(identity, 1)

        self.version_card = CardWidget(self)
        card_layout = QVBoxLayout(self.version_card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(10)
        self.update_notice = QFrame(self.version_card)
        self.update_notice.setObjectName("updateNotice")
        notice_layout = QHBoxLayout(self.update_notice)
        notice_layout.setContentsMargins(12, 10, 12, 10)
        notice_layout.setSpacing(10)
        self.update_icon = QLabel(self.update_notice)
        self.update_icon.setFixedSize(32, 32)
        notice_layout.addWidget(self.update_icon)
        notice_text = QVBoxLayout()
        notice_text.setSpacing(2)
        self.version_status = StrongBodyLabel(t("desktop.update_not_checked"), self.version_card)
        self.version_detail = BodyLabel(t("desktop.update_check_desc"), self.version_card)
        notice_text.addWidget(self.version_status)
        notice_text.addWidget(self.version_detail)
        notice_layout.addLayout(notice_text, 1)
        self.update_badge = StrongBodyLabel(t("desktop.update_available_badge"), self.update_notice)
        self.update_badge.setObjectName("updateBadge")
        notice_layout.addWidget(self.update_badge)
        card_layout.addWidget(self.update_notice)
        self.progress = ProgressBar(self.version_card)
        self.progress.hide()
        auto_check_text = t("desktop.auto_check_updates")
        self.auto_check_switch = SwitchButton(auto_check_text, self.version_card)
        self.auto_check_switch.setOnText(auto_check_text)
        self.auto_check_switch.setOffText(auto_check_text)
        self.auto_check_switch.setChecked(self._auto_check_enabled())
        controls = QHBoxLayout()
        self.check_button = PushButton(FluentIcon.SYNC, t("desktop.check_updates"), self.version_card)
        self.download_button = AccentPushButton(FluentIcon.DOWNLOAD, t("desktop.download_update"), self.version_card)
        self.download_button.hide()
        self.install_button = AccentPushButton(FluentIcon.SYNC, t("desktop.install_update"), self.version_card)
        self.install_button.hide()
        controls.addWidget(self.auto_check_switch)
        controls.addStretch(1)
        controls.addWidget(self.check_button)
        actions = QHBoxLayout()
        actions.addWidget(self.download_button)
        actions.addWidget(self.install_button)
        actions.addStretch(1)
        self.release_button = HyperlinkButton(FluentIcon.GLOBE, self.release_url, t("desktop.open_release"), self.version_card)
        card_layout.addWidget(self.progress)
        card_layout.addLayout(controls)
        card_layout.addLayout(actions)

        self.repository_button = HyperlinkButton(FluentIcon.GITHUB, REPOSITORY_URL, t("desktop.github_repository"), self)
        self.author_button = HyperlinkButton(FluentIcon.PEOPLE, "https://github.com/Guovin", t("desktop.author_homepage"), self)
        self.changelog_button = PushButton(FluentIcon.DOCUMENT, t("desktop.view_changelog"), self.version_card)
        self.changelog_button.clicked.connect(self._show_changelog)
        secondary_actions = QHBoxLayout()
        secondary_actions.addWidget(self.changelog_button)
        secondary_actions.addWidget(self.release_button)
        secondary_actions.addStretch(1)
        card_layout.addLayout(secondary_actions)

        links = QHBoxLayout()
        links.addWidget(self.repository_button)
        links.addWidget(self.author_button)
        links.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(hero)
        layout.addWidget(self.version_card)
        layout.addLayout(links)
        layout.addStretch(1)

        self.check_button.clicked.connect(lambda: self.check_for_updates(automatic=False))
        self.download_button.clicked.connect(self._download)
        self.install_button.clicked.connect(self._install)
        self.manager.check_started.connect(self._checking)
        self.manager.check_finished.connect(self._checked)
        self.manager.check_failed.connect(self._check_failed)
        self.manager.failed.connect(self._failed)
        self.manager.download_progress.connect(self._download_progress)
        self.manager.download_finished.connect(self._download_finished)
        self.auto_check_timer = QTimer(self)
        self.auto_check_timer.setInterval(self.AUTO_CHECK_INTERVAL_MS)
        self.auto_check_timer.timeout.connect(self._run_automatic_check)
        self.auto_check_switch.checkedChanged.connect(self._set_auto_check_enabled)
        qconfig.themeChangedFinished.connect(self._refresh_update_notice_style)
        self._set_update_available_presentation(False)
        if self.auto_check_switch.isChecked():
            self._start_automatic_checks()

    def _show_changelog(self):
        dialog = ChangelogDialog(str(self.info.get("version") or ""), self)
        dialog.exec()

    def check_for_updates(self, automatic: bool = False):
        if self.manager.is_checking:
            return False
        self._automatic_check = automatic
        return self.manager.check()

    def _auto_check_enabled(self) -> bool:
        return QSettings().value(self.AUTO_CHECK_SETTING, True, bool)

    def _set_auto_check_enabled(self, enabled: bool):
        QSettings().setValue(self.AUTO_CHECK_SETTING, enabled)
        if enabled:
            self._start_automatic_checks()
        else:
            self.auto_check_timer.stop()

    def _start_automatic_checks(self):
        self.auto_check_timer.start()
        QTimer.singleShot(0, self._run_automatic_check)

    def _run_automatic_check(self):
        if self.auto_check_switch.isChecked():
            self.check_for_updates(automatic=True)

    def _checking(self):
        self.check_button.setEnabled(False)
        if not self._automatic_check:
            self._update_state = "checking"
            self.status_changed.emit("checking", {})
            self.version_status.setText(t("desktop.checking_updates"))

    def _checked(self, result: dict):
        automatic = self._automatic_check
        self._automatic_check = False
        self._update_state = "available" if result["newer"] else "current"
        self._update_result = result
        self.check_button.setEnabled(True)
        self.release_url = result["release_url"]
        self.release_button.setUrl(self.release_url)
        self.asset_url = result["asset_url"]
        self.asset_name = result["asset_name"]
        self.asset_sha256 = result.get("asset_sha256") or ""
        if result["newer"]:
            latest = self._latest_display(result)
            unread = self._is_update_unread(latest)
            if not automatic:
                self.mark_update_read(emit_status=False)
                unread = False
            self.status_changed.emit("available", {"version": latest, "unread": unread})
            self.version_status.setText(t("desktop.update_available_dialog_body").format(version=latest))
            self.version_detail.setText(t("desktop.update_available_desc"))
            self._set_update_available_presentation(True)
            self.download_button.setVisible(bool(self.asset_url))
            self.install_button.hide()
            if automatic and unread:
                self.update_notification_requested.emit({"version": latest})
        else:
            self.status_changed.emit("current", {})
            self.version_status.setText(t("desktop.up_to_date"))
            self.version_detail.setText(t("desktop.current_version_latest").format(version=result["current"]))
            self._set_update_available_presentation(False)
            self.download_button.hide()
            self.install_button.hide()

    @staticmethod
    def _latest_display(result: dict) -> str:
        latest = str(result.get("latest") or "")
        revision = result.get("latest_revision")
        return f"{latest} (r{revision})" if revision else latest

    def _set_update_available_presentation(self, available: bool):
        self.update_icon.setVisible(available)
        self.update_badge.setVisible(available)
        self.update_notice.setProperty("updateAvailable", available)
        self._refresh_update_notice_style()

    def _refresh_update_notice_style(self):
        if self._update_state == "available":
            dark = isDarkTheme()
            background = "rgba(22, 163, 74, 0.16)" if dark else "#ECFDF5"
            border = "#34D399" if dark else "#A7F3D0"
            badge_background = "rgba(52, 211, 153, 0.24)" if dark else "#D1FAE5"
            badge_color = "#D1FAE5" if dark else "#047857"
            icon_color = "#6EE7B7" if dark else "#047857"
            self.update_icon.setPixmap(FluentIcon.UPDATE.icon(color=QColor(icon_color)).pixmap(24, 24))
        else:
            background = "transparent"
            border = "transparent"
            badge_background = "transparent"
            badge_color = "transparent"
        self.update_notice.setStyleSheet(
            f"QFrame#updateNotice {{ background: {background}; border: 1px solid {border}; border-radius: 8px; }}"
            f"QLabel#updateBadge {{ background: {badge_background}; color: {badge_color}; border: none; border-radius: 9px; padding: 2px 8px; }}"
        )

    def _is_update_unread(self, version: str) -> bool:
        settings = QSettings()
        return version not in {
            str(settings.value(self.READ_UPDATE_SETTING, "") or ""),
            str(settings.value(self.IGNORED_UPDATE_SETTING, "") or ""),
        }

    def mark_update_read(self, emit_status: bool = True):
        if self._update_state != "available" or not self._update_result:
            return
        latest = self._latest_display(self._update_result)
        QSettings().setValue(self.READ_UPDATE_SETTING, latest)
        if emit_status:
            self.status_changed.emit("available", {"version": latest, "unread": False})

    def ignore_available_update(self):
        if self._update_state != "available" or not self._update_result:
            return
        latest = self._latest_display(self._update_result)
        settings = QSettings()
        settings.setValue(self.READ_UPDATE_SETTING, latest)
        settings.setValue(self.IGNORED_UPDATE_SETTING, latest)
        self.status_changed.emit("available", {"version": latest, "unread": False})

    def _check_failed(self, message: str):
        automatic = self._automatic_check
        self._automatic_check = False
        self.check_button.setEnabled(True)
        if not automatic:
            self._failed(message)

    def _download(self):
        if self.asset_url and self.asset_name:
            self.status_changed.emit("downloading", {})
            self.progress.setValue(0)
            self.progress.show()
            self.download_button.setEnabled(False)
            self.manager.download(self.asset_url, self.asset_name, self.asset_sha256)

    def _download_progress(self, value: int):
        self.progress.setValue(value)

    def _download_finished(self, path: str):
        self._update_state = "downloaded"
        self.downloaded_path = path
        self.status_changed.emit("downloaded", {})
        self.download_button.setEnabled(True)
        self.version_status.setText(t("desktop.update_downloaded"))
        self.version_detail.setText(path)
        self._set_update_available_presentation(False)
        if self.asset_sha256:
            self.install_button.show()
            InfoBar.success(t("desktop.update_downloaded"), t("desktop.update_ready_to_install"), parent=self, position=InfoBarPosition.TOP)
        else:
            InfoBar.warning(t("desktop.update_downloaded"), t("desktop.update_manual_install"), parent=self, position=InfoBarPosition.TOP)
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _install(self):
        if not self.downloaded_path or not self.asset_sha256:
            return
        dialog = self._install_confirmation_dialog()
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            launch_update(self.downloaded_path, self.asset_sha256)
        except UpdateInstallError as exc:
            InfoBar.error(t("desktop.update_install_failed"), str(exc), parent=self, position=InfoBarPosition.TOP)
            return
        self.install_button.setEnabled(False)
        self.version_status.setText(t("desktop.update_installing"))
        QApplication.instance().quit()

    def _install_confirmation_dialog(self):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle(t("desktop.install_update"))
        dialog.setText(t("desktop.install_update_confirm"))
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Yes)
        apply_dialog_theme(dialog)
        localize_dialog_buttons(dialog)
        return dialog

    def _failed(self, message: str):
        self._update_state = "failed"
        self.status_changed.emit("failed", {})
        self.check_button.setEnabled(True)
        self.download_button.setEnabled(True)
        self.version_status.setText(t("desktop.update_check_failed"))
        self._set_update_available_presentation(False)
        InfoBar.error(t("desktop.update_check_failed"), message, parent=self, position=InfoBarPosition.TOP)

    def retranslate(self):
        self.version_label.setText(t("desktop.version_value").format(version=self.info.get("version") or "--"))
        self.author_label.setText(t("desktop.author_value").format(author="Guovin"))
        self.check_button.setText(t("desktop.check_updates"))
        auto_check_text = t("desktop.auto_check_updates")
        self.auto_check_switch.setOnText(auto_check_text)
        self.auto_check_switch.setOffText(auto_check_text)
        self.download_button.setText(t("desktop.download_update"))
        self.install_button.setText(t("desktop.install_update"))
        self.release_button.setText(t("desktop.open_release"))
        self.update_badge.setText(t("desktop.update_available_badge"))
        self.repository_button.setText(t("desktop.github_repository"))
        self.author_button.setText(t("desktop.author_homepage"))
        self.changelog_button.setText(t("desktop.view_changelog"))
        if self._update_state == "not_checked":
            self.version_status.setText(t("desktop.update_not_checked"))
            self.version_detail.setText(t("desktop.update_check_desc"))
        elif self._update_state == "checking":
            self.version_status.setText(t("desktop.checking_updates"))
        elif self._update_state == "available" and self._update_result:
            self.version_status.setText(
                t("desktop.update_available_dialog_body").format(
                    version=self._latest_display(self._update_result)
                )
            )
            self.version_detail.setText(t("desktop.update_available_desc"))
            self._set_update_available_presentation(True)
        elif self._update_state == "current" and self._update_result:
            self.version_status.setText(t("desktop.up_to_date"))
            self.version_detail.setText(t("desktop.current_version_latest").format(version=self._update_result["current"]))
        elif self._update_state == "failed":
            self.version_status.setText(t("desktop.update_check_failed"))
        elif self._update_state == "downloaded":
            self.version_status.setText(t("desktop.update_downloaded"))

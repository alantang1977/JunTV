import os

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, FluentIcon, HyperlinkButton, InfoBar, InfoBarPosition, ProgressBar, PushButton, StrongBodyLabel, SubtitleLabel

from desktop_ui.update_manager import REPOSITORY_URL, UpdateManager
from desktop_ui.update_installer import UpdateInstallError, launch_update
from desktop_ui.changelog_dialog import ChangelogDialog
from desktop_ui.widgets import AccentPushButton, apply_dialog_theme, localize_dialog_buttons
from utils.config import resource_path
from utils.i18n import t
from utils.tools import get_version_info


class AboutPage(QWidget):
    status_changed = Signal(str, object)
    AUTO_CHECK_INITIAL_DELAY_MS = 5_000
    AUTO_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1_000

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
        self.manager = UpdateManager(str(info.get("version") or "0"), self)

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
        self.version_status = StrongBodyLabel(t("desktop.update_not_checked"), self.version_card)
        self.version_detail = BodyLabel(t("desktop.update_check_desc"), self.version_card)
        self.progress = ProgressBar(self.version_card)
        self.progress.hide()
        actions = QHBoxLayout()
        self.check_button = AccentPushButton(FluentIcon.SYNC, t("desktop.check_updates"), self.version_card)
        self.download_button = PushButton(FluentIcon.DOWNLOAD, t("desktop.download_update"), self.version_card)
        self.download_button.hide()
        self.install_button = AccentPushButton(FluentIcon.SYNC, t("desktop.install_update"), self.version_card)
        self.install_button.hide()
        self.release_button = HyperlinkButton(FluentIcon.GLOBE, self.release_url, t("desktop.open_release"), self.version_card)
        actions.addWidget(self.check_button)
        actions.addWidget(self.download_button)
        actions.addWidget(self.install_button)
        actions.addWidget(self.release_button)
        card_layout.addWidget(self.version_status)
        card_layout.addWidget(self.version_detail)
        card_layout.addWidget(self.progress)
        card_layout.addLayout(actions)

        self.repository_button = HyperlinkButton(FluentIcon.GITHUB, REPOSITORY_URL, t("desktop.github_repository"), self)
        self.author_button = HyperlinkButton(FluentIcon.PEOPLE, "https://github.com/Guovin", t("desktop.author_homepage"), self)
        self.changelog_button = PushButton(FluentIcon.DOCUMENT, t("desktop.view_changelog"), self.version_card)
        self.changelog_button.clicked.connect(self._show_changelog)
        actions.addWidget(self.changelog_button)
        actions.addStretch(1)

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
        self.auto_check_timer.timeout.connect(lambda: self.check_for_updates(automatic=True))
        self.auto_check_timer.start()
        QTimer.singleShot(
            self.AUTO_CHECK_INITIAL_DELAY_MS,
            lambda: self.check_for_updates(automatic=True),
        )

    def _show_changelog(self):
        dialog = ChangelogDialog(str(self.info.get("version") or ""), self)
        dialog.exec()

    def check_for_updates(self, automatic: bool = False):
        if self.manager.is_checking:
            return False
        self._automatic_check = automatic
        return self.manager.check()

    def _checking(self):
        self.check_button.setEnabled(False)
        if not self._automatic_check:
            self._update_state = "checking"
            self.status_changed.emit("checking", {})
            self.version_status.setText(t("desktop.checking_updates"))

    def _checked(self, result: dict):
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
            self.status_changed.emit("available", {"version": result["latest"]})
            self.version_status.setText(t("desktop.update_available").format(version=result["latest"]))
            self.version_detail.setText(t("desktop.update_available_desc"))
            self.download_button.setVisible(bool(self.asset_url))
            self.install_button.hide()
        else:
            self.status_changed.emit("current", {})
            self.version_status.setText(t("desktop.up_to_date"))
            self.version_detail.setText(t("desktop.current_version_latest").format(version=result["current"]))
            self.download_button.hide()
            self.install_button.hide()

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
        InfoBar.error(t("desktop.update_check_failed"), message, parent=self, position=InfoBarPosition.TOP)

    def retranslate(self):
        self.version_label.setText(t("desktop.version_value").format(version=self.info.get("version") or "--"))
        self.author_label.setText(t("desktop.author_value").format(author="Guovin"))
        self.check_button.setText(t("desktop.check_updates"))
        self.download_button.setText(t("desktop.download_update"))
        self.install_button.setText(t("desktop.install_update"))
        self.release_button.setText(t("desktop.open_release"))
        self.repository_button.setText(t("desktop.github_repository"))
        self.author_button.setText(t("desktop.author_homepage"))
        self.changelog_button.setText(t("desktop.view_changelog"))
        if self._update_state == "not_checked":
            self.version_status.setText(t("desktop.update_not_checked"))
            self.version_detail.setText(t("desktop.update_check_desc"))
        elif self._update_state == "checking":
            self.version_status.setText(t("desktop.checking_updates"))
        elif self._update_state == "available" and self._update_result:
            self.version_status.setText(t("desktop.update_available").format(version=self._update_result["latest"]))
            self.version_detail.setText(t("desktop.update_available_desc"))
        elif self._update_state == "current" and self._update_result:
            self.version_status.setText(t("desktop.up_to_date"))
            self.version_detail.setText(t("desktop.current_version_latest").format(version=self._update_result["current"]))
        elif self._update_state == "failed":
            self.version_status.setText(t("desktop.update_check_failed"))
        elif self._update_state == "downloaded":
            self.version_status.setText(t("desktop.update_downloaded"))

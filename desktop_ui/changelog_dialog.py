import re

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QTextBrowser
from qfluentwidgets import isDarkTheme, qconfig

from utils.config import resource_path
from utils.i18n import get_language, t


def extract_release_notes(markdown: str, version: str, language: str | None = None) -> str:
    """Return one release's notes in the requested language."""
    version = str(version or "").lstrip("vV")
    if not version:
        return ""
    heading = re.escape(version)
    match = re.search(
        rf"^##\s+v?{heading}\s*$([\s\S]*?)(?=^##\s+v|\Z)",
        markdown,
        re.MULTILINE,
    )
    if not match:
        return ""

    section = match.group(0).strip()
    language = language or get_language()
    english = re.search(r"<details>\s*<summary>English</summary>([\s\S]*?)</details>", section, re.IGNORECASE)
    if language.startswith("en"):
        english_content = english.group(1).strip() if english else section
        content = f"## v{version}\n\n{english_content}" if english else english_content
    else:
        content = re.sub(r"<details>[\s\S]*?</details>", "", section, flags=re.IGNORECASE).strip()
    return content


class ChangelogDialog(QDialog):
    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.version = str(version or "")
        self.setObjectName("changelogDialog")
        self.setWindowTitle(t("desktop.changelog_title").format(version=self.version))
        self.resize(760, 620)

        self.viewer = QTextBrowser(self)
        self.viewer.setOpenExternalLinks(True)
        self.viewer.setMarkdown(self._read_changelog(self.version))

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.close_button = self.buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setText(t("desktop.close"))
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.viewer)
        layout.addWidget(self.buttons)
        qconfig.themeChangedFinished.connect(self._schedule_theme_refresh)
        self._apply_theme()

    def _schedule_theme_refresh(self, *_):
        QTimer.singleShot(0, self._apply_theme)

    def _apply_theme(self):
        dark = isDarkTheme()
        background = "#202020" if dark else "#FFFFFF"
        surface = "#27272A" if dark else "#F8FAFC"
        foreground = "#E2E8F0" if dark else "#1F2937"
        muted = "#CBD5E1" if dark else "#475569"
        border = "#3F3F46" if dark else "#E2E8F0"
        hover = "#323232" if dark else "#E2E8F0"

        palette = self.viewer.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(surface))
        palette.setColor(QPalette.ColorRole.Text, QColor(foreground))
        palette.setColor(QPalette.ColorRole.Link, QColor("#60A5FA" if dark else "#2563EB"))
        self.viewer.setPalette(palette)
        self.setStyleSheet(
            f"""
            QDialog#changelogDialog {{ background-color: {background}; }}
            QTextBrowser {{
                background-color: {surface}; color: {foreground};
                border: 1px solid {border}; border-radius: 6px; padding: 10px;
            }}
            QDialogButtonBox QPushButton {{
                background-color: {surface}; color: {muted}; border: 1px solid {border};
                border-radius: 5px; padding: 5px 14px; min-width: 72px;
            }}
            QDialogButtonBox QPushButton:hover {{ background-color: {hover}; color: {foreground}; }}
            """
        )

    def retranslate(self):
        self.setWindowTitle(t("desktop.changelog_title").format(version=self.version))
        self.viewer.setMarkdown(self._read_changelog(self.version))
        self.close_button.setText(t("desktop.close"))

    @staticmethod
    def _read_changelog(version):
        path = resource_path("CHANGELOG.md")
        try:
            with open(path, "r", encoding="utf-8") as file:
                content = extract_release_notes(file.read(), version)
                return content or t("desktop.changelog_version_unavailable").format(version=version)
        except OSError:
            return t("desktop.changelog_unavailable")

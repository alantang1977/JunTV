import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from desktop_ui.playback import (
    MODE_ASK,
    MODE_BROWSER,
    MODE_EXTERNAL,
    build_player_command,
    discover_players,
    playback_mode,
    play_url,
    PlaybackPreferencesDialog,
)
from utils.i18n import set_language, t


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QCoreApplication.setOrganizationName("IPTV-API-tests")
        QCoreApplication.setApplicationName("playback")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = QSettings()
        self.settings.clear()

    def test_defaults_to_browser_and_opens_url(self):
        self.assertEqual(playback_mode(), MODE_BROWSER)
        with patch("desktop_ui.playback.QDesktopServices.openUrl", return_value=True) as open_url:
            self.assertTrue(play_url("https://example.invalid/live.m3u8"))
        self.assertEqual(open_url.call_args.args[0].toString(), "https://example.invalid/live.m3u8")

    def test_invalid_saved_mode_falls_back_to_browser(self):
        self.settings.setValue("playback/mode", "unknown")
        self.assertEqual(playback_mode(), MODE_BROWSER)

    def test_external_command_replaces_url_without_shell(self):
        program, args = build_player_command(
            "/Applications/VLC.app/Contents/MacOS/VLC",
            "--network-caching=1000 {url}",
            "https://example.invalid/a stream.m3u8",
        )
        self.assertEqual(program, "/Applications/VLC.app/Contents/MacOS/VLC")
        self.assertEqual(args, ["--network-caching=1000", "https://example.invalid/a stream.m3u8"])

    def test_external_command_appends_url_when_placeholder_is_missing(self):
        _program, args = build_player_command("mpv", "--force-window=yes", "https://example.invalid/live")
        self.assertEqual(args, ["--force-window=yes", "https://example.invalid/live"])

    def test_known_modes_are_preserved(self):
        for mode in (MODE_BROWSER, MODE_EXTERNAL, MODE_ASK):
            self.settings.setValue("playback/mode", mode)
            self.assertEqual(playback_mode(), mode)

    def test_discovers_linux_players_available_on_path(self):
        with (
            patch("desktop_ui.playback.sys.platform", "linux"),
            patch("desktop_ui.playback.shutil.which", side_effect=lambda name: "/usr/bin/vlc" if name == "vlc" else None),
            patch("desktop_ui.playback.os.path.isfile", return_value=True),
        ):
            self.assertEqual(discover_players(), [("VLC", "/usr/bin/vlc")])

    def test_preferences_dialog_retranslates_controls(self):
        set_language("en")
        dialog = PlaybackPreferencesDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.windowTitle(), "Playback preferences")
        self.assertEqual(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).text(), "Save")
        set_language("zh_CN")
        dialog.retranslate()
        self.assertEqual(dialog.windowTitle(), "播放偏好")
        self.assertEqual(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).text(), "保存")
        self.assertEqual(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).text(), "取消")

    def test_preferences_dialog_uses_localized_cancel_button_initially(self):
        set_language("zh_CN")
        dialog = PlaybackPreferencesDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).text(), "取消")

    def test_playback_method_prompt_uses_open_wording(self):
        set_language("zh_CN")
        self.assertEqual(t("desktop.choose_playback_method_hint"), "请选择打开方式")
        self.assertEqual(t("desktop.play_in_browser"), "浏览器")

    def test_preferences_hides_template_until_advanced_options_are_enabled(self):
        dialog = PlaybackPreferencesDialog()
        self.addCleanup(dialog.deleteLater)
        dialog.mode.setCurrentIndex(1)
        self.assertFalse(dialog.advanced_toggle.isChecked())
        self.assertTrue(dialog.arguments.isHidden())
        dialog.advanced_toggle.setChecked(True)
        self.assertFalse(dialog.arguments.isHidden())

    def test_dashboard_plays_best_url_without_source_picker(self):
        from desktop_ui.pages.dashboard import DashboardPage

        page = object()
        with patch("desktop_ui.pages.dashboard.play_url") as play:
            DashboardPage._play_channel(page, {
                "best_url": "https://example.invalid/best",
                "playable_results": [
                    {"url": "https://example.invalid/slow", "speed": 1},
                    {"url": "https://example.invalid/best", "speed": 10},
                ],
            })
        play.assert_called_once_with("https://example.invalid/best", page)

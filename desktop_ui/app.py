import os
import sys

from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths, Qt
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from qfluentwidgets import Theme, setFontFamilies, setTheme, setThemeColor


def _prepare_runtime():
    QCoreApplication.setOrganizationName("IPTV-API")
    QCoreApplication.setApplicationName("IPTV-API Desktop")
    if getattr(sys, "frozen", False):
        data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        os.makedirs(data_dir, exist_ok=True)
        os.chdir(data_dir)


def _copy_runtime_resources():
    if not getattr(sys, "frozen", False):
        return
    from utils.config import config
    config.copy("config")
    if sys.platform == "win32":
        config.copy(os.path.join("utils", "nginx-rtmp-win32"))


def _configure_fonts():
    candidates = {
        "darwin": ["PingFang SC", "Helvetica Neue", "Arial"],
        "win32": ["Segoe UI", "Microsoft YaHei", "Arial"],
    }.get(sys.platform, ["Noto Sans CJK SC", "Noto Sans", "DejaVu Sans"])
    available = set(QFontDatabase.families())
    families = [family for family in candidates if family in available]
    if not families:
        families = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).families()
    setFontFamilies(families)


def _confirm_update_launch():
    path = os.environ.pop("IPTV_API_UPDATE_HEALTH_FILE", "")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as file:
            file.write("ok")
    except OSError:
        pass


def main():
    _prepare_runtime()
    if "--service" in sys.argv:
        _copy_runtime_resources()
        from service.app import run_service
        run_service()
        return 0
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("IPTV API")
    app.setQuitOnLastWindowClosed(False)
    _configure_fonts()
    try:
        _copy_runtime_resources()
        from utils.config import resource_path
        from desktop_ui.main_window import MainWindow
    except ValueError as exc:
        QMessageBox.critical(None, "配置错误", str(exc))
        return 2
    icon_path = "static/images/macos_app_icon.icns" if sys.platform == "darwin" else "favicon.ico"
    app.setWindowIcon(QIcon(resource_path(icon_path)))
    theme = str(QSettings().value("appearance/theme", "system"))
    setTheme({"dark": Theme.DARK, "light": Theme.LIGHT}.get(theme, Theme.AUTO))
    setThemeColor("#0E5CAD")
    window = MainWindow()
    window.show()
    _confirm_update_launch()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

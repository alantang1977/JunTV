import os
import sys

from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths, Qt
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from qfluentwidgets import Theme, setFontFamilies, setTheme, setThemeColor

from desktop_ui.runtime import RuntimeDirectoryError, prepare_runtime_directory


def _prepare_runtime():
    QCoreApplication.setOrganizationName("IPTV-API")
    QCoreApplication.setApplicationName("IPTV-API Desktop")
    data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    saved_directory = str(QSettings().value("runtime/data_directory", "") or "").strip()
    prepare_runtime_directory(
        sys.argv,
        fallback_directory=data_dir,
        prefer_executable_directory=sys.platform == "win32",
        saved_directory=saved_directory,
    )


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


def _verify_runtime():
    if sys.platform == "win32":
        import win32api
        import win32con
        import win32gui

    return 0


def main():
    if "--verify-runtime" in sys.argv:
        return _verify_runtime()
    try:
        _prepare_runtime()
    except RuntimeDirectoryError as exc:
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "IPTV API", str(exc))
        return 2
    if "--service" in sys.argv:
        _copy_runtime_resources()
        from service.app import run_service
        try:
            parent_index = sys.argv.index("--parent-pid") + 1
            parent_pid = int(sys.argv[parent_index])
        except (ValueError, IndexError):
            parent_pid = 0
        run_service(parent_pid=parent_pid)
        return 0
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("IPTV API")
    app.setQuitOnLastWindowClosed(False)
    from desktop_ui.single_instance import bind_activation, create_single_instance_server

    try:
        single_instance = create_single_instance_server()
    except RuntimeError as exc:
        QMessageBox.critical(None, "IPTV API", str(exc))
        return 2
    if single_instance is None:
        return 0
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
    bind_activation(single_instance, window.show_and_raise)
    window.show()
    _confirm_update_launch()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from fixture import (
    build_demo_data,
    generate_demo_logos,
    seed_demo_repository,
    summary_json,
)


LANGUAGES = ("zh_CN", "en")
THEMES = ("light", "dark")


def _configure_environment(language: str) -> None:
    for name in ("language", "LANGUAGE", "Settings_language", "SETTINGS_LANGUAGE"):
        os.environ.pop(name, None)
    os.environ["open_service"] = "False"
    os.environ["update_startup"] = "False"
    os.environ["public_domain"] = "192.168.1.100"
    os.environ["SERVICE_PORT"] = "8080"
    os.environ["PUBLIC_PORT"] = "8080"
    os.environ["PUBLIC_URL"] = ""
    os.environ["IPTV_API_SKIP_VERSION_CHECK"] = "1"


def _patch_runtime_paths(runtime_root: Path) -> None:
    import utils.constants as constants

    output_root = runtime_root / "output"
    config_root = runtime_root / "config"
    output_root.mkdir(parents=True, exist_ok=True)
    config_root.mkdir(parents=True, exist_ok=True)
    for name, value in vars(constants).copy().items():
        if not isinstance(value, str):
            continue
        normalized = value.replace("\\", "/")
        if normalized == "output":
            setattr(constants, name, str(output_root))
        elif normalized.startswith("output/"):
            setattr(constants, name, str(output_root / normalized.removeprefix("output/")))
    for name in (
        "whitelist_path",
        "blacklist_path",
        "subscribe_path",
        "local_path",
        "alias_path",
        "epg_path",
    ):
        path = config_root / f"{name.removesuffix('_path')}.txt"
        path.write_text("", encoding="utf-8")
        setattr(constants, name, str(path))


def _find_native_window_id(pid: int, width: int, height: int) -> str:
    swift = f"""
import CoreGraphics
let targetPID = {pid}
let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as! [[String: Any]]
for window in windows {{
    let ownerPID = window[kCGWindowOwnerPID as String] as? Int ?? -1
    let name = window[kCGWindowName as String] as? String ?? ""
    let layer = window[kCGWindowLayer as String] as? Int ?? -1
    let number = window[kCGWindowNumber as String] as? Int ?? -1
    let sharing = window[kCGWindowSharingState as String] as? Int ?? -1
    let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let windowWidth = bounds["Width"] as? Int ?? 0
    let windowHeight = bounds["Height"] as? Int ?? 0
    if ownerPID == targetPID && name == "IPTV-API" && layer == 0 && windowWidth == {width} && windowHeight == {height} {{
        print("\\(number)\\t\\(sharing)")
        break
    }}
}}
"""
    result = subprocess.run(
        ["/usr/bin/swift", "-e", swift],
        check=True,
        capture_output=True,
        text=True,
    )
    window_rows = result.stdout.strip().splitlines()
    if not window_rows:
        raise RuntimeError("Unable to locate the native macOS GUI window")
    window_id, _, sharing = window_rows[0].partition("\t")
    if sharing == "0":
        raise RuntimeError("The native macOS GUI window is not shareable")
    return window_id


def _capture_native_window(output_path: Path, width: int, height: int) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Native GUI showcase capture currently requires macOS")
    window_id = _find_native_window_id(os.getpid(), width, height)
    temporary_path = output_path.with_suffix(".capture.png")
    subprocess.run(
        [
            "/usr/sbin/screencapture",
            "-x",
            "-o",
            f"-l{window_id}",
            str(temporary_path),
        ],
        check=True,
    )
    if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
        raise RuntimeError(f"Screenshot was not created: {temporary_path}")
    temporary_path.replace(output_path)


def _activate_macos_application() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send_object = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", objc))
        send_bool = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_bool,
        )(("objc_msgSend", objc))
        application_class = objc.objc_getClass(b"NSApplication")
        shared_application = objc.sel_registerName(b"sharedApplication")
        activate = objc.sel_registerName(b"activateIgnoringOtherApps:")
        application = send_object(application_class, shared_application)
        send_bool(application, activate, True)
        return True
    except (AttributeError, OSError):
        return False


def _output_filename(language: str, theme: str) -> str:
    language_prefix = "desktop-ui-en" if language == "en" else "desktop-ui"
    return f"{language_prefix}.png" if theme == "light" else f"{language_prefix}-dark.png"


def capture_language(language: str, output_dir: Path, theme: str = "light") -> int:
    language = "en" if language.startswith("en") else "zh_CN"
    theme = "dark" if theme == "dark" else "light"
    _configure_environment(language)
    os.chdir(REPOSITORY_ROOT)

    from PySide6.QtCore import QCoreApplication, QTimer, Qt
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import Theme, setFontFamilies, setTheme, setThemeColor

    QCoreApplication.setOrganizationName("IPTV-API Screenshot")
    QCoreApplication.setApplicationName(f"IPTV-API GUI Showcase {language}")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication([])
    app.setApplicationDisplayName("IPTV API")
    app.setQuitOnLastWindowClosed(False)
    setFontFamilies(
        ["Helvetica Neue", "Arial"]
        if language == "en"
        else ["PingFang SC", "Helvetica Neue", "Arial"]
    )
    setTheme(Theme.DARK if theme == "dark" else Theme.LIGHT)
    setThemeColor("#0E5CAD")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = (output_dir / _output_filename(language, theme)).resolve()
    exit_state = {"code": 0, "error": ""}

    with tempfile.TemporaryDirectory(prefix=f"iptv-gui-showcase-{language}-") as temporary:
        runtime_root = Path(temporary)
        _patch_runtime_paths(runtime_root)
        demo_data = build_demo_data(language, runtime_root / "logos")
        generate_demo_logos(demo_data["logo_specs"])

        import utils.constants as constants
        from utils.config import config
        from desktop_ui.main_window import MainWindow
        from desktop_ui.platform_integration import set_macos_activation_policy
        from utils.i18n import set_language, t

        config.set("Settings", "language", language)
        set_language(language)

        db_path = Path(constants.channel_results_path)
        snapshot = seed_demo_repository(db_path, demo_data)
        set_macos_activation_policy(False)
        window = MainWindow(start_runtime=False)
        window.resize(1280, 800)
        window.navigationInterface.panel.collapse()
        window.channels.reload()
        window.rtmp.reload_channels()
        window.tasks.refresh()
        window.dashboard.refresh_metrics()
        window._service_status_changed("running")
        window.rtmp.set_snapshot(snapshot)
        window.dashboard.set_stream_snapshot(snapshot)
        window.channels.set_stream_snapshot(snapshot)
        window._update_rtmp_navigation_status(snapshot)
        # Put only the dashboard into its running visual state. Do not call
        # run_once(): showcase screenshots must never start real update work.
        window.dashboard.set_running(True)
        window.dashboard.progress.setValue(64)
        window.dashboard.progress_title.setText(
            t("desktop.testing_channel").format(
                name=window.dashboard.channel_model.rows[3]["name"]
            )
        )
        next_update = datetime.now() + timedelta(hours=6)
        window.dashboard.status_card.detail_label.setText(
            t("desktop.next_update_time").format(
                time=next_update.strftime("%Y-%m-%d %H:%M:%S")
            )
        )
        window.show()
        _activate_macos_application()
        window.raise_()
        window.activateWindow()

        model = window.dashboard.channel_model
        loader = window.channel_logo_loader
        for row in range(model.rowCount()):
            model.data(model.index(row, 0), Qt.ItemDataRole.DecorationRole)

        deadline = time.monotonic() + 20
        capture_started = {"value": False}

        def fail(message: str) -> None:
            exit_state["code"] = 1
            exit_state["error"] = message
            window._force_quit = True
            window.close()
            app.quit()

        def finish_capture() -> None:
            try:
                _capture_native_window(output_path, 1280, 800)
                print(
                    f"{language}: {summary_json(db_path, snapshot)}, "
                    f"service={window._service_status}, logos={model.rowCount()}/{model.rowCount()}, "
                    f"output={output_path}"
                )
            except Exception as exc:
                fail(str(exc))
                return
            window._force_quit = True
            window.close()
            app.quit()

        def ready_to_capture() -> None:
            if capture_started["value"]:
                return
            icons = [
                loader.source_icon(str(row.get("logo") or ""))
                for row in model.rows
                if row.get("logo")
            ]
            custom_icons_ready = all(
                icon is not None
                and not icon.isNull()
                and icon.cacheKey() != loader.fallback_source_icon.cacheKey()
                for icon in icons
            )
            logos_ready = (
                len(icons) == model.rowCount()
                and custom_icons_ready
            )
            state_ready = (
                model.rowCount() == 16
                and window._service_status == "running"
                and window.dashboard._running
                and window.dashboard.progress.value() == 64
                and window.dashboard.pause_button.isVisible()
                and window.dashboard.cancel_button.isVisible()
                and snapshot["active_count"] == 2
                and snapshot["starting_count"] == 1
                and logos_ready
            )
            if state_ready:
                capture_started["value"] = True
                _activate_macos_application()
                window.raise_()
                window.activateWindow()
                # Let the activity/glass animation paint at least one frame.
                QTimer.singleShot(700, finish_capture)
                return
            if time.monotonic() >= deadline:
                fail(
                    "GUI showcase did not become ready: "
                    f"channels={model.rowCount()}, service={window._service_status}, "
                    f"logos_ready={logos_ready}"
                )
                return
            QTimer.singleShot(50, ready_to_capture)

        QTimer.singleShot(0, ready_to_capture)
        app.exec()
        window.shutdown()

    if exit_state["error"]:
        print(exit_state["error"], file=sys.stderr)
    return exit_state["code"]


def validate_packaging_rules() -> None:
    dockerignore = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if "scripts/gui_showcase" not in dockerignore:
        raise ValueError("scripts/gui_showcase is not excluded by .dockerignore")
    if "tests" not in dockerignore:
        raise ValueError("Tests are not excluded by .dockerignore")
    if "output/screenshots" not in dockerignore:
        raise ValueError("Runtime screenshot cache is not excluded by .dockerignore")
    for spec_path in (
        REPOSITORY_ROOT / "desktop_ui" / "desktop_ui.spec",
        REPOSITORY_ROOT / "tkinter_ui" / "tkinter_ui.spec",
    ):
        if "gui_showcase" in spec_path.read_text(encoding="utf-8"):
            raise ValueError(f"GUI showcase tooling is referenced by {spec_path}")


def check_fixture() -> int:
    _configure_environment("zh_CN")
    os.chdir(REPOSITORY_ROOT)
    validate_packaging_rules()
    for language in LANGUAGES:
        with tempfile.TemporaryDirectory(prefix=f"iptv-gui-showcase-check-{language}-") as temporary:
            root = Path(temporary)
            data = build_demo_data(language, root / "logos")
            snapshot = seed_demo_repository(root / "channel_results.db", data, now=1_800_000_000)
            print(f"{language}: {summary_json(root / 'channel_results.db', snapshot)}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate deterministic IPTV-API desktop GUI showcase screenshots."
    )
    parser.add_argument(
        "--language",
        choices=(*LANGUAGES, "all"),
        default="all",
        help="Screenshot language. Defaults to both Chinese and English.",
    )
    parser.add_argument(
        "--theme",
        choices=(*THEMES, "all"),
        default="all",
        help="Screenshot theme. Defaults to both light and dark variants.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "docs" / "images",
        help="Directory for generated PNG screenshots.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate fixtures and packaging exclusions without opening the GUI.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check:
        return check_fixture()
    if args.language == "all":
        languages = LANGUAGES
    else:
        languages = (args.language,)
    themes = THEMES if args.theme == "all" else (args.theme,)
    if args.language == "all" or args.theme == "all":
        for language in languages:
            for theme in themes:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--language",
                        language,
                        "--theme",
                        theme,
                        "--output-dir",
                        str(args.output_dir.resolve()),
                    ],
                    cwd=REPOSITORY_ROOT,
                )
                if result.returncode:
                    return result.returncode
        return 0
    return capture_language(args.language, args.output_dir, args.theme)


if __name__ == "__main__":
    raise SystemExit(main())

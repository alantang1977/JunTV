from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

EXCLUDED_PLUGIN_NAMES = {
    "libqpdf.dylib",
    "libqpdf.so",
    "libqtvirtualkeyboardplugin.dylib",
    "libqtvirtualkeyboardplugin.so",
    "qpdf.dll",
    "qtvirtualkeyboardplugin.dll",
}
binaries = [
    (source, destination)
    for source, destination in binaries
    if Path(source).name.lower() not in EXCLUDED_PLUGIN_NAMES
]

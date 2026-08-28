from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

SUPPORTED_TRANSLATIONS = ("_en.qm", "_zh_CN.qm")
datas = [
    (source, destination)
    for source, destination in datas
    if "translations" not in Path(source).parts
    or Path(source).name.endswith(SUPPORTED_TRANSLATIONS)
]

# -*- mode: python ; coding: utf-8 -*-
import gzip
import json
import os
import shutil
import sys


with open("version.json", encoding="utf-8") as file:
    version_data = json.load(file)

name = f"{version_data['name']}-GUI-v{version_data['version']}"


def compress_ip_database():
    source = os.path.abspath(
        os.path.join(SPECPATH, "..", "utils", "ip_checker", "data", "qqwry.ipdb")
    )
    output_dir = os.path.abspath(
        os.path.join(SPECPATH, "..", "build", "desktop_ui-assets")
    )
    output = os.path.join(output_dir, "qqwry.ipdb.gz")
    os.makedirs(output_dir, exist_ok=True)
    with open(source, "rb") as source_file, open(output, "wb") as output_file:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=output_file,
            mtime=0,
        ) as compressed_file:
            shutil.copyfileobj(source_file, compressed_file)
    return output


config_files = [
    "config.ini",
    "demo.txt",
    "local.txt",
    "whitelist.txt",
    "blacklist.txt",
    "subscribe.txt",
    "epg.txt",
    "alias.txt",
]
datas = [(os.path.join("..", "config", item), "config") for item in config_files]
datas.extend([
    ("../config/logo", "config/logo"),
    ("../locales", "locales"),
    (compress_ip_database(), "utils/ip_checker/data"),
    ("../favicon.ico", "."),
    ("../CHANGELOG.md", "."),
    ("../version.json", "."),
    ("../service/nginx.conf.template", "service"),
])
if sys.platform == "win32":
    datas.append(("../utils/nginx-rtmp-win32", "utils/nginx-rtmp-win32"))
if sys.platform == "darwin":
    datas.append(("../static/images/macos_app_icon.icns", "static/images"))

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[os.path.join(SPECPATH, "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PIL"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
updater_analysis = Analysis(
    ["updater.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PIL"],
    noarchive=False,
    optimize=0,
)
updater_pyz = PYZ(updater_analysis.pure)
updater = EXE(
    updater_pyz,
    updater_analysis.scripts,
    updater_analysis.binaries,
    updater_analysis.datas,
    [],
    name="updater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="../favicon.ico" if sys.platform == "win32" else None,
)
collection = COLLECT(
    exe,
    updater,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=name,
)
if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name=f"{name}.app",
        icon="../static/images/macos_app_icon.icns",
        bundle_identifier="com.iptv-api.desktop",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleDisplayName": "IPTV API",
            "CFBundleName": "IPTV API",
        },
    )

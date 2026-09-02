import json
import os
import platform
import re
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QSaveFile, QStandardPaths, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


from utils.version_check import LATEST_RELEASE_API, REPOSITORY_URL, build_revision, parse_release


UPDATE_MANIFEST_NAME = "UPDATE-MANIFEST.json"
REQUIRED_BUILD_ASSETS = ("windows-x64", "macos-arm64")


def _platform_asset_key(
    platform_name: str | None = None,
    machine_name: str | None = None,
) -> str:
    platform_name = platform_name or sys.platform
    machine_name = (machine_name or platform.machine()).lower()
    platform_token = "windows" if platform_name == "win32" else "macos" if platform_name == "darwin" else ""
    if not platform_token:
        return ""
    arch_token = "arm64" if machine_name in {"arm64", "aarch64"} else "x64"
    return f"{platform_token}-{arch_token}"


def _manifest_asset(assets: list[dict]) -> dict | None:
    return next(
        (
            asset for asset in assets
            if str(asset.get("name", "")).lower() == UPDATE_MANIFEST_NAME.lower()
        ),
        None,
    )


def _manifest_from_bytes(content: bytes) -> dict:
    manifest = json.loads(content.decode("utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("更新清单格式无效")
    if not isinstance(manifest.get("builds"), list):
        raise ValueError("更新清单缺少构建记录")
    return manifest


def _revision_asset_matches(name: str, key: str, version: str, revision: int) -> bool:
    platform_token, arch_token = key.split("-", maxsplit=1)
    platform_label = "Windows" if platform_token == "windows" else "macOS"
    return bool(re.search(
        rf"-{platform_label}-{arch_token}-v{re.escape(version)}-r{revision}\.zip$",
        name,
        flags=re.IGNORECASE,
    ))


def _legacy_asset_for_platform(assets: list[dict], key: str, version: str) -> dict | None:
    platform_token, arch_token = key.split("-", maxsplit=1)
    platform_label = "Windows" if platform_token == "windows" else "macOS"
    pattern = re.compile(
        rf"-{platform_label}-{arch_token}-v{re.escape(version)}\.zip$",
        flags=re.IGNORECASE,
    )
    return next(
        (asset for asset in assets if pattern.search(str(asset.get("name", "")))),
        None,
    )


def _asset_selection(
    assets: list[dict],
    version: str,
    manifest: dict | None = None,
    platform_name: str | None = None,
    machine_name: str | None = None,
) -> tuple[dict | None, int]:
    key = _platform_asset_key(platform_name, machine_name)
    if not key:
        return None, 0
    assets_by_name = {
        str(asset.get("name", "")): asset
        for asset in assets
        if str(asset.get("name", ""))
    }
    candidates = []
    if manifest and str(manifest.get("version") or "").lstrip("v") == version:
        for build in manifest.get("builds") or []:
            if not isinstance(build, dict) or str(build.get("status") or "active").lower() != "active":
                continue
            revision = build_revision(build.get("revision"))
            build_assets = build.get("assets")
            if not revision or not isinstance(build_assets, dict):
                continue
            if not all(str(build_assets.get(item) or "") in assets_by_name for item in REQUIRED_BUILD_ASSETS):
                continue
            asset_name = str(build_assets.get(key) or "")
            asset = assets_by_name.get(asset_name)
            if asset and _revision_asset_matches(asset_name, key, version, revision):
                candidates.append((revision, asset))
    if candidates:
        revision, asset = max(candidates, key=lambda item: item[0])
        return asset, revision
    return _legacy_asset_for_platform(assets, key, version), 0


def _asset_for_platform(
    assets: list[dict],
    version: str = "",
    manifest: dict | None = None,
) -> dict | None:
    return _asset_selection(assets, version, manifest)[0]


def _sha256_from_digest(value: object) -> str:
    digest = str(value or "").strip().lower()
    if digest.startswith("sha256:"):
        digest = digest[7:]
    return digest if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest) else ""


def _checksum_asset(assets: list[dict]) -> dict | None:
    return next((asset for asset in assets if str(asset.get("name", "")).lower() == "sha256sums.txt"), None)


def _checksum_for_asset(content: bytes, asset_name: str) -> str:
    for line in content.decode("utf-8-sig", errors="replace").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip("*") == asset_name:
            return _sha256_from_digest(parts[0])
    return ""


class UpdateManager(QObject):
    check_started = Signal()
    check_finished = Signal(dict)
    check_failed = Signal(str)
    failed = Signal(str)
    download_progress = Signal(int)
    download_finished = Signal(str)

    def __init__(self, current_version: str, parent=None, current_revision: object = 0):
        super().__init__(parent)
        self.current_version = current_version
        self.current_revision = build_revision(current_revision)
        self.network = QNetworkAccessManager(self)
        self.check_reply = None
        self.download_reply = None
        self.download_file = None
        self.download_sha256 = ""

    @property
    def is_checking(self) -> bool:
        return self.check_reply is not None

    def check(self):
        if self.is_checking:
            return False
        self.check_started.emit()
        request = self._request(LATEST_RELEASE_API)
        reply = self.network.get(request)
        self.check_reply = reply
        reply.finished.connect(lambda: self._check_finished(reply))
        return True

    def download(self, url: str, name: str, expected_sha256: str = ""):
        if self.download_reply:
            return
        directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        directory = directory or os.path.expanduser("~/Downloads")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, os.path.basename(name))
        target = QSaveFile(path)
        if not target.open(QSaveFile.OpenModeFlag.WriteOnly):
            self.failed.emit(target.errorString())
            return
        reply = self.network.get(self._request(url))
        self.download_reply = reply
        self.download_file = target
        self.download_sha256 = _sha256_from_digest(expected_sha256)
        reply.readyRead.connect(lambda: target.write(reply.readAll()))
        reply.downloadProgress.connect(self._download_progress)
        reply.finished.connect(lambda: self._download_finished(reply, target, path))

    def _request(self, url: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "IPTV-API-Desktop")
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        return request

    def _check_finished(self, reply: QNetworkReply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise RuntimeError(reply.errorString())
            data = json.loads(bytes(reply.readAll()).decode("utf-8"))
            assets = data.get("assets") or []
            manifest_asset = _manifest_asset(assets)
            if manifest_asset:
                manifest_url = str(manifest_asset.get("browser_download_url") or "")
                if not manifest_url:
                    raise RuntimeError("更新清单缺少下载地址")
                manifest_reply = self.network.get(self._request(manifest_url))
                self.check_reply = manifest_reply
                manifest_reply.finished.connect(
                    lambda: self._manifest_finished(manifest_reply, data)
                )
                return
            self._complete_release_check(data, None)
        except Exception as exc:
            self.check_failed.emit(str(exc))
        finally:
            if self.check_reply is reply:
                self.check_reply = None
            reply.deleteLater()

    def _manifest_finished(self, reply: QNetworkReply, data: dict):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise RuntimeError(reply.errorString())
            manifest = _manifest_from_bytes(bytes(reply.readAll()))
            self._complete_release_check(data, manifest)
        except Exception as exc:
            self.check_failed.emit(str(exc))
        finally:
            if self.check_reply is reply:
                self.check_reply = None
            reply.deleteLater()

    def _complete_release_check(self, data: dict, manifest: dict | None):
        assets = data.get("assets") or []
        latest = str(data.get("tag_name") or data.get("name") or "").lstrip("v")
        asset, latest_revision = _asset_selection(assets, latest, manifest)
        release = parse_release(
            data,
            self.current_version,
            self.current_revision,
            latest_revision,
        )
        result = {
            **release,
            "asset_url": asset.get("browser_download_url") if asset else "",
            "asset_name": asset.get("name") if asset else "",
            "asset_sha256": _sha256_from_digest(asset.get("digest")) if asset else "",
        }
        checksum = _checksum_asset(assets)
        if asset and checksum and not result["asset_sha256"]:
            checksum_url = str(checksum.get("browser_download_url") or "")
            if not checksum_url:
                raise RuntimeError("更新包缺少 SHA-256 校验信息")
            checksum_reply = self.network.get(self._request(checksum_url))
            self.check_reply = checksum_reply
            checksum_reply.finished.connect(
                lambda: self._checksum_finished(checksum_reply, result)
            )
            return
        self.check_finished.emit(result)

    def _checksum_finished(self, reply: QNetworkReply, result: dict):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise RuntimeError(reply.errorString())
            checksum = _checksum_for_asset(bytes(reply.readAll()), str(result["asset_name"]))
            if not checksum:
                raise RuntimeError("更新包缺少 SHA-256 校验信息")
            result["asset_sha256"] = checksum
            self.check_finished.emit(result)
        except Exception as exc:
            self.check_failed.emit(str(exc))
        finally:
            self.check_reply = None
            reply.deleteLater()

    def _download_progress(self, received: int, total: int):
        self.download_progress.emit(int(received / total * 100) if total > 0 else 0)

    def _download_finished(self, reply: QNetworkReply, target: QSaveFile, path: str):
        try:
            target.write(reply.readAll())
            if reply.error() != QNetworkReply.NetworkError.NoError:
                target.cancelWriting()
                raise RuntimeError(reply.errorString())
            if not target.commit():
                raise RuntimeError(target.errorString())
            if self.download_sha256:
                from desktop_ui.updater import sha256_file
                if sha256_file(Path(path)) != self.download_sha256:
                    Path(path).unlink(missing_ok=True)
                    raise RuntimeError("更新包 SHA-256 校验失败")
            self.download_progress.emit(100)
            self.download_finished.emit(path)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            reply.deleteLater()
            self.download_reply = None
            self.download_file = None

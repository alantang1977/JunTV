import json
import os
import platform
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QSaveFile, QStandardPaths, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


from utils.version_check import LATEST_RELEASE_API, REPOSITORY_URL, parse_release


def _asset_for_platform(assets: list[dict]) -> dict | None:
    platform_token = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else ""
    if not platform_token:
        return None
    machine = platform.machine().lower()
    arch_token = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    candidates = [
        asset for asset in assets
        if platform_token in str(asset.get("name", "")).lower()
        and str(asset.get("name", "")).lower().endswith(".zip")
    ]
    exact = [asset for asset in candidates if arch_token in str(asset.get("name", "")).lower()]
    compatible = exact or [
        asset for asset in candidates
        if not any(token in str(asset.get("name", "")).lower() for token in ("x64", "arm64"))
    ]
    return sorted(compatible, key=lambda asset: str(asset.get("name", "")), reverse=True)[0] if compatible else None


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

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self.current_version = current_version
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
            release = parse_release(data, self.current_version)
            latest = release["latest"]
            assets = data.get("assets") or []
            asset = _asset_for_platform(assets)
            result = {
                **release,
                "latest": latest,
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
        except Exception as exc:
            self.check_failed.emit(str(exc))
        finally:
            if self.check_reply is reply:
                self.check_reply = None
            reply.deleteLater()

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

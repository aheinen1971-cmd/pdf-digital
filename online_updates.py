# SPDX-License-Identifier: AGPL-3.0-only
import json
import re

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

REPOSITORY = "aheinen1971-cmd/pdf-werkbank"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES = f"https://github.com/{REPOSITORY}/releases"


def available_release(data, current):
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    match = re.fullmatch(r"v([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})", tag if isinstance(tag, str) else "")
    if not match:
        return None
    version = tuple(map(int, match.groups()))
    if version <= current:
        return None
    package = f"PDF-Werkbank-{'.'.join(match.groups())}-Windows.zip"
    assets = data.get("assets")
    if not isinstance(assets, list):
        return None
    if not any(isinstance(asset, dict) and asset.get("name") == package
               and asset.get("state") == "uploaded" for asset in assets):
        return None
    return tag, f"{RELEASES}/tag/{tag}"


class ReleaseCheck(QObject):
    finished = Signal(object, bool, bool)

    def __init__(self, version, parent):
        super().__init__(parent)
        self.version = version
        self.network = QNetworkAccessManager(self)
        self.pending = None
        self.manual = False

    def check(self, manual=False):
        self.manual |= manual
        if self.pending is not None:
            return
        request = QNetworkRequest(QUrl(LATEST))
        request.setRawHeader(b"User-Agent", b"PDF-Werkbank")
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(6000)
        self.pending = self.network.get(request)
        self.pending.finished.connect(self.complete)

    def complete(self):
        reply, self.pending = self.pending, None
        manual, self.manual = self.manual, False
        success = reply.error() == QNetworkReply.NetworkError.NoError
        update = None
        if success:
            try:
                data = json.loads(bytes(reply.readAll()))
                if not isinstance(data, dict):
                    raise ValueError("Unerwartete Release-Antwort")
                update = available_release(data, self.version)
            except (ValueError, UnicodeError):
                success = False
        reply.deleteLater()
        self.finished.emit(update, success, manual)


# SPDX-License-Identifier: AGPL-3.0-only
import json
import re

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from update_worker import LATEST, available_release


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

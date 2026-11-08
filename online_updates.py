# SPDX-License-Identifier: AGPL-3.0-only
import json

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
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
        self.attempt = 0
        self.error_detail = ""
        self.retry = QTimer(self, singleShot=True, interval=1500)
        self.retry.timeout.connect(self.request_release)

    def check(self, manual=False):
        self.manual |= manual
        if self.pending is not None or self.retry.isActive():
            return
        self.attempt = 0
        self.error_detail = ""
        self.request_release()

    def request_release(self):
        self.attempt += 1
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
        success = reply.error() == QNetworkReply.NetworkError.NoError
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        update = None
        if success:
            try:
                data = json.loads(bytes(reply.readAll()))
                if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str) or not isinstance(data.get("assets"), list):
                    raise ValueError("Unerwartete Release-Antwort")
                update = available_release(data, self.version)
            except (ValueError, UnicodeError):
                success = False
                self.error_detail = "GitHub hat keine gültigen Freigabedaten geliefert."
        elif code:
            self.error_detail = f"GitHub-Abruf fehlgeschlagen (HTTP {code})."
        else:
            self.error_detail = "Keine Verbindung zu GitHub oder Zeitlimit überschritten."
        reply.deleteLater()
        if not success and self.attempt == 1 and (not code or code in (429, 500, 502, 503, 504)):
            self.retry.start()
            return
        if success:
            self.error_detail = ""
        manual, self.manual = self.manual, False
        self.finished.emit(update, success, manual)

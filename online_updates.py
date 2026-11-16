# SPDX-License-Identifier: AGPL-3.0-only
import json

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from update_worker import LATEST, available_release

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QPlainTextEdit


class UpdateWindow(QDialog):
    """One reusable surface; closing it never interrupts a verified update job."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(parent.ui("Aktualisierung"))
        self.resize(570, 330)
        self.setLayoutDirection(parent.layoutDirection())
        layout = QVBoxLayout(self)
        self.versions = QLabel()
        self.versions.setTextFormat(Qt.TextFormat.PlainText)
        self.versions.setWordWrap(True)
        layout.addWidget(self.versions)
        self.preservation = QLabel(parent.ui("Sprache, Werkzeugleiste, Druckvorlagen und Lesestellen werden vor dem Versionswechsel gesichert."))
        self.preservation.setWordWrap(True)
        layout.addWidget(self.preservation)
        self.news_button = QPushButton(parent.ui("Was ist neu?"))
        self.news_button.setCheckable(True)
        layout.addWidget(self.news_button)
        self.news = QPlainTextEdit()
        self.news.setReadOnly(True)
        self.news.setMaximumHeight(180)
        self.news.hide()
        self.news_button.toggled.connect(self.news.setVisible)
        layout.addWidget(self.news)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.action = QPushButton(parent.ui("Vorbereiten"))
        self.action.hide()
        layout.addWidget(self.action)
        self.close_button = QPushButton(parent.ui("Schließen"))
        self.close_button.clicked.connect(self.hide)
        layout.addWidget(self.close_button)
        self.callback = None
        self.action.clicked.connect(self.confirm)

    def set_versions(self, installed, available=None, notes=""):
        parent = self.parent()
        self.versions.setText(parent.ui_format("Installiert: {0}", installed) + "\n" +
                              parent.ui_format("Verfügbar: {0}", available or parent.ui("Noch nicht geprüft")))
        self.news.setPlainText(notes[:24000] if isinstance(notes, str) and notes.strip() else parent.ui("Keine Versionshinweise verfügbar."))

    def confirm(self):
        callback, self.callback = self.callback, None
        self.action.hide()
        if callback:
            callback()

    def display(self, text, busy=False, percent=None, detail="", confirm=None):
        self.message.setText(text)
        self.details.setPlainText(detail)
        self.details.setVisible(bool(detail))
        self.callback = confirm
        self.action.setVisible(confirm is not None)
        self.progress.setVisible(busy or percent is not None)
        self.progress.setRange(0, 100 if percent is not None or not busy else 0)
        if percent is not None:
            self.progress.setValue(max(0, min(100, percent)))
        self.close_button.setText(self.parent().ui("Im Hintergrund weiter" if busy else "Schließen"))


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
        self.latest_version = None
        self.release_notes = ""
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
                latest = available_release(data, (0, 0, 0))
                self.latest_version = latest[0] if latest else None
                body = data.get('body')
                self.release_notes = body[:24000] if latest and isinstance(body, str) else ""
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
        else:
            self.latest_version = None
            self.release_notes = ""
        manual, self.manual = self.manual, False
        self.finished.emit(update, success, manual)

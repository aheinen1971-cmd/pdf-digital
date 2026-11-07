import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread

import pymupdf as pdf
from PySide6.QtCore import QEventLoop, QObject, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit, QPushButton

import local_ai
import pdf_werkbank

app = QApplication([])
model = Path(sys.argv[1]).resolve()
with model.open("rb") as stream:
    assert hashlib.file_digest(stream, "sha256").hexdigest() == local_ai.MODEL_SHA256

contents = b"Model download test" * 16384


class DownloadHost(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(contents)))
        self.end_headers()
        self.wfile.write(contents)


server = ThreadingHTTPServer(("127.0.0.1", 0), DownloadHost)
Thread(target=server.serve_forever, daemon=True).start()
original = local_ai.MODEL_URL, local_ai.MODEL_SIZE, local_ai.MODEL_SHA256
with TemporaryDirectory() as tmp:
    folder = Path(tmp)
    try:
        local_ai.MODEL_URL = f"http://127.0.0.1:{server.server_port}/model?download=true"
        local_ai.MODEL_SIZE = len(contents)
        for valid in (True, False):
            target = folder / ("good.gguf" if valid else "bad.gguf")
            local_ai.MODEL_SHA256 = hashlib.sha256(contents).hexdigest() if valid else "0" * 64
            loop = QEventLoop()
            owner = QObject()
            download = local_ai.ModelDownload(target, owner)
            result = []
            download.finished.connect(lambda: (result.append(True), loop.quit()))
            download.failed.connect(lambda _: (result.append(False), loop.quit()))
            QTimer.singleShot(0, download.start)
            timeout = QTimer(owner, singleShot=True)
            timeout.timeout.connect(loop.quit)
            timeout.start(5000)
            loop.exec()
            assert result == [valid], result
            assert target.is_file() == valid
            if valid:
                assert target.read_bytes() == contents
    finally:
        local_ai.MODEL_URL, local_ai.MODEL_SIZE, local_ai.MODEL_SHA256 = original
        server.shutdown()
        server.server_close()

    text_path = folder / "Text.pdf"
    with pdf.open() as doc:
        doc.new_page().insert_text((45, 100), "Wir hat einen Termin am 12. Mai.")
        doc.save(text_path)

    pdf_werkbank.model_path = lambda: model
    window = pdf_werkbank.Window()
    window.open_path(str(text_path))
    real_exec = QDialog.exec
    suggestions = []

    def drive_dialog(dialog):
        generate = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Vorschlag erstellen")
        _, suggestion = dialog.findChildren(QPlainTextEdit)

        def capture():
            if suggestion.toPlainText():
                suggestions.append(suggestion.toPlainText())
                QTimer.singleShot(0, dialog.reject)

        suggestion.textChanged.connect(capture)
        QTimer.singleShot(0, generate.click)
        timer = QTimer(dialog, singleShot=True)
        timer.timeout.connect(dialog.reject)
        timer.start(90000)
        return real_exec(dialog)

    QDialog.exec = drive_dialog
    try:
        window.ai_text()
    finally:
        QDialog.exec = real_exec
    assert suggestions and "Wir haben" in suggestions[0], suggestions
    assert "12. Mai" in suggestions[0], suggestions
    assert "Wir hat" in window.current.doc[0].get_text()
    window.close_tab(0)
    print("Downloadintegrität und vollständiger KI-Dialog mit echtem lokalem Modell geprüft.")
    print(suggestions[0])



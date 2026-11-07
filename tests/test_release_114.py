# SPDX-License-Identifier: AGPL-3.0-only
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QLineEdit, QListWidget, QMessageBox, QPushButton

from pdf_werkbank import Window


app = QApplication([])
settings = QSettings("PDF Werkbank", "PDF Werkbank")
keys = ("workspace/maps", "scan/a4", "scan/ocr")
previous = {key: settings.value(key) for key in keys}

try:
    with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        root = Path(folder)
        original, edited, other = [root / name for name in ("Original.pdf", "Bearbeitet.pdf", "Anlage.pdf")]
        with pdf.open() as document:
            for index in range(220):
                document.new_page().insert_text((60, 100), f"Seite {index + 1}")
            document.save(original)
        with pdf.open() as document:
            document.new_page().insert_text((60, 100), "Anlage")
            document.save(other)

        window = Window()
        window.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
        commands = []
        groups = window.groups
        window.groups = [("Bearbeiten", [("Schwärzen", lambda: commands.append("Schwärzen"))])]

        def select_command(dialog):
            dialog.findChild(QLineEdit).setText("schwarzen")
            assert dialog.findChild(QListWidget).count() == 1
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", select_command):
            window.command_palette()
        assert commands == ["Schwärzen"]
        assert "Ctrl+K" in [action.shortcut().toString() for action in window.actions()]
        window.groups = groups

        window.show()
        window.open_path(str(original))
        assert window.thumbs.count() == 220 and not window.canvas.pixmap().isNull()
        with patch.object(QInputDialog, "getInt", return_value=(200, True)):
            window.jump_to_page()
        assert window.current.page == 199 and "200" in window.status.text()
        for _ in range(20):
            window.next_thumbnail()
        assert not window.thumbs.item(199).icon().isNull() and len(window.thumb_icons) < 30
        with patch("pdf_werkbank.pixmap", side_effect=AssertionError("Ansicht wurde erneut gerendert")):
            window.render()
        window.current.checkpoint()
        window.current.doc[199].insert_text((60, 150), "Bearbeitete Seite")
        window.changed()
        assert "Bearbeitete Seite" in window.current.doc[199].get_text()
        window.open_path(str(other))
        settings.remove("workspace/maps")
        with patch.object(QInputDialog, "getText", return_value=("Testvorgang", True)), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(
                QFileDialog, "getSaveFileName", return_value=(str(edited), "")):
            window.save_workspace()
        records = json.loads(settings.value("workspace/maps", "", str))["Testvorgang"]
        assert Path(records[0]["path"]) == edited and records[0]["page"] == 199 and records[1]["active"]
        reopened = Window()
        with patch.object(QInputDialog, "getItem", return_value=("Testvorgang", True)):
            reopened.open_workspace()
            reopened.open_workspace()
        assert len(reopened.sessions) == 2 and reopened.current.path == other
        assert reopened.sessions[0].page == 199 and "Bearbeitete Seite" in reopened.sessions[0].doc[199].get_text()
        for item in (window, reopened):
            while item.sessions:
                item.close_tab(0)

        settings.setValue("scan/a4", True)
        settings.setValue("scan/ocr", False)
        inbox = root / "Posteingang"
        window.inbox_folder = lambda: inbox
        window.scan_inbox = lambda: None
        calls = []
        def scan(path, samsung=False):
            calls.append(samsung)
            doc = pdf.open()
            doc.new_page(width=400, height=600).insert_text((40, 80), f"Scan Nummer {len(calls)}")
            return doc

        def confirm_scan():
            dialog = QApplication.activeModalWidget()
            if dialog and dialog.windowTitle() == "Samsung → Posteingang" and calls:
                button = next(item for item in dialog.findChildren(QPushButton) if item.text() == "Seite scannen")
                button.click()
                dialog.accept()
            else:
                QTimer.singleShot(20, confirm_scan)

        with patch.object(window, "scan_page", side_effect=scan):
            QTimer.singleShot(20, confirm_scan)
            window.quick_samsung_scan()
        assert calls == [True, True]
        with pdf.open(next(inbox.glob("*.pdf"))) as saved:
            assert len(saved) == 2 and tuple(saved[0].rect)[2:] == pdf.paper_size("a4")
            assert "Scan Nummer 2" in saved[1].get_text()
finally:
    for key, value in previous.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()

print("Befehlssuche, Seite 200, Ansichts-Cache, Arbeitsmappe und Samsung-Schnellscan geprüft.")

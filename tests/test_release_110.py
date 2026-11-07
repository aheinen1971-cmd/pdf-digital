# SPDX-License-Identifier: AGPL-3.0-only
import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QMimeData, QPointF, QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QInputDialog
from PySide6.QtWidgets import QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton

from filing import filename_hint, folder_matches
from page_tools import edit_image, image_at, twain_command
from pdf_standards import validate_standard
from pdf_werkbank import POINTS_PER_MM, TransferList, Window, paragraph_at, save_copy


def make_pdf(path, *texts):
    with pdf.open() as doc:
        for text in texts:
            doc.new_page().insert_text((50, 100), text)
        doc.save(path)


def button(dialog, label):
    return next(item for item in dialog.findChildren(QPushButton) if item.text() == label)


app = QApplication([])
settings = QSettings("PDF Werkbank", "PDF Werkbank")
keys = ("search/folder", "scan/archive-folder", "scan/twain-console")
previous = {key: settings.value(key) for key in keys}
try:
    with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        root = Path(folder)
        library, inbox, feeder = (root / key for key in ("Bibliothek", "Posteingang", "Einzug"))
        for path in (library, inbox, feeder):
            path.mkdir()
        nested = library / "Unterordner"
        nested.mkdir()
        left, right, match = library / "Links.pdf", library / "Rechts.pdf", nested / "Rechnung.pdf"
        make_pdf(left, "Erste Seite", "Zweite Seite")
        make_pdf(right, "Andere Seite")
        make_pdf(match, "Stromrechnung vom 07.11.2026", "Rechnungsnummer 3088")
        with pdf.open(match) as doc:
            assert filename_hint(doc, date(2025, 1, 1)) == ("2026-11-07 – Stromrechnung.pdf", "Energie")
            save_copy(doc, nested / "Geschützt.pdf", password="Pass", encryption=pdf.PDF_ENCRYPT_AES_256,
                      owner_pw="Pass", user_pw="Pass")
        (nested / "Defekt.pdf").write_bytes(b"broken")
        hits, skipped, _, _ = folder_matches(library, "RECHNUNGSNUMMER   3088", True, lambda _: True)
        assert [(path, page) for path, page, _ in hits] == [(match, 1)] and skipped == 2
        assert not folder_matches(library, "3088", False, lambda _: True)[0]
        assert folder_matches(library, "3088", True, lambda _: False)[3] == "abgebrochen"

        window = Window()
        window.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(library)), patch.object(
                window, "folder_search_options", return_value=("3088", True, None, ("", "", 0), False)), patch.object(
                QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            window.search_folder()
        assert window.current.path == match and window.current.page == 1
        window.close_tab(0)

        window.inbox_folder = lambda: inbox
        settings.setValue("scan/archive-folder", str(library))
        with pdf.open(match) as doc:
            draft = window.queue_scan(doc)
        def review_inbox(dialog):
            assert dialog.findChild(QListWidget).count() == 1
            assert any(field.text() == "Energie" for field in dialog.findChildren(QLineEdit))
            button(dialog, "Prüfen und archivieren").click()
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog, "exec", review_inbox), patch.object(window, "archive_document", return_value=None):
            window.scan_inbox()
        assert draft.exists()
        java, jar = (os.environ.get(f"PDF_WERKBANK_QA_{key}") for key in ("JAVA", "VERA"))
        if java and jar:
            archive = library / "Energie" / "2026-11-07 – Stromrechnung.pdf"
            with patch.object(QDialog, "exec", review_inbox), patch.object(
                    window, "preview_send_from_path", return_value=True), patch.object(
                    window, "standard_tools", return_value=(java, jar)):
                window.scan_inbox()
            assert not draft.exists() and validate_standard(java, jar, archive, "2b")[0]
            with pdf.open(archive) as archived:
                assert archived[1].search_for("3088")

        source = window.open_path(str(left))
        destination = window.open_path(str(right))
        window.transfer_page(source, 1, destination, 0, move=False)
        assert len(source.doc) == 2 and "Zweite" in destination.doc[0].get_text()
        window.undo()
        assert len(destination.doc) == 1
        window.transfer_page(source, 0, destination, 1, move=True)
        assert len(source.doc) == 1 and "Erste" in destination.doc[1].get_text()
        window.undo()
        assert len(source.doc) == 2 and len(destination.doc) == 1

        drag_calls = []
        origin = TransferList(None)
        drop = TransferList(lambda sender, page, receiver, index: drag_calls.append((sender, page, receiver, index)) or True)
        mime = QMimeData()
        mime.setData(TransferList.MIME, b"1")
        class Drop:
            def source(self): return origin
            def mimeData(self): return mime
            def position(self): return QPointF(1, 1)
            def acceptProposedAction(self): pass
        drop.dropEvent(Drop())
        assert drag_calls == [(origin, 1, drop, 0)]

        window.tabs.setCurrentIndex(0)
        page = source.doc[0]
        window.grid_mm = 5
        snapped = window.snapped(page, pdf.Point(14, 28))
        assert abs(snapped.x - 5 * POINTS_PER_MM) < 0.1 and abs(snapped.y - 10 * POINTS_PER_MM) < 0.1
        target = paragraph_at(page, pdf.Point(61, 96))
        def enlarge(dialog):
            dialog.findChild(QCheckBox).setChecked(True)
            dialog.findChild(QPlainTextEdit).setPlainText("Ein längerer korrigierter Absatz mit ausreichend Platz.")
            dimensions = dialog.findChildren(QDoubleSpinBox)
            dimensions[2].setValue(140)
            dimensions[3].setValue(30)
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, "exec", enlarge):
            assert window.styled_text(page, pdf.Point(61, 96), target)
        assert "längerer korrigierter Absatz" in page.get_text()
        with pdf.open() as blank:
            raster = blank.new_page(width=100, height=100).get_pixmap()
            page.insert_image(pdf.Rect(80, 230, 110, 260), pixmap=raster)
            for name in ("0002-300-300.png", "0001-300-300.png"):
                raster.save(feeder / name)
        selected = image_at(page, pdf.Point(90, 240))
        def position(dialog):
            x, y = dialog.findChildren(QDoubleSpinBox)
            x.setValue(45)
            y.setValue(85)
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, "exec", position):
            edit_image(page, selected, offset=window.image_position(page, selected))
        assert abs(source.doc[0].get_image_info()[0]["bbox"][0] - 45 * POINTS_PER_MM) < 0.1

        with patch.object(QInputDialog, "getInt", return_value=(30, True)), patch.object(
                window, "scan_device", return_value=(1, b"jam")), patch.object(QMessageBox, "information"):
            with window.scan_feeder(feeder) as stack:
                assert len(stack) == 2 and abs(stack[0].rect.width - 24) < 0.1

        naps = root / "NAPS2.Console.exe"
        naps.touch()
        settings.setValue("scan/twain-console", str(naps))
        commands = []
        def acquire(command, env, label):
            commands.append(command)
            if "--listdevices" in command:
                return 0, b"Test Scanner\n"
            make_pdf(Path(command[2]), "Seite A", "Seite B")
            return 0, b""
        with patch.object(window, "acquire_command", side_effect=acquire), patch.object(
                QInputDialog, "getItem", side_effect=[("Test Scanner", True), ("Blatteinzug", True)]):
            with window.scan_twain(feeder) as stack:
                assert len(stack) == 2
        assert commands == [twain_command(naps), twain_command(naps, feeder / "TWAIN.pdf", "Test Scanner")]
        (feeder / "TWAIN.pdf").unlink()
        with patch.object(window, "acquire_command", side_effect=[(0, b"Test Scanner"), (1, b"0 pages")]), patch.object(
                QInputDialog, "getItem", side_effect=[("Test Scanner", True), ("Scannerglas", True)]), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(
                window, "scan_page", return_value="WIA") as fallback:
            assert window.scan_twain(feeder) == "WIA"
            fallback.assert_called_once_with(feeder / "WIA.png")
        for session in window.sessions:
            session.changed = False
        while window.sessions:
            window.close_tab(0)
finally:
    for key, value in previous.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()

print("Scan-Posteingang, Ordnersuche, Präzisionswerkzeuge, Seitentransfer und Scannerwege geprüft.")

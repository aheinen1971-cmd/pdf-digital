# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QTableWidget

from page_tools import ocr_lines
from pdf_standards import validate_standard
from pdf_werkbank import Window, signal_startup_ready
from update_worker import digest_file, failed_update, prepared_update


app = QApplication([])
settings = QSettings("PDF Werkbank", "PDF Werkbank")
keys = ("batch/profiles", "toolbar/favorites")
previous = {key: settings.value(key) for key in keys}
try:
    with TemporaryDirectory(ignore_cleanup_errors=True) as folder, pdf.open() as image_doc:
        root = Path(folder)
        test_pdf, other = root / "Scan.pdf", root / "Andere.pdf"
        image = image_doc.new_page()
        image.insert_text((45, 85), "Rechnungsnummer 4718")
        with pdf.open() as document:
            page = document.new_page()
            page.insert_image(page.rect, pixmap=image.get_pixmap(matrix=pdf.Matrix(2, 2)))
            page.insert_text((45, 85), "Rechnungsnummer 4713", render_mode=3)
            document.new_page().insert_text((45, 85), "Sichtbarer Originaltext")
            document.save(test_pdf)
        with pdf.open() as document:
            document.new_page().insert_text((45, 85), "Zieldokument")
            document.save(other)
        settings.setValue("batch/profiles", json.dumps({"Für den Versand": ["ocr", "compress"]}))
        settings.remove("toolbar/favorites")
        window = Window()
        window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
        session = window.open_path(str(test_pdf))
        pixels = session.doc[0].get_pixmap().digest
        assert len(ocr_lines(session.doc[0])) == 1 and not ocr_lines(session.doc[1])

        def edit(dialog):
            table = dialog.findChild(QTableWidget)
            assert table.rowCount() == 1 and "4713" in table.item(0, 0).text()
            table.item(0, 0).setText("Rechnungsnummer 4718")
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", edit):
            window.correct_ocr()
        assert session.doc[0].get_pixmap().digest == pixels
        assert session.doc[0].search_for("4718") and not session.doc[0].search_for("4713")
        assert session.history[-1][3] == "OCR-Text korrigieren"
        window.undo()
        assert session.doc[0].search_for("4713")
        window.redo()
        assert session.doc[0].search_for("4718")
        java, jar = (os.environ.get(f"PDF_WERKBANK_QA_{name}") for name in ("JAVA", "VERA"))
        if java and jar:
            output = root / "Archiviert.pdf"
            with patch.object(window, "standard_tools", return_value=(java, jar)), patch.object(
                    window, "preview_send_from_path", return_value=True):
                assert window.archive_document(session.doc, output, False, True) == output
            assert validate_standard(java, jar, output, "2b")[0]
            with pdf.open(output) as saved:
                assert saved[0].search_for("4718") and not saved[0].search_for("4713")

        window.pin_favorite("profile:Für den Versand", True)
        reopened = Window()
        assert reopened.favorite_actions[-1].text() == "Ablauf: Für den Versand"
        with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(test_pdf)], "")), patch.object(
                reopened, "run_batch") as batch:
            reopened.favorite_actions[-1].trigger()
            batch.assert_called_once_with([str(test_pdf)], ["ocr", "compress"])

        destination = window.open_path(str(other))
        window.transfer_page(session, 0, destination, 1, move=True)
        assert len(session.doc) == 1 and len(destination.doc) == 2
        window.undo()
        assert len(session.doc) == 2 and len(destination.doc) == 1
        window.redo()
        assert len(session.doc) == 1 and len(destination.doc) == 2
        window.undo()
        window.edit_page("rotate", 90)
        assert not destination.redo_stack
        window.redo()
        assert len(session.doc) == 2

        marker = root / "ready"
        with patch.dict(os.environ, {"PDF_WERKBANK_STARTUP_READY": str(marker)}):
            signal_startup_ready()
        assert marker.read_text(encoding="utf-8") == "bereit"
        update_root = root / "Updates"
        update_root.mkdir()
        exe = update_root / "PDF-Werkbank-1.15.0.exe"
        exe.write_bytes(b"candidate")
        digest = digest_file(exe)
        (update_root / "1.15.0.json").write_text(json.dumps({"sha256": digest}), encoding="utf-8")
        with patch("update_worker.update_root", return_value=update_root), patch(
                "update_worker.runtime_ready", return_value=True):
            assert prepared_update((1, 14, 0)) == exe
            (update_root / "1.15.0.failed").write_text(digest, encoding="utf-8")
            assert failed_update("1.15.0", digest)
            assert prepared_update((1, 14, 0)) is None
        for document in window.sessions:
            document.changed = False
        while window.sessions:
            window.close_tab(0)
finally:
    for key, value in previous.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()

print("OCR-Korrektur, PDF/A, Ablauf-Favoriten, Undo/Redo und Startmarkierungen geprüft.")

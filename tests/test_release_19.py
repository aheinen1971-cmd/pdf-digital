# SPDX-License-Identifier: AGPL-3.0-only
import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton

from document_review import attachments, label_conflicts, page_labels
from pdf_standards import validate_standard
from pdf_werkbank import Window


def button(dialog, text):
    return next(item for item in dialog.findChildren(QPushButton) if item.text() == text)


app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as folder, pdf.open() as image_doc, pdf.open() as source:
    root = Path(folder)
    original = root / "Original.pdf"
    target = root / "Archiv.pdf"
    attachment = root / "Daten.txt"
    attachment.write_bytes(b"Beispielinhalt")
    first = source.new_page()
    first.insert_text((50, 110), "Vorhandene Word-Prüfung")
    first.add_text_annot((85, 230), "Kontrollhinweis")
    first.add_file_annot((85, 320), b"Seitenanlage", "Seitenanlage.txt")
    image = image_doc.new_page()
    image.insert_text((50, 140), "Durchsuchbarer Testscan", fontsize=28)
    second = source.new_page()
    second.insert_image(second.rect, pixmap=image.get_pixmap(matrix=pdf.Matrix(3, 3)))
    third = source.new_page()
    third.set_rotation(90)
    third.add_text_annot((85, 280), "Andere Notiz")
    source.save(original)
    before = original.read_bytes()
    assert label_conflicts(source[1], list(page_labels(source[1], original.name, "", "{Seite}", "mittig", 10, date.today())))
    window = Window()
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    window.open_path(str(original))
    settings = QSettings("PDF Werkbank", "PDF Werkbank")
    saved_templates = settings.value("document/page-labels")

    def configure_labels(dialog):
        head, foot, pages = dialog.findChildren(QLineEdit)[:3]
        head.setText("Akte {Datei}")
        foot.setText("Seite {Seite} von {Seiten}")
        pages.setText("1, 3")
        button(dialog, "Vorlage speichern").click()
        assert any("Vorschauseite" in label.text() for label in dialog.findChildren(QLabel))
        return QDialog.DialogCode.Accepted

    try:
        with patch.object(QDialog, "exec", configure_labels), patch.object(
                QInputDialog, "getText", return_value=("Prüfvorlage", True)):
            window.headers_and_footers()
        assert "Prüfvorlage" in settings.value("document/page-labels", "", str)
        doc = window.current.doc
        assert "Akte Original" in doc[0].get_text()
        assert "Seite 3 von 3" in doc[2].get_text()
        assert not doc[1].get_text().strip()
        window.undo()
        assert "Akte Original" not in window.current.doc[0].get_text()
    finally:
        if saved_templates is None:
            settings.remove("document/page-labels")
        else:
            settings.setValue("document/page-labels", saved_templates)

    summary = root / "Kommentare.txt"
    def comments_dialog(dialog):
        dialog.findChild(QLineEdit).setText("Andere")
        assert dialog.findChild(QListWidget).count() == 1
        button(dialog, "Gefilterte Übersicht speichern").click()
        button(dialog, "Zur Stelle").click()
        return QDialog.DialogCode.Accepted

    with patch.object(QDialog, "exec", comments_dialog), patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(summary), "")):
        window.review_comments()
    assert window.current.page == 2
    assert "Andere Notiz" in summary.read_text(encoding="utf-8")
    assert "Kontrollhinweis" not in summary.read_text(encoding="utf-8")

    extracted = root / "Gespeichert.txt"
    def attachments_dialog(dialog):
        assert dialog.findChild(QListWidget).count() == 1
        button(dialog, "Datei hinzufügen").click()
        assert dialog.findChild(QListWidget).count() == 2
        button(dialog, "Anlage speichern unter").click()
        assert extracted.read_bytes() == attachment.read_bytes()
        button(dialog, "Anlage entfernen").click()
        assert dialog.findChild(QListWidget).count() == 1
        return QDialog.DialogCode.Rejected

    with patch.object(QDialog, "exec", attachments_dialog), patch.object(
            QFileDialog, "getOpenFileName", return_value=(str(attachment), "")), patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(extracted), "")), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        window.manage_attachments()
    assert len(list(attachments(window.current.doc))) == 1
    window.undo()
    assert "Daten.txt" in window.current.doc.embfile_names()
    window.undo()

    java, jar = (os.environ.get(f"PDF_WERKBANK_QA_{key}") for key in ("JAVA", "VERA"))
    with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted), patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(target), "")), patch.object(
            window, "standard_tools", return_value=(java or "java", jar or "vera")), patch(
            "pdf_werkbank.validate_standard", return_value=(False, ["Testfehler"])):
        try:
            window.archive_copy()
        except AssertionError as exc:
            assert "veraPDF hat die Archivkopie abgelehnt" in str(exc), exc
        else:
            raise AssertionError("Fehlende Validierungsmeldung")
        assert not target.exists()
    if java and jar:
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted), patch.object(
                QFileDialog, "getSaveFileName", return_value=(str(target), "")):
            window.archive_copy()
        assert validate_standard(java, jar, target, "2b")[0]
        with pdf.open(target) as archive:
            assert archive[0].search_for("Word-Prüfung")
            assert archive[1].search_for("Durchsuchbarer Testscan")
            assert not archive[2].get_text().strip()
        if os.environ.get("PDF_WERKBANK_QA_OUTPUT"):
            (Path(os.environ["PDF_WERKBANK_QA_OUTPUT"]) / "Archivsuchtext.pdf").write_bytes(target.read_bytes())
    assert original.read_bytes() == before
    assert not window.current.doc[1].get_text().strip()
    window.close_tab(0)

print("Kopf- und Fußzeilen, Kommentare, Anlagen und Archivworkflow geprüft.")

# SPDX-License-Identifier: AGPL-3.0-only
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree

import pymupdf as pdf
import pdf_werkbank
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QFileDialog, QInputDialog, QLineEdit, QMessageBox, QPushButton

from office_export import export_word_layout
from pdf_standards import accessibility_blocks, export_accessible, export_archive, validate_standard
from pdf_werkbank import Window, save_copy


def crash(root, original, protected):
    app = QApplication([])
    window = Window()
    window.start_recovery(root)
    window.open_path(str(original))
    with patch.object(QInputDialog, "getText", return_value=("Testpass", True)):
        window.open_path(str(protected))
    window.current.doc[0].insert_text((40, 400), "Änderung vor dem Absturz")
    window.changed()
    os._exit(0)


if len(sys.argv) == 5 and sys.argv[1] == "crash":
    crash(*map(Path, sys.argv[2:]))


app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    root = Path(folder)
    archive, accessible, original, protected = [root / name for name in
                                                ("Archiv.pdf", "Lesefassung.pdf", "Original.pdf", "Geschützt.pdf")]
    with pdf.open() as doc, pdf.open() as drawing:
        image = drawing.new_page(width=90, height=48)
        image.draw_rect(pdf.Rect(8, 8, 82, 40), color=(0, 0, 1), fill=(0, 0.8, 1))
        page = doc.new_page()
        page.insert_text((50, 70), "Überschrift", fontname="hebo", fontsize=24)
        page.insert_text((50, 110), "Absatz für die Word-Prüfung.", fontsize=12)
        page.insert_image(pdf.Rect(50, 145, 140, 193), pixmap=image.get_pixmap())
        for y in (240, 260, 280):
            page.draw_line((50, y), (220, y))
        for x in (50, 135, 220):
            page.draw_line((x, 240), (x, 280))
        for position, text in [((55, 254), "Name"), ((142, 254), "Wert"), ((55, 274), "A"), ((142, 274), "42")]:
            page.insert_text(position, text)
        doc.new_page().insert_text((50, 70), "Zweite Seite")
        doc.save(original)
        save_copy(doc, protected, encryption=pdf.PDF_ENCRYPT_AES_256, owner_pw="Testpass",
                  user_pw="Testpass", password="Testpass")

        word = root / "Layout.docx"
        export_word_layout(doc, word)
        with ZipFile(word) as package:
            assert package.testzip() is None and "word/media/bild1.png" in package.namelist()
            xml = ElementTree.fromstring(package.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            assert len(xml.findall(".//w:tbl", ns)) == 1 and len(xml.findall(".//w:sectPr", ns)) == 2
            assert xml.find(".//w:b", ns) is not None and xml.find(".//w:drawing", ns) is not None
        windows = Path(os.environ["WINDIR"])
        export_archive(doc, archive, windows / "System32/spool/drivers/color/sRGB Color Space Profile.icm")
        pages = [accessibility_blocks(page) for page in doc]
        pages[0][0] = ("H1", pages[0][0][1], pages[0][0][2])
        pages[0] = [(role, box, "Blaues Rechteck" if role == "Figure" else text) for role, box, text in pages[0]]
        export_accessible(doc, accessible, pages, "Dokumenttest", "de-DE", windows / "Fonts/arial.ttf")
        with pdf.open(archive) as archived, pdf.open(accessible) as tagged:
            assert len(doc) == len(archived) == len(tagged) == 2
            assert "pdfaid:conformance>B" in archived.get_xml_metadata()
            assert tagged.xref_get_key(tagged.pdf_catalog(), "StructTreeRoot")[0] == "xref"
            assert "Überschrift" in tagged[0].get_text() and "Zweite Seite" in tagged[1].get_text()
            assert tagged[0].get_pixmap().width == doc[0].get_pixmap().width
            assert all(page.get_image_info() for page in archived)
        qa_output = os.environ.get("PDF_WERKBANK_QA_OUTPUT")
        if qa_output:
            for path in (archive, accessible, original, word):
                (Path(qa_output) / path.name).write_bytes(path.read_bytes())
        if os.environ.get("PDF_WERKBANK_QA_JAVA") and os.environ.get("PDF_WERKBANK_QA_VERA"):
            java, jar = (os.environ[f"PDF_WERKBANK_QA_{name}"] for name in ("JAVA", "VERA"))
            result = validate_standard(java, jar, archive, "2b")
            assert result[0], result
            assert not validate_standard(java, jar, original, "2b")[0]
            tagged_result = validate_standard(java, jar, accessible, "ua1")
            assert tagged_result[0], tagged_result
            assert not validate_standard(java, jar, original, "ua1")[0]
            print("veraPDF bestätigt die PDF/A-2b- und PDF/UA-1-Testdateien und erkennt Gegenbeispiele.")

    recovery = root / "Recovery"
    runner = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    subprocess.run([sys.executable, "-c", runner, str(Path(pdf_werkbank.__file__).parent),
                    __file__, "crash", str(recovery), str(original), str(protected)], check=True, timeout=20)
    snapshots = list(recovery.glob("*/*.pdf"))
    assert len(snapshots) == 1
    with pdf.open(snapshots[0]) as encrypted:
        assert encrypted.needs_pass and not encrypted.authenticate("falsch")
    window = Window()
    window.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(
            QInputDialog, "getText", return_value=("Testpass", True)):
        window.start_recovery(recovery)
    assert len(window.sessions) == 2 and window.current.changed
    assert "Änderung vor dem Absturz" in window.current.doc[0].get_text()
    with pdf.open(protected) as unchanged:
        assert unchanged.authenticate("Testpass") and "Absturz" not in unchanged[0].get_text()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
        window.close()
    assert not list(recovery.iterdir())

    settings = QSettings("PDF Werkbank", "PDF Werkbank")
    old = settings.value("batch/profiles")
    try:
        settings.remove("batch/profiles")
        window = Window()
        def create_profile(dialog):
            dialog.findChild(QLineEdit).setText("Test-Versand")
            for checkbox in dialog.findChildren(QCheckBox):
                checkbox.setChecked(True)
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "Vorlage speichern").click()
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog, "exec", create_profile):
            window.batch_profiles()
        assert "Test-Versand" in settings.value("batch/profiles", "", str)
        target = root / "Stapel"
        target.mkdir()
        scan_path = root / "Scan.pdf"
        with pdf.open() as image_source, pdf.open() as scan:
            image_page = image_source.new_page()
            image_page.insert_text((50, 90), "Durchsuchbarer Testscan", fontsize=28)
            scan_page = scan.new_page()
            scan_page.insert_image(scan_page.rect, pixmap=image_page.get_pixmap(matrix=pdf.Matrix(3, 3)))
            scan.save(scan_path)
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted), patch.object(
                QFileDialog, "getOpenFileNames", return_value=([str(original), str(protected), str(scan_path)], "")), patch.object(
                QFileDialog, "getExistingDirectory", return_value=str(target)), patch.object(
                QMessageBox, "exec", return_value=0):
            window.batch_profiles()
            result = target / "Original - Stapel.pdf"
            assert result.is_file() and not (target / "Geschützt - Stapel.pdf").exists()
            first = result.read_bytes()
            window.batch_profiles()
            assert result.read_bytes() == first
        with pdf.open(result) as processed:
            assert all(page.rotation == 90 for page in processed)
        with pdf.open(target / "Scan - Stapel.pdf") as processed:
            assert processed[0].rotation == 90
            assert "Durchsuchbarer Testscan" in processed[0].get_text()
    finally:
        if old is None:
            settings.remove("batch/profiles")
        else:
            settings.setValue("batch/profiles", old)
        settings.sync()

print("Wiederherstellung, kombinierter Stapel, Word-Layout und PDF-Standards geprüft.")

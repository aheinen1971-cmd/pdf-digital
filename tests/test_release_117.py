# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QListWidget, QMessageBox, QTableWidget, QPushButton

from intake import archive_profile, audit_pdf, explorer_paths, install_explorer_commands, invoice_fields
from office_export import export_invoices
from page_tools import missing_required_fields
from pdf_standards import export_archive, mark_archive
from pdf_werkbank import Window


app = QApplication([])
with TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / "Rechnung Ä 7.pdf"
    scan = root / "Scan.pdf"
    archive = root / "Archiv.pdf"
    fake = root / "Falsches Archiv.pdf"
    invalid = root / "Defekt.pdf"
    locked = root / "Geschützt.pdf"
    tessdata = Path(os.environ.get("PDF_WERKBANK_QA_TESSDATA", root))
    tools = tuple(os.environ.get("PDF_WERKBANK_QA_" + key) for key in ("JAVA", "VERA"))
    with pdf.open() as doc:
        page = doc.new_page()
        page.insert_text((65, 105), "Rechnungsnummer: RE-0017\nRechnungsdatum: 07.11.2026\n"
                                    "Zwischensumme: 10,00 EUR\nZu zahlen: 11,90 EUR", fontsize=22)
        with pdf.open() as images:
            images.new_page().insert_image(page.rect, pixmap=page.get_pixmap(dpi=175))
            images.save(scan)
        doc.new_page()
        doc.save(source)
        doc.save(locked, encryption=pdf.PDF_ENCRYPT_AES_256, user_pw="test", owner_pw="owner")
        mark_archive(doc)
        assert archive_profile(doc) == "2b"
        doc.save(fake)
        if all(tools):
            color = Path(os.environ["WINDIR"]) / "System32/spool/drivers/color/sRGB Color Space Profile.icm"
            export_archive(doc, archive, color)
        doc.set_xml_metadata('<x xmlns:a="http://www.aiim.org/pdfa/ns/id/" a:part="4" a:conformance="F"/>')
        assert archive_profile(doc) == "4f"
    invalid.write_bytes(b"this is not a PDF")
    assert audit_pdf(source, None).missing_text == (2,)
    assert audit_pdf(source, None).archive == "Keine PDF/A-Kennung"
    assert audit_pdf(invalid, None).opening == "Lesefehler"
    assert audit_pdf(locked, None).opening == "Passwortgeschützt"
    if all(tools):
        assert audit_pdf(archive, tools).archive == "Bestanden"
        assert audit_pdf(fake, tools).archive == "Abweichungen"

    expected = ["RE-0017", "07.11.2026", "11,90 EUR"]
    fields = invoice_fields(source, tessdata)
    assert [field.value for field in fields] == expected
    assert all(field.page == 0 and field.rect.width > 0 for field in fields)
    if (tessdata / "deu.traineddata").is_file():
        assert [field.value for field in invoice_fields(scan, tessdata)] == expected
    ambiguous = root / "Mehrdeutig.pdf"
    with pdf.open() as doc:
        doc.new_page().insert_text((65, 80), "Rechnungsnummer: 12\nRechnungsnummer: 13\n"
                                            "Rechnungsdatum: 31.02.2026\nMwSt: 7,00 EUR")
        doc.save(ambiguous)
    assert invoice_fields(ambiguous, tessdata) == (None, None, None)
    xlsx = root / "Rechnungen.xlsx"
    export_invoices([("=1+1", *expected[1:], str(source), "1 / 1 / 1")], xlsx)
    with ZipFile(xlsx) as workbook:
        assert workbook.testzip() is None
        sheet = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        assert sheet.find(".//{*}f") is None
        assert "=1+1" in [item.text for item in sheet.findall(".//{*}t")]
        assert sheet.find(".//{*}hyperlink").get("ref") == "D2"
        rels = ElementTree.fromstring(workbook.read("xl/worksheets/_rels/sheet1.xml.rels"))
        assert rels[0].get("Target") == source.as_uri()
    original = source.read_bytes()

    window = Window()
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    def review(dialog):
        table = dialog.findChild(QTableWidget)
        assert table.item(0, 0).text() == expected[0]
        table.item(0, 0).setText("RE-KORRIGIERT")
        table.setCurrentCell(0, 2)
        next(button for button in dialog.findChildren(QPushButton) if "Excel speichern" in button.text()).click()
        return QDialog.DialogCode.Accepted

    with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(source)], "")), patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(xlsx), "")), patch.object(QDialog, "exec", review):
        window.invoice_review()
    with ZipFile(xlsx) as workbook:
        assert b"RE-KORRIGIERT" in workbook.read("xl/worksheets/sheet1.xml")

    def archive_dialog(dialog):
        table = dialog.findChild(QTableWidget)
        for row in range(table.rowCount()):
            if table.item(row, 0).text() == source.name:
                assert table.item(row, 4).text() == "2"
                table.cellDoubleClicked.emit(row, 0)
                break
        return QDialog.DialogCode.Accepted

    with patch.object(QFileDialog, "getExistingDirectory", return_value=str(root)), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.No), patch.object(
            window, "standard_tools", return_value=None), patch.object(QDialog, "exec", archive_dialog):
        window.archive_check()
    assert window.current.path == source and window.current.page == 1

    assert explorer_paths([source, source, ambiguous]) == [source, ambiguous]
    merged = root / "Zusammen.pdf"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(merged), "")):
        window.explorer_action("--explorer-merge", [str(ambiguous), str(source)])
    with pdf.open(merged) as doc:
        assert len(doc) == 3 and "13" in doc[0].get_text() and "RE-0017" in doc[1].get_text()
    with patch.object(window, "read_batch_profiles", return_value={"Test": ["compress"]}), patch.object(
            QInputDialog, "getItem", return_value=("Test", True)), patch.object(window, "run_batch") as batch:
        window.explorer_action("--explorer-workflow", [str(source), str(ambiguous)])
        batch.assert_called_once_with([source, ambiguous], ["compress"])

    launcher = os.environ.get("PDF_WERKBANK_QA_LAUNCHER")
    if launcher:
        shortcut_dir = root / "SendTo"
        shortcut_dir.mkdir()
        install_explorer_commands(launcher, shortcut_dir)
        assert len(list(shortcut_dir.glob("*.lnk"))) == 2

    editable, fixed = root / "Pflichtfelder.pdf", root / "Fest.pdf"
    with pdf.open() as doc:
        doc.new_page().insert_text((50, 55), "Formular Seite 1")
        page = doc.new_page()
        page.insert_text((50, 55), "Formular Seite 2")
        for index, (label, value, required) in enumerate((("Name", " ", True), ("Optional", "", False),
                                                          ("Anzahl", "0", True), ("Zustimmung", False, True))):
            widget = pdf.Widget()
            widget.field_name = label
            widget.field_type = pdf.PDF_WIDGET_TYPE_CHECKBOX if label == "Zustimmung" else pdf.PDF_WIDGET_TYPE_TEXT
            widget.field_value = value
            widget.field_flags = pdf.PDF_FIELD_IS_REQUIRED if required else 0
            top = 85 + index * 40
            widget.rect = pdf.Rect(50, top, 70 if label == "Zustimmung" else 210, top + 20)
            page.add_widget(widget)
        doc.save(editable)
    window.open_path(str(editable))
    assert [(page, name) for page, _, name in missing_required_fields(window.current.doc)] == [
        (1, "Name"), (1, "Zustimmung")]
    def pick_field(dialog):
        rows = dialog.findChild(QListWidget)
        assert rows.count() == 2
        rows.setCurrentRow(1)
        return QDialog.DialogCode.Accepted
    with patch.object(QDialog, "exec", pick_field), patch.object(window, "preview_send") as preview:
        window.flatten_form_copy()
        preview.assert_not_called()
    assert window.current.page == 1 and window.mode == "Formular"
    page = window.current.doc[1]
    for widget in page.widgets():
        if widget.field_name in ("Name", "Zustimmung"):
            widget.field_value = "Mustername" if widget.field_name == "Name" else widget.on_state()
            widget.update()
    assert not missing_required_fields(window.current.doc)
    with patch.object(window, "preview_send", return_value=True), patch.object(window, "output_path", return_value=fixed):
        window.flatten_form_copy()
    with pdf.open(fixed) as doc:
        assert len(doc) == 2 and not any(doc[1].widgets()) and "Mustername" in doc[1].get_text()
    with pdf.open(editable) as doc:
        assert len(missing_required_fields(doc)) == 2
    assert source.read_bytes() == original
    if os.environ.get("PDF_WERKBANK_QA_OUTPUT"):
        (Path(os.environ["PDF_WERKBANK_QA_OUTPUT"]) / "Pflichtfelder-fest-117.pdf").write_bytes(fixed.read_bytes())
    for session in window.sessions:
        session.doc.close()

print("Archivstatus, OCR-Rechnungen und Excel, Explorer-Stapel und Pflichtfelder geprüft.")

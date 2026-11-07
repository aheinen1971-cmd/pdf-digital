# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QTableWidget

from office_export import export_linked_sheet, read_form_sheet, read_form_values, workbook_sheets
from pdf_werkbank import Window
from preferences import FORM_TEMPLATES


def make_form(path):
    with pdf.open() as document:
        page = document.new_page()
        for i, (name, kind, flags) in enumerate((
                ("Kundennr.", pdf.PDF_WIDGET_TYPE_TEXT, pdf.PDF_FIELD_IS_REQUIRED),
                ("Anrede", pdf.PDF_WIDGET_TYPE_COMBOBOX, 0),
                ("Einwilligung", pdf.PDF_WIDGET_TYPE_CHECKBOX, 0))):
            field = pdf.Widget()
            field.field_name, field.field_type, field.field_flags = name, kind, flags
            field.rect = pdf.Rect(50, 60 + i * 40, 180, 84 + i * 40)
            if name == "Anrede":
                field.choice_values = ["Frau", "Herr"]
            page.add_widget(field)
        document.save(path)


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    source, book = root / "Formular.pdf", root / "Liste.xlsx"
    target = root / "Ausgabe"
    target.mkdir()
    make_form(source)
    original = hashlib.sha256(source.read_bytes()).digest()
    existing = target / "alt.pdf"
    existing.write_bytes(b"bereits vorhanden")
    headers = ["Kunden-ID", "Anrede", "Zustimmung", "Dateiname"]
    values = [
        ("0007", "Frau", "Ja", "fertig"),
        ("0008", "Herr", "Nein", ""),
        ("", "Frau", "Ja", "Pflicht"),
        ("09", "Ungültig", "Ja", "Auswahl"),
        ("10", "Frau", "Ja", "Doppelt"),
        ("11", "Herr", "Nein", "doppelt.pdf"),
        ("12", "Frau", "Ja", "alt"),
        ("13", "Frau", "Ja", "../Unerlaubt"),
        ("14", "Frau", "Ja", "abbestellt"),
        ("15", "Frau", "Ja", "wettlauf"),
    ]
    export_linked_sheet(values, book, "Kunden", headers, [20] * 4)
    assert workbook_sheets(book) == ["Kunden"]
    assert read_form_sheet(book, "Kunden")[0][1]["Kunden-ID"] == "0007"
    shared_book = root / "Shared.xlsx"
    with ZipFile(book) as original_book, ZipFile(shared_book, "w") as updated:
        for name in ("xl/workbook.xml", "xl/_rels/workbook.xml.rels"):
            payload = original_book.read(name)
            if name.endswith(".rels"):
                payload = payload.replace(b"</Relationships>", b'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
            updated.writestr(name, payload)
        updated.writestr("xl/sharedStrings.xml", "<sst><si><t>Kunden-ID</t></si><si><r><t>00</t></r><r><t>42</t></r></si></sst>")
        updated.writestr("xl/worksheets/sheet1.xml", '<worksheet><sheetData><row r="1"><c r="B1" t="s"><v>0</v></c></row><row r="3"><c r="B3" t="s"><v>1</v></c></row><row r="4"><c r="B4"><f>1+2</f><v>3</v></c></row></sheetData></worksheet>')
    assert read_form_sheet(shared_book, "Kunden") == [
        (3, {"Kunden-ID": "0042"}, []), (4, {"Kunden-ID": ""}, ["Formel oder Zellfehler in B4"])]

    window = Window()
    window.favorite_settings = QSettings(str(root / "prefs.ini"), QSettings.Format.IniFormat)
    window.favorite_settings.setValue(FORM_TEMPLATES, '{"Liste":[["Kunden-ID",["Kundennummer","Kundennr."],true],'
                                      '["Anrede",["Anrede"],false],["Zustimmung",["Einwilligung"],false]]}')
    report = []

    def review(dialog):
        table = dialog.findChild(QTableWidget)
        states = [table.item(row, 3).text() for row in range(table.rowCount())]
        assert states[:2] == ["Bereit", "Bereit"]
        for row, phrase in enumerate(("Pflichtwert", "Ungültige Auswahl", "Doppelter", "Doppelter",
                                      "existiert bereits", "Ungültiger PDF-Dateiname"), 2):
            assert phrase in states[row]
            assert not table.item(row, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
        table.cellDoubleClicked.emit(0, 2)
        table.item(8, 0).setCheckState(Qt.CheckState.Unchecked)
        (target / "wettlauf.pdf").write_bytes(b"parallel erstellt")
        next(button for button in dialog.findChildren(QPushButton)
             if button.text() == "Auswahl als PDFs speichern").click()
        return dialog.result()

    def preview(doc, *_, **options):
        assert options["review_only"]
        assert {field.field_name: field.field_value for field in doc[0].widgets()}["Kundennr."] == "0007"

    def report_result(dialog):
        report.append((dialog.text(), dialog.detailedText()))
        return 0

    with patch("pdf_werkbank.QFileDialog.getOpenFileName", side_effect=[(str(source), ""), (str(book), "")]), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(target)), \
            patch("pdf_werkbank.QInputDialog.getItem", return_value=("Liste", True)), \
            patch.object(QDialog, "exec", review), patch.object(QMessageBox, "exec", report_result), \
            patch.object(window, "preview_send", side_effect=preview) as show, \
            patch.object(window, "error", side_effect=AssertionError):
        window.fill_form_batch()
    show.assert_called_once()
    assert read_form_values(target / "fertig.pdf") == {"Kundennr.": "0007", "Anrede": "Frau", "Einwilligung": "Yes"}
    assert read_form_values(target / "Formular-003.pdf") == {"Kundennr.": "0008", "Anrede": "Herr", "Einwilligung": ""}
    assert sorted(path.name for path in target.iterdir()) == ["Formular-003.pdf", "alt.pdf", "fertig.pdf", "wettlauf.pdf"]
    assert existing.read_bytes() == b"bereits vorhanden"
    assert (target / "wettlauf.pdf").read_bytes() == b"parallel erstellt"
    assert "2 von 3" in report[0][0] and "existiert bereits" in report[0][1]
    assert hashlib.sha256(source.read_bytes()).digest() == original
    window.close()

print("Excel-Import, Formularvorschau, Pflichtwerte, Datenauswahl und überschreibfreies Speichern geprüft.")

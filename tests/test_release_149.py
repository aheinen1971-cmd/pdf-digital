# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTableWidget

from office_export import export_linked_sheet, read_form_values, scan_keys
from pdf_werkbank import Window
from preferences import FORM_TEMPLATES
from scan_form import read_scan_text
from update_worker import digest_file


def make_scan(path, number, image_only=False):
    with pdf.open() as document:
        page = document.new_page(width=480, height=310)
        page.insert_text((35, 90), f"Kundennummer: {number}", fontsize=28)
        page.insert_text((35, 155), "Andere Nummer: 0001", fontsize=22)
        if image_only:
            raster = page.get_pixmap(matrix=pdf.Matrix(2, 2), alpha=False)
            document.delete_page(0)
            page = document.new_page(width=480, height=310)
            page.insert_image(page.rect, pixmap=raster)
        field = pdf.Widget()
        field.field_name, field.field_type = "Name", pdf.PDF_WIDGET_TYPE_TEXT
        field.rect = pdf.Rect(40, 220, 300, 250)
        field.field_flags = pdf.PDF_FIELD_IS_REQUIRED
        page.add_widget(field)
        document.save(path)


app = QApplication.instance() or QApplication([])
assert scan_keys("Kundennummer: 00012\nAndere Nummer 0001", {"0001": [2]}, "Kundennummer") == ["00012"]
assert scan_keys("X-0001 und 00012", {"0001": [2]}, "") == []
with TemporaryDirectory() as directory:
    root = Path(directory)
    target = root / "Ausgabe"
    target.mkdir()
    book = root / "Liste.xlsx"
    export_linked_sheet([("0001", "Anna", "Anna"), ("0002", "Berta", "Berta"),
                         ("0003", "Dora", "Dora"), ("0003", "Eva", "Eva"), ("0004", "Mara", "Mara")],
                        book, "Daten", ["Kundennummer", "Name", "Dateiname"], [20] * 3)
    paths = [root / f"{stem}.pdf" for stem in ("B", "A", "Doppelt", "Fehlt", "A-Kopie")]
    for i, (path, number) in enumerate(zip(paths, ("0002", "0001", "0003", "9999", "0001"))):
        make_scan(path, number, image_only=i == 0)
    checksums = {path: digest_file(path) for path in paths}
    workbook_checksum = digest_file(book)
    window = Window()
    window.favorite_settings = QSettings(str(root / "prefs.ini"), QSettings.Format.IniFormat)
    window.favorite_settings.setValue(FORM_TEMPLATES, '{"Kunden":[["Name",["Name"],true]]}')
    tessdata = Path(os.environ["PDF_WERKBANK_QA_TESSDATA"]) if "PDF_WERKBANK_QA_TESSDATA" in os.environ else None
    with pdf.open(paths[0]) as doc:
        assert not doc[0].get_text().strip()

    def read_text(doc, data):
        return read_scan_text(doc, tessdata or data)

    def review(dialog):
        table = dialog.findChild(QTableWidget)
        assert [table.item(i, 2).text() for i in range(5)] == ["0002", "0001", "0003", "9999", "0001"]
        assert table.item(0, 3).text() == "3" and table.item(0, 0).checkState() == Qt.CheckState.Checked
        assert table.item(1, 0).checkState() == table.item(4, 0).checkState() == Qt.CheckState.Unchecked
        assert "mehrfach" in table.item(1, 4).text()
        assert "Mehrere" in table.item(2, 4).text()
        assert "Keine passende" in table.item(3, 4).text()
        table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        table.cellDoubleClicked.emit(3, 1)
        assert table.item(3, 3).text() == "6" and table.item(3, 4).text() == "Von Hand bestätigt"
        dialog.accept()
        return dialog.result()

    with patch("pdf_werkbank.QFileDialog.getOpenFileNames", return_value=(list(map(str, paths)), "")), \
            patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(book), "")), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(target)), \
            patch("pdf_werkbank.QInputDialog.getItem", side_effect=[("Kundennummer", True), ("Kunden", True), ("Zeile 6 · 0004", True)]), \
            patch("pdf_werkbank.QInputDialog.getText", return_value=("Kundennummer", True)), \
            patch("pdf_werkbank.read_scan_text", side_effect=read_text), \
            patch.object(QDialog, "exec", review), patch.object(QMessageBox, "exec", return_value=0), \
            patch.object(window, "error", side_effect=AssertionError):
        window.scan_excel_batch()
    assert {path.stem: read_form_values(path)["Name"] for path in target.glob("*.pdf")} == {
        "Anna": "Anna", "Berta": "Berta", "Mara": "Mara"}
    assert all(digest_file(path) == checksum for path, checksum in checksums.items())
    assert digest_file(book) == workbook_checksum
    window.close()

print("OCR, vertauschte Reihenfolge, doppelte Kennungen, manuelle Zuordnung und ausgefüllte PDFs geprüft.")

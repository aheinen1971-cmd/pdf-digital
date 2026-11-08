# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget

from office_export import export_linked_sheet, read_form_sheet, read_form_values, scan_keys
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
    report = root / "Zuordnungsprotokoll.xlsx"
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
        if dialog.windowTitle().startswith("Excel-Zeile zuordnen"):
            excerpts, excel = dialog.findChildren(QPlainTextEdit)
            assert "Kundennummer: 9999" in excerpts.toPlainText()
            candidates = dialog.findChild(QComboBox)
            candidates.setCurrentIndex(candidates.findData(6))
            assert "Kennung in A6" in excel.toPlainText() and "Mara" in excel.toPlainText()
            dialog.accept()
            return dialog.result()
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
        (target / "Anna.pdf").write_bytes(b"Vorhandene Datei")
        dialog.accept()
        return dialog.result()

    def finish(dialog):
        next(button for button in dialog.buttons() if button.text() == "Zuordnungsprotokoll speichern").click()
        return dialog.result()

    with patch("pdf_werkbank.QFileDialog.getOpenFileNames", return_value=(list(map(str, paths)), "")), \
            patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(book), "")), \
            patch("pdf_werkbank.QFileDialog.getSaveFileName", return_value=(str(report), "")), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(target)), \
            patch("pdf_werkbank.QInputDialog.getItem", side_effect=[("Kundennummer", True), ("Kunden", True)]), \
            patch("pdf_werkbank.QInputDialog.getText", return_value=("Kundennummer", True)), \
            patch("pdf_werkbank.read_scan_text", side_effect=read_text), \
            patch.object(QDialog, "exec", review), patch.object(QMessageBox, "exec", finish), \
            patch.object(window, "error", side_effect=AssertionError):
        window.scan_excel_batch()
    assert (target / "Anna.pdf").read_bytes() == b"Vorhandene Datei"
    assert {path.stem: read_form_values(path)["Name"] for path in target.glob("*.pdf") if path.stem != "Anna"} == {
        "Berta": "Berta", "Mara": "Mara"}
    records = {Path(row["Originalscan"]).stem: row for _, row, _ in read_form_sheet(report, "Zuordnung")}
    assert records["B"]["Excel-Zelle"] == "A3"
    assert records["B"]["Tabellenblatt"] == "Daten"
    assert records["B"]["Zuordnung"] == "Automatisch erkannt"
    assert records["B"]["Status"] == "Gespeichert"
    assert records["B"]["Ziel-PDF"] == str(target / "Berta.pdf")
    assert records["Fehlt"]["Excel-Zelle"] == "A6"
    assert records["Fehlt"]["Gelesene Kennung"] == "9999"
    assert records["Fehlt"]["Excel-Kennung"] == "0004"
    assert records["Fehlt"]["Zuordnung"] == "Von Hand bestätigt"
    assert records["Doppelt"]["Status"] == "Übersprungen"
    assert records["Doppelt"]["Grund"] == "Mehrere Kennungen oder Excel-Zeilen"
    assert records["A"]["Status"] == "Speichern fehlgeschlagen"
    assert records["A"]["Grund"] == "Zieldatei existiert bereits."
    assert all(digest_file(path) == checksum for path, checksum in checksums.items())
    assert digest_file(book) == workbook_checksum
    window.close()

    settings_path = str(root / "prefs.ini")
    window = Window()
    window.favorite_settings = QSettings(settings_path, QSettings.Format.IniFormat)
    inbox = root / "Posteingang"
    inbox.mkdir()
    inbox_scan = inbox / "2026-11-08-Test.pdf"
    make_scan(inbox_scan, "0123")
    window.favorite_settings.setValue("scan/inbox-folder", str(inbox))
    with patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(book), "")), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(target)), \
            patch("pdf_werkbank.QInputDialog.getItem", side_effect=[("Kundennummer", True), ("Kunden", True)]), \
            patch("pdf_werkbank.QInputDialog.getText", return_value=("Kundennummer", True)), \
            patch.object(window, "error", side_effect=AssertionError):
        window.scan_excel_batch(configure_job=True)
    window.close()

    window = Window()
    window.favorite_settings = QSettings(settings_path, QSettings.Format.IniFormat)
    assert window.windowTitle().startswith("PDF Digital ")
    assert window.pending_scan_job(window.scan_drafts()) == [inbox_scan]

    def check_job(dialog):
        table = dialog.findChild(QTableWidget)
        if (target / "Testkunde.pdf").exists():
            raise AssertionError("Eine Ausgabe existiert bereits vor der Freigabe.")
        if table.item(0, 0).checkState() == Qt.CheckState.Checked:
            assert table.item(0, 4).text() == "Eindeutig zugeordnet"
            dialog.accept()
        else:
            assert table.item(0, 4).text() == "Keine passende Excel-Zeile erkannt"
            dialog.reject()
        return dialog.result()

    with patch.object(QDialog, "exec", check_job), patch.object(QMessageBox, "exec", return_value=0), \
            patch.object(window, "error", side_effect=AssertionError):
        window.show_open_work()
        overview = window.open_work_dialog
        table = overview.findChild(QTableWidget)
        job_row = next(row for row in range(table.rowCount()) if table.item(row, 0).text() == "Scan-Auftrag")
        table.cellDoubleClicked.emit(job_row, 0)
        assert window.pending_scan_job(window.scan_drafts()) == [inbox_scan]
        export_linked_sheet([("0123", "Testkunde", "Testkunde")], book, "Daten",
                            ["Kundennummer", "Name", "Dateiname"], [20] * 3)
        table.cellDoubleClicked.emit(job_row, 0)
        assert read_form_values(target / "Testkunde.pdf")["Name"] == "Testkunde"
        assert all(table.item(row, 0).text() != "Scan-Auftrag" for row in range(table.rowCount()))
        overview.close()
    window.close()

    window = Window()
    window.favorite_settings = QSettings(settings_path, QSettings.Format.IniFormat)
    assert inbox_scan.is_file() and window.scan_drafts() == [inbox_scan]
    assert window.pending_scan_job(window.scan_drafts()) == []
    result_pdf = target / "Testkunde.pdf"
    assert window.open_path(result_pdf)

    def press(dialog, label):
        next(button for button in dialog.findChildren(QPushButton) if button.text() == label).click()

    def sources(dialog):
        if dialog.windowTitle() == "Quellen der fertigen PDF":
            press(dialog, "Zur Excel-Zelle")
            press(dialog, "Originalscan öffnen")
        else:
            assert dialog.windowTitle() == "Excel · Daten · A2"
            sheet = dialog.findChild(QTableWidget)
            assert sheet.currentItem().text() == "0123"
            assert sheet.item(0, 1).text() == "Testkunde"
            dialog.reject()
        return dialog.result()

    with patch.object(QDialog, "exec", sources), patch.object(window, "error", side_effect=AssertionError):
        window.show_scan_sources()
        assert window.current.path == inbox_scan

    identical = inbox / "2026-11-08-Kopie.pdf"
    identical.write_bytes(inbox_scan.read_bytes())
    same_key = inbox / "2026-11-08-Neuer-Scan.pdf"
    make_scan(same_key, "0123")
    fresh = inbox / "2026-11-08-Neu.pdf"
    make_scan(fresh, "4321")
    fresh_copy = inbox / "2026-11-08-Neu-Kopie.pdf"
    fresh_copy.write_bytes(fresh.read_bytes())
    export_linked_sheet([("0123", "Testkunde", "Testkunde"), ("4321", "Neu", "Neu"), ("7654", "Alternativ", "Alternativ")],
                        book, "Daten", ["Kundennummer", "Name", "Dateiname"], [20] * 3)

    def duplicates(dialog):
        if dialog.windowTitle().startswith("Excel-Zeile zuordnen"):
            candidates = dialog.findChild(QComboBox)
            candidates.setCurrentIndex(candidates.findData(4))
            dialog.accept()
            return dialog.result()
        grid = dialog.findChild(QTableWidget)
        by_name = {grid.item(row, 1).text(): row for row in range(grid.rowCount())}
        assert "Datei bereits verarbeitet" in grid.item(by_name[identical.name], 4).text()
        assert "Kennung bereits verarbeitet" in grid.item(by_name[same_key.name], 4).text()
        for path in (fresh, fresh_copy):
            assert "Dateiidentischer Scan im Stapel" in grid.item(by_name[path.name], 4).text()
        assert all(grid.item(row, 0).checkState() == Qt.CheckState.Unchecked for row in by_name.values())
        grid.cellDoubleClicked.emit(by_name[fresh_copy.name], 1)
        grid.item(by_name[fresh.name], 0).setCheckState(Qt.CheckState.Checked)
        grid.item(by_name[fresh_copy.name], 0).setCheckState(Qt.CheckState.Checked)
        save = next(button for button in dialog.findChildren(QPushButton)
                    if button.text() == "Auswahl als PDF-Kopien speichern")
        assert not save.isEnabled()
        grid.selectRow(by_name[identical.name])
        press(dialog, "Bisherige PDF öffnen")
        return dialog.result()

    with patch.object(QDialog, "exec", duplicates), patch.object(window, "error", side_effect=AssertionError):
        window.run_inbox_scan_job()
    assert window.current.path == result_pdf
    assert not (target / "Neu.pdf").exists()

    export_linked_sheet([("0000", "Andere Zeile", "Andere")], book, "Daten",
                        ["Kundennummer", "Name", "Dateiname"], [20] * 3)
    record = next(record for record in window.scan_sources().values() if record["value"] == "0123")
    with patch.object(window, "error") as error:
        window.show_scan_excel_cell(window, record, book)
        assert "passt nicht mehr" in str(error.call_args.args[0])
    while window.sessions:
        window.close_tab(0)
    window.close()

print("OCR, Scan-Auftrag, Dubletten im und nach dem Stapel, Originalscan und Excel-Zellensprung geprüft.")

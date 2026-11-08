# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree.ElementTree import fromstring, tostring
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QTableWidget

from form_run import FormRun
from office_export import (CORRECTION_SHEET, correction_changes, export_linked_sheet, read_form_sheet,
                           read_form_values)
from pdf_werkbank import Window
from preferences import FORM_TEMPLATES
from update_worker import digest_file


def button(dialog, title):
    return next(item for item in dialog.findChildren(QPushButton) if item.text() == title)


def edit_xlsx(source, target, replacements):
    with ZipFile(source) as old, ZipFile(target, "w") as new:
        for name in old.namelist():
            payload = old.read(name)
            if name == "xl/worksheets/sheet1.xml":
                sheet = fromstring(payload)
                cells = {cell.get("r"): cell for cell in sheet.findall(".//{*}c")}
                for address, value in replacements.items():
                    cells[address].find("{*}is/{*}t").text = value
                payload = tostring(sheet)
            new.writestr(name, payload)


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    template, workbook = root / "Vorlage.pdf", root / "Liste.xlsx"
    target = root / "PDFs"
    target.mkdir()
    with pdf.open() as document:
        page = document.new_page()
        for index, name in enumerate(("ID", "Tag")):
            widget = pdf.Widget()
            widget.field_name, widget.field_type = name, pdf.PDF_WIDGET_TYPE_TEXT
            widget.rect = pdf.Rect(35, 35 + 30 * index, 180, 60 + 30 * index)
            page.add_widget(widget)
        document.save(template)
    export_linked_sheet([("Erste", "0001", "01.01.2026"), ("Zweite", "0002", "01.02.2026"),
                         ("Dritte", "0002", "02.02.2026"), ("Vierte", "0004", "31.04.2026")],
                        workbook, "Daten", ["Dateiname", "Kundennummer", "Datum"], [20] * 3)
    original = digest_file(workbook)
    config = root / "settings.ini"
    window = Window()
    window.favorite_settings = QSettings(str(config), QSettings.Format.IniFormat)
    window.favorite_settings.setValue(FORM_TEMPLATES, '{"Kunden":[["Kundennummer",["ID"],true,["unique"]],'
                                      '["Datum",["Tag"],true,["date"]]]}')
    correction = root / "Korrekturen.xlsx"

    def first(dialog):
        button(dialog, "Fehlerhafte Zeilen exportieren").click()
        dialog.accept()
        return dialog.result()

    with patch("pdf_werkbank.QFileDialog.getOpenFileName", side_effect=[(str(template), ""), (str(workbook), "")]), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(target)), \
            patch("pdf_werkbank.QInputDialog.getItem", return_value=("Kunden", True)), \
            patch("pdf_werkbank.QFileDialog.getSaveFileName", return_value=(str(correction), "")), \
            patch.object(QDialog, "exec", first), patch.object(QMessageBox, "exec", return_value=0), \
            patch.object(window, "error", side_effect=AssertionError):
        window.fill_form_batch()
    assert len(list(target.glob("*.pdf"))) == 1
    first_hash = digest_file(target / "Erste.pdf")
    assert digest_file(workbook) == original
    correction_rows = read_form_sheet(correction, CORRECTION_SHEET)
    assert [row["Werkbank-Zeile"] for _, row, _ in correction_rows] == ["3", "4", "5"]
    assert "B3" in correction_rows[0][1]["Fehler"] and "B4" in correction_rows[0][1]["Fehler"]
    with ZipFile(correction) as archive:
        sheet = fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert {cell.get("r") for cell in sheet.findall(".//{*}c") if cell.get("s") == "4"} >= {"F2", "F3", "G4"}

    corrected = root / "Bearbeitet.xlsx"
    edit_xlsx(correction, corrected, {"F3": "0003", "G4": "30.04.2026"})
    journal = next(target.glob("*.werkbanklauf"))
    run = FormRun.load(journal)
    baseline = read_form_sheet(workbook, "Daten")
    stale = root / "Veraltet.xlsx"
    edit_xlsx(corrected, stale, {"C2": "veraltet"})
    for file, finished, expected in ((stale, {}, "seit dem Export geändert"), (corrected, {3}, "bereits fertig")):
        try:
            correction_changes(file, run.data["correction_id"], baseline, baseline, run.data["columns"], finished, {})
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(expected)
    window.close()

    window = Window()
    window.favorite_settings = QSettings(str(config), QSettings.Format.IniFormat)
    visited = []

    def resume(dialog):
        table = dialog.findChild(QTableWidget)
        if dialog.windowTitle() == "Korrekturen prüfen":
            assert table.item(0, 0).text() == "B4"
            assert [table.item(0, i).text() for i in (2, 3)] == ["0002", "0003"]
            assert table.item(1, 0).text() == "C5"
            assert not FormRun.load(journal).data.get("corrections")
            visited.append("Änderungen")
            dialog.accept()
        elif not visited:
            button(dialog, "Korrekturliste übernehmen").click()
        else:
            assert [table.item(row, 3).text() for row in range(4)] == ["Gespeichert", "Bereit", "Bereit", "Bereit"]
            visited.append("Erneut geprüft")
            dialog.accept()
        return dialog.result()

    with patch("pdf_werkbank.QFileDialog.getOpenFileName", side_effect=[(str(journal), ""), (str(corrected), "")]), \
            patch.object(QDialog, "exec", resume), patch.object(QMessageBox, "exec", return_value=0), \
            patch.object(window, "error", side_effect=AssertionError):
        window.resume_form_fill()
    assert visited == ["Änderungen", "Erneut geprüft"]
    assert digest_file(workbook) == original and digest_file(target / "Erste.pdf") == first_hash
    assert read_form_values(target / "Dritte.pdf")["ID"] == "0003"
    assert read_form_values(target / "Vierte.pdf")["Tag"] == "30.04.2026"
    assert set(FormRun.load(journal).data["corrections"]) == {"4", "5"}
    window.close()

print("Markierte Korrekturliste, Änderungsvorschau, Schutz vor veralteten Listen und Fortsetzen geprüft.")

# SPDX-License-Identifier: AGPL-3.0-only
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree.ElementTree import SubElement, fromstring, tostring
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox, QPushButton, QTableWidget

from office_export import export_linked_sheet, parse_form_date, read_form_sheet, read_form_values, serial_date
from pdf_werkbank import Window
from preferences import form_templates
from update_worker import digest_file


def fixture(path):
    with pdf.open() as doc:
        page = doc.new_page()
        for index, name in enumerate(("ID", "Datum")):
            widget = pdf.Widget()
            widget.field_name, widget.field_type = name, pdf.PDF_WIDGET_TYPE_TEXT
            widget.rect = pdf.Rect(40, 40 + index * 30, 170, 62 + index * 30)
            page.add_widget(widget)
        doc.save(path)


def workbook(path, ids, dates, serial=False):
    source = path.with_suffix(".stage.xlsx")
    export_linked_sheet([(str(i), code, day) for i, (code, day) in enumerate(zip(ids, dates), 1)],
                        source, "Daten", ["Dateiname", "Kundennummer", "Geburtstag"], [20] * 3)
    if not serial:
        source.replace(path)
        return
    with ZipFile(source) as original, ZipFile(path, "w") as target:
        for name in original.namelist():
            raw = original.read(name)
            if name == "xl/worksheets/sheet1.xml":
                sheet = fromstring(raw)
                cell = next(item for item in sheet.findall(".//{*}c") if item.get("r") == "C3")
                for child in list(cell):
                    cell.remove(child)
                cell.attrib.update({"s": "1", "t": "n"})
                SubElement(cell, cell.tag.split("}")[0] + "}v").text = "40729"
                raw = tostring(sheet)
            target.writestr(name, raw)
        target.writestr("xl/styles.xml", '<styleSheet><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
    source.unlink()


app = QApplication.instance() or QApplication([])
assert serial_date("40729", False) == serial_date("39267", True) == date(2011, 7, 5)
assert serial_date("60", False) is None and serial_date("1.5", False) is None
assert parse_form_date("29.02.2024") == date(2024, 2, 29)
assert parse_form_date("2026-02-29") is None and parse_form_date("31.04.2026") is None
with TemporaryDirectory() as folder:
    root = Path(folder)
    source, excel, config = root / "Vorlage.pdf", root / "Liste.xlsx", root / "settings.ini"
    target = root / "Ausgabe"
    target.mkdir()
    fixture(source)
    workbook(excel, ["0001", "0002", "0002", "0004"], ["29.02.2024", "2025-06-01", "2025-06-02", "31.04.2026"])
    assert read_form_sheet(excel, "Daten").columns == {"Dateiname": "A", "Kundennummer": "B", "Geburtstag": "C"}
    window = Window()
    window.favorite_settings = QSettings(str(config), QSettings.Format.IniFormat)

    def create_template(dialog):
        dialog.findChild(QLineEdit).setText("Kunden")
        table = dialog.findChild(QTableWidget)
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Spalte hinzufügen").click()
        for index, (heading, alias, rule) in enumerate((("Kundennummer", "ID", 3), ("Geburtstag", "Datum", 4))):
            table.item(index, 0).setText(heading)
            table.cellWidget(index, 1).setPlainText(alias)
            table.item(index, 2).setCheckState(Qt.CheckState.Checked)
            table.item(index, rule).setCheckState(Qt.CheckState.Checked)
        next(button for button in dialog.findChildren(QPushButton)
             if button.text() == "Vorlage speichern und Vorschau").click()
        return dialog.result()

    with patch.object(QDialog, "exec", create_template):
        assert window.form_template_dialog(["ID", "Datum"])[0] == "Kunden"
    assert [column[3] for column in form_templates(window.favorite_settings)["Kunden"]] == [["unique"], ["date"]]
    stages = []

    def review(dialog):
        table = dialog.findChild(QTableWidget)
        stages.append([table.item(row, 3).text() for row in range(table.rowCount())])
        dialog.accept()
        return dialog.result()

    with patch("pdf_werkbank.QFileDialog.getOpenFileName", side_effect=[(str(source), ""), (str(excel), "")]), \
            patch("pdf_werkbank.QInputDialog.getItem", return_value=("Kunden", True)), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(target)), \
            patch.object(QDialog, "exec", review), patch.object(QMessageBox, "exec", return_value=0), \
            patch.object(window, "error", side_effect=AssertionError):
        window.fill_form_batch()
    assert stages[0][0] == "Bereit"
    assert "B3:" in stages[0][1] and "B4" in stages[0][1]
    assert "B4:" in stages[0][2] and "B3" in stages[0][2]
    assert "C5: Ungültiges Datum" in stages[0][3]
    first = digest_file(target / "1.pdf")
    assert len(list(target.glob("*.pdf"))) == 1
    journal = next(target.glob("*.werkbanklauf"))
    window.close()

    workbook(excel, ["0001", "0002", "0003", "0004"], ["29.02.2024", 40729, "2025-06-02", "2026-04-30"], serial=True)
    assert read_form_sheet(excel, "Daten").date_cells[3, "Geburtstag"] == date(2011, 7, 5)
    window = Window()
    window.favorite_settings = QSettings(str(config), QSettings.Format.IniFormat)
    with patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(journal), "")), \
            patch.object(QDialog, "exec", review), patch.object(QMessageBox, "exec", return_value=0), \
            patch.object(window, "error", side_effect=AssertionError):
        window.resume_form_fill()
    assert stages[1] == ["Gespeichert", "Bereit", "Bereit", "Bereit"]
    assert digest_file(target / "1.pdf") == first
    assert read_form_values(target / "2.pdf") == {"ID": "0002", "Datum": "05.07.2011"}
    assert len(list(target.glob("*.pdf"))) == 4
    window.close()

print("Prüfregeln gespeichert, Excel-Zellen exakt gemeldet und korrigierten Lauf fortgesetzt.")

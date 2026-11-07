# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree.ElementTree import SubElement, fromstring, tostring
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from office_export import fill_form, form_entry_columns, form_row_plan, read_form_sheet, workbook_sheets
from pdf_werkbank import Window
from preferences import FORM_TEMPLATES


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    source, target = root / "Vorlage.pdf", root / "Eingabe.xlsx"
    mapping = [["Kunden-ID", ["Kundennr."], True], ["Anrede", ["A", "B"], True],
               ["Zustimmung", ["JaNein"], False]]
    with pdf.open() as doc:
        page = doc.new_page()
        fields = [("Kundennr.", pdf.PDF_WIDGET_TYPE_TEXT, []),
                  ("A", pdf.PDF_WIDGET_TYPE_COMBOBOX, ["Firma, GmbH", "Frau"]),
                  ("B", pdf.PDF_WIDGET_TYPE_LISTBOX, ["Firma, GmbH", "Herr"]),
                  ("JaNein", pdf.PDF_WIDGET_TYPE_CHECKBOX, [])]
        for index, (name, kind, options) in enumerate(fields):
            widget = pdf.Widget()
            widget.field_name, widget.field_type = name, kind
            widget.rect = pdf.Rect(50, 50 + index * 30, 200, 75 + index * 30)
            if options:
                widget.choice_values = options
            page.add_widget(widget)
        doc.save(source)

    window = Window()
    window.favorite_settings = QSettings(str(root / "prefs.ini"), QSettings.Format.IniFormat)
    window.favorite_settings.setValue(FORM_TEMPLATES,
        '{"Kunden":[["Kunden-ID",["Kundennr."],true],["Anrede",["A","B"],true],["Zustimmung",["JaNein"],false]]}')
    with patch("pdf_werkbank.QInputDialog.getItem", return_value=("Kunden", True)), \
            patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
            patch("pdf_werkbank.QFileDialog.getSaveFileName", return_value=(str(target), "")), \
            patch.object(window, "error", side_effect=AssertionError):
        window.create_form_entry()
    assert workbook_sheets(target) == ["Eingabe"]
    filled = root / "Ausgefüllt.xlsx"
    with ZipFile(target) as book, ZipFile(filled, "w") as saved:
        sheet_name = "xl/worksheets/sheet1.xml"
        sheet = fromstring(book.read(sheet_name))
        headers = sheet.findall("{*}sheetData/{*}row/{*}c")
        assert [cell.findtext("{*}is/{*}t") for cell in headers] == ["Kunden-ID", "Anrede", "Zustimmung", "Dateiname"]
        assert [cell.get("s") for cell in headers] == ["2", "2", "3", "3"]
        assert all(column.get("style") == "1" for column in sheet.findall("{*}cols/{*}col"))
        rules = sheet.find("{*}dataValidations")
        assert rules.get("count") == "3"
        assert [(rule.get("sqref"), rule.get("type"), rule.findtext("{*}formula1")) for rule in rules] == [
            ("A2:A501", "custom", "LEN(TRIM(A2))>0"), ("B2:B501", "list", "Auswahl_2"),
            ("C2:C501", "list", "Auswahl_3")]
        workbook = fromstring(book.read("xl/workbook.xml"))
        assert workbook.findall("{*}sheets/{*}sheet")[1].get("state") == "hidden"
        assert [item.text for item in workbook.findall("{*}definedNames/{*}definedName")] == [
            "'_Auswahl'!$B$2:$B$2", "'_Auswahl'!$C$2:$C$3"]
        lookup = fromstring(book.read("xl/worksheets/sheet2.xml"))
        assert [item.text for item in lookup.findall(".//{*}t") if item.text] == ["Firma, GmbH", "Ja", "Nein"]
        styles = fromstring(book.read("xl/styles.xml"))
        assert styles.findall("{*}cellXfs/{*}xf")[1].get("numFmtId") == "49"
        namespace = sheet.tag.split("}")[0] + "}"
        row = SubElement(sheet.find("{*}sheetData"), namespace + "row", r="2")
        for column, value in zip("ABCD", ["0007", "Firma, GmbH", "Ja", "Kunde"]):
            cell = SubElement(row, namespace + "c", r=f"{column}2", t="inlineStr")
            SubElement(SubElement(cell, namespace + "is"), namespace + "t").text = value
        for name in book.namelist():
            saved.writestr(name, tostring(sheet) if name == sheet_name else book.read(name))
    rows = read_form_sheet(filled, "Eingabe")
    with pdf.open(source) as doc:
        plan = form_row_plan(doc, mapping, rows, root, source)
        assert plan[0][1] == "Kunde.pdf" and not plan[0][4]
        fill_form(doc, plan[0][3])
        values = {field.field_name: field.field_value for field in doc[0].widgets()}
        assert values == {"Kundennr.": "0007", "A": "Firma, GmbH", "B": "Firma, GmbH", "JaNein": "Yes"}
        assert len(form_entry_columns(doc, [["dateiname", ["Kundennr."], False]])) == 1
    window.close()

print("Excel-Eingabevorlage, Pflichtmarkierung, Auswahllisten und Rückimport ins PDF geprüft.")

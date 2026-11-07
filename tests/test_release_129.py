# SPDX-License-Identifier: AGPL-3.0-only
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QLabel, QPlainTextEdit, QPushButton, QTableWidget

from pdf_werkbank import Window
from plan_measure import CountedSymbol, Measurement, add_cutout, decode_plans, encode_plans, measurement_group_totals, polygon_area


def ring(*points):
    return tuple(map(pdf.Point, points))


app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    source = root / "Plan.pdf"
    with pdf.open() as doc:
        doc.new_page(width=400, height=300)
        doc.new_page(width=400, height=300)
        doc.save(source)
    original = source.read_bytes()
    window = Window()
    window.measurement_store = lambda: root / "Messungen"
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    session = window.open_path(str(source))
    first = window.current_plan(create=True)
    first.scale = 0.05
    polygon = ring((20, 20), (220, 20), (220, 140), (20, 140))
    hole = ring((40, 40), (80, 40), (80, 80), (40, 80))
    first.marks.append(add_cutout(Measurement(polygon, polygon_area(polygon), True, "Boden"), hole))
    first.marks.append(Measurement(ring((30, 170), (130, 170)), 100, name="=1+1"))
    first.symbols.append(CountedSymbol(pdf.Point(60, 60), "Steckdose"))
    window.go_page(1)
    second = window.current_plan(create=True)
    second.symbols.extend((CountedSymbol(pdf.Point(50, 60), "Steckdose"),
                           CountedSymbol(pdf.Point(90, 60), "Leuchte")))

    def handle(dialog):
        if dialog.windowTitle() == "Raum und Gewerk zuweisen":
            room, trade = dialog.findChildren(QComboBox)
            room.setEditText("Küche")
            trade.setEditText("Bodenbelag")
            return QDialog.DialogCode.Accepted
        table = dialog.findChild(QTableWidget)
        assert table.rowCount() == 5 and table.columnCount() == 7
        table.selectRow(0)
        flags = QItemSelectionModel.SelectionFlag
        table.selectionModel().select(table.model().index(1, 0), flags.Select | flags.Rows)
        next(button for button in dialog.findChildren(QPushButton) if "Gewerk zuweisen" in button.text()).click()
        assert table.item(1, 5).text() == "Küche" and table.item(1, 6).text() == "Bodenbelag"
        table.item(1, 5).setText("küche")
        table.item(1, 6).setText("Bodenbelag")
        for row, room in ((2, "Küche"), (3, "Küche"), (4, "Flur")):
            table.item(row, 5).setText(room)
            table.item(row, 6).setText("Elektro")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Messliste speichern").click()
        return QDialog.DialogCode.Rejected

    with patch.object(QDialog, "exec", handle):
        window.measurement_list()
    assert first.marks[0].room == "Küche" and second.symbols[-1].trade == "Elektro"
    groups = {(row[0], row[1]): row[2:] for row in measurement_group_totals(session.doc, session.plan_measurements)}
    kitchen = groups[("Küche", "Bodenbelag")]
    assert all(math.isclose(actual, expected) for actual, expected in zip(kitchen, (56, 5, 0, 32, 8)))
    assert groups[("Küche", "Elektro")] == (0, 0, 2, 0, 0)
    assert groups[("Flur", "Elektro")] == (0, 0, 1, 0, 0)

    old = json.loads(encode_plans(session.doc, session.plan_measurements))
    for page in old["pages"]:
        for mark in page["marks"]:
            mark.pop("room")
            mark.pop("trade")
        page["symbols"] = [symbol[:3] for symbol in page["symbols"]]
    old_plans = decode_plans(session.doc, json.dumps(old))
    assert all(not item.room and not item.trade for plan in old_plans.values() for item in (*plan.marks, *plan.symbols))

    workbook = root / "Aufmaß.xlsx"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(workbook), "")):
        window.export_measurement_excel()
    with ZipFile(workbook) as archive:
        book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        assert [sheet.get("name") for sheet in book.findall(".//{*}sheet")] == ["Messliste", "Gruppensummen"]
        details = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        summary = ElementTree.fromstring(archive.read("xl/worksheets/sheet2.xml"))
        assert details.find(".//{*}f") is None
        assert any(item.text == "=1+1" for item in details.findall(".//{*}t"))
        cells = {cell.get("r"): cell for cell in summary.findall(".//{*}c")}
        assert math.isclose(float(cells["C3"].findtext("{*}v")), 56)
        assert math.isclose(float(cells["D3"].findtext("{*}v")), 5)
        assert cells["E4"].get("t") == "n" and cells["E4"].findtext("{*}v") == "2"
    if os.environ.get("PDF_WERKBANK_QA_GROUP"):
        (Path(os.environ["PDF_WERKBANK_QA_GROUP"]) / "Aufmass-Gruppen.xlsx").write_bytes(workbook.read_bytes())
    window.close_tab(0)
    window.open_path(str(source), page=1)
    assert window.current_plan().symbols[-1].room == "Flur"
    assert source.read_bytes() == original

    action = next(action for action in window.favorite_toolbar.actions() if action.text() == "Info")
    visited = []
    def info_dialog(dialog):
        visited.append(dialog.windowTitle())
        if dialog.windowTitle() == "Info zu PDF Werkbank":
            assert any("1.29.0" in label.text() for label in dialog.findChildren(QLabel))
            assert any(button.text() == "Update-Info" for button in dialog.findChildren(QPushButton))
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "Lizenz anzeigen").click()
        else:
            assert "GNU AFFERO GENERAL PUBLIC LICENSE" in dialog.findChild(QPlainTextEdit).toPlainText()
        return QDialog.DialogCode.Rejected
    with patch.object(QDialog, "exec", info_dialog):
        action.trigger()
    assert visited == ["Info zu PDF Werkbank", "Lizenz · PDF Werkbank"]
    window.close_tab(0)

print("Raum- und Gewerkezuweisung, alte Projekte, numerische Excel-Gruppensummen und Info-Bereich geprüft.")

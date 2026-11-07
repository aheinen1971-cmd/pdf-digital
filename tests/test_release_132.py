# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QLineEdit, QListWidget, QMessageBox,
                               QPushButton, QTableWidget)

from office_export import map_form_values
from pdf_werkbank import Window
from preferences import form_templates, validate_form_templates
from work_backup import backup_check_rows, read_backup, restore_backup, write_backup


def fixture(path, fields):
    with pdf.open() as document:
        page = document.new_page()
        for index, (name, value) in enumerate(fields.items()):
            field = pdf.Widget()
            field.field_name = name
            field.field_value = value
            field.field_type = pdf.PDF_WIDGET_TYPE_TEXT
            field.rect = pdf.Rect(50, 50 + 25 * index, 200, 70 + 25 * index)
            page.add_widget(field)
        document.save(path)


def button(dialog, text):
    return next(item for item in dialog.findChildren(QPushButton) if item.text() == text)


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    config = root / "templates.ini"
    window = Window()
    window.favorite_settings = QSettings(str(config), QSettings.Format.IniFormat)
    files = [root / f"{name}.pdf" for name in ("Alias", "Gleich", "Widerspruch", "Leer", "Fehlt")]
    for path, data in zip(files, ({"Kundennr.": "=001"}, {"Kundennr.": "002", "Kundennummer": "002"},
                                  {"Kundennr.": "003", "Kundennummer": "004"},
                                  {"Kundennummer": " "}, {"Anderes Feld": "Wert"})):
        fixture(path, data)
    target = root / "Vorlage.xlsx"
    snapshots = []
    reuse = False

    def interact(dialog):
        title = dialog.windowTitle()
        table = dialog.findChild(QTableWidget)
        if title == "Excel-Formularvorlagen":
            if reuse:
                assert dialog.findChild(QComboBox).currentText() == "Kundenliste"
                assert table.cellWidget(0, 1).toPlainText().splitlines() == ["Kundennr.", "Kundennummer"]
                button(dialog, "Gespeicherte Vorlage verwenden").click()
            else:
                save = button(dialog, "Vorlage speichern und Vorschau")
                save.click()
                assert not form_templates(window.favorite_settings)
                dialog.findChild(QLineEdit).setText("Kundenliste")
                fields = dialog.findChild(QListWidget)
                for alias in ("Kundennr.", "Kundennummer"):
                    fields.itemDoubleClicked.emit(fields.findItems(alias, Qt.MatchFlag.MatchExactly)[0])
                table.item(0, 0).setText("Kunden-ID")
                table.item(0, 2).setCheckState(Qt.CheckState.Checked)
                save.click()
        else:
            raise AssertionError(title)
        return dialog.result()

    def save_preview():
        dialog = window.form_preview
        table = dialog.findChild(QTableWidget)
        assert dialog.windowTitle() == "Formular-Export prüfen – Kundenliste"
        assert table.horizontalHeaderItem(2).text() == "Kunden-ID"
        snapshots.append([(table.item(row, 2).text(), table.item(row, 2).toolTip()) for row in range(5)])
        button(dialog, "Mit Hinweisen speichern").click()

    with patch.object(QDialog, "exec", interact), \
            patch("pdf_werkbank.QFileDialog.getOpenFileNames", return_value=(list(map(str, files)), "")), \
            patch("pdf_werkbank.QFileDialog.getSaveFileName", return_value=(str(target), "")), \
            patch.object(window, "error", side_effect=AssertionError):
        window.export_form_batch(use_template=True)
        save_preview()
        window.favorite_settings = QSettings(str(config), QSettings.Format.IniFormat)
        reuse = True
        window.export_form_batch(use_template=True)
        save_preview()

    assert snapshots[0] == snapshots[1]
    preview = snapshots[0]
    assert [cell[0] for cell in preview] == ["=001", "002", "Widerspruch: Kunden-ID",
                                             "Pflichtwert leer: Kunden-ID", "Pflichtwert fehlt: Kunden-ID"]
    assert "Kundennr.: 003" in preview[2][1] and "Kundennummer: 004" in preview[2][1]
    with ZipFile(target) as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        cells = {cell.get("r"): cell for cell in sheet.findall(".//{*}c")}
        assert cells["C2"].get("t") == "inlineStr" and cells["C2"].find(".//{*}t").text == "=001"
        assert cells["C3"].find(".//{*}t").text == "002"
        assert all(address not in cells for address in ("C4", "C5", "C6"))
        assert cells["B4"].find(".//{*}t").text == "Widerspruch: Kunden-ID"
        assert not sheet.findall(".//{*}f")

    templates = form_templates(window.favorite_settings)
    values, issues = map_form_values({"x": ("A", "B")}, [["X", ["x"], False], ["Y", ["y"], False]])
    assert values == [None, None] and set(issues) == {0}
    for malformed in ({"A": [["Hinweis", ["x"], False]]},
                      {"A": [["X", [], True]]},
                      {"A": [["X", ["x"], True], ["x", ["y"], False]]}):
        try:
            validate_form_templates(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError(malformed)
    backup = root / "Backup.werkbankbackup"
    write_backup(backup, window.favorite_settings, root / "plans", "Vorlagen prüfen")
    data = read_backup(backup, "Vorlagen prüfen")
    assert ("Excel-Formularvorlage", "Kundenliste", "Lesbar") in backup_check_rows(data)
    fresh = QSettings(str(root / "fresh.ini"), QSettings.Format.IniFormat)
    restore_backup(data, {"settings"}, fresh, root / "plans")
    assert form_templates(fresh) == templates

    def edit_and_copy(dialog):
        dialog.findChild(QLineEdit).setText("Kopie")
        table = dialog.findChild(QTableWidget)
        table.item(0, 0).setText("Neue Überschrift")
        button(dialog, "Vorlage speichern und Vorschau").click()
        return dialog.result()

    with patch.object(QDialog, "exec", edit_and_copy):
        assert window.form_template_dialog([])[0] == "Kopie"
    assert form_templates(window.favorite_settings)["Kundenliste"] == templates["Kundenliste"]

    def delete_copy(dialog):
        combo = dialog.findChild(QComboBox)
        combo.setCurrentText("Kopie")
        button(dialog, "Vorlage löschen").click()
        dialog.reject()
        return dialog.result()

    with patch.object(QDialog, "exec", delete_copy), \
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        assert window.form_template_dialog([]) is None
    assert form_templates(window.favorite_settings) == templates
    window.close()

print("Gespeicherte Formularvorlagen, Pflichtwerte, Widersprüche, Excel und Wiederherstellung geprüft.")

# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QDate, QSettings, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDateEdit, QDialogButtonBox,
                               QLineEdit, QListWidget, QPushButton, QTableWidget)

from filing import folder_matches, indexed_matches, search_rectangles
from office_export import read_form_values
from pdf_werkbank import Window
from work_backup import backup_check_rows, read_backup, restore_backup, write_backup


def create_form(path, fields):
    with pdf.open() as document:
        document.new_page()
        page = document.new_page()
        page.insert_text((72, 695), "Beleg AB12O5 und Rechnungsbetrag")
        for index, (name, value, flags) in enumerate(fields):
            field = pdf.Widget()
            field.field_name = name
            field.field_value = value
            field.field_type = pdf.PDF_WIDGET_TYPE_TEXT
            field.field_flags = flags
            field.rect = pdf.Rect(50, 50 + index * 25, 200, 69 + index * 25)
            page.add_widget(field)
        document.save(path)


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    window = Window()
    settings = QSettings(str(root / "test.ini"), QSettings.Format.IniFormat)
    window.favorite_settings = settings
    pdfs = root / "PDFs"
    pdfs.mkdir()
    first, second = pdfs / "A.pdf", pdfs / "B.pdf"
    create_form(first, [("Name", "=1+2", 0), ("Leer", "", 0),
                        ("Geheim", "Privat", pdf.PDF_TX_FIELD_IS_PASSWORD)])
    create_form(second, [("Nachname", "Müller", 0)])
    settings.setValue("invoice-watch/note/" + "a" * 64, "Notiz")
    for char, target in (("b", first), ("c", pdfs / "Fehlt.pdf")):
        settings.setValue("invoice-watch/archived/" + char * 64, char * 64 + "|" + str(target))
    plans = root / "plans"
    plan = plans / ("b" * 64) / ("c" * 64 + ".json")
    plan.parent.mkdir(parents=True)
    plan.write_text('{"version":1,"pages":[]}', encoding="utf-8")
    backup = root / "Arbeitsstand.werkbankbackup"
    password = "Rücksicherung prüfen"
    write_backup(backup, settings, plans, password)
    expected = read_backup(backup, password)
    check = backup_check_rows(expected)
    assert ("Archivverweis", str(first), "Datei vorhanden") in check
    assert ("Archivverweis", str(pdfs / "Fehlt.pdf"), "Datei fehlt oder ist nicht erreichbar") in check
    assert any(row[0] == "Messliste" for row in check)
    saved = {key: settings.value(key) for key in settings.allKeys()}
    with patch("pdf_werkbank.QInputDialog.getItem", return_value=("Rücksicherung testen", True)), \
            patch("pdf_werkbank.QInputDialog.getText", return_value=(password, True)), \
            patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(backup), "")), \
            patch.object(window, "restore_backup_dialog") as restore, patch.object(window, "error") as error:
        QTimer.singleShot(0, lambda: app.activeModalWidget().reject())
        window.work_backup()
        restore.assert_not_called()
        error.assert_not_called()
    assert saved == {key: settings.value(key) for key in settings.allKeys()}
    assert list(plans.glob("*/*.json")) == [plan]

    for search in (folder_matches, lambda *args, **kw: indexed_matches(root / "search.sqlite", *args, **kw)):
        hits, *_ = search(pdfs, "AB1205", False, lambda _: True, fuzzy=True)
        assert len(hits) == 2 and hits[0][1] == 1 and "⟦ab12o5⟧" in hits[0][2]
        assert hits[0][3] == "ab12o5"
    with pdf.open(first) as document:
        assert search_rectangles(document[1], "ＡＢ１２Ｏ５")
        assert search_rectangles(document[1], "ab12o5")
        assert not search_rectangles(document[0], "ab12o5")
    seen = []

    def choose_search_result():
        dialog = app.activeModalWidget()
        seen.append(dialog.findChild(QListWidget).item(0).text())
        dialog.accept()

    with patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(pdfs)), \
            patch.object(window, "folder_search_options", return_value=("AB1205", False, None, ("", "", 0), True)), \
            patch.object(window.scroll, "ensureVisible") as scroll:
        QTimer.singleShot(15, choose_search_result)
        window.search_folder()
        assert window.current.page == 1 and window.search_text == "ab12o5"
        assert "⟦ab12o5⟧" in seen[0]
        scroll.assert_called_once()
    window.close_tab(0)

    assert read_form_values(first) == {"Name": "=1+2", "Leer": ""}
    assert read_form_values(second) == {"Nachname": "Müller"}
    workbook = root / "Formulare.xlsx"
    preview = []

    def approve_export():
        dialog = app.activeModalWidget()
        table = dialog.findChild(QTableWidget)
        preview.extend(table.item(0, col).text() for col in range(table.columnCount()))
        dialog.accept()

    with patch("pdf_werkbank.QFileDialog.getOpenFileNames", return_value=([str(first), str(second)], "")), \
            patch("pdf_werkbank.QFileDialog.getSaveFileName", return_value=(str(workbook), "")):
        QTimer.singleShot(15, approve_export)
        window.export_form_batch()
    assert "Fehlt" in preview and "" in preview
    with ZipFile(workbook) as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = sheet.findall(".//{*}row")
        assert len(rows) == 3 and not sheet.findall(".//{*}f")
        assert any(cell.text == "=1+2" for cell in rows[1].findall(".//{*}t"))
        assert any(cell.text == "Müller" for cell in rows[2].findall(".//{*}t"))
        assert b"Geheim" not in archive.read("xl/worksheets/sheet1.xml")

    incoming = root / "Rechnungen"
    incoming.mkdir()
    invoice = incoming / "Beleg.xml"
    invoice.write_bytes(b"<defekt>" + root.name.encode("ascii"))
    key = "invoice-watch/reminder/" + hashlib.sha256(invoice.read_bytes()).hexdigest()
    settings.setValue("invoice-watch/folder", str(incoming))
    window.show()
    window.invoice_folder()
    dialog = window._invoice_folder_window
    table = dialog.findChild(QTableWidget)
    for _ in range(350):
        if table.rowCount():
            break
        QTest.qWait(50)
    assert table.rowCount() == 1
    reminder = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Wiedervorlage")

    def set_reminder(day, enabled=True):
        editor = app.activeModalWidget()
        editor.findChild(QDateEdit).setDate(QDate(day.year, day.month, day.day))
        editor.findChild(QLineEdit).setText("Rückfrage")
        editor.findChild(QCheckBox).setChecked(enabled)
        editor.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()

    today = date.today()
    filters = dialog.findChild(QComboBox)
    for day, status, due in ((today, "Heute fällig", True), (today - timedelta(days=1), "Überfällig", True),
                              (today + timedelta(days=1), "Rückfrage", False)):
        QTimer.singleShot(0, lambda: set_reminder(day))
        reminder.click()
        assert status in table.item(0, 6).text()
        assert settings.value(key) == day.isoformat() + "|Rückfrage"
        filters.setCurrentText("Wiedervorlage fällig")
        assert table.rowCount() == int(due)
        filters.setCurrentText("Alle Wiedervorlagen")
        assert table.rowCount() == 1
    write_backup(backup, settings, plans)
    restored = QSettings(str(root / "restored.ini"), QSettings.Format.IniFormat)
    restore_backup(read_backup(backup), {"archive"}, restored, root / "restored")
    assert restored.value(key) == settings.value(key)
    QTimer.singleShot(0, lambda: set_reminder(today, False))
    reminder.click()
    assert not settings.contains(key) and table.rowCount() == 0
    dialog.done(0)
    window.close()

print("Rücksicherungstest, Suchausschnitte und Markierungen, Wiedervorlagen und Formular-XLSX geprüft.")

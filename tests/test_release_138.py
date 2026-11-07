# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QTableWidget

from filing import file_signature
from form_run import FormRun
from office_export import export_linked_sheet, read_form_values
from pdf_werkbank import Window
from update_worker import digest_file
from work_backup import automatic_backup, read_backup, write_backup


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as directory:
    root = Path(directory)
    old, new = root / "Alt", root / "Neu"
    old.mkdir()
    new.mkdir()
    names = {"invoice-watch/folder": "Rechnungen", "invoice-watch/archive": "Rechnungsarchiv",
             "scan/inbox-folder": "ScanEingang", "scan/archive-folder": "ScanArchiv",
             "form-export/run-folder": "Ausgabe"}
    for label in (*names.values(), "PDF-Vorlage", "Excel-Daten"):
        (old / label).mkdir()
    settings = QSettings(str(root / "Vorher.ini"), QSettings.Format.IniFormat)
    for key, label in names.items():
        settings.setValue(key, str(old / label))
    template, book = old / "PDF-Vorlage" / "Leer.pdf", old / "Excel-Daten" / "Liste.xlsx"
    with pdf.open() as doc:
        page = doc.new_page()
        field = pdf.Widget()
        field.field_name = "Name"
        field.field_type = pdf.PDF_WIDGET_TYPE_TEXT
        field.rect = pdf.Rect(40, 40, 150, 70)
        page.add_widget(field)
        doc.save(template)
    export_linked_sheet([("A", "Erste"), ("B", "Zweite")], book, "Eingabe", ["Name", "Dateiname"], [20, 20])
    output = old / names["form-export/run-folder"]
    run = FormRun.begin(output, template, digest_file(template), book, "Eingabe", [["Name", ["Name"], True]])
    run.remember_overview(2, file_signature(book))
    candidate = root / "candidate.pdf"
    candidate.write_bytes(template.read_bytes())
    run.publish(2, output / "Erste.pdf", {"Name": "A"}, candidate)
    first_digest = digest_file(output / "Erste.pdf")
    settings.setValue("form-export/runs", [str(run.path)])
    scan = old / "ScanEingang" / "2026-01-01-Test.pdf"
    scan.write_bytes(template.read_bytes())
    archive = old / "Rechnungsarchiv" / "Beleg.pdf"
    archive.write_bytes(template.read_bytes())
    archive_key = "invoice-watch/archived-digest/" + first_digest
    settings.setValue(archive_key, first_digest + "|" + str(archive))
    backup = root / "Arbeitsstand.werkbankbackup"
    write_backup(backup, settings, root / "Messungen", "Umzug testen")
    data = read_backup(backup, "Umzug testen")
    assert set(data["locations"]["folders"]) == set(names)
    assert data["locations"]["runs"][str(run.path)]["records"]["2"]["done"]
    automatic = automatic_backup(settings, root / "Messungen", "1.37.1", "1.38.0", root / "Automatik")
    assert read_backup(automatic)["locations"] == data["locations"]

    # Move the input/output files into the new tree, leaving the journal in the backup only.
    for label in (*names.values(), "PDF-Vorlage", "Excel-Daten"):
        folder = new / label
        folder.mkdir()
        for path in (old / label).iterdir():
            if path != run.path:
                path.replace(folder / path.name)
    run.path.unlink()
    for folder in old.iterdir():
        folder.rmdir()
    old.rmdir()
    fresh = QSettings(str(root / "Frisch.ini"), QSettings.Format.IniFormat)
    window = Window()
    window.favorite_settings = fresh
    moved_run = new / output.name / run.path.name
    map_keys = [*names.values(), "PDF-Vorlage", "Excel-Daten"]

    def restore(dialog):
        table = dialog.findChild(QTableWidget)
        assert any("Ordner fehlt" in table.item(row, 2).text() for row in range(table.rowCount()))
        button = next(button for button in dialog.findChildren(QPushButton)
                      if button.text() == "Ordner und Formularläufe neu zuordnen")
        for _ in map_keys:
            button.click()
        assert any(table.item(row, 2).text() == "Lauf übernehmen" for row in range(table.rowCount()))
        assert not fresh.allKeys() and not moved_run.exists()
        dialog.accept()
        return dialog.result()

    with patch.object(window, "measurement_store", return_value=root / "FrischeMessungen"), \
            patch.object(window, "error", side_effect=AssertionError), \
            patch("pdf_werkbank.QInputDialog.getItem", side_effect=[(str(old / label), True) for label in map_keys]), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", side_effect=[str(new / label) for label in map_keys]), \
            patch.object(QDialog, "exec", restore):
        window.restore_backup_dialog(data, fresh)
    assert all(fresh.value(key) == str(new / label) for key, label in names.items())
    assert fresh.value(archive_key) == first_digest + "|" + str(new / "Rechnungsarchiv" / "Beleg.pdf")
    assert window.scan_drafts() == [new / "ScanEingang" / scan.name]
    assert window.open_form_runs()[0][3] == moved_run
    assert fresh.value("form-export/runs", [], list) == [str(moved_run)]
    assert digest_file(moved_run.parent / "Erste.pdf") == first_digest
    assert FormRun.load(moved_run).data["workbook"] == str(new / "Excel-Daten" / book.name)

    def resume(dialog):
        table = dialog.findChild(QTableWidget)
        assert [table.item(row, 3).text() for row in range(2)] == ["Gespeichert", "Bereit"]
        dialog.accept()
        return dialog.result()

    with patch.object(QDialog, "exec", resume), patch.object(QMessageBox, "exec", return_value=0):
        window.run_form_fill(moved_run)
    assert read_form_values(moved_run.parent / "Zweite.pdf") == {"Name": "B"}
    assert digest_file(moved_run.parent / "Erste.pdf") == first_digest
    window.close()

print("Frisches Profil, verschobene Ordner, Archivzuordnung und fortgesetzter Formularlauf geprüft.")

# SPDX-License-Identifier: AGPL-3.0-only
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QMessageBox, QPushButton, QTableWidget

from form_run import FormRun
from office_export import export_linked_sheet, read_form_values
from pdf_werkbank import Window
from project_move import file_hash, read_project, write_project
from update_worker import digest_file
from work_backup import BackupPasswordRequired


def reject(path, password, error, message):
    try:
        read_project(path, password)
    except error as exc:
        assert message in str(exc)
    else:
        raise AssertionError("Ungültiges Paket wurde akzeptiert.")


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as directory:
    root = Path(directory)
    old, new = root / "Alter-PC", root / "Neuer-PC"
    old.mkdir()
    new.mkdir()
    template, workbook = old / "Formular.pdf", old / "Liste.xlsx"
    with pdf.open() as doc:
        page = doc.new_page()
        field = pdf.Widget()
        field.field_name = "Name"
        field.field_type = pdf.PDF_WIDGET_TYPE_TEXT
        field.rect = pdf.Rect(40, 40, 150, 70)
        page.add_widget(field)
        doc.save(template)
    export_linked_sheet([("A", ""), ("B", "")], workbook, "Eingabe", ["Name", "Dateiname"], [20, 20])
    run = FormRun.begin(old, template, digest_file(template), workbook, "Eingabe", [["Name", ["Name"], True]])
    candidate = root / "candidate.pdf"
    candidate.write_bytes(template.read_bytes())
    run.publish(2, old / "Formular-002.pdf", {"Name": "A"}, candidate)
    first = digest_file(old / "Formular-002.pdf")
    scan = old / "2026-01-01-Scan.pdf"
    scan.write_bytes(template.read_bytes())
    packet = root / "Umzug.werkbankprojekt"
    password = "Prüfpasswort für den Umzug"
    sender = Window()
    sender.favorite_settings = QSettings(str(old / "prefs.ini"), QSettings.Format.IniFormat)

    def choose(dialog):
        next(button for button in dialog.findChildren(QPushButton)
             if button.text() == "Formularlauf auswählen").click()
        dialog.findChild(QListWidget).item(0).setCheckState(Qt.CheckState.Checked)
        dialog.accept()
        return dialog.result()

    with patch.object(sender, "scan_drafts", return_value=[scan]), \
            patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(run.path), "")), \
            patch("pdf_werkbank.QFileDialog.getSaveFileName", return_value=(str(packet), "")), \
            patch.object(QDialog, "exec", choose), patch.object(sender, "backup_password_dialog", return_value=(True, password)), \
            patch.object(sender, "project_file_preview", return_value=True) as preview:
        sender.export_project_move()
        assert len(preview.call_args.args[2]) == 4
    reject(packet, None, BackupPasswordRequired, "Passwort")
    reject(packet, "Falsches Passwort", ValueError, "falsch")
    changed = root / "Manipuliert.werkbankprojekt"
    changed.write_bytes(packet.read_bytes()[:-1] + bytes([packet.read_bytes()[-1] ^ 1]))
    reject(changed, password, ValueError, "beschädigt")
    plain = root / "NurScans.werkbankprojekt"
    write_project(plain, None, {"Scans/" + scan.name: (scan, None)})
    assert read_project(plain)[0] is None
    unsafe = root / "Unsicher.werkbankprojekt"
    with ZipFile(unsafe, "w") as archive:
        archive.writestr("projekt.json", json.dumps({"version": 1, "run": None, "files": {"Scans/../f.pdf": file_hash(b"a")}}))
        archive.writestr("Scans/../f.pdf", b"a")
    reject(unsafe, None, ValueError, "Dateiname")
    for path in old.iterdir():
        path.unlink()
    old.rmdir()
    sender.close()

    receiver = Window()
    receiver.favorite_settings = QSettings(str(new / "prefs.ini"), QSettings.Format.IniFormat)
    inbox = new / "Scan-Posteingang"
    inbox.mkdir()
    existing = inbox / scan.name
    existing.write_bytes(b"vorhanden")
    receiver.favorite_settings.setValue("scan/inbox-folder", str(inbox))
    with patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(packet), "")), \
            patch("pdf_werkbank.QInputDialog.getText", return_value=(password, True)), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(new)), \
            patch.object(receiver, "project_file_preview", side_effect=lambda *args: args[-1]) as preview:
        receiver.import_project_move()
        receiver.import_project_move()
        assert preview.call_args.args[-1] is False
    assert existing.read_bytes() == b"vorhanden" and len(list(inbox.glob("*.pdf"))) == 2
    receiver.close()
    receiver = Window()
    receiver.favorite_settings = QSettings(str(new / "prefs.ini"), QSettings.Format.IniFormat)
    receiver.show_open_work()
    dashboard = receiver.open_work_dialog
    table = dashboard.findChild(QTableWidget)
    assert table.item(0, 0).text() == "Formularlauf"
    imported = new / "Umzug – Projekt" / "Ausgabe" / "Formularlauf.werkbanklauf"
    assert imported.is_file() and receiver.favorite_settings.value("form-export/runs", [], list) == [str(imported)]

    def resume(dialog):
        rows = dialog.findChild(QTableWidget)
        assert [rows.item(index, 3).text() for index in range(2)] == ["Gespeichert", "Bereit"]
        dialog.accept()
        return dialog.result()

    with patch.object(QDialog, "exec", resume), patch.object(QMessageBox, "exec", return_value=0), \
            patch.object(receiver, "error", side_effect=AssertionError):
        table.cellDoubleClicked.emit(0, 0)
    assert digest_file(imported.parent / "Formular-002.pdf") == first
    assert read_form_values(imported.parent / "Formular-003.pdf") == {"Name": "B"}
    assert not receiver.open_form_runs()
    receiver.close()

print("Geschützter Projektumzug, Konflikte, Scan-Import und Fortsetzen aus Offene Arbeiten geprüft.")

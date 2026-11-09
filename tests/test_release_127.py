# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QFileDialog, QInputDialog, QPushButton, QTableWidget

import pdf_werkbank as werkbank
from pdf_werkbank import Window
from plan_measure import Measurement
from update_worker import digest_file
from work_backup import archive_remap, automatic_backup, backup_preview, read_backup, restore_backup

app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
    notes = "invoice-watch/note/" + "a" * 64
    settings.setValue(notes, "Rückfrage an den Lieferanten")
    plans = root / "Messungen"
    relative = "b" * 64 + "/" + "c" * 64 + ".json"
    existing = plans / relative
    existing.parent.mkdir(parents=True)
    existing.write_text('{"version":1,"pages":[]}', encoding="utf-8")
    saved_time = existing.stat().st_mtime_ns
    old, new = root / "Altes Archiv", root / "Neues Archiv"
    (new / "2026").mkdir(parents=True)
    verified = new / "2026" / "Beleg.xml"
    verified.write_bytes(b"correct source")
    checksum = digest_file(verified)
    key = "invoice-watch/archived/" + "d" * 64
    missing = "invoice-watch/archived/" + "e" * 64
    wrong = new / "2026" / "Falsch.xml"
    wrong.write_bytes(b"wrong source")
    settings.setValue(key, f"{checksum}|{old / '2026' / 'Beleg.xml'}")
    settings.setValue(missing, f"{checksum}|{old / '2026' / 'Falsch.xml'}")
    settings.setValue("invoice-watch/archive", str(old))
    target = automatic_backup(settings, plans, "1.26.0", "1.27.0", root / "Sicherungen")
    initial = target.read_bytes()
    backup = read_backup(target)
    assert backup["archive"][notes] == "Rückfrage an den Lieferanten"
    assert backup["archive_root"] == str(old) and backup["plan_times"][relative] == saved_time
    with patch("work_backup.write_backup", side_effect=OSError("Speicher voll")):
        try:
            automatic_backup(settings, plans, "1.26.0", "1.27.0", target.parent, refresh=True)
        except OSError:
            pass
        else:
            raise AssertionError("Fehlgeschlagene Sicherung wurde akzeptiert.")
    assert target.read_bytes() == initial
    assert automatic_backup(settings, plans, "1.26.0", "1.27.0", target.parent) == target
    existing.write_text('{"version":1,"pages":[{"page":0}]}', encoding="utf-8")
    newer = saved_time + 5_000_000_000
    os.utime(existing, ns=(newer, newer))
    rows = backup_preview(backup, settings, plans)
    assert any("Lokale Fassung ist neuer" in row[2] for row in rows)
    newer_data = existing.read_bytes()
    mapped, report = archive_remap(backup["archive"], old, new)
    assert mapped[key].endswith(str(verified)) and missing not in mapped
    assert len(report) == 2 and report[0][2] != report[1][2]
    destination = QSettings(str(root / "destination.ini"), QSettings.Format.IniFormat)
    assert restore_backup(backup, {"plans", "archive"}, destination, plans, mapped) == 1
    assert existing.read_bytes() == newer_data
    assert destination.value("invoice-watch/archived-digest/" + checksum, "", str).endswith(str(verified))

    window = Window()
    window.measurement_store = lambda: plans
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))

    def preview(dialog):
        table = dialog.findChild(QTableWidget)
        row_count = table.rowCount()
        next(box for box in dialog.findChildren(QCheckBox) if "Messlisten" in box.text()).setChecked(False)
        assert table.rowCount() < row_count
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Archivordner neu zuordnen").click()
        assert any("Datei geprüft" == table.item(row, 2).text() for row in range(table.rowCount()))
        return QDialog.DialogCode.Rejected

    with patch.object(QDialog, "exec", preview), patch.object(QInputDialog, "getText", return_value=(str(old), True)), patch.object(
            QFileDialog, "getExistingDirectory", return_value=str(new)):
        window.restore_backup_dialog(backup, destination)

    path = root / "Plan.pdf"
    with pdf.open() as doc:
        doc.new_page(width=300, height=250)
        doc.save(path)
    session = window.open_path(str(path))
    plan = window.current_plan(create=True)
    plan.scale = 0.05
    plan.marks.append(Measurement((pdf.Point(25, 80), pdf.Point(125, 80)), 100, name="Wand"))
    for x in (125, 145, 145, 165):
        window.edit_measure_point(pdf.Point(x, 80))
    assert plan.marks[-1].label(plan.scale) == "7 m"
    plan.marks[-1] = replace(plan.marks[-1], name="Neue Bezeichnung")
    window.undo_measure_point()
    assert plan.marks[-1].label(plan.scale) == "6 m" and plan.marks[-1].name == "Neue Bezeichnung"
    window.undo_measure_point()
    assert plan.marks[-1].label(plan.scale) == "5 m"
    window.close_tab(0)

    settings.setValue("update/last-active-version", "1.26.0")
    with patch.object(werkbank, "QApplication", return_value=app), patch.object(werkbank, "QSettings", return_value=settings), patch.object(
            werkbank, "prepared_update", return_value=None), patch.object(
            werkbank, "automatic_backup", side_effect=OSError("Speicher voll")), patch.dict(
            os.environ, {"PDF_WERKBANK_LAUNCHER": str(root / "Starter.exe"), "PDF_WERKBANK_UPDATE_FORWARDED": ""}):
        assert werkbank.main() == 23
    assert settings.value("update/last-active-version", "", str) == "1.26.0"

print("Automatisches Backup und Fehlerstopp, Vorschau, verifizierte Archivzuordnung und mehrstufiges Messpunkt-Rückgängig geprüft.")

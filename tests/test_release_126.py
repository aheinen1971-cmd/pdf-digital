# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QInputDialog, QPushButton, QTableWidget

from filing import file_signature
from pdf_werkbank import Window
from plan_measure import Measurement, add_cutout, move_vertex, polygon_area
from work_backup import read_backup, restore_backup, write_backup


def ring(*points):
    return tuple(map(pdf.Point, points))


area = ring((20, 20), (220, 20), (220, 120), (20, 120))
hole = ring((40, 40), (60, 40), (60, 60), (40, 60))
mark = add_cutout(Measurement(area, polygon_area(area), True, "Küche"), hole)
assert move_vertex(mark, 1, 0, pdf.Point(35, 35)).measured_area() < mark.measured_area()
try:
    move_vertex(mark, 1, 0, pdf.Point(10, 10))
except ValueError:
    pass
else:
    raise AssertionError("Ungültige Aussparung übernommen")

app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    path = root / "Plan.pdf"
    with pdf.open() as doc:
        doc.new_page(width=400, height=300)
        doc.save(path)
    window = Window()
    window.measurement_store = lambda: root / "Ursprung"
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    session = window.open_path(str(path))
    plan = window.current_plan(create=True)
    plan.scale = 0.05
    line = ring((40, 200), (140, 200))
    plan.marks.extend((mark, Measurement(line, 100, name="Wand")))
    window.measure_assist = "Aus"
    window.start_measure("Messpunkt verschieben")
    for x, y in ((140, 200), (180, 200)):
        QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    assert plan.marks[-1].label(plan.scale) == "7 m" and plan.marks[-1].name == "Wand"
    assert window.save_measurements(session)
    settings = QSettings(str(root / "origin.ini"), QSettings.Format.IniFormat)
    settings.setValue("batch/profiles", '{"Ablauf":["ocr"]}')
    note_key = "invoice-watch/note/" + "a" * 64
    settings.setValue(note_key, "Käuferreferenz angefragt")
    backup = root / "Backup.werkbankbackup"
    write_backup(backup, settings, window.measurement_store())
    data = read_backup(backup)
    migrated = QSettings(str(root / "other.ini"), QSettings.Format.IniFormat)
    target = root / "Ziel"
    restore_backup(data, {"plans", "archive"}, migrated, target)
    assert migrated.value(note_key) == "Käuferreferenz angefragt"
    assert not migrated.contains("batch/profiles")
    restore_backup(data, {"settings"}, migrated, target)
    assert "Ablauf" in migrated.value("batch/profiles", "", str)
    renamed = root / "Anderer Name.pdf"
    renamed.write_bytes(path.read_bytes())
    window.measurement_store = lambda: target
    window.open_path(str(renamed))
    assert window.current_plan().marks[-1].label(window.current_plan().scale) == "7 m"
    assert window.current.plan_source_stamp == file_signature(renamed)

    incoming = root / "Rechnungen"
    incoming.mkdir()
    source = incoming / "Defekt.xml"
    source.write_bytes(b"<defekt>" + root.name.encode("ascii"))
    settings = window.favorite_settings
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
    filters = dialog.findChild(QComboBox)
    filters.setCurrentText("Fehler")
    assert table.rowCount() == 1
    button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Notiz")
    with patch.object(QInputDialog, "getMultiLineText", return_value=("Lieferant angefragt", True)):
        button.click()
    assert table.item(0, 5).text() == "Lieferant angefragt"
    filters.setCurrentText("Neu")
    assert table.rowCount() == 1
    next(button for button in dialog.findChildren(QPushButton) if button.text() == "Prüfstatus ändern").click()
    assert table.rowCount() == 0
    filters.setCurrentText("Alle")
    assert table.item(0, 4).text() == "Gesehen"
    dialog.done(0)
    settings.remove("invoice-watch/folder")
    for item in window.sessions:
        item.doc.close()

print("Messpunktkorrektur, Aussparungsgrenzen, Backup-Auswahl und PDF-Umzug, Rechnungsfilter und Belegnotizen geprüft.")

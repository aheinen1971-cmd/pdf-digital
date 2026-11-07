# SPDX-License-Identifier: AGPL-3.0-only
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from pdf_werkbank import Window, save_copy
from plan_measure import METRES_PER_POINT, calibrated_scale, format_measure, polygon_area


def points(values):
    return tuple(pdf.Point(x, y) for x, y in values)


def invalid_area(values):
    try:
        polygon_area(points(values))
    except ValueError:
        return
    raise AssertionError("Ungültiger Flächenumriss akzeptiert")


rectangle = ((80, 90), (280, 90), (280, 210), (80, 210))
assert polygon_area(points(rectangle)) == 24000
assert polygon_area(points(rectangle[::-1])) == 24000
assert polygon_area(points(((0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)))) == 7
assert polygon_area(points(((0, 0), (1, 0), (2, 0), (2, 2), (0, 2)))) == 4
invalid_area(((0, 0), (4, 4), (0, 4), (4, 0)))
invalid_area(((0, 0), (4, 0), (1, 0), (1, 4), (0, 4)))
invalid_area(((0, 0), (1, 0), (2, 0)))
invalid_area(((0, 0), (1, 0), (1, 0), (0, 1)))
assert format_measure(1250.0, "m²") == "1.250 m²"
assert format_measure(0.000012, "m²") == "0,000012 m²"
assert math.isclose(calibrated_scale(*points(((0, 0), (3, 4))), 10), 2)

app = QApplication([])
qa = os.environ.get("PDF_WERKBANK_QA_MEASURE")
if qa:
    font = QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts/arial.ttf"))
    if font >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font)[0], 10))

with TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / "Testgrundriss.pdf"
    with pdf.open() as document:
        page = document.new_page(width=400, height=320)
        page.insert_text((50, 46), "Grundriss - Referenzkante oben 10 m", fontsize=13)
        page.draw_rect(pdf.Rect(80, 90, 280, 210), color=(0.1, 0.1, 0.1), width=1.5)
        page.draw_line((180, 90), (180, 210), color=(0.4, 0.4, 0.4), width=0.8)
        document.new_page(width=400, height=320).insert_text((50, 50), "Zweiter Plan")
        document.save(source)
    original = source.read_bytes()
    window = Window()
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    window.open_path(str(source))
    session = window.current

    def click(x, y):
        page = session.doc[session.page]
        target = pdf.Point(x, y) * page.rotation_matrix
        QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton,
                         pos=QPoint(round(target.x * window.scale), round(target.y * window.scale)))

    with patch.object(QMessageBox, "information") as missing:
        window.start_measure("Strecke messen")
        missing.assert_called_once()
    with patch.object(QInputDialog, "getDouble", return_value=(100.0, True)):
        window.set_plan_scale()
    assert math.isclose(window.current_plan().scale, 100 * METRES_PER_POINT)
    assert "Maßstab 1 : 100" in window.status.text()
    window.start_measure("Strecke messen")
    click(80, 90)
    click(280, 90)
    plan = window.current_plan()
    assert plan.marks[0].label(plan.scale) == "7,056 m"
    assert not session.changed
    window.start_measure("Kalibrieren")
    with patch.object(QInputDialog, "getDouble", return_value=(10.0, True)):
        click(80, 90)
        click(280, 90)
    assert plan.marks[0].label(plan.scale) == "10 m"

    window.zoom = 2.0
    window.render()
    window.start_measure("Fläche messen")
    for x, y in rectangle:
        click(x, y)
    QTest.keyClick(window.canvas, Qt.Key.Key_Return)
    assert len(plan.marks) == 2 and plan.marks[1].label(plan.scale) == "60 m²"
    window.start_measure("Fläche messen")
    click(80, 90)
    QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
    assert not window.measure_points and len(plan.marks) == 2

    click(80, 90)
    window.go_page(1)
    assert window.current_plan() is None and not window.measure_points
    window.go_page(0)
    assert window.current_plan() is plan
    window.edit_page("rotate", 90)
    assert window.current_plan() is plan
    window.start_measure("Strecke messen")
    click(80, 90)
    click(80, 210)
    assert plan.marks[-1].label(plan.scale) == "6 m"
    window.remove_measurement()
    assert len(plan.marks) == 2
    window.undo()
    assert session.doc[0].rotation == 0 and window.current_plan() is plan
    assert [mark.label(plan.scale) for mark in plan.marks] == ["10 m", "60 m²"]
    for angle in (90, 180, 270, 0):
        session.doc[0].set_rotation(angle)
        window.clear_page_cache(session)
        window.zoom = 1.5
        window.render()
        window.start_measure("Strecke messen")
        click(80, 90)
        click(280, 210)
        assert math.isclose(plan.marks[-1].amount * plan.scale, math.hypot(10, 6))
        window.remove_measurement()
    copy = root / "Kopie.pdf"
    save_copy(session.doc, copy)
    with pdf.open(copy) as document:
        assert not list(document[0].annots()) and "60 m²" not in document[0].get_text()
    assert source.read_bytes() == original
    if qa:
        window.zoom = 1.4
        window.render()
        window.show()
        QApplication.processEvents()
        assert window.canvas.grab().save(str(Path(qa) / "Messvorschau.png"))
    for item in window.sessions:
        item.doc.close()

print("Maßstab, Kalibrierung, Strecken, Flächen, Zoom, Drehung, Seitentrennung und PDF-Kopie geprüft.")

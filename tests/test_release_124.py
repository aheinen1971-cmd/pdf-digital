# SPDX-License-Identifier: AGPL-3.0-only
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QLabel, QMessageBox, QSlider

from pdf_werkbank import Window
from plan_measure import Measurement, alignment, measurement_rows, snap_point, vector_geometry


a, b, tx, ty = alignment(((20, 20), (20, 100)), ((10, 15), (50, 15)))
assert (a, b, tx, ty) == (0, 2, 50, 0)
try:
    alignment(((0, 0), (2, 2)), ((1, 1), (1, 1)))
except ValueError:
    pass
else:
    raise AssertionError("Identische Referenzpunkte akzeptiert.")

app = QApplication([])
qa = os.environ.get("PDF_WERKBANK_QA_MEASURE")
if qa:
    font = QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts/arial.ttf"))
    if font >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font)[0], 10))

with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    source, copy, scan = [root / (name + ".pdf") for name in ("Plan", "Plan-Kopie", "Scan")]
    with pdf.open() as doc:
        page = doc.new_page(width=400, height=320)
        page.draw_rect(pdf.Rect(80, 90, 280, 210), width=2)
        page.insert_text((70, 50), "Referenzplan")
        doc.save(source)
        with pdf.open() as images:
            images.new_page(width=400, height=320).insert_image(page.rect, pixmap=page.get_pixmap(dpi=150))
            images.save(scan)
    copy.write_bytes(source.read_bytes())
    window = Window()
    window.measurement_store = lambda: root / "Messungen"
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    session = window.open_path(str(source))
    page = session.doc[0]
    geometry = vector_geometry(page)
    assert snap_point(pdf.Point(84, 94), geometry, 9) == pdf.Point(80, 90)
    assert snap_point(pdf.Point(180, 94), geometry, 9) == pdf.Point(180, 90)
    assert snap_point(pdf.Point(200, 160), geometry, 9) is None
    for angle in (0, 90, 180, 270):
        page.set_rotation(angle)
        window.zoom = 2
        window.render()
        point = pdf.Point(82, 92) * page.rotation_matrix
        assert window.measure_position(QPointF(point.x * 2, point.y * 2)) == pdf.Point(80, 90)
    page.set_rotation(0)
    window.clear_page_cache(session)
    window.render()
    plan = window.current_plan(create=True)
    plan.scale = 0.05
    points = tuple(map(pdf.Point, ((80, 90), (280, 90), (280, 210), (80, 210))))
    plan.marks.extend([Measurement(points[:2], 200, name="=1+1"), Measurement(points, 24000, True, "Küche")])
    assert measurement_rows(session.doc, session.plan_measurements)[-2:] == [
        (None, "Summe Strecken", 10, "m"), (None, "Summe Flächen", 60.000000000000014, "m²")]
    spreadsheet = root / "Maße.xlsx"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(spreadsheet), "")):
        window.export_measurement_excel()
    with ZipFile(spreadsheet) as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert sheet.find(".//{*}f") is None
        cells = {cell.get("r"): cell for cell in sheet.findall(".//{*}c")}
        assert cells["B2"].findtext(".//{*}t") == "=1+1"
        assert cells["C4"].get("t") == "n" and math.isclose(float(cells["C4"].findtext("{*}v")), 10)
        assert cells["C5"].get("t") == "n" and math.isclose(float(cells["C5"].findtext("{*}v")), 60)

    project = root / "Plan.messprojekt"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(project), "")):
        window.transfer_measurement_project(False)
    window.open_path(str(copy))
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(project), "")):
        assert window.transfer_measurement_project(True)
    assert window.current_plan().marks[1].name == "Küche"
    window.open_path(str(scan))
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(project), "")), patch.object(window, "error") as error:
        assert not window.transfer_measurement_project(True)
        assert "anderen PDF-Fassung" in str(error.call_args.args[0])
    assert not window.measurement_geometry()[0]
    window.start_measure("Kalibrieren")
    window.measure_pointer(QPointF(180, 180))
    assert window.measure_cursor is not None
    if qa:
        window.show()
        QApplication.processEvents()
        window.measure_pointer(QPointF(180, 180))
        assert window.canvas.grab().save(str(Path(qa) / "Lupe.png"))

    revised = root / "Geänderter Grundriss.pdf"
    with pdf.open() as doc:
        page = doc.new_page(width=400, height=320)
        page.draw_rect(pdf.Rect(95, 110, 295, 230), width=2)
        page.draw_line((195, 110), (195, 230), color=(0.75, 0, 0), width=3)
        page.set_rotation(90)
        doc.save(revised)

    def compare(dialog):
        mode = dialog.findChild(QComboBox)
        slider = dialog.findChild(QSlider)
        canvas = next(label for label in dialog.findChildren(QLabel) if label.pixmap() and not label.pixmap().isNull())
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(148, 167))
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(518, 167))
        mode.setCurrentIndex(1)
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(389, 176))
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(389, 546))
        mode.setCurrentIndex(2)
        assert slider.isEnabled()
        slider.setValue(0)
        before = canvas.pixmap().toImage()
        slider.setValue(85)
        after = canvas.pixmap().toImage()
        assert before != after
        assert after.pixelColor(333, 275).red() > after.pixelColor(333, 275).green() + 70
        assert after.pixelColor(148, 250).red() < 80
        if qa:
            dialog.show()
            QApplication.processEvents()
            assert dialog.grab().save(str(Path(qa) / "Überlagerung.png"))
        return QDialog.DialogCode.Rejected

    with pdf.open(source) as fixed, pdf.open(revised) as moving, patch.object(QDialog, "exec", compare):
        window.overlay_dialog(fixed, 0, moving)
    assert copy.read_bytes() == source.read_bytes()
    for item in window.sessions:
        item.doc.close()

print("Fangpunkte, Lupe, numerischer Excel-Export, Projektaustausch und ausgerichtete Überlagerung geprüft.")

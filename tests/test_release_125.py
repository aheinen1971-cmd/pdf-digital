# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QTableWidget

from pdf_werkbank import Window, plan_difference
from plan_measure import Measurement, add_cutout, decode_plans, draw_plan_copy, encode_plans, measurement_rows, polygon_area


def ring(points):
    return tuple(map(pdf.Point, points))


outer = ring(((60, 60), (260, 60), (260, 180), (60, 180)))
hole = ring(((100, 100), (140, 100), (140, 140), (100, 140)))
mark = Measurement(outer, polygon_area(outer), area=True)
assert add_cutout(mark, hole).label(0.05) == "56 m²"
for invalid in (ring(((60, 90), (80, 90), (80, 110), (60, 110))),
                ring(((120, 120), (170, 120), (170, 160), (120, 160))),
                ring(((20, 30), (40, 30), (40, 40), (20, 40)))):
    try:
        add_cutout(add_cutout(mark, hole), invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("Ungültige Aussparung angenommen")

app = QApplication([])
image_a = QImage(20, 20, QImage.Format.Format_RGB32)
image_a.fill(QColor("white"))
image_b = image_a.copy()
for y in range(4, 16):
    image_a.setPixelColor(4, y, QColor("black"))
    image_b.setPixelColor(15, y, QColor("black"))
    image_a.setPixelColor(9, y, QColor("black"))
    image_b.setPixelColor(9, y, QColor("black"))
diff = plan_difference(image_a, image_b)
assert diff.pixelColor(4, 9) == QColor(194, 32, 47)
assert diff.pixelColor(15, 9) == QColor(0, 112, 67)
assert diff.pixelColor(9, 9).red() == diff.pixelColor(9, 9).green()

with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    path = root / "Plan.pdf"
    with pdf.open() as doc:
        page = doc.new_page(width=360, height=260)
        page.insert_text((50, 40), "Beispiel-Aufmaß", fontsize=13)
        doc.new_page(width=360, height=260)
        doc.save(path)
    original = path.read_bytes()
    window = Window()
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    window.measurement_store = lambda: root / "Messungen"
    session = window.open_path(str(path))
    plan = window.current_plan(create=True)
    plan.scale = 0.05
    plan.marks.append(mark)
    window.start_measure("Aussparung messen")
    for point in hole:
        QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(int(point.x), int(point.y)))
    QTest.keyClick(window.canvas, Qt.Key.Key_Return)
    assert plan.marks[-1].label(plan.scale) == "56 m²"
    assert window.area_edges(plan.marks[-1], plan.scale) == ("32 m", "8 m")
    assert "Netto: 56 m²" in window.statusBar().currentMessage()
    window.go_page(1)
    window.symbol_category = "Steckdose"
    window.start_measure("Symbole zählen")
    for x, y in ((60, 90), (120, 90), (120, 90)):
        QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    assert len(window.current_plan().symbols) == 2 and not window.current_plan().scale
    window.symbol_category = "Leuchte"
    QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(60, 130))
    assert len(window.current_plan().symbols) == 3
    window.remove_measurement()
    assert len(window.current_plan().symbols) == 2
    data = encode_plans(session.doc, session.plan_measurements)
    restored = decode_plans(session.doc, data)
    rows = measurement_rows(session.doc, restored)
    assert (2, "Steckdose", 2, "Stk.") in rows and (None, "Summe Steckdose", 2, "Stk.") in rows
    for name, value, unit in (("Summe Umfänge", 32, "m"), ("Summe Innenkanten", 8, "m"), ("Summe Flächen", 56, "m²")):
        row = next(row for row in rows if row[1] == name)
        assert abs(row[2] - value) < 1e-8 and row[3] == unit

    font = Path(os.environ["WINDIR"]) / "Fonts/arial.ttf"
    with draw_plan_copy(session.doc, restored, font) as output:
        text = " ".join(output[1].get_text().split())
        assert "Steckdose 1" in text and "Steckdose 2" in text
        rendered = output[0].get_pixmap()
        assert rendered.pixel(106, 108) == (255, 255, 255)
        assert rendered.pixel(230, 150) != (255, 255, 255)
        if os.environ.get("PDF_WERKBANK_QA_MEASURE"):
            output.save(Path(os.environ["PDF_WERKBANK_QA_MEASURE"]) / "Aufmass.pdf")
    assert path.read_bytes() == original

    incoming = root / "Eingang"
    incoming.mkdir()
    settings = window.favorite_settings
    settings.setValue("invoice-watch/folder", str(incoming))
    window.show()
    window.invoice_folder(show=False)
    dialog = window._invoice_folder_window
    assert not dialog.isVisible()
    (incoming / "Beleg.xml").write_text("<invoice/>", encoding="utf-8")
    with patch("invoice_watch.validate_invoice_xml", return_value=None):
        for _ in range(350):
            if window.invoice_badge.text() == "Rechnungen: 1 neu":
                break
            QTest.qWait(50)
        assert window.invoice_badge.text() == "Rechnungen: 1 neu", (window.invoice_badge.text(), dialog.findChild(QTableWidget).rowCount(), [item.text() for item in dialog.findChildren(QLabel)])
        window.invoice_badge.click()
        assert dialog.isVisible() and dialog.findChild(QTableWidget).rowCount() == 1
        QTest.qWait(850)
        assert window.invoice_badge.text() == "Rechnungen"
        dialog.close()
        assert not dialog.isVisible() and window._invoice_folder_window is dialog
        window.close()
        assert window._invoice_folder_window is None
    settings.remove("invoice-watch/folder")
    session.doc.close()

print("Nettoflächen, getrennte Kanten, Stückzahlen ohne Maßstab, PDF-Export, Änderungsfarben und Hintergrundzähler geprüft.")

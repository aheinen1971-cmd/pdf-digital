# SPDX-License-Identifier: AGPL-3.0-only
import cmath
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from pdf_werkbank import ScanFieldPreview, Window
from scan_form import (SCAN_FORM_TEMPLATES, aligned_boxes, anchor_image, capture_scan_fields, locate_anchor,
                       scan_form_templates)


SIZE = (400, 500)
POINTS = (complex(80, 115), complex(280, 360))
SHAPES = (((-14, -12), (-14, 13)), ((-14, 13), (12, 13)), ((-7, -4), (11, -4)),
          ((5, -12), (5, 6)), ((-12, 4), (-2, 4)))
OTHER = (((-12, -12), (13, 12)), ((-12, 12), (13, -12)), ((-8, -11), (5, -11)),
         ((-4, 2), (11, 2)), ((-10, 8), (-10, -2)))


def make(path, convert=lambda point: point, show=True, field=False):
    with pdf.open() as doc:
        page = doc.new_page(width=SIZE[0], height=SIZE[1])
        if show:
            for center, segments in zip(POINTS, (SHAPES, OTHER)):
                for start, end in segments:
                    a, b = [convert(center + complex(*position)) for position in (start, end)]
                    page.draw_line((a.real, a.imag), (b.real, b.imag), width=2.3)
        if field:
            widget = pdf.Widget()
            widget.field_name = "Kunde"
            widget.field_type = pdf.PDF_WIDGET_TYPE_TEXT
            widget.rect = pdf.Rect(140, 170, 250, 200)
            page.add_widget(widget)
        doc.save(path)


def button(dialog, label):
    return next(item for item in dialog.findChildren(QPushButton) if item.text() == label)


def click_points(view, points):
    for point in points:
        pos = QPoint(round(point.real / SIZE[0] * view.width()), round(point.imag / SIZE[1] * view.height()))
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=pos)


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    reference, translated, blank = [root / (name + ".pdf") for name in ("Referenz", "Neuer-Scan", "Unscharf")]
    factor = cmath.rect(1.04, math.radians(4))
    shift = complex(17, -13)
    convert = lambda point: point * factor + shift
    make(reference, field=True)
    make(translated, convert)
    make(blank, show=False)
    window = Window()
    settings = QSettings(str(root / "test.ini"), QSettings.Format.IniFormat)
    window.favorite_settings = settings
    window.open_path(str(reference))
    with patch("pdf_werkbank.QInputDialog.getText", return_value=("Papier", True)):
        window.save_scan_field_template()

    def capture(dialog):
        button(dialog, "Zwei Punkte setzen").click()
        view = dialog.findChild(ScanFieldPreview)
        click_points(view, POINTS)
        assert len(view.anchors) == 2
        dialog.accept()
        return dialog.result()

    with patch("pdf_werkbank.QInputDialog.getItem", return_value=("Papier", True)), \
            patch.object(QDialog, "exec", capture), patch.object(window, "error", side_effect=AssertionError):
        window.set_scan_template_anchors()
    saved = scan_form_templates(settings)["Papier"]
    anchors = saved[0]["anchors"]
    assert len(anchors) == 2
    assert settings.contains(SCAN_FORM_TEMPLATES)
    moved_points = [convert(point) for point in POINTS]
    expected_points = [[point.real / SIZE[0], point.imag / SIZE[1]] for point in moved_points]
    expected = aligned_boxes(saved, anchors, expected_points, SIZE[0] / SIZE[1])[0]

    with pdf.open(translated) as doc:
        raster = anchor_image(doc[0])
        detected = [locate_anchor(raster, anchor) for anchor in anchors]
        assert all(point is not None for point in detected), detected
        for actual, wanted in zip(detected, expected_points):
            assert max(abs(a - b) for a, b in zip(actual, wanted)) < .018, (actual, wanted)
    with pdf.open(blank) as doc:
        assert all(locate_anchor(anchor_image(doc[0]), anchor) is None for anchor in anchors)
    with pdf.open() as doc:
        page = doc.new_page(width=SIZE[0], height=SIZE[1])
        for offset in (0, 50):
            for start, end in SHAPES:
                a, b = [POINTS[0] + complex(*point) + offset for point in (start, end)]
                page.draw_line((a.real, a.imag), (b.real, b.imag), width=2.3)
        assert locate_anchor(anchor_image(page), anchors[0]) is None

    def automatic(dialog):
        view = dialog.findChild(ScanFieldPreview)
        assert len(view.anchors) == 2
        assert max(abs(a - b) for a, b in zip(view.fields[0]["box"], expected)) < .025
        dialog.accept()
        return dialog.result()

    def manual(dialog):
        view = dialog.findChild(ScanFieldPreview)
        assert not view.anchors and view.fields[0]["box"] == saved[0]["box"]
        button(dialog, "Zwei Punkte von Hand setzen").click()
        click_points(view, moved_points)
        assert len(view.anchors) == 2
        assert max(abs(a - b) for a, b in zip(view.fields[0]["box"], expected)) < .008
        dialog.accept()
        return dialog.result()

    for source, interaction in ((translated, automatic), (blank, manual)):
        session = window.open_path(str(source))
        with patch("pdf_werkbank.QInputDialog.getItem", return_value=("Papier", True)), \
                patch.object(QDialog, "exec", interaction), patch.object(window, "error", side_effect=AssertionError):
            window.use_scan_field_template()
        assert capture_scan_fields(session.doc)[0]["box"] != saved[0]["box"]
        session.changed = False
    assert scan_form_templates(settings)["Papier"] == saved
    while window.sessions:
        window.close_tab(0)
    window.close()

print("Zwei Referenzpunkte, automatische Drehung und Skalierung, leere Seite und manuelle Ausrichtung geprüft.")

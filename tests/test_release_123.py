# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTableWidget

from pdf_werkbank import Window
from plan_measure import Measurement, draw_plan_copy, polygon_area, store_path, stored_bytes


app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
    folder = Path(temporary)
    path = folder / "Plan.pdf"
    with pdf.open() as doc:
        for rotation in (0, 90, 180, 270):
            page = doc.new_page(width=400, height=320)
            page.draw_rect(pdf.Rect(80, 90, 280, 210), width=1)
            page.insert_text((60, 44), f"Grundriss {rotation} Grad", fontsize=13)
            page.set_rotation(rotation)
        doc.save(path)
    original = path.read_bytes()
    window = Window()
    window.measurement_store = lambda: folder / "Messungen"
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    session = window.open_path(str(path))
    for index in range(4):
        window.go_page(index)
        plan = window.current_plan(create=True)
        plan.scale = 0.05
        line = tuple(map(pdf.Point, ((80, 90), (280, 90))))
        area = tuple(map(pdf.Point, ((80, 90), (280, 90), (280, 210), (80, 210))))
        plan.marks.extend([Measurement(line, 200), Measurement(area, polygon_area(area), True)])

    def naming(dialog):
        table = dialog.findChild(QTableWidget)
        assert table.rowCount() == 8
        for row in range(8):
            table.item(row, 1).setText("Längskante" if row % 2 == 0 else "Küche")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Messliste speichern").click()
        return QDialog.DialogCode.Rejected

    with patch.object(QDialog, "exec", naming):
        window.measurement_list()
    assert session.plan_saved_token
    store = store_path(window.measurement_store(), path, session.plan_source_stamp)
    assert stored_bytes(store)
    window.close_tab(0)
    session = window.open_path(str(path))
    assert len(session.plan_measurements) == 4
    assert window.current_plan().marks[1].caption(window.current_plan().scale) == "Küche – 60 m²"

    second = window.open_path(str(path))
    assert window.current_plan().marks[0].name == "Längskante"
    window.current_plan().marks.pop()
    assert window.save_measurements(second)
    with patch.object(window, "error") as report:
        assert not window.save_measurements(session)
        assert "anderen Tab" in str(report.call_args.args[0])
    window.close_tab(1)

    destination = folder / "Kopie mit Maßen.pdf"
    with patch.object(window, "preview_send", return_value=False), patch.object(window, "output_path") as no_output:
        window.export_measurements()
        no_output.assert_not_called()
    with patch.object(window, "preview_send", return_value=True), patch.object(window, "output_path", return_value=destination):
        window.export_measurements()
    with pdf.open(destination) as doc:
        assert len(doc) == 4
        for index, page in enumerate(doc):
            assert page.rotation == 90 * index
            text = " ".join(page.get_text().split())
            assert "Längskante – 10 m" in text and "Küche – 60 m²" in text, (index, text)
            assert "Grundriss" in text and len(page.get_drawings()) >= 8
            for line in [line for block in page.get_text("dict")["blocks"] for line in block.get("lines", ())
                         if any("Küche" in span["text"] for span in line["spans"])]:
                vector = pdf.Point(line["dir"]) * pdf.Matrix(page.rotation)
                assert abs(vector.x - 1) < 0.01 and abs(vector.y) < 0.01
    assert path.read_bytes() == original
    with pdf.open(path) as doc:
        assert all("Küche" not in page.get_text() for page in doc)
    if os.environ.get("PDF_WERKBANK_QA_MEASURE"):
        (Path(os.environ["PDF_WERKBANK_QA_MEASURE"]) / "Masskopie.pdf").write_bytes(destination.read_bytes())

    version = folder / "Geänderter Plan.pdf"
    with pdf.open(path) as doc:
        doc[0].insert_text((50, 66), "Neue Fassung")
        doc.save(version)
    window.close_tab(0)
    path.write_bytes(version.read_bytes())
    window.open_path(str(path))
    assert not window.current.plan_measurements
    window.close_tab(0)

print("Namen, Speichern und Wiederöffnen, Versionsbindung, konkurrierende Tabs und PDF-Kopien bei vier Drehungen geprüft.")

# SPDX-License-Identifier: AGPL-3.0-only
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTableWidget

from pdf_werkbank import Window
from scan_form import (SCAN_FORM_TEMPLATES, anchor_image, capture_anchor, capture_scan_fields,
                       match_scan_templates)
from update_worker import digest_file


POSITIONS = ((.24, .24), (.68, .69))
SYMBOLS = ("K", "G", "X", "P")


def make(path, symbols, name=None):
    with pdf.open() as document:
        page = document.new_page(width=400, height=500)
        for (x, y), symbol in zip(POSITIONS, symbols):
            page.insert_text((x * 400 - 11, y * 500 + 13), symbol, fontname="hebo", fontsize=36)
        if name:
            field = pdf.Widget()
            field.field_name, field.field_type = name, pdf.PDF_WIDGET_TYPE_TEXT
            field.rect = pdf.Rect(90, 180, 240, 212)
            page.add_widget(field)
        document.save(path)


def template(path):
    with pdf.open(path) as doc:
        fields = capture_scan_fields(doc)
        raster = anchor_image(doc[0])
        fields[0]["anchors"] = [capture_anchor(raster, point) for point in POSITIONS]
        return fields


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as directory:
    root = Path(directory)
    sources = [root / f"{name}.pdf" for name in ("Scan-A", "Scan-B", "Unklar", "Konflikt", "Wettlauf")]
    ref_a, ref_b = root / "Vorlage-A.pdf", root / "Vorlage-B.pdf"
    make(ref_a, SYMBOLS[:2], "Kundenname")
    make(ref_b, SYMBOLS[2:], "Rechnungsnummer")
    for path, symbols in zip(sources, (SYMBOLS[:2], SYMBOLS[2:], (), SYMBOLS[:2], SYMBOLS[:2])):
        make(path, symbols)
    templates = {"Kunden": template(ref_a), "Rechnung": template(ref_b)}
    hashes = {path: digest_file(path) for path in sources}
    with pdf.open(sources[0]) as doc:
        assert list(match_scan_templates(doc, templates)) == ["Kunden"]
        assert len(match_scan_templates(doc, {**templates, "Ähnliche Kunden": templates["Kunden"]})) == 2
    target = root / "Ausgabe"
    target.mkdir()
    existing = target / "Konflikt - ausfüllbar.pdf"
    existing.write_bytes(b"vorhanden")
    race = target / "Wettlauf - ausfüllbar.pdf"
    window = Window()
    window.favorite_settings = QSettings(str(root / "prefs.ini"), QSettings.Format.IniFormat)
    window.favorite_settings.setValue(SCAN_FORM_TEMPLATES, json.dumps(templates))
    reports = []

    def review(dialog):
        table = dialog.findChild(QTableWidget)
        assert [table.item(i, 2).text() for i in range(5)] == ["Kunden", "Rechnung", "", "", "Kunden"]
        assert table.item(0, 0).checkState() == Qt.CheckState.Checked
        assert table.item(2, 0).checkState() == Qt.CheckState.Unchecked
        assert table.item(3, 3).text() == "Namenskonflikt im Zielordner"
        table.cellDoubleClicked.emit(2, 1)
        assert table.item(2, 3).text() == "In Vorschau bestätigt"
        assert table.item(2, 0).checkState() == Qt.CheckState.Checked
        race.write_bytes(b"spaeter geschrieben")
        dialog.accept()
        return dialog.result()

    def report(dialog):
        reports.append((dialog.text(), dialog.detailedText()))
        return 0

    with patch("pdf_werkbank.QFileDialog.getOpenFileNames", return_value=(list(map(str, sources)), "")), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(target)), \
            patch("pdf_werkbank.QInputDialog.getItem", return_value=("Rechnung", True)), \
            patch.object(window, "preview_scan_field_template", return_value=templates["Rechnung"]) as preview, \
            patch.object(QDialog, "exec", review), patch.object(QMessageBox, "exec", report), \
            patch.object(window, "error", side_effect=AssertionError):
        window.scan_field_batch()
    preview.assert_called_once()
    for name, expected in (("Scan-A", "Kundenname"), ("Scan-B", "Rechnungsnummer"), ("Unklar", "Rechnungsnummer")):
        with pdf.open(target / f"{name} - ausfüllbar.pdf") as doc:
            assert [field.field_name for field in doc[0].widgets()] == [expected]
    assert existing.read_bytes() == b"vorhanden" and race.read_bytes() == b"spaeter geschrieben"
    assert "3 von 4" in reports[0][0] and "Wettlauf.pdf" in reports[0][1]
    assert all(digest_file(path) == checksum for path, checksum in hashes.items())
    window.close()

print("Gemischter Scan-Stapel, mehrdeutige Vorlagen, Vorschau, PDF-Kopien und Namenskonflikte geprüft.")

# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox, QPushButton, QCheckBox

from assistants import append_scan, form_candidates, scan_quality, similar_scan
from pdf_werkbank import CompareDialog, Window, align_pages


app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as folder, pdf.open() as source, pdf.open() as newer:
    root = Path(folder)
    original, outgoing = root / "Original.pdf", root / "Versand.pdf"
    page = source.new_page(width=420, height=594)
    page.insert_text((35, 50), "Geheim und sichtbar")
    page.draw_rect(pdf.Rect(35, 85, 48, 98), width=1.5)
    page.draw_line(pdf.Point(35, 130), pdf.Point(140, 130), width=1.5)
    source.new_page(width=420, height=594)
    source.set_metadata({"subject": "Geheim"})
    source.set_xml_metadata("<example>Geheim</example>")
    source.embfile_add("notiz.txt", b"Geheim")
    source[0].add_file_annot((300, 50), b"Geheim", "notiz.txt")
    source.save(original)
    page = source[0]
    assert {kind for kind, _ in form_candidates(page)} == {"Kontrollkästchen", "Textfeld"}
    blank = scan_quality(source[1])
    populated = scan_quality(page)
    assert blank[0] and not populated[0]
    assert similar_scan(populated[2], scan_quality(page)[2])
    assert not similar_scan(blank[2], populated[2])
    with pdf.open() as rasterized:
        scan = rasterized.new_page(width=420, height=594)
        scan.insert_image(scan.rect, pixmap=page.get_pixmap(matrix=pdf.Matrix(2, 2), annots=False))
        assert {kind for kind, _ in form_candidates(scan)} == {"Kontrollkästchen", "Textfeld"}
    with pdf.open() as adjusted:
        append_scan(adjusted, page, a4=True, angle=2)
        assert tuple(adjusted[0].rect)[2:] == pdf.paper_size("a4")
        assert "sichtbar" in adjusted[0].get_text()
    with pdf.open() as tilted, pdf.open() as corrected:
        scan = tilted.new_page()
        for y in range(100, 700, 40):
            scan.draw_line((50, y), (480, y + 40), width=2)
        angle = scan_quality(scan)[1]
        assert 4 <= angle <= 6
        append_scan(corrected, scan, angle=angle)
        assert scan_quality(corrected[0])[1] == 0

    newer.new_page(width=420, height=594).insert_text((35, 50), "Öffentlich und sichtbar")
    newer.new_page(width=420, height=594)
    comparison = CompareDialog(None, source, newer, ("alt", "neu"), align_pages(source, newer))
    assert "1 geänderte Textstellen" in comparison.result.text()
    comparison.pages.setCurrentIndex(1)
    comparison.next_change()
    assert comparison.pages.currentIndex() == 0
    report = root / "Bericht.txt"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(report), "")):
        comparison.save_report()
    content = report.read_text(encoding="utf-8")
    assert "− Geheim" in content and "+ Öffentlich" in content

    window = Window()
    window.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    window.open_path(str(original))

    def accept_suggestions(dialog):
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Alle markieren").click()
        for option in dialog.findChildren(QCheckBox):
            option.setChecked(True)
        return QDialog.DialogCode.Accepted

    with patch.object(QDialog, "exec", accept_suggestions):
        window.suggest_fields()
    assert len(list(window.current.doc[0].widgets())) == 2 and window.current.changed
    window.undo()
    assert not list(window.current.doc[0].widgets())
    with patch.object(QDialog, "exec", accept_suggestions), patch.object(
            QMessageBox, "exec", return_value=0), patch.object(
            QInputDialog, "getMultiLineText", return_value=("Geheim", True)), patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(outgoing), "")):
        window.dispatch_check()
    with pdf.open(outgoing) as saved:
        assert "Geheim" not in saved[0].get_text() and "sichtbar" in saved[0].get_text()
        assert not saved.embfile_names() and not saved.get_xml_metadata() and not saved.metadata["subject"]
        assert not list(saved[0].annots())
    assert "Geheim" in window.current.doc[0].get_text() and source.embfile_names()
    window.close_tab(0)

print("Scanassistent, Textvergleich, Versandcheck und Formularvorschläge geprüft.")

from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf as pdf
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from pdf_werkbank import CompareDialog, Window, align_pages, compare_images, compressed_copy, extract_pages, page_selection, save_copy


def add_page(doc, text):
    page = doc.new_page()
    page.insert_text((54, 110), text, fontsize=26)
    return page


app = QApplication([])
root = Path(__file__).parent
with TemporaryDirectory() as folder:
    folder = Path(folder)
    before_path, after_path = folder / "Alt.pdf", folder / "Neu.pdf"
    with pdf.open() as before, pdf.open() as after:
        for text in ("Einleitung", "Termin am Montag", "Schluss"):
            add_page(before, text)
        for text in ("Einleitung", "Zusatzblatt", "Termin am Dienstag", "Schluss"):
            add_page(after, text)
        before.save(before_path)
        after.save(after_path)
        assert page_selection("3, 1-2, 3", 3) == [2, 0, 1]
        for invalid in ("", "1,", "0", "4", "3-2", "1a"):
            try:
                page_selection(invalid, 3)
            except ValueError:
                pass
            else:
                raise AssertionError(invalid)
        extract_path = folder / "Auszug.pdf"
        with extract_pages(before, [2, 0]) as extract:
            save_copy(extract, extract_path)
        with pdf.open(extract_path) as check:
            assert [p.get_text().strip() for p in check] == ["Schluss", "Einleitung"]
        assert len(before) == 3
        pairs = align_pages(before, after)
        assert pairs[0] == (0, 0, "equal") and pairs[-1] == (2, 3, "equal")
        assert pairs[1:3] == [(None, 1, "insert"), (1, 2, "replace")]
        assert not compare_images(before[0], after[0])[1]
        assert compare_images(before[1], after[2])[1]
        assert compare_images(None, after[1])[1]
        dialog = CompareDialog(None, before, after, ("Alt.pdf", "Neu.pdf"), [(1, 2, "replace")])
        dialog.show()
        app.processEvents()
        assert "Unterschiede markiert" in dialog.result.text()
        assert dialog.grab().save(str(root / "compare-preview.png"))
        dialog.close()

    with pdf.open() as doc, pdf.open() as scan_source:
        raster = add_page(scan_source, "Prüfung für die PDF Werkbank").get_pixmap(matrix=pdf.Matrix(4, 4))
        for _ in range(2):
            page = doc.new_page()
            page.insert_image(page.rect, pixmap=raster)
        form_page = add_page(doc, "Vorhandener Text")
        widget = pdf.Widget()
        widget.rect = pdf.Rect(60, 130, 220, 155)
        widget.field_type = pdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = "Feld"
        widget.field_value = "Beispiel"
        form_page.add_widget(widget)
        doc.new_page()
        original = folder / "Scan.pdf"
        doc.save(original)
        compact = folder / "Kompakt.pdf"
        size_before, size_after = compressed_copy(doc, compact, "Kleine Datei")
        assert size_after < size_before, (size_before, size_after)
        with pdf.open(compact) as check:
            assert len(check) == 4
            page = check[2]
            assert "Vorhandener Text" in page.get_text()
            field = next(page.widgets())
            assert field.field_value == "Beispiel"
            assert "/N" in check.xref_object(field.xref)
            assert check[0].get_images(full=True)[0][2] < doc[0].get_images(full=True)[0][2]
            check[0].get_pixmap().save(root / "compact-preview.png")
        original_pixels = doc[0].get_pixmap().samples

    window = Window()
    window.open_path(str(original))
    QInputDialog.getItem = lambda *a, **kw: ("Deutsch und Englisch", True)
    reports = []
    QMessageBox.exec = lambda box: reports.append((box.text(), box.detailedText()))
    window.ocr(True)
    assert len(reports) == 1 and "2 Seiten mit neuer Textebene" in reports[0][0], reports
    assert "1 Seiten hatten bereits Text" in reports[0][0] and "Seite 4" in reports[0][1], reports
    for index in (0, 1):
        assert "Prüfung für die PDF Werkbank" in window.current.doc[index].get_text()
        assert window.current.doc[index].get_pixmap().samples == original_pixels
    searchable = folder / "Durchsuchbar.pdf"
    save_copy(window.current.doc, searchable)
    with pdf.open(searchable) as check:
        assert all(check[i].search_for("Prüfung") for i in (0, 1))
        assert check[2].get_text().startswith("Vorhandener Text")
    window.undo()
    assert not window.current.doc[0].get_text().strip()
    print(f"Auszug, Vergleich, Komprimierung ({size_before} → {size_after} Byte), Dokument-OCR und Rückgängig geprüft.")



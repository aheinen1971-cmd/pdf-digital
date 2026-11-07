# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QPushButton, QTableWidget

from pdf_werkbank import Window


def make_pdf(path, pages):
    with pdf.open() as doc:
        for rotation, fields in pages:
            page = doc.new_page()
            for index, (name, value) in enumerate(fields):
                field = pdf.Widget()
                field.field_name = name
                field.field_value = value
                field.field_type = pdf.PDF_WIDGET_TYPE_TEXT
                field.rect = pdf.Rect(55, 90 + index * 35, 220, 114 + index * 35)
                page.add_widget(field)
            page.set_rotation(rotation)
        doc.save(path)


def click(owner, label):
    next(button for button in owner.findChildren(QPushButton) if button.text() == label).click()


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    conflict = root / "Konflikt.pdf"
    blank = root / "Leer.pdf"
    missing = root / "Fehlt.pdf"
    make_pdf(conflict, [(0, []), (90, [("Kundennr.", "01"), ("Fremdes Feld", "privat")]),
                        (0, [("Kundennummer", "02")])])
    make_pdf(blank, [(0, []), (0, [("Kundennummer", "")])])
    make_pdf(missing, [(0, [("Fremdes Feld", "anderer Wert")])])
    window = Window()
    window.favorite_settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
    template = "Kunden", [["Kunden-ID", ["Kundennr.", "Kundennummer"], True]]
    window.show()
    with patch.object(window, "error", side_effect=AssertionError), \
            patch.object(window.scroll, "ensureVisible") as scroll, \
            patch("pdf_werkbank.QMessageBox.information") as notice:
        window.export_form_batch(use_template=True, paths=[conflict, blank, missing], template=template)
        preview = window.form_preview
        table = preview.findChild(QTableWidget)
        table.cellDoubleClicked.emit(0, 2)
        assert not preview.isVisible() and window.current.path == conflict
        assert window.current.page == 1 and window.mode == "Formular"
        session = window.current
        page = session.doc[1]
        boxes = window.form_highlights(session, page)
        assert len(boxes) == 1 and boxes[0] == pdf.Rect(55, 90, 220, 114)
        transformed = boxes[0] * page.rotation_matrix
        scroll.assert_called_with(round(transformed.x0 * window.scale), round(transformed.y0 * window.scale), 25, 65)
        center = (transformed.tl + transformed.br) * (window.scale / 2)
        pixel = window.canvas.pixmap().toImage().pixelColor(round(center.x), round(center.y))
        assert pixel.red() > pixel.blue(), pixel.name()
        click(window.statusBar(), "Nächste Feldseite")
        assert window.current.page == 2 and len(window.form_highlights(session, session.doc[2])) == 1
        click(window.statusBar(), "Nächste Feldseite")
        assert window.current.page == 1
        click(window.statusBar(), "Zur Formularvorschau")
        assert window.form_preview is preview and preview.isVisible()
        table.cellDoubleClicked.emit(0, 2)
        assert len(window.sessions) == 1
        click(window.statusBar(), "Zur Formularvorschau")
        table.cellDoubleClicked.emit(1, 2)
        assert window.current.path == blank and window.current.page == 1
        assert window.form_highlights(window.current, window.current.doc[1])
        assert not window.form_highlights(session, session.doc[1])
        click(window.statusBar(), "Zur Formularvorschau")
        table.cellDoubleClicked.emit(2, 2)
        assert preview.isVisible() and window.form_jump is None
        assert "Kundennr.\nKundennummer" in notice.call_args.args[2]

        index = next(i for i, item in enumerate(window.sessions) if item.path == blank)
        window.close_tab(index)
        edited = root / "Korrigiert.pdf"
        make_pdf(edited, [(0, []), (0, [("Kundennummer", "12345")])])
        edited.replace(blank)
        click(preview, "Mit Hinweisen speichern")
        assert window.form_preview is preview and "neu einlesen" in notice.call_args.args[2]
        click(preview, "PDFs neu einlesen")
        assert window.form_preview is not preview
        updated = window.form_preview.findChild(QTableWidget)
        assert updated.item(1, 2).text() == "12345"
        window.form_preview.reject()
        assert window.form_preview is None and window.form_jump is None
        while window.sessions:
            window.close_tab(0)
    window.close()

print("Doppelklick, markierte und gedrehte Felder, mehrere Seiten, fehlende Felder und aktualisierte Vorschau geprüft.")

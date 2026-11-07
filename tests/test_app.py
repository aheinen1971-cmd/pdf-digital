from pathlib import Path
import shutil
from PySide6.QtWidgets import QApplication, QInputDialog
from PySide6.QtCore import QRect
from PySide6.QtPrintSupport import QPrinter
import pymupdf as pdf
from pdf_werkbank import Window, find_update, print_pages, save_copy

app = QApplication([])
work = Path(__file__).parent
path = work / "test-input.pdf"
with pdf.open() as doc:
    for n in range(1, 4):
        page = doc.new_page()
        page.insert_text((56, 80), f"Testdokument - Seite {n}", fontsize=22)
        page.insert_text((56, 115), "PDF Werkbank: Vorschau, Notiz und Druck.", fontsize=12)
        if n == 1:
            widget = pdf.Widget()
            widget.rect = pdf.Rect(56, 155, 260, 183)
            widget.field_type = pdf.PDF_WIDGET_TYPE_TEXT
            widget.field_name = "Name"
            widget.field_value = "Vorname"
            widget.field_border_color = (0.2, 0.4, 0.6)
            widget.field_border_width = 1
            page.add_widget(widget)
    doc.save(path)

window = Window()
window.error = lambda error: (_ for _ in ()).throw(AssertionError(error))
window.show()
window.open_path(str(path))
app.processEvents()
assert window.canvas.pixmap().width() > 0
window.next_thumbnail()
assert not window.thumbs.item(0).icon().isNull()
window.set_mode("Markieren")
window.annotate(QRect(50, 98, 285, 22))
first = window.current.doc[0]
assert list(first.annots())[0].type[1] == "Highlight"
QInputDialog.getMultiLineText = lambda *args, **kwargs: ("Interne Notiz", True)
window.set_mode("Notiz")
window.annotate(QRect(300, 200, 0, 0))
assert len(list(window.current.doc[0].annots())) == 2
QInputDialog.getText = lambda *args, **kwargs: ("Max Muster", True)
window.set_mode("Formular")
window.annotate(QRect(68, 163, 0, 0))
first = window.current.doc[0]
assert next(first.widgets()).field_value == "Max Muster"
window.edit_page("rotate", 90)
assert window.current.doc[0].rotation == 90
window.edit_page("move", 1)
assert "Seite 1" in window.current.doc[1].get_text()
window.undo()
assert window.current.doc[0].rotation == 90
window.undo()
assert window.current.doc[0].rotation == 0
QInputDialog.getText = lambda *args, **kwargs: ("Seite 3", True)
window.search()
assert window.current.page == 2
window.go_page(0)
window.thumbs.setCurrentRow(0)
window.tools.setCurrentIndex(1)
app.processEvents()
assert window.grab().save(str(work / "app-preview.png"))
saved = work / "test-saved.pdf"
save_copy(window.current.doc, saved)
with pdf.open(saved) as check:
    page = check[0]
    field = next(page.widgets())
    assert field.field_value == "Max Muster"
    assert "/N" in check.xref_object(field.xref)
    assert len(list(page.annots())) == 2
    assert "Testdokument" in page.get_text()
    assert not list(page.get_images())
    page.get_pixmap().save(work / "saved-page.png")

output = work / "test-printed.pdf"
printer = QPrinter(QPrinter.PrinterMode.HighResolution)
printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
printer.setOutputFileName(str(output))
print_pages(window.current.doc, printer, [2, 0])
with pdf.open(output) as result:
    assert len(result) == 2
    assert len(result[0].get_images()) == 1
    result[1].get_pixmap().save(work / "printed-page.png")

updates = work / "test-updates"
updates.mkdir(exist_ok=True)
for name in ("PDF-Werkbank-1.0.0.exe", "PDF-Werkbank-1.2.0.exe", "PDF-Werkbank-1.10.0.exe", "PDF-Werkbank-999.exe"):
    (updates / name).touch()
assert find_update(updates).name == "PDF-Werkbank-1.10.0.exe"
assert find_update(updates, (1, 10, 0)) is None
window.open_path(str(path))
assert len(window.sessions) == 2
window.close_tab(1)
assert len(window.sessions) == 1
print("Vorschau, Miniaturen, Markierung, Notiz, Formular, Seiten, Suche, Kopie, PDF-Druck und Versionsauswahl OK")



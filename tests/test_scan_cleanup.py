from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf as pdf
from PySide6.QtWidgets import QApplication, QDialog

from pdf_werkbank import SCAN_PROFILES, Window, enhance_scan

app = QApplication([])
root = Path(__file__).parent

with TemporaryDirectory() as temp:
    source = Path(temp) / "Scan.pdf"
    with pdf.open() as doc:
        page = doc.new_page()
        page.draw_rect(page.rect, fill=(0.82, 0.8, 0.75), color=None)
        page.insert_text((45, 100), "Schlecht lesbarer Scan", color=(0.27, 0.28, 0.3), fontsize=25)
        raster = page.get_pixmap()
    with pdf.open() as doc:
        page = doc.new_page()
        page.insert_image(page.rect, pixmap=raster)
        before = page.get_pixmap().samples
        cleanup = enhance_scan(page, SCAN_PROFILES[1], dpi=72)
        assert cleanup.n == 1
        assert cleanup.pixel(10, 10) == (255,)
        assert min(cleanup.samples) < 40
        assert page.get_pixmap().samples == before
        doc.save(source)

    window = Window()
    window.error = lambda error: (_ for _ in ()).throw(AssertionError(error))
    window.open_path(str(source))
    output = Path(temp) / "Verbessert.pdf"
    window.output_path = lambda *args: output
    original_exec = QDialog.exec

    def check_scan(dialog):
        assert dialog.windowTitle() == "Scan-Seite lesbarer machen"
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(root / "cleanup-preview.png"))
        return QDialog.DialogCode.Accepted

    QDialog.exec = check_scan
    try:
        window.scan_cleanup()
    finally:
        QDialog.exec = original_exec
    with pdf.open(output) as check:
        assert len(check) == 1
        assert check[0].get_pixmap().pixel(10, 10)[0] > 240
        assert not check[0].get_text()

    window.close_tab(0)
    print("Scan-Aufbereitung und PDF-Ausgabe geprüft.")


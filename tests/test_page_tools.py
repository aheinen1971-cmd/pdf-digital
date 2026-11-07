# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QFileDialog

from page_tools import crop_page, edit_image, image_at, scanned_document
from pdf_werkbank import Window, save_copy

app = QApplication([])
here = Path(__file__).parent


def raster(color, width=36, height=24):
    return pdf.Pixmap(pdf.csRGB, width, height, bytes(color) * width * height, False)


with TemporaryDirectory() as folder:
    root = Path(folder)
    source, target = root / "Original.pdf", root / "Bearbeitet.pdf"
    green = root / "Ersatz.png"
    raster((0, 200, 0)).save(green)
    with pdf.open() as document:
        page = document.new_page(width=360, height=440)
        first = page.insert_image(pdf.Rect(36, 36, 136, 96), pixmap=raster((230, 0, 0)), keep_proportion=False)
        page.insert_image(pdf.Rect(190, 40, 260, 100), xref=first, keep_proportion=False)
        page.insert_text((45, 76), "Text über dem Bild", fontsize=11)
        page.insert_text((20, 420), "Außerhalb des Ausschnitts")
        other = document.new_page(width=360, height=440)
        other.insert_image(pdf.Rect(36, 36, 136, 96), xref=first, keep_proportion=False)
        document.save(source)
    original = source.read_bytes()
    window = Window()
    try:
        window.error = lambda error: (_ for _ in ()).throw(AssertionError(error))
        window.open_path(str(source))
        window.set_mode("Bild verschieben")
        window.annotate(QRect(60, 58, 71, 101), QPoint(60, 58), QPoint(60, 160))
        page = window.current.doc[0]
        assert page.get_image_info()[0]["bbox"] == (36, 138, 136, 198), page.get_image_info()
        assert page.get_image_info()[1]["bbox"] == (190, 40, 260, 100)
        assert "Text über dem Bild" in page.get_text()
        window.set_mode("Bild ersetzen")
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(green), "Bilder")):
            window.annotate(QRect(200, 45, 1, 1))
        page = window.current.doc[0]
        samples = page.get_pixmap()
        assert samples.pixel(200, 48) == (0, 200, 0)
        assert samples.pixel(39, 142) == (230, 0, 0)
        assert window.current.doc[1].get_pixmap().pixel(39, 42) == (230, 0, 0)
        window.set_mode("Zuschneiden")
        window.annotate(QRect(QPoint(20, 20), QPoint(290, 250)))
        save_copy(window.current.doc, target)
        with pdf.open(target) as result:
            assert tuple(result[0].cropbox) == (20, 20, 290, 250)
            assert "Text über dem Bild" in result[0].get_text()
            assert "Außerhalb" not in result[0].get_text()
            assert tuple(result[1].cropbox) == (0, 0, 360, 440)
            result[0].get_pixmap().save(here / "page-tools-preview.png")
        window.undo()
        assert tuple(window.current.doc[0].cropbox) == (0, 0, 360, 440)
        window.undo()
        assert window.current.doc[0].get_pixmap().pixel(200, 48) == (230, 0, 0)
        window.undo()
        assert window.current.doc[0].get_image_info()[0]["bbox"] == (36, 36, 136, 96)
        assert source.read_bytes() == original
    finally:
        if window.sessions:
            window.sessions[0].changed = False
            window.close_tab(0)

    with pdf.open() as document:
        page = document.new_page(width=400, height=500)
        page.set_cropbox(pdf.Rect(30, 40, 330, 440))
        page.set_rotation(90)
        crop_page(page, pdf.Rect(10, 20, 100, 200))
        assert tuple(page.cropbox) == (40, 60, 130, 240), page.cropbox
        assert (page.rect.width, page.rect.height) == (180, 90)
        try:
            crop_page(page, pdf.Rect(0, 0, 2, 2))
        except ValueError:
            pass
        else:
            raise AssertionError("Kleinstausschnitt wurde angenommen.")

    with pdf.open() as document, pdf.open() as nested:
        nested_page = nested.new_page(width=100, height=100)
        nested_page.insert_image(nested_page.rect, pixmap=raster((230, 0, 0)))
        page = document.new_page()
        page.show_pdf_page(pdf.Rect(0, 0, 200, 200), nested, 0)
        before = page.get_pixmap().samples
        try:
            edit_image(page, image_at(page, pdf.Point(100, 100)), offset=pdf.Point(20, 20))
        except ValueError as exc:
            assert "komplex" in str(exc)
        else:
            raise AssertionError("Verschachteltes Bild wurde verändert.")
        assert page.get_pixmap().samples == before

    scan = root / "Scan.png"
    raster((30, 70, 140), 300, 600).save(scan)
    with scanned_document(scan, (300, 300)) as document:
        assert tuple(document[0].rect) == (0, 0, 72, 144)
        assert document[0].get_pixmap().pixel(30, 40) == (30, 70, 140)
        save_copy(document, root / "Scan.pdf")
    with pdf.open(root / "Scan.pdf") as document:
        assert len(document) == 1

    class ScanResult:
        returncode = 0

        def poll(self):
            return self.returncode

        def wait(self):
            return self.returncode

    def fake_scanner(command, **kwargs):
        output = Path(kwargs["env"]["PDF_WERKBANK_SCAN_FILE"])
        raster((30, 70, 140), 300, 600).save(output)
        kwargs["stdout"].write(b"300,300\n")
        return ScanResult()

    target = root / "Vom Scanner.pdf"
    scanner_window = Window()
    scanner_window.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    try:
        with patch("pdf_werkbank.subprocess.Popen", side_effect=fake_scanner), patch.object(
                QFileDialog, "getSaveFileName", return_value=(str(target), "PDF-Dateien")):
            scanner_window.scan_from_device()
        assert scanner_window.current.path == target
        assert tuple(scanner_window.current.doc[0].rect) == (0, 0, 72, 144)
    finally:
        if scanner_window.sessions:
            scanner_window.close_tab(0)

    no_device = ScanResult()
    no_device.returncode = 3
    messages = []
    scanner_window.error = messages.append
    with patch("pdf_werkbank.subprocess.Popen", return_value=no_device):
        scanner_window.scan_from_device()
    assert len(messages) == 1 and "keinen WIA-Scanner" in messages[0]


print("Zuschneiden, Bildbewegung, Bildersatz, Rückgängig, geteilte Bildobjekte und Scan-PDF-Ausgabe geprüft.")

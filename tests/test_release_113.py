# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QPushButton, QSpinBox

from page_tools import booklet_sheets, impose_booklet
from pdf_standards import export_vector_archive, validate_standard
from pdf_werkbank import Window, print_pages


def number(label, x):
    return next(word[0] for word in x if word[4] == label)


app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    root = Path(folder)
    source = root / "Quelle.pdf"
    target = root / "Geprüft.pdf"
    booklet_path = root / "Druck-PDF.pdf"
    windows = Path(os.environ["WINDIR"])
    profile = windows / "System32/spool/drivers/color/sRGB Color Space Profile.icm"
    java, jar = (os.environ.get(f"PDF_WERKBANK_QA_{key}") for key in ("JAVA", "VERA"))
    with pdf.open() as doc:
        for index in range(1, 6):
            page = doc.new_page(width=595, height=842)
            page.insert_font(fontname="Werkbank", fontfile=str(windows / "Fonts/arial.ttf"), set_simple=True)
            page.insert_text((50, 100), f"Blattnummer {index}", fontname="Werkbank", fontsize=30)
            page.draw_rect(pdf.Rect(60, 170, 510, 680), color=(0, 0.2, 0.8), width=2)
        doc.set_xml_metadata("""<x:xmpmeta xmlns:x="adobe:ns:meta/">
          <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
            <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
              <xmp:CreatorTool>Werkbank-Test</xmp:CreatorTool>
            </rdf:Description>
          </rdf:RDF></x:xmpmeta>""")
        doc.save(source)
        plan = booklet_sheets(range(5))
        assert plan == [((None, 0), (1, None)), ((None, 2), (3, 4))]
        assert booklet_sheets(range(8)) == [((7, 0), (1, 6)), ((5, 2), (3, 4))]
        assert booklet_sheets([2, 0, 1, 4]) == [((4, 2), (0, 1))]
        assert booklet_sheets([2]) == [((None, 2), (None, None))]
        with impose_booklet(doc, plan) as booklet:
            assert len(booklet) == 4 and tuple(booklet[0].rect)[2:] == pdf.paper_size("a4-l")
            fold = booklet[0].rect.width / 2
            assert number("1", booklet[0].get_text("words")) > fold
            assert number("2", booklet[1].get_text("words")) < fold
            assert number("3", booklet[2].get_text("words")) > fold
            assert number("4", booklet[3].get_text("words")) < fold < number("5", booklet[3].get_text("words"))
            assert all(not page.get_image_info() and page.get_drawings() for page in booklet)
            window = Window()
            window.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
            window.open_path(str(source))
            def review(dialog):
                assert any("Vorderseite · Leer | Seite 1" in label.text() for label in dialog.findChildren(QLabel))
                dialog.findChild(QSpinBox).setValue(2)
                assert any("Rückseite · Seite 4 | Seite 5" in label.text() for label in dialog.findChildren(QLabel))
                next(button for button in dialog.findChildren(QPushButton)
                     if button.text() == "Druck-PDF speichern").click()
                return QDialog.DialogCode.Rejected
            with patch.object(QDialog, "exec", review), patch.object(
                    QFileDialog, "getSaveFileName", return_value=(str(booklet_path), "")):
                window.booklet_preview(booklet, plan, None)
            assert booklet_path.is_file()
            printout = root / "Virtueller Druck.pdf"
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(printout))
            printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            printer.setPageOrientation(QPageLayout.Orientation.Landscape)
            printer.setFullPage(True)
            print_pages(booklet, printer, range(len(booklet)))
            with pdf.open(printout) as printed:
                assert len(printed) == 4 and printed[0].rect.width > printed[0].rect.height

        with pdf.open() as mixed:
            page = mixed.new_page(width=300, height=450)
            page.insert_text((40, 90), "Gedrehte Seite")
            page.set_rotation(90)
            mixed.new_page(width=500, height=300)
            with impose_booklet(mixed, booklet_sheets([0, 1])) as imposed:
                assert "Gedrehte Seite" in imposed[0].get_text()
                assert not imposed[1].get_text().strip()
            assert mixed[0].rotation == 90

        export_vector_archive(doc, target, profile)
        with pdf.open(target) as archived:
            assert "Werkbank-Test" in archived.get_xml_metadata()
            assert "pdfaid:conformance>B" in archived.get_xml_metadata()
            for a, b in zip(doc, archived, strict=True):
                assert a.read_contents() == b.read_contents()
                assert a.get_text() == b.get_text() and a.get_drawings() == b.get_drawings()
                assert a.get_pixmap(matrix=pdf.Matrix(2, 2)).digest == b.get_pixmap(matrix=pdf.Matrix(2, 2)).digest
                assert not b.get_image_info()
        if java and jar:
            result = validate_standard(java, jar, target, "2b")
            assert result[0], result
            assert not validate_standard(java, jar, source, "2b")[0]
        qa = os.environ.get("PDF_WERKBANK_QA_OUTPUT")
        if qa:
            for path in (target, booklet_path):
                (Path(qa) / path.name).write_bytes(path.read_bytes())
        signed_form = doc[0]
        signature = pdf.Widget()
        signature.rect = pdf.Rect(100, 100, 150, 125)
        signature.field_type = pdf.PDF_WIDGET_TYPE_SIGNATURE
        signature.field_name = "Signatur"
        signed_form.add_widget(signature)
        try:
            export_vector_archive(doc, root / "Signiert.pdf", profile)
        except ValueError as exc:
            assert "Signatur" in str(exc)
        else:
            raise AssertionError("Signaturfeld wurde übergangen")

    rejected = root / "Abgelehnt.pdf"
    with patch.object(window, "standard_tools", return_value=(java, jar)), patch.object(
            window, "output_path", return_value=rejected), patch(
            "pdf_werkbank.validate_standard", return_value=(False, ["Testablehnung"])):
        try:
            window.archive_vector_copy()
        except AssertionError as exc:
            assert "Testablehnung" in str(exc)
        else:
            raise AssertionError("Normfehler wurde nicht gemeldet")
    assert not rejected.exists()
    window.close_tab(0)

print("Faltvorschau, Druck-PDF, Seitenreihenfolge, Originaltext, Vektoren und PDF/A geprüft.")

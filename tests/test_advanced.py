from pathlib import Path
from PySide6.QtWidgets import QApplication
import pymupdf as pdf
from pdf_werkbank import recognize_page, redact_area, replace_span, save_copy, text_spans

app = QApplication([])
work = Path(__file__).parent
with pdf.open() as doc:
    page = doc.new_page()
    page.insert_text((50, 80), "Vertraulich: IBAN 000123456", fontsize=16)
    page.insert_text((50, 115), "Erhalten bleibt diese Zeile", fontsize=16)
    page.draw_rect(pdf.Rect(60, 150, 225, 200), color=(1, 0, 0), fill=(1, 0, 0))
    page.insert_text((50, 250), "Änderung möglich", fontsize=20)
    raster = page.get_pixmap(matrix=pdf.Matrix(2, 2))
    page.insert_image(pdf.Rect(45, 300, 345, 725), pixmap=raster)
    target = next(span for _, span in text_spans(page) if "Änderung" in span["text"])
    replace_span(page, target, "Korrektur möglich")
    assert "Änderung möglich" not in page.get_text()
    assert "Korrektur möglich" in page.get_text()
    redact_area(page, pdf.Rect(48, 58, 300, 88))
    redact_area(page, pdf.Rect(80, 160, 160, 183))
    redact_area(page, pdf.Rect(65, 325, 170, 350))
    save_copy(doc, work / "test-redacted.pdf")
with pdf.open(work / "test-redacted.pdf") as check:
    page = check[0]
    assert "000123456" not in page.get_text()
    assert "Erhalten bleibt diese Zeile" in page.get_text()
    assert "Korrektur möglich" in page.get_text()
    assert not any(b"000123456" in (check.xref_stream(xref) or b"") for xref in range(1, check.xref_length()))
    result = page.get_pixmap()
    assert result.pixel(100, 170) == (0, 0, 0)
    assert result.pixel(100, 335) == (0, 0, 0)
    img = pdf.Pixmap(check, page.get_images(full=True)[0][0])
    assert img.pixel(150, 140) == (255, 255, 255)
    result.save(work / "redacted-page.png")

with pdf.open() as doc:
    source = doc.new_page()
    source.insert_text((50, 100), "Prüfung für die PDF Werkbank", fontsize=24)
    source.insert_text((50, 150), "The quick brown fox jumps over the lazy dog.", fontsize=20)
    raster = source.get_pixmap(matrix=pdf.Matrix(3, 3))
    scan = doc.new_page()
    scan.insert_image(scan.rect, pixmap=raster)
    assert not scan.get_text().strip()
    before = scan.get_pixmap().samples
    recognize_page(scan, "deu+eng", work / "tessdata")
    assert "Prüfung für die PDF Werkbank" in scan.get_text(), repr(scan.get_text())
    assert "quick brown fox" in scan.get_text()
    assert before == scan.get_pixmap().samples
    save_copy(doc, work / "test-ocr.pdf")
with pdf.open(work / "test-ocr.pdf") as check:
    assert "Prüfung" in check[1].get_text()
    assert check[1].search_for("Werkbank")
print("Textkorrektur, Entfernung von Text/Bild/Vektor, unsichtbare deutsche und englische OCR erfolgreich.")



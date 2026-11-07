# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QLabel, QMessageBox, QPushButton

from intake import InvoiceField, duplicate_groups, inspect_duplicate, invoice_match_key
from pdf_werkbank import Window


XML = b"""<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
xmlns:c="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
xmlns:a="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
<c:ID>RE-0042</c:ID><c:IssueDate>2026-11-07</c:IssueDate><c:DocumentCurrencyCode>EUR</c:DocumentCurrencyCode>
<a:LegalMonetaryTotal><c:PayableAmount currencyID="EUR">1234.90</c:PayableAmount></a:LegalMonetaryTotal></Invoice>"""


def key(value):
    return invoice_match_key(tuple(InvoiceField(text, None, None) for text in (" RE-0042 ", "07.11.2026", value)))


assert key("€ 1.234,90") == key("1,234.90 EUR") == key("1234,900 EUR")
assert key("1234,90 USD") != key("1234,90 EUR")
assert key("1234,90") != key("1234,90 EUR")
assert key("USD 1234,90 EUR") is None
assert invoice_match_key((None, None, None)) is None

app = QApplication([])
if os.environ.get("PDF_WERKBANK_QA_OUTPUT"):
    font = QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts/arial.ttf"))
    if font >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font)[0], 10))
with TemporaryDirectory() as folder:
    root = Path(folder)
    nested = root / "Archiv"
    nested.mkdir()
    first = root / "Scan.pdf"
    copy = nested / "Kopie.pdf"
    xml = root / "Rechnung.xml"
    other = root / "Andere Währung.xml"
    invalid = root / "Defekt.pdf"
    tessdata = Path(os.environ.get("PDF_WERKBANK_QA_TESSDATA", root))
    real_ocr = (tessdata / "deu.traineddata").is_file()
    with pdf.open() as text, pdf.open() as output:
        page = text.new_page()
        page.insert_text((65, 105), "Rechnungsnummer: RE-0042\nRechnungsdatum: 07.11.2026\nZu zahlen: 1234,90 EUR",
                         fontsize=22)
        if real_ocr:
            output.new_page().insert_image(page.rect, pixmap=page.get_pixmap(dpi=175))
            output.save(first)
        else:
            text.save(first)
    copy.write_bytes(first.read_bytes())
    xml.write_bytes(XML)
    other.write_bytes(XML.replace(b"EUR", b"USD"))
    invalid.write_bytes(b"broken")
    files = [first, copy, xml, other, invalid]
    before = {path: path.read_bytes() for path in files}
    cache = {}
    sources = [inspect_duplicate(path, tessdata, cache) for path in files]
    exact, possible = duplicate_groups(sources)
    assert [[source.path for source in group] for group in exact] == [[first, copy]]
    assert [[source.path for source in group] for group in possible] == [[first, copy, xml]]
    assert sources[-1].problem and len(cache) == 4
    assert not inspect_duplicate(root / "vermisst.pdf", tessdata, cache).digest

    window = Window()
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    seen = []
    def review(dialog):
        groups, left, right = dialog.findChildren(QComboBox)
        assert groups.count() == 2 and "Dateiidentisch" in groups.itemText(0)
        groups.setCurrentIndex(1)
        assert "Möglicher Doppelbeleg" in groups.currentText()
        assert left.count() == right.count() == 3
        for combo in (left, right):
            combo.setCurrentIndex(next(index for index in range(combo.count()) if
                                       combo.itemText(index).startswith("Rechnung.xml" if combo == right else "Scan.pdf")))
        labels = dialog.findChildren(QLabel)
        assert any("Rechnungsnummer  RE-0042" in label.text() and "Betrag  1234,90 EUR" in label.text() for label in labels)
        assert any(label.pixmap() is not None and not label.pixmap().isNull() for label in labels)
        seen.append(True)
        if os.environ.get("PDF_WERKBANK_QA_OUTPUT"):
            dialog.show()
            QApplication.processEvents()
            assert dialog.grab().save(str(Path(os.environ["PDF_WERKBANK_QA_OUTPUT"]) / "Dubletten-119.png"))
        return QDialog.DialogCode.Rejected

    if real_ocr:
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(root)), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(QDialog, "exec", review):
            window.duplicate_check()
        assert seen
    with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(first), str(xml)], "")), patch.object(
            window, "duplicate_check") as dispatch:
        def invoice_dialog(dialog):
            next(button for button in dialog.findChildren(QPushButton) if "auf Dubletten prüfen" in button.text()).click()
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog, "exec", invoice_dialog):
            window.invoice_review()
        dispatch.assert_called_once_with([first, xml])
    assert all(path.read_bytes() == value for path, value in before.items())

print("Dateiidentische Kopien, Scan und XML, Währungstrennung, Vorschau und Rechnungseingang geprüft.")

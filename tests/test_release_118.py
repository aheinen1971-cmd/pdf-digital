# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QListWidget, QMessageBox, QPushButton, QTableWidget

from intake import audit_pdf, read_invoice, structured_invoice
from office_export import export_linked_sheet
from pdf_werkbank import Window


UBL = b"""<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
xmlns:c="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
xmlns:a="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
<c:ID>RE-0042</c:ID><c:IssueDate>2026-11-07</c:IssueDate><c:DocumentCurrencyCode>EUR</c:DocumentCurrencyCode>
<a:LegalMonetaryTotal><c:TaxInclusiveAmount>16.90</c:TaxInclusiveAmount>
<c:PayableAmount currencyID="EUR">11.90</c:PayableAmount></a:LegalMonetaryTotal></Invoice>"""
CII = b"""<r:CrossIndustryInvoice xmlns:r="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
xmlns:a="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
xmlns:u="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
<r:ExchangedDocument><a:ID>RE-0042</a:ID><a:IssueDateTime><u:DateTimeString format="102">20261107</u:DateTimeString>
</a:IssueDateTime></r:ExchangedDocument><r:SupplyChainTradeTransaction><a:ApplicableHeaderTradeSettlement>
<a:InvoiceCurrencyCode>EUR</a:InvoiceCurrencyCode><a:SpecifiedTradeSettlementHeaderMonetarySummation>
<a:DuePayableAmount>11.90</a:DuePayableAmount></a:SpecifiedTradeSettlementHeaderMonetarySummation>
</a:ApplicableHeaderTradeSettlement></r:SupplyChainTradeTransaction></r:CrossIndustryInvoice>"""

app = QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    output = root / "Ausgabe"
    output.mkdir()
    first, second = root / "A.pdf", root / "B.pdf"
    scan, embedded = root / "Scan.pdf", root / "ZUGFeRD.pdf"
    xml = root / "XRechnung.xml"
    xml.write_bytes(UBL)
    for file, label in ((first, "Erstes Dokument"), (second, "Zweites Dokument"), (embedded, "ZUGFeRD Sichtfassung")):
        with pdf.open() as doc:
            page = doc.new_page()
            page.insert_text((65, 105), label, fontsize=22)
            if file == embedded:
                doc.embfile_add("factur-x.xml", CII)
            doc.save(file)
    with pdf.open() as text, pdf.open() as image:
        page = text.new_page()
        page.insert_text((65, 105), "Rechnungsnummer: RE-0042\nRechnungsdatum: 07.11.2026\nZu zahlen: 11,90 EUR",
                         fontsize=22)
        image.new_page().insert_image(page.rect, pixmap=page.get_pixmap(dpi=150))
        image.save(scan)

    expected = ["RE-0042", "07.11.2026", "11,90 EUR"]
    for path in (xml, embedded):
        fields, origin = read_invoice(path, None)
        assert [field.value for field in fields] == expected
        assert "XML" in origin and all(field.page is None for field in fields)
    assert structured_invoice(CII)[1] == "CII"
    for bad in ('<!DOCTYPE Invoice [<!ENTITY x "x">]><Invoice/>'.encode("utf-16"),
                UBL.replace(b"2026-11-07", b"2026-02-31"), UBL.replace(b"11.90", b"NaN")):
        try:
            structured_invoice(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Ungültige Rechnungs-XML akzeptiert")

    window = Window()
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    original = {file: file.read_bytes() for file in (first, second, scan, embedded, xml)}
    merged = output / "Zusammen.pdf"

    def reorder(dialog):
        rows = dialog.findChild(QListWidget)
        assert rows.count() == 2 and "1 Seite" in rows.item(0).text()
        rows.setCurrentRow(1)
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Nach oben").click()
        return QDialog.DialogCode.Accepted

    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(merged), "")), patch.object(
            QDialog, "exec", return_value=QDialog.DialogCode.Rejected):
        window.explorer_action("--explorer-merge", [str(first), str(second)])
    assert not merged.exists()
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(merged), "")), patch.object(QDialog, "exec", reorder):
        window.explorer_action("--explorer-merge", [str(first), str(second)])
    with pdf.open(merged) as doc:
        assert [page.get_text().strip() for page in doc] == ["Zweites Dokument", "Erstes Dokument"]

    conflict = output / "A - Stapel.pdf"
    conflict.write_bytes(b"keep")
    def workflow(dialog):
        rows = dialog.findChild(QListWidget)
        assert "existiert bereits" in rows.item(0).text()
        assert "B - Stapel.pdf" in rows.item(1).text()
        return QDialog.DialogCode.Accepted
    with patch.object(QFileDialog, "getExistingDirectory", return_value=str(output)), patch.object(
            QDialog, "exec", workflow), patch.object(QMessageBox, "exec", return_value=0):
        window.run_batch([first, second], ["rotate"])
    assert conflict.read_bytes() == b"keep"
    with pdf.open(output / "B - Stapel.pdf") as doc:
        assert doc[0].rotation == 90
    other = root / "andere"
    other.mkdir()
    duplicate = other / second.name
    duplicate.write_bytes(second.read_bytes())
    with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
        assert not window.batch_preview([second, duplicate], other, ("rotate",))

    empty_report = output / "Leer.xlsx"
    export_linked_sheet([], empty_report, "Archiv-Check", ("Datei",), (40,), 1)
    with ZipFile(empty_report) as archive:
        assert b"hyperlinks" not in archive.read("xl/worksheets/sheet1.xml")

    workbook = output / "Rechnungen.xlsx"
    def review(dialog):
        table = dialog.findChild(QTableWidget)
        assert table.rowCount() == 3
        for row in range(3):
            assert [table.item(row, col).text() for col in range(3)] == expected
        table.setCurrentCell(1, 0)
        table.setCurrentCell(2, 2)
        table.item(1, 0).setText("RE-korrigiert")
        next(button for button in dialog.findChildren(QPushButton) if "Excel speichern" in button.text()).click()
        return QDialog.DialogCode.Accepted

    if Path(os.environ.get("PDF_WERKBANK_QA_TESSDATA", ""), "deu.traineddata").is_file():
        with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(scan), str(xml), str(embedded)], "")), patch.object(
                QFileDialog, "getSaveFileName", return_value=(str(workbook), "")), patch.object(QDialog, "exec", review):
            window.invoice_review()
        with ZipFile(workbook) as archive:
            assert b"RE-korrigiert" in archive.read("xl/worksheets/sheet1.xml")
            links = ElementTree.fromstring(archive.read("xl/worksheets/_rels/sheet1.xml.rels"))
            assert [link.get("Target") for link in links] == [path.as_uri() for path in (scan, xml, embedded)]

    tools = tuple(os.environ.get("PDF_WERKBANK_QA_" + key) for key in ("JAVA", "VERA"))
    if all(tools) and Path(os.environ.get("PDF_WERKBANK_QA_TESSDATA", ""), "deu.traineddata").is_file():
        report = output / "Archivbericht.xlsx"
        def archive_dialog(dialog):
            if dialog.windowTitle() == "Stapelvorschau":
                assert "Ohne Suchtext: 1" in dialog.findChild(QListWidget).item(0).text()
                return QDialog.DialogCode.Accepted
            table = dialog.findChild(QTableWidget)
            dialog.findChild(QComboBox).setCurrentIndex(2)
            rows = [row for row in range(table.rowCount()) if not table.isRowHidden(row)]
            assert len(rows) == 1 and table.item(rows[0], 0).text() == scan.name
            next(button for button in dialog.findChildren(QPushButton) if "Ergebnisse als Excel" in button.text()).click()
            table.selectRow(rows[0])
            next(button for button in dialog.findChildren(QPushButton) if "Suchtext ergänzen" in button.text()).click()
            return QDialog.DialogCode.Accepted
        with patch.object(QFileDialog, "getExistingDirectory", side_effect=[str(root), str(output)]), patch.object(
                QFileDialog, "getSaveFileName", return_value=(str(report), "")), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.No), patch.object(
                QMessageBox, "exec", return_value=0), patch.object(window, "standard_tools", return_value=tools), patch.object(
                QDialog, "exec", archive_dialog):
            window.archive_check()
        with ZipFile(report) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml")
            assert b"Scan.pdf" in sheet and b"A.pdf" not in sheet
        repaired = output / "Scan - OCR Archiv.pdf"
        assert repaired.is_file()
        result = audit_pdf(repaired, tools)
        assert result.archive == "Bestanden" and not result.missing_text
        if os.environ.get("PDF_WERKBANK_QA_OUTPUT"):
            (Path(os.environ["PDF_WERKBANK_QA_OUTPUT"]) / "OCR-Archiv-118.pdf").write_bytes(repaired.read_bytes())

    assert all(path.read_bytes() == before for path, before in original.items())
    for session in window.sessions:
        session.doc.close()

print("Stapelvorschau, Reihenfolge, Zielkonflikte, Archivbericht und OCR-Kopie, UBL/CII und gemeinsame Rechnungsliste geprüft.")

# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton, QTableWidget

from filing import file_signature
from invoice_validation import InvoiceValidation, KOSIT_FOLDER
from invoice_watch import invoice_archive_state, read_watched_invoice
from pdf_werkbank import Window


XML = b"""<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
xmlns:c="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
xmlns:a="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
<c:ID>R-NUMBER</c:ID><c:IssueDate>2026-11-07</c:IssueDate><c:DocumentCurrencyCode>EUR</c:DocumentCurrencyCode>
<a:LegalMonetaryTotal><c:PayableAmount currencyID="EUR">35.00</c:PayableAmount></a:LegalMonetaryTotal></Invoice>"""


def until(condition):
    for _ in range(450):
        if condition():
            return
        QTest.qWait(50)
    raise AssertionError("Zustand nicht erreicht.")


app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    incoming, destination = root / "Eingang", root / "Archiv"
    incoming.mkdir()
    destination.mkdir()
    a, b, c, d, e, f = [incoming / f"{letter}.xml" for letter in "ABCDEF"]
    for path in (a, b, c, d, e, f):
        number = root.name.encode("ascii") + (b"C" if path == d else path.stem.encode("ascii"))
        path.write_bytes(XML.replace(b"NUMBER", number))
    (destination / e.name).write_bytes(b"bereits belegt")
    window = Window()
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    settings = window.favorite_settings
    settings.setValue("invoice-watch/folder", str(incoming))

    def validator(data, *_):
        return InvoiceValidation("rejected" if root.name.encode("ascii") + b"B" in data else "passed",
                                 "Testregeln", ())

    with patch("invoice_watch.validate_invoice_xml", side_effect=validator), patch.object(
            window, "invoice_java", return_value=(root / "java.exe", root / "rules")), patch(
            "pdf_werkbank.ensure_validator", return_value=(root / "validator.jar", root / "rules")) as setup:
        window.invoice_folder()
        dialog = window._invoice_folder_window
        table = dialog.findChild(QTableWidget)
        until(lambda: table.rowCount() == 6)
        table.selectAll()
        assert len(table.selectionModel().selectedRows()) == 6
        check = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Auswahl prüfen")
        archive = next(button for button in dialog.findChildren(QPushButton) if "Archiv freigeben" in button.text())
        checked = []

        def check_dialog(report):
            assert report.windowTitle() == "Ergebnis der Sammelprüfung"
            rows = report.findChild(QTableWidget)
            assert rows.rowCount() == 6
            assert "Fehler" in rows.item(1, 1).text()
            assert "Dublette" in rows.item(2, 2).text() and "Dublette" in rows.item(3, 2).text()
            checked.append(True)
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", check_dialog):
            check.click()
        assert checked and setup.call_count == 1 and len(table.selectionModel().selectedRows()) == 6

        def confirm(report):
            assert report.windowTitle() == "Archivfreigabe für Auswahl"
            rows = report.findChild(QTableWidget)
            approved = [rows.item(row, 0).checkState() == Qt.CheckState.Checked for row in range(6)]
            assert approved == [True, False, False, False, False, True]
            assert not rows.item(4, 0).flags() & Qt.ItemFlag.ItemIsEnabled
            assert "Zieldatei existiert" in rows.item(4, 3).text()
            assert not (destination / a.name).exists()
            return QDialog.DialogCode.Rejected

        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(destination)), patch.object(QDialog, "exec", confirm):
            archive.click()
        assert set(path.name for path in destination.iterdir()) == {e.name}

        def commit(report):
            confirm(report)
            f.write_bytes(f.read_bytes() + b"\n")
            return QDialog.DialogCode.Accepted

        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(destination)), patch.object(
                QDialog, "exec", commit), patch.object(QMessageBox, "exec", return_value=0):
            archive.click()
        assert (destination / a.name).read_bytes() == a.read_bytes()
        assert (destination / e.name).read_bytes() == b"bereits belegt"
        assert not (destination / f.name).exists()
        assert {path.name for path in destination.iterdir()} == {a.name, e.name}
        dialog.done(0)
        settings.remove("invoice-watch/folder")

    ubl = os.environ.get("PDF_WERKBANK_QA_UBL")
    base = Path(os.environ.get("LOCALAPPDATA", str(root))) / "PDF Werkbank" / "Prüfwerkzeuge"
    tool_folder = base / KOSIT_FOLDER
    java = base / "Java/bin/java.exe"
    if ubl and java.is_file() and (tool_folder / "validator.jar").is_file():
        valid, invalid = root / "Gültig.xml", root / "Ungültig.xml"
        valid.write_bytes(Path(ubl).read_bytes())
        invalid.write_bytes(b"<Invoice>")
        originals = [read_watched_invoice(path, file_signature(path), root) for path in (valid, invalid)]
        with patch.object(window, "invoice_java", return_value=(java, tool_folder)):
            results = window.check_invoice_batch(originals, window)
        assert [item.validation.status for _, item, _ in results] == ["passed", "rejected"]
        assert not invoice_archive_state(results[0][1])[1]

print("Mehrfachauswahl, Sammelprüfung, Dubletten, Archivvorschau, Abbruch, Zielkonflikt und Dateiveränderung geprüft.")

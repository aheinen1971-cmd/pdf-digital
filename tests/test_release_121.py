# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton, QTableWidget

from filing import file_signature
from invoice_validation import InvoiceValidation, KOSIT_FOLDER
from invoice_watch import IntakeSettler, archive_setting_key, archive_watched_invoice, read_watched_invoice
from pdf_werkbank import Window


XML = b"""<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
xmlns:c="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
xmlns:a="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
<c:ID>R-901</c:ID><c:IssueDate>2026-11-07</c:IssueDate><c:DocumentCurrencyCode>EUR</c:DocumentCurrencyCode>
<a:LegalMonetaryTotal><c:PayableAmount currencyID="EUR">35.00</c:PayableAmount></a:LegalMonetaryTotal></Invoice>"""


def until(condition, timeout=12000):
    for _ in range(timeout // 40):
        if condition():
            return
        QTest.qWait(40)
    raise AssertionError("Zustand nicht erreicht.")


app = QApplication([])
with TemporaryDirectory() as directory:
    root = Path(directory)
    incoming, archive = root / "Eingang", root / "Archiv"
    incoming.mkdir()
    archive.mkdir()
    first = incoming / "Rechnung.xml"
    first.write_bytes(XML[:30])
    settle = IntakeSettler(incoming)
    assert not settle.poll(0) and not settle.poll(2)
    first.write_bytes(XML)
    assert not settle.poll(3) and not settle.poll(5)
    assert settle.poll(5.5) == {first: file_signature(first)}

    tessdata = Path(os.environ.get("PDF_WERKBANK_QA_TESSDATA", root))
    record = read_watched_invoice(first, file_signature(first), tessdata)
    assert record.source.fields[0].value == "R-901" and record.check_problem == "E-Rechnung noch nicht geprüft"
    with first.open("ab") as writer:
        if os.name == "nt":
            try:
                read_watched_invoice(first, file_signature(first), tessdata)
            except OSError:
                pass
            else:
                raise AssertionError("Eine offene Schreibdatei wurde eingelesen.")
        writer.write(b" ")
    try:
        archive_watched_invoice(record, incoming, archive)
    except ValueError:
        pass
    else:
        raise AssertionError("Eine geänderte Datei wurde freigegeben.")
    assert not list(archive.iterdir())
    first.write_bytes(XML)

    base = Path(os.environ.get("LOCALAPPDATA", str(root))) / "PDF Werkbank" / "Prüfwerkzeuge"
    ubl = os.environ.get("PDF_WERKBANK_QA_UBL")
    tool_folder = base / KOSIT_FOLDER
    java = base / "Java/bin/java.exe"
    if ubl and java.is_file() and (tool_folder / "validator.jar").is_file():
        valid = root / "Offizielle XRechnung.xml"
        valid.write_bytes(Path(ubl).read_bytes())
        checked = read_watched_invoice(valid, file_signature(valid), tessdata,
                                       (java, tool_folder / "validator.jar", tool_folder / "rules"))
        assert checked.validation.status == "passed"

    window = Window()
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    settings = window.favorite_settings
    settings.setValue("invoice-watch/folder", str(incoming))
    settings.remove(archive_setting_key(first))
    second = incoming / "Kopie.xml"
    with patch("invoice_watch.validate_invoice_xml", return_value=InvoiceValidation("rejected", "Testprofil", ())):
        window.invoice_folder()
        dialog = window._invoice_folder_window
        window.invoice_folder()
        assert dialog is window._invoice_folder_window
        table = dialog.findChild(QTableWidget)
        with second.open("wb") as writer:
            writer.write(XML)
            writer.flush()
            until(lambda: table.rowCount() == 1)
            if os.name == "nt":
                QTest.qWait(1100)
                assert table.rowCount() == 1
            assert not list(archive.iterdir())
        until(lambda: table.rowCount() == 2)
        assert "Dateiidentisch" in table.item(0, 3).text()
        assert table.item(0, 2).text() in ("Fehler gefunden", "E-Rechnung noch nicht geprüft")

        index = next(row for row in range(table.rowCount()) if table.item(row, 0).text() == first.name)
        table.selectRow(index)
        button = next(b for b in dialog.findChildren(QPushButton) if "Archiv freigeben" in b.text())
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(archive)), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            button.click()
        assert not list(archive.iterdir())
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(archive)), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            button.click()
        assert first.read_bytes() == (archive / first.name).read_bytes() == XML
        assert "Freigegeben" in table.item(index, 4).text()
        unchanged = read_watched_invoice(first, file_signature(first), tessdata)
        try:
            archive_watched_invoice(unchanged, incoming, archive)
        except FileExistsError:
            pass
        else:
            raise AssertionError("Das vorhandene Archivziel wurde überschrieben.")
        try:
            archive_watched_invoice(unchanged, incoming, incoming)
        except ValueError:
            pass
        else:
            raise AssertionError("Der überwachte Ordner wurde als Archiv akzeptiert.")
        dialog.reject()
        assert window._invoice_folder_window is dialog and not dialog.isVisible()
        window.invoice_folder()
        dialog = window._invoice_folder_window
        table = dialog.findChild(QTableWidget)
        until(lambda: table.rowCount() == 2)
        index = next(row for row in range(table.rowCount()) if table.item(row, 0).text() == first.name)
        assert "Freigegeben" in table.item(index, 4).text()
        dialog.done(0)
        assert window._invoice_folder_window is None
    settings.remove(archive_setting_key(first))
    settings.remove("invoice-watch/folder")
    settings.remove("invoice-watch/archive")

print("Kopierwartezeit, offene Datei, KoSIT, Dubletten, Freigabe, Kollisionsschutz und Neustart geprüft.")

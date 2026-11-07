# SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

import pymupdf as pdf
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization.pkcs12 import serialize_key_and_certificates
from cryptography.x509.oid import NameOID
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QInputDialog, QListWidget, QCheckBox, QPushButton

from office_export import export_excel, export_word
from pdf_signatures import sign_pdf, verify_pdf
from pdf_werkbank import Session, Window, save_copy

app = QApplication([])

with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    root = Path(folder)
    source = root / "Original.pdf"
    with pdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 75), "Probe für Word & Excel")
        doc.new_page().insert_text((40, 75), "Zweite Seite")
        doc.save(source)

    window = Window()
    window.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    window.open_path(str(source))
    session = window.current
    try:
        with patch.object(QInputDialog, "getItem", return_value=("Dropdown", True)), patch.object(
                QInputDialog, "getText", return_value=("Auswahl", True)), patch.object(
                QInputDialog, "getMultiLineText", return_value=("Rot\nBlau", True)):
            assert window.create_field(session.doc[0], pdf.Rect(40, 100, 140, 125))
        with patch.object(QInputDialog, "getText", return_value=("2", True)):
            assert window.put_link(session.doc[0], pdf.Rect(40, 130, 160, 148))
        with patch.object(QInputDialog, "getText", return_value=("1-2", True)):
            window.add_page_numbers()
        with patch.object(QInputDialog, "getItem", return_value=("GEPRÜFT", True)):
            assert window.put_stamp(session.doc[0], pdf.Point(40, 160))
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            # Update a real existing text run while keeping its text.
            page = session.doc[0]
            span = window.text_at(page, pdf.Point(55, 73))
            assert window.styled_text(page, pdf.Point(55, 73), span)
        def bookmarks(dialog):
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "Hinzufügen").click()
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog, "exec", bookmarks), patch.object(QInputDialog, "getText", return_value=("Anfang", True)):
            window.bookmarks()
        saved = root / "bearbeitet.pdf"
        save_copy(session.doc, saved)
        with pdf.open(saved) as reopened:
            page = reopened[0]
            field = next(page.widgets())
            assert field.field_name == "Auswahl" and field.choice_values == ["Rot", "Blau"]
            assert page.get_links()[0]["page"] == 1
            assert "GEPRÜFT" in page.get_text()
            assert reopened.get_toc()[0][1:] == ["Anfang", 1]
            assert reopened[1].get_text().strip().endswith("2")
            export_word(reopened, root / "Probe.docx")
            export_excel(reopened, root / "Probe.xlsx")
        with ZipFile(root / "Probe.docx") as word:
            assert b"Probe f" in word.read("word/document.xml")
        with ZipFile(root / "Probe.xlsx") as excel:
            assert b"sheet2.xml" in excel.read("xl/_rels/workbook.xml.rels")
            assert b"Zweite Seite" in excel.read("xl/worksheets/sheet2.xml")
        locked = root / "Geschützt.pdf"
        save_copy(session.doc, locked, encryption=pdf.PDF_ENCRYPT_AES_256, owner_pw="Testpass",
                  user_pw="Testpass", password="Testpass")
        with pdf.open(locked) as protected:
            assert protected.needs_pass and not protected.authenticate("Falsch")
        restored = Session(locked, "Testpass")
        try:
            restored.checkpoint()
            restored.doc[0].set_rotation(90)
            restored.undo()
            assert restored.doc[0].rotation == 0
            save_copy(restored.doc, root / "Kopie.pdf", password=restored.password, encryption=pdf.PDF_ENCRYPT_KEEP)
            with pdf.open(root / "Kopie.pdf") as check:
                assert check.needs_pass and check.authenticate("Testpass")
        finally:
            restored.doc.close()
    finally:
        session.changed = False
        window.close_tab(0)

    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PDF Werkbank Test")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(True, True, False, False, False, True, True, None, None), critical=True)
            .sign(key, hashes.SHA256()))
    p12 = root / "test.p12"
    p12.write_bytes(serialize_key_and_certificates(b"test", key, cert, None,
                                                   serialization.BestAvailableEncryption(b"test")))
    trust = root / "test.pem"
    trust.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    signed = root / "signiert.pdf"
    sign_pdf(source, signed, p12, "test")
    result = verify_pdf(signed, trust)
    assert result and result[0][1:4] == (True, True, True), result
    with pdf.open(signed) as altered:
        altered[0].insert_text((40, 300), "Nach der Signatur geändert")
        altered.saveIncr()
    assert not verify_pdf(signed, trust)[0][3]

    scanner = Window()
    output = root / "Stapel.pdf"
    def fake_scan(_):
        page_doc = pdf.open()
        page_doc.new_page().insert_text((30, 90), str(len(scanned) + 1))
        scanned.append(True)
        return page_doc
    scanned = []
    scanner.scan_page = fake_scan
    scanner.save_scanned_document = lambda doc: save_copy(doc, output)
    def scan_dialog(dialog):
        add = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Seite scannen")
        add.click()
        add.click()
        listing = dialog.findChild(QListWidget)
        listing.insertItem(0, listing.takeItem(1))
        dialog.findChild(QCheckBox).setChecked(False)
        return QDialog.DialogCode.Accepted
    with patch.object(QDialog, "exec", scan_dialog):
        scanner.scan_stack()
    with pdf.open(output) as merged:
        assert [page.get_text().strip() for page in merged] == ["2", "1"]

    batch_dir = root / "stapel-ausgabe"
    batch_dir.mkdir()
    window = Window()
    with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(source), str(locked)], "PDF")), patch.object(
            QFileDialog, "getExistingDirectory", return_value=str(batch_dir)), patch.object(
            QInputDialog, "getItem", return_value=("Alle Seiten nach rechts drehen", True)), patch.object(
            QMessageBox, "exec", return_value=0), patch.object(
            QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
        window.batch_process()
        with pdf.open(batch_dir / "Original - Stapel.pdf") as result:
            assert all(page.rotation == 90 for page in result)
        assert not (batch_dir / "Geschützt - Stapel.pdf").exists()
    with pdf.open(source) as result:
        assert all(page.rotation == 0 for page in result)


print("Scan-Stapel, Text, Felder, Links, Lesezeichen, Stempel, Seitenzahlen, Office-Export, Verschlüsselung und Signatur geprüft.")

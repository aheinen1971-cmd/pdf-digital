# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf as pdf

from invoice_validation import ENGINE_HASH, validate_invoice_xml
from pdf_standards import export_archive, validate_standard


PROBES = ("PDF/A", "E-Rechnung", "OCR", "Lokale KI")
USB = {"PDF/A": "USB-Zusatzpaket PDF/A (Java und veraPDF)",
       "E-Rechnung": "USB-Zusatzpaket E-Rechnungen (Java und KoSIT)",
       "OCR": "USB-Offline-Installation (OCR Deutsch und Englisch)",
       "Lokale KI": "USB-Zusatzpaket Lokale KI (Sprachmodell)"}

INVOICE = b"""<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</cbc:CustomizationID>
 <cbc:ProfileID>urn:fdc:peppol.eu:2017:poacc:billing:01:1.0</cbc:ProfileID>
 <cbc:ID>WERKBANK-OFFLINE-TEST</cbc:ID>
 <cbc:IssueDate>2026-01-01</cbc:IssueDate>
 <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
 <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
</Invoice>"""


def sample_page():
    document = pdf.open()
    page = document.new_page(width=440, height=145)
    page.insert_text((24, 48), "Offline Test Werkbank", fontsize=27)
    page.insert_text((24, 96), "Invoice check ready", fontsize=27)
    return document


def check_pdfa(java, jar):
    if not java.is_file() or not jar.is_file():
        return "Fehlt", "Java fehlt." if not java.is_file() else "veraPDF fehlt."
    profile = Path(os.environ["WINDIR"]) / "System32/spool/drivers/color/sRGB Color Space Profile.icm"
    with sample_page() as sample, TemporaryDirectory(prefix="werkbank-offline-") as folder:
        target = Path(folder) / "PDF-A-Test.pdf"
        export_archive(sample, target, profile)
        passed, details = validate_standard(java, jar, target, "2b")
    if not passed:
        return "Startfehler", "veraPDF hat die PDF/A-Testdatei abgelehnt. " + " · ".join(details[:3])
    return "Bereit", "veraPDF hat die Testdatei als PDF/A-2b bestätigt."


def check_invoice(java, jar, rules):
    if not java.is_file() or not jar.is_file() or not (rules / "scenarios.xml").is_file():
        return "Fehlt", "Java fehlt." if not java.is_file() else "KoSIT oder das XRechnungs-Regelpaket fehlt."
    with jar.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != ENGINE_HASH:
            return "Startfehler", "Die Prüfsumme des KoSIT-Prüfers stimmt nicht."
    result = validate_invoice_xml(INVOICE, java, jar, rules)
    if (result.status != "rejected" or not result.scenario
            or not any(issue.rule.startswith("cvc-") for issue in result.issues)
            or not any(issue.rule == "BT-115" for issue in result.issues)):
        return "Startfehler", "KoSIT hat den erwarteten Fehler in der Testrechnung nicht gemeldet."
    return "Bereit", "KoSIT hat die XRechnung erkannt und die unvollständige Testrechnung abgewiesen."


def check_ocr(data):
    missing = [language for language in ("deu", "eng") if not (data / f"{language}.traineddata").is_file()]
    if missing:
        return "Fehlt", "OCR-Sprachdaten fehlen: " + ", ".join(missing)
    with sample_page() as sample, pdf.open() as image:
        page = image.new_page(width=440, height=145)
        page.insert_image(page.rect, pixmap=sample[0].get_pixmap(matrix=pdf.Matrix(2, 2), alpha=False))
        for language, expected in (("deu", "offline test werkbank"), ("eng", "invoice check ready")):
            recognized = page.get_textpage_ocr(language=language, dpi=160, full=True, tessdata=str(data))
            text = " ".join(page.get_text(textpage=recognized).casefold().split())
            if expected not in text:
                return "Startfehler", f"Die OCR hat den erwarteten Text für {language} nicht erkannt."
    return "Bereit", "Der Testscan wurde auf Deutsch und Englisch gelesen."


def run_probe(function, *args):
    try:
        return function(*args)
    except Exception as exc:
        return "Startfehler", str(exc) or type(exc).__name__

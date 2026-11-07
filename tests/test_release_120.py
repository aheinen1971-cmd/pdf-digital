# SPDX-License-Identifier: AGPL-3.0-only
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QPlainTextEdit, QPushButton, QTableWidget

from intake import invoice_xml
from invoice_validation import parse_report, validate_invoice_xml, xml_root
from pdf_werkbank import Window


REPORT = """<rep:report xmlns:rep="http://www.xoev.de/de/validator/varl/1"
xmlns:s="http://www.xoev.de/de/validator/framework/1/scenarios" valid="false">
<rep:scenarioMatched><s:scenario><s:name>EN16931 XRechnung (UBL Invoice)</s:name>
<s:customLevel level="warning">BR-CL-23 BR-CL-21</s:customLevel></s:scenario>
<rep:validationStepResult id="val-sch.1" valid="false">
<rep:message code="BR-CL-23" level="error" xpathLocation="/Q{urn:test}Invoice[1]">Test zu BT-5</rep:message>
</rep:validationStepResult></rep:scenarioMatched><rep:assessment><rep:accept/></rep:assessment></rep:report>"""

app = QApplication([])
with TemporaryDirectory() as folder:
    folder = Path(folder)
    report = folder / "report.xml"
    report.write_text(REPORT, encoding="utf-8")
    warning = parse_report(report, 0)
    assert warning.status == "passed" and warning.issues[0].level == "warning"
    assert warning.issues[0].field == "Rechnungswährung" and warning.issues[0].location == "/Invoice[1]"
    for invalid, code in ((REPORT, 1), (REPORT.replace("BR-CL-23 BR-CL-21", "BR-CL-21"), 0),
                          (REPORT.replace('code="BR-CL-23"', 'code="PROCESSING_ERROR"'), 0)):
        report.write_text(invalid, encoding="utf-8")
        try:
            parse_report(report, code)
        except ValueError:
            pass
        else:
            raise AssertionError("Widersprüchlicher Bericht wurde akzeptiert.")
    try:
        xml_root('<!DOCTYPE a [<!ENTITY e "x">]><a>&e;</a>'.encode("utf-16"))
    except ValueError:
        pass
    else:
        raise AssertionError("DTD wurde akzeptiert.")
    malformed = validate_invoice_xml(b"<Invoice>", None, None, None)
    assert malformed.status == "rejected" and malformed.issues[0].rule == "XML"
    assert "Zeile 1" in malformed.issues[0].location

    java, tools, ubl, cii = (os.environ.get(f"PDF_WERKBANK_QA_{key}") for key in ("JAVA", "KOSIT", "UBL", "CII"))
    if all((java, tools, ubl, cii)):
        tools = Path(tools)
        jar, rules = tools / "validator.jar", tools / "rules"
        ubl, cii = Path(ubl).read_bytes(), Path(cii).read_bytes()
        assert validate_invoice_xml(ubl, java, jar, rules).status == "passed"
        assert validate_invoice_xml(cii, java, jar, rules).status == "passed"
        generic = cii.replace(b"urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
                              b"urn:cen.eu:en16931:2017")
        check = validate_invoice_xml(generic, java, jar, rules)
        assert check.status == "passed" and check.scenario == "EN16931 (CII)"

        invalid = re.sub(rb"<cbc:BuyerReference>.*?</cbc:BuyerReference>", b"", ubl, count=1)
        invalid = re.sub(rb"(<cbc:PayableAmount[^>]*>).*?(</cbc:PayableAmount>)", rb"\g<1>1.00\2", invalid, count=1)
        assert invalid != ubl
        rejected = validate_invoice_xml(invalid, java, jar, rules)
        assert rejected.status == "rejected"
        issues = {issue.rule: issue for issue in rejected.issues}
        assert "Käuferreferenz" in issues["BR-DE-15"].field
        assert "Zahlbetrag" in issues["BR-CO-16"].field
        assert "LegalMonetaryTotal" in issues["BR-CO-16"].location
        without_number = re.sub(rb"<cbc:ID>[^<]*</cbc:ID>", b"", ubl, count=1)
        missing = validate_invoice_xml(without_number, java, jar, rules)
        assert missing.status == "rejected" and missing.issues[0].field == "Rechnungsnummer"
        assert any(issue.location.startswith("Zeile ") for issue in missing.issues)
        unsupported = validate_invoice_xml(ubl.replace(b"urn:xeinkauf.de:kosit:xrechnung_3.0", b"urn:unbekannt"),
                                           java, jar, rules)
        assert unsupported.status == "unsupported" and unsupported.issues[0].rule == "BT-24"

        path = folder / "Eingang.xml"
        path.write_bytes(invalid)
        embedded = folder / "Eingang.pdf"
        plain = folder / "Papier.pdf"
        with pdf.open() as document:
            document.new_page()
            document.save(plain)
            document.embfile_add("factur-x.xml", cii)
            document.save(embedded)
        assert invoice_xml(embedded) == (cii, True) and invoice_xml(plain) == (None, False)
        assert validate_invoice_xml(invoice_xml(embedded)[0], java, jar, rules).status == "passed"
        window = Window()
        window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
        seen = []
        def interaction(dialog):
            if dialog.windowTitle() == "Rechnungseingang":
                button = next(button for button in dialog.findChildren(QPushButton) if "E-Rechnung der Auswahl" in button.text())
                button.click()
            else:
                assert dialog.windowTitle() == "E-Rechnung prüfen"
                table = dialog.findChild(QTableWidget)
                row = next(row for row in range(table.rowCount()) if table.item(row, 2).text() == "BR-DE-15")
                table.setCurrentCell(row, 2)
                assert "Käuferreferenz" in dialog.findChild(QPlainTextEdit).toPlainText()
                seen.append(True)
            return QDialog.DialogCode.Rejected

        with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(path)], "")), patch(
                "pdf_werkbank.ensure_validator", return_value=(jar, rules)), patch.object(QDialog, "exec", interaction):
            window.invoice_review()
        assert seen and path.read_bytes() == invalid

print("KoSIT-Berichte, Warnungen, Feldfehler, PDF-Anlage und Aufruf im Rechnungseingang geprüft.")

# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from xml.etree import ElementTree
from zipfile import ZipFile

from intake import CII, INVOICE_XML_LIMIT, RAM, UBL_COMMON, UBL_TYPES

ENGINE_URL = "https://github.com/itplr-kosit/validator/releases/download/v1.6.3/validator-1.6.3-standalone.jar"
ENGINE_HASH = "799e64befca97d4080e03608c80b85dd5a5ecc5f4ae4f35d1116ec2855b9a7c9"
RULES_URL = ("https://github.com/itplr-kosit/validator-configuration-xrechnung/releases/download/v2026-08-31/"
             "xrechnung-3.0.2-validator-configuration-2026-08-31.zip")
RULES_HASH = "2530cd107c414511c5d0462ec10f886910395abfca820db82e83d70bf01221a8"
KOSIT_FOLDER = "KoSIT-1.6.3-XRechnung-2026-08-31"
RULES_VERSION = "XRechnung 3.0.2 · KoSIT-Regelpaket 2026-08-31"
REPORT = "{http://www.xoev.de/de/validator/varl/1}"
SCENARIO = "{http://www.xoev.de/de/validator/framework/1/scenarios}"

FIELDS = {
    "BT-1": "Rechnungsnummer", "BT-2": "Rechnungsdatum", "BT-3": "Rechnungstyp",
    "BT-5": "Rechnungswährung", "BT-9": "Fälligkeitsdatum", "BT-10": "Käuferreferenz (z. B. Leitweg-ID)",
    "BT-23": "Geschäftsprozesstyp", "BT-24": "Spezifikationskennung", "BT-27": "Name des Verkäufers",
    "BT-31": "Umsatzsteuer-ID des Verkäufers", "BT-34": "Elektronische Adresse des Verkäufers",
    "BT-44": "Name des Käufers", "BT-49": "Elektronische Adresse des Käufers",
    "BT-72": "Liefer- oder Leistungsdatum", "BT-81": "Zahlungsart", "BT-84": "Kontokennung (z. B. IBAN)",
    "BT-109": "Rechnungssumme ohne Umsatzsteuer", "BT-110": "Umsatzsteuerbetrag",
    "BT-112": "Rechnungssumme mit Umsatzsteuer", "BT-113": "Bereits gezahlter Betrag",
    "BT-114": "Rundungsbetrag", "BT-115": "Zahlbetrag", "BT-129": "Berechnete Menge",
    "BT-131": "Nettobetrag der Rechnungsposition", "BT-146": "Netto-Einzelpreis", "BT-153": "Artikelname",
}


@dataclass(frozen=True)
class InvoiceIssue:
    level: str
    field: str
    rule: str
    location: str
    message: str


@dataclass(frozen=True)
class InvoiceValidation:
    status: str
    scenario: str
    issues: tuple


def download_checked(url, expected, target):
    with urlopen(url, timeout=30) as response:
        payload = response.read(16 * 1024 * 1024 + 1)
    if len(payload) > 16 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("Die Prüfsumme des KoSIT-Downloads stimmt nicht.")
    target.write_bytes(payload)


def ensure_validator(root):
    jar, rules = root / "validator.jar", root / "rules"
    if jar.is_file() and (rules / "scenarios.xml").is_file():
        with jar.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != ENGINE_HASH:
                raise ValueError("Der lokale KoSIT-Prüfer ist beschädigt. Bitte seinen Ordner umbenennen und erneut prüfen.")
        return jar, rules
    root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root.parent) as temporary:
        staging = Path(temporary) / "kosit"
        staging.mkdir()
        archive = Path(temporary) / "rules.zip"
        download_checked(ENGINE_URL, ENGINE_HASH, staging / jar.name)
        download_checked(RULES_URL, RULES_HASH, archive)
        with ZipFile(archive) as package:
            package.extractall(staging / rules.name)
        if not (staging / "rules" / "scenarios.xml").is_file():
            raise ValueError("Im KoSIT-Paket fehlt die Regelkonfiguration.")
        try:
            staging.rename(root)
        except FileExistsError:
            if not jar.is_file() or not (rules / "scenarios.xml").is_file():
                raise
    return jar, rules


def xml_root(data):
    if len(data) > INVOICE_XML_LIMIT:
        raise ValueError("Die Rechnungs-XML ist größer als 8 MiB.")
    if b"<!DOCTYPE" in data.replace(b"\x00", b"").upper():
        raise ValueError("Die Rechnungs-XML enthält eine unzulässige Dokumenttyp-Deklaration.")
    return ElementTree.fromstring(data)


def friendly_fields(message):
    terms = list(dict.fromkeys(re.findall(r"\bBT-\d+\b", message)))
    return ", ".join(FIELDS.get(term, term) for term in terms)


def parse_report(report, returncode):
    root = ElementTree.parse(report).getroot()
    if root.tag != REPORT + "report":
        raise ValueError("Der KoSIT-Prüfer hat keinen gültigen Prüfbericht erzeugt.")
    matched = root.find(REPORT + "scenarioMatched")
    scope = matched if matched is not None else root
    scenario = scope.findtext(f"{SCENARIO}scenario/{SCENARIO}name", "")
    overrides = {code: element.get("level") for element in scope.findall(f".//{SCENARIO}customLevel")
                 for code in (element.text or "").split() if element.get("level")}
    assessment = root.find(REPORT + "assessment")
    accepted = assessment is not None and assessment.find(REPORT + "accept") is not None
    rejected = assessment is not None and assessment.find(REPORT + "reject") is not None
    if returncode not in (0, 1) or accepted == rejected or (accepted and returncode):
        raise ValueError("Der KoSIT-Prüfer konnte die Prüfung nicht vollständig abschließen.")
    issues = []
    for item in scope.findall(f".//{REPORT}validationStepResult/{REPORT}message"):
        message = " ".join("".join(item.itertext()).split())
        location = re.sub(r"Q\{[^}]*\}", "", item.get("xpathLocation", ""))
        if item.get("lineNumber"):
            location = f"Zeile {item.get('lineNumber')}, Spalte {item.get('columnNumber', '–')}"
        code = item.get("code", "")
        issues.append(InvoiceIssue(overrides.get(code, item.get("level", "information")),
                                   friendly_fields(message), code, location, message))
    steps = scope.findall(REPORT + "validationStepResult")
    if (any(issue.rule == "PROCESSING_ERROR" for issue in issues)
            or root.find(".//{http://www.w3.org/1999/xhtml}div[@class='processing-errors']") is not None
            or any(item.get("id", "").startswith("val-sch") and item.get("valid") == "false"
                   and not item.findall(REPORT + "message") for item in steps)):
        raise ValueError("Eine KoSIT-Prüfstufe lieferte einen unvollständigen Bericht.")
    if accepted and (not scenario or not any(item.get("id", "").startswith("val-sch") for item in steps)
                     or any(issue.level in ("error", "fatal") for issue in issues)):
        raise ValueError("Der KoSIT-Bericht enthält keine bestätigte Regelprüfung.")
    status = "passed" if accepted else "rejected" if scenario else "unsupported"
    if status == "unsupported":
        issues.insert(0, InvoiceIssue("error", "Spezifikationskennung", "BT-24", "",
                                      "Das deklarierte Profil passt zu keinem Szenario des installierten Regelpakets."))
    return InvoiceValidation(status, scenario, tuple(issues))


def missing_core_fields(root):
    cbc = "{" + UBL_COMMON + "BasicComponents-2}"
    cac = "{" + UBL_COMMON + "AggregateComponents-2}"
    rsm, ram = "{" + CII + "}", "{" + RAM + "}"
    if root.tag == rsm + "CrossIndustryInvoice":
        document = f"{rsm}ExchangedDocument/"
        settlement = f"{rsm}SupplyChainTradeTransaction/{ram}ApplicableHeaderTradeSettlement/"
        tests = (
            ("BT-1", document + ram + "ID", "ExchangedDocument/ID"),
            ("BT-2", document + ram + "IssueDateTime/{*}DateTimeString", "ExchangedDocument/IssueDateTime"),
            ("BT-115", settlement + f"{ram}SpecifiedTradeSettlementHeaderMonetarySummation/{ram}DuePayableAmount",
             "SpecifiedTradeSettlementHeaderMonetarySummation/DuePayableAmount"),
        )
    elif root.tag in {f"{{urn:oasis:names:specification:ubl:schema:xsd:{name}-2}}{name}" for name in UBL_TYPES}:
        tests = (("BT-1", cbc + "ID", "ID"), ("BT-2", cbc + "IssueDate", "IssueDate"),
                 ("BT-115", cac + "LegalMonetaryTotal/" + cbc + "PayableAmount", "LegalMonetaryTotal/PayableAmount"))
    else:
        return ()
    return tuple(InvoiceIssue("error", FIELDS[term], term, name, "Das Feld fehlt oder ist leer.")
                 for term, path, name in tests if not root.findtext(path, "").strip())


def validate_invoice_xml(data, java, jar, rules):
    try:
        source = xml_root(data)
    except ElementTree.ParseError as exc:
        line, column = exc.position
        issue = InvoiceIssue("error", "XML-Struktur", "XML", f"Zeile {line}, Spalte {column + 1}", str(exc))
        return InvoiceValidation("rejected", "", (issue,))
    with TemporaryDirectory(prefix="werkbank-rechnung-") as folder:
        folder = Path(folder)
        xml = folder / "Rechnung.xml"
        xml.write_bytes(data)
        with (folder / "stdin").open("w+b") as stdin:
            result = subprocess.run([str(java), "-Duser.language=de", "-Duser.country=DE", "-jar", str(jar),
                                     "-s", str(rules / "scenarios.xml"), "-r", str(rules), "-o", str(folder), str(xml)],
                                    stdin=stdin, capture_output=True, timeout=75,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        report = folder / "Rechnung-report.xml"
        if not report.is_file():
            raise ValueError("Der KoSIT-Prüfer hat keinen Bericht erzeugt. Java 11 oder neuer wird benötigt.")
        validation = parse_report(report, result.returncode)
    if validation.status == "rejected":
        return InvoiceValidation(validation.status, validation.scenario, (*missing_core_fields(source), *validation.issues))
    return validation

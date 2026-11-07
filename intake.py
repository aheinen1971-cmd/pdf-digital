# SPDX-License-Identifier: AGPL-3.0-only
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import pymupdf as pdf

from pdf_standards import ARCHIVE_PROFILES, validate_standard


@dataclass
class AuditResult:
    opening: str
    profile: str = ""
    archive: str = "Nicht geprüft"
    missing_text: tuple = ()
    details: str = ""
    readable: bool = False


def archive_profile(doc):
    metadata = doc.get_xml_metadata()
    if not metadata:
        return ""
    if "<!DOCTYPE" in metadata:
        raise ValueError("Unsichere XMP-Metadaten.")
    root = ElementTree.fromstring(metadata)
    namespace = "{http://www.aiim.org/pdfa/ns/id/}"
    values = {}
    for key in ("part", "conformance"):
        tag = namespace + key
        found = {value.strip().lower() for node in root.iter()
                 if (value := (node.text if node.tag == tag else node.get(tag))) and value.strip()}
        if len(found) > 1:
            raise ValueError("Widersprüchliche PDF/A-Kennung.")
        values[key] = next(iter(found), "")
    return values["part"] + values["conformance"] if values["part"] else ""


def audit_pdf(path, tools):
    try:
        with pdf.open(path) as doc:
            if not doc.is_pdf or not len(doc):
                return AuditResult("Keine lesbare PDF")
            if doc.needs_pass:
                return AuditResult("Passwortgeschützt")
            result = AuditResult("Repariert geöffnet" if doc.is_repaired else "Lesbar", readable=True)
            result.missing_text = tuple(page.number + 1 for page in doc if not page.get_text().strip())
            try:
                result.profile = archive_profile(doc)
            except (ElementTree.ParseError, ValueError) as exc:
                result.archive = "XMP fehlerhaft"
                result.details = str(exc)
    except Exception as exc:
        return AuditResult("Lesefehler", details=str(exc))
    if result.archive == "XMP fehlerhaft":
        return result
    if not result.profile:
        result.archive = "Keine PDF/A-Kennung"
    elif result.profile not in ARCHIVE_PROFILES:
        result.archive = "Unbekanntes PDF/A-Profil"
    elif tools:
        try:
            passed, details = validate_standard(*tools, path, result.profile)
            result.archive = "Bestanden" if passed else "Abweichungen"
            result.details = "\n".join(details)
        except Exception as exc:
            result.archive = "Prüffehler"
            result.details = str(exc)
    return result


@dataclass(frozen=True)
class InvoiceField:
    value: str
    page: int
    rect: pdf.Rect


INVOICE_LABELS = (
    (r"rechnungs[\s-]*(?:nummer|nr\.?|no\.?)|invoice\s*(?:number|no\.?|#)", r"beleg[\s-]*(?:nummer|nr\.?)"),
    (r"rechnungs[\s-]*datum|invoice\s*date", r"datum"),
    (r"(?:zu\s+zahlen|zahlbetrag|endbetrag|amount\s+due)",
     r"(?:rechnungsbetrag|gesamtbetrag|bruttobetrag|bruttosumme|total\s+amount|grand\s+total)"),
)
INVOICE_VALUES = (
    re.compile(r"(?=[\w./-]*\d)[\w][\w./-]{0,49}", re.I),
    re.compile(r"(?:\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2})(?!\d)"),
    re.compile(r"(?:(?:EUR|€|CHF|USD|\$)\s*)?\d+(?:[. ,'’]\d{3})*[,.]\d{2}(?!\d)(?:\s*(?:EUR|€|CHF|USD|\$))?", re.I),
)


def invoice_value(column, text):
    match = INVOICE_VALUES[column].match(text)
    if not match:
        return ""
    value = match.group().strip()
    if column != 1:
        return value
    for pattern in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return ""


def invoice_fields(path, tessdata):
    with pdf.open(path) as doc:
        if not doc.is_pdf or doc.needs_pass or not len(doc):
            raise ValueError("Keine lesbare PDF ohne Passwort.")
        candidates = [[], [], []]
        for index in sorted(set(range(min(3, len(doc)))) | {len(doc) - 1}):
            page = doc[index]
            textpage = None
            if not page.get_text().strip():
                if not page.get_image_info() and not page.get_drawings():
                    continue
                textpage = page.get_textpage_ocr(language="deu+eng", dpi=200, full=True, tessdata=str(tessdata))
            flags = pdf.TEXTFLAGS_DICT & ~pdf.TEXT_PRESERVE_IMAGES
            lines = [(pdf.Rect(line["bbox"]), "".join(span["text"] for span in line["spans"]).strip())
                     for block in page.get_text("dict", flags=flags, textpage=textpage, sort=True)["blocks"]
                     for line in block.get("lines", ())]
            for line_index, (rect, text) in enumerate(lines):
                for column, labels in enumerate(INVOICE_LABELS):
                    for priority, label in enumerate(labels):
                        match = re.match(rf"^(?:{label})(?!\w)\s*[:#–]?\s*", text, re.I)
                        if not match:
                            continue
                        remainder = text[match.end():].strip()
                        location = rect
                        if not remainder and line_index + 1 < len(lines):
                            next_rect, next_text = lines[line_index + 1]
                            same_row = abs(next_rect.y0 - rect.y0) <= rect.height / 2 and next_rect.x0 >= rect.x1
                            below = 0 <= next_rect.y0 - rect.y0 <= rect.height * 2 and abs(next_rect.x0 - rect.x0) < 35
                            if same_row or below:
                                location, remainder = next_rect, next_text
                        value = invoice_value(column, remainder)
                        if value:
                            candidates[column].append((priority, InvoiceField(value, index, location)))
    result = []
    for options in candidates:
        if options:
            priority = min(rank for rank, _ in options)
            best = [field for rank, field in options if rank == priority]
            result.append(best[0] if len({field.value for field in best}) == 1 else None)
        else:
            result.append(None)
    return tuple(result)


EXPLORER_COMMANDS = {
    "--explorer-merge": "PDF Werkbank - Zusammenführen",
    "--explorer-workflow": "PDF Werkbank - Gespeicherten Ablauf starten",
}


def explorer_paths(arguments):
    paths = {}
    for argument in arguments:
        path = Path(argument).resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError(f"Bitte nur PDF-Dateien auswählen: {path.name}")
        paths.setdefault(os.path.normcase(str(path)), path)
    return list(paths.values())


def merge_pdfs(paths, output):
    if len(paths) < 2:
        raise ValueError("Bitte mindestens zwei PDFs auswählen.")
    for path in paths:
        with pdf.open(path) as source:
            if not source.is_pdf or source.needs_pass or not len(source):
                raise ValueError(f"Keine lesbare PDF ohne Passwort: {path.name}")
            if source.get_sigflags() > 0:
                raise ValueError(f"Signierte PDFs können nicht über den Explorer zusammengeführt werden: {path.name}")
            output.insert_pdf(source, join_duplicates=False)


def install_explorer_commands(launcher, folder):
    launcher = Path(launcher).resolve(strict=True)
    folder = Path(folder)
    if launcher.suffix.lower() != ".exe" or not launcher.is_file() or not folder.is_dir():
        raise ValueError("Der Programmstarter oder der Windows-Ordner Senden an fehlt.")
    script = """
$ErrorActionPreference = 'Stop'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($env:WERKBANK_SHORTCUT_PATH)
$shortcut.TargetPath = $env:WERKBANK_SHORTCUT_EXE
$shortcut.Arguments = $env:WERKBANK_SHORTCUT_ARGUMENT
$shortcut.WorkingDirectory = [IO.Path]::GetDirectoryName($env:WERKBANK_SHORTCUT_EXE)
$shortcut.Save()
"""
    for argument, label in EXPLORER_COMMANDS.items():
        env = dict(os.environ, WERKBANK_SHORTCUT_PATH=str(folder / (label + ".lnk")),
                   WERKBANK_SHORTCUT_EXE=str(launcher), WERKBANK_SHORTCUT_ARGUMENT=argument)
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       env=env, check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)

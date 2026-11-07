# SPDX-License-Identifier: AGPL-3.0-only
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

import pymupdf as pdf


def filename_hint(doc, received):
    lines = []
    for page in list(range(min(2, len(doc)))):
        lines.extend(doc[page].get_text(sort=True).splitlines())
    sample = "\n".join(lines)[:12000]
    kind, folder = "Scan", "Sonstiges"
    for pattern, title, group in (
        (r"stromrechnung|stromlieferung|stromverbrauch", "Stromrechnung", "Energie"),
        (r"rechnung|invoice", "Rechnung", "Rechnungen"),
        (r"kontoauszug", "Kontoauszug", "Bank"),
        (r"versicherungs(schein|beitrag)|police", "Versicherung", "Versicherungen"),
        (r"steuerbescheid", "Steuerbescheid", "Steuern"),
        (r"vertrag", "Vertrag", "Verträge"),
    ):
        if re.search(pattern, sample, re.IGNORECASE):
            kind, folder = title, group
            break
    found_date = received
    for match in re.finditer(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](20\d{2})(?!\d)", sample):
        day, month, year = map(int, match.groups())
        try:
            found_date = date(year, month, day)
            break
        except ValueError:
            continue
    return f"{found_date.isoformat()} – {kind}.pdf", folder


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def pdf_files(folder, recursive=True):
    for parent, directories, files in os.walk(folder, followlinks=False):
        directories[:] = sorted(name for name in directories if recursive and not
                                (os.path.islink(Path(parent) / name) or os.path.isjunction(Path(parent) / name)))
        for name in sorted(files, key=str.casefold):
            path = Path(parent) / name
            if path.suffix.casefold() == ".pdf" and not path.is_symlink():
                yield path


def folder_matches(folder, query, recursive, tick, limit=1000):
    needle = normalized(query)
    if not needle:
        raise ValueError("Bitte einen Suchbegriff eingeben.")
    results = []
    skipped = 0
    scanned = 0
    for path in pdf_files(folder, recursive):
        if not tick(path):
            return results, skipped, scanned, "abgebrochen"
        try:
            with pdf.open(path) as doc:
                if not doc.is_pdf or doc.needs_pass:
                    skipped += 1
                    continue
                scanned += 1
                for page in doc:
                    if not tick(path):
                        return results, skipped, scanned, "abgebrochen"
                    text = normalized(page.get_text(sort=True))
                    hit = text.find(needle)
                    if hit >= 0:
                        excerpt = text[max(0, hit - 42):hit + len(needle) + 60]
                        results.append((path, page.number, excerpt))
                        if len(results) >= limit:
                            return results, skipped, scanned, "Treffergrenze erreicht"
        except (OSError, RuntimeError, ValueError):
            skipped += 1
    return results, skipped, scanned, ""

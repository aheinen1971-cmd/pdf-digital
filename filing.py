# SPDX-License-Identifier: AGPL-3.0-only
import os
import re
import sqlite3
import unicodedata
from contextlib import closing
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


def scan_breaks(doc):
    starts = []
    previous_blank = False
    for page in doc:
        lines = [line.strip() for line in page.get_text(sort=True).splitlines() if line.strip()]
        heading = "\n".join(lines[:8])
        sequence = re.search(r"\bseite\s*(\d+)\s*(?:von|/)", heading, re.I)
        first = sequence is not None and int(sequence[1]) == 1
        continued = sequence is not None and int(sequence[1]) > 1
        subject = re.search(r"^(?:(?:strom|jahres)?rechnung|kontoauszug|steuerbescheid|"
                            r"versicherungsschein|vertrag|betreff)\b", heading, re.I | re.M)
        if page.number and lines and (first or not continued and (subject or previous_blank)):
            starts.append(page.number)
        previous_blank = False
        if not lines:
            scale = 110 / max(page.rect.width, page.rect.height)
            image = page.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csGRAY)
            samples = image.samples
            previous_blank = sum(value < 205 for value in samples) <= len(samples) // 140
    return starts


def scan_ranges(total, starts):
    boundaries = [0, *sorted({index for index in starts if 0 < index < total}), total]
    return list(zip(boundaries, boundaries[1:])) if total else []


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def search_rectangles(page, term):
    matches = page.search_for(term)
    if matches:
        return matches
    needle = normalized(term)
    if not needle:
        return []
    words = [(pdf.Rect(*word[:4]), normalized(word[4])) for word in page.get_text("words", sort=True)]
    spans, parts = [], []
    offset = 0
    for box, word in words:
        if word:
            spans.append((offset, offset + len(word), box))
            parts.append(word)
            offset += len(word) + 1
    text = " ".join(parts)
    found = re.search(re.escape(needle), text)
    if found is None:
        return []
    return [box for start, end, box in spans if start < found.end() and end > found.start()]


def archive_target(root, inbox, filename, group):
    invalid = r'[<>:"/\\|?*\x00-\x1f]'
    filename, group = filename.strip(), group.strip()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("Bitte zuerst einen vorhandenen Archivordner wählen.")
    if not filename or filename.endswith(".") or re.search(invalid, filename) or os.path.isreserved(filename):
        raise ValueError("Bitte einen gültigen Dateinamen eingeben.")
    if group in (".", "..") or group.endswith(".") or re.search(invalid, group) or os.path.isreserved(group):
        raise ValueError("Bitte einen einzelnen gültigen Unterordner eingeben.")
    target = root / group / (filename if filename.lower().endswith(".pdf") else filename + ".pdf")
    resolved = target.resolve()
    if target.exists() or resolved.is_relative_to(inbox.resolve()) or not resolved.is_relative_to(root.resolve()):
        raise ValueError("Bitte einen freien Namen im gewählten Archivordner außerhalb des Scan-Posteingangs wählen.")
    return target


def pdf_files(folder, recursive=True, suffixes=(".pdf",)):
    for parent, directories, files in os.walk(folder, followlinks=False):
        directories[:] = sorted(name for name in directories if recursive and not
                                (os.path.islink(Path(parent) / name) or os.path.isjunction(Path(parent) / name)))
        for name in sorted(files, key=str.casefold):
            path = Path(parent) / name
            if path.suffix.casefold() in suffixes and not path.is_symlink():
                yield path


class SearchMatches:
    def __init__(self, query, fuzzy, limit):
        self.needle = normalized(query)
        if not self.needle:
            raise ValueError("Bitte einen Suchbegriff eingeben.")
        self.limit = limit
        self.fuzzy = fuzzy
        self.exact, self.possible = [], []
        groups = ("0o", "1il|", "2z", "5s", "8b")
        choices = {char: "[" + re.escape(group) + "]" for group in groups for char in group}
        self.pattern = None
        if fuzzy and 3 <= len(self.needle) <= 96 and any(char.isdecimal() for char in self.needle):
            terms = "".join(choices.get(char, re.escape(char)) for char in self.needle)
            self.pattern = re.compile(r"(?<!\w)" + terms + r"(?!\w)")

    def add(self, path, page, text):
        start = text.find(self.needle)
        if start >= 0:
            word = self.needle
            results = self.exact
        elif self.pattern and len(self.possible) < self.limit:
            match = next((m for m in self.pattern.finditer(text)
                          if 1 <= sum(a != b for a, b in zip(self.needle, m[0])) <= 2), None)
            if match is None:
                return False
            start, word, results = match.start(), match[0], self.possible
        else:
            return False
        left, end = max(0, start - 42), start + len(word)
        right = min(len(text), end + 60)
        excerpt = ("…" if left else "") + text[left:start] + "⟦" + text[start:end] + "⟧" + text[end:right]
        if right < len(text):
            excerpt += "…"
        hit = (path, page, excerpt)
        results.append((*hit, word if results is self.possible else None) if self.fuzzy else hit)
        return len(self.exact) >= self.limit

    def results(self, skipped, scanned, status=""):
        combined = self.exact + self.possible
        if len(combined) >= self.limit and not status:
            status = "Treffergrenze erreicht"
        return combined[:self.limit], skipped, scanned, status


def folder_matches(folder, query, recursive, tick, limit=1000, filters=("", "", 0), fuzzy=False):
    matches = SearchMatches(query, fuzzy, limit)
    skipped = 0
    scanned = 0
    for path in pdf_files(folder, recursive):
        if not tick(path):
            return matches.results(skipped, scanned, "abgebrochen")
        try:
            if not matches_filters(path.relative_to(folder), path.stat().st_mtime_ns, filters):
                continue
            with pdf.open(path) as doc:
                if not doc.is_pdf or doc.needs_pass:
                    skipped += 1
                    continue
                scanned += 1
                for page in doc:
                    if not tick(path):
                        return matches.results(skipped, scanned, "abgebrochen")
                    text = normalized(page.get_text(sort=True))
                    if matches.add(path, page.number, text):
                        return matches.results(skipped, scanned)
        except (OSError, RuntimeError, ValueError):
            skipped += 1
    return matches.results(skipped, scanned)


def matches_filters(relative, modified, filters):
    name, directory, since = filters
    return (modified >= since and normalized(name) in normalized(relative.name)
            and normalized(directory) in normalized(str(relative.parent)))


def open_index(database):
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database, timeout=5)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA secure_delete = ON")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                scope TEXT, path TEXT, modified INTEGER, size INTEGER, identity TEXT, valid INTEGER,
                PRIMARY KEY (scope, path)
            );
            CREATE TABLE IF NOT EXISTS pages (
                scope TEXT, path TEXT, number INTEGER, text TEXT,
                PRIMARY KEY (scope, path, number),
                FOREIGN KEY (scope, path) REFERENCES documents(scope, path) ON DELETE CASCADE
            );
        """)
        return db
    except Exception:
        db.close()
        raise


def search_scope(folder, recursive):
    return f"{int(recursive)}:{os.path.normcase(str(folder.resolve()))}"


def clear_index(database, folder):
    if database.is_file():
        with closing(open_index(database)) as db, db:
            db.executemany("DELETE FROM documents WHERE scope = ?",
                           [(search_scope(folder, recursive),) for recursive in (False, True)])


def file_signature(path):
    metadata = path.stat()
    return metadata.st_mtime_ns, metadata.st_size, str(metadata.st_ino)


def indexed_matches(database, folder, query, recursive, tick, limit=1000, filters=("", "", 0), fuzzy=False):
    matches = SearchMatches(query, fuzzy, limit)
    scope = search_scope(folder, recursive)
    updated = 0
    present = set()
    skipped = 0
    with closing(open_index(database)) as db:
        existing = {path: (modified, size, identity, valid) for path, modified, size, identity, valid in
                    db.execute("SELECT path, modified, size, identity, valid FROM documents WHERE scope = ?", (scope,))}
        for path in pdf_files(folder, recursive):
            if not tick(path):
                return [], skipped, updated, "abgebrochen"
            relative = str(path.relative_to(folder))
            try:
                signature = file_signature(path)
            except OSError:
                skipped += 1
                continue
            previous = existing.get(relative)
            if previous is not None and previous[:3] == signature:
                present.add(relative)
                skipped += not previous[3]
                continue
            texts = []
            valid = False
            try:
                with pdf.open(path) as doc:
                    if doc.is_pdf and not doc.needs_pass:
                        for page in doc:
                            if not tick(path):
                                return [], skipped, updated, "abgebrochen"
                            texts.append(normalized(page.get_text(sort=True)))
                        valid = True
                if file_signature(path) != signature:
                    skipped += 1
                    continue
            except (OSError, RuntimeError, ValueError):
                texts.clear()
            with db:
                db.execute("DELETE FROM documents WHERE scope = ? AND path = ?", (scope, relative))
                db.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)", (scope, relative, *signature, valid))
                db.executemany("INSERT INTO pages VALUES (?, ?, ?, ?)",
                               ((scope, relative, number, text) for number, text in enumerate(texts)))
            updated += 1
            present.add(relative)
            skipped += not valid
        with db:
            db.executemany("DELETE FROM documents WHERE scope = ? AND path = ?",
                           ((scope, path) for path in existing.keys() - present))
        query_rows = db.execute("""
            SELECT p.path, p.number, p.text, d.modified
            FROM pages p JOIN documents d USING(scope, path)
            WHERE p.scope = ? AND d.valid = 1 AND (? OR instr(p.text, ?) > 0)
            ORDER BY p.path, p.number
        """, (scope, matches.pattern is not None, matches.needle))
        for count, (path, page, text, modified) in enumerate(query_rows):
            if count % 64 == 0 and not tick(folder / path):
                return matches.results(skipped, updated, "abgebrochen")
            if path in present and matches_filters(Path(path), modified, filters):
                if matches.add(folder / path, page, text):
                    break
        return matches.results(skipped, updated)

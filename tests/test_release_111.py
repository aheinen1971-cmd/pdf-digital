# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QListWidget

from filing import clear_index, indexed_matches, scan_breaks, scan_ranges
from pdf_werkbank import Window, save_copy


def make_pdf(path, texts):
    with pdf.open() as doc:
        for text in texts:
            doc.new_page().insert_text((50, 75), text)
        save_copy(doc, path)


def proceed(_):
    return True


app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    root = Path(folder)
    inbox = root / "Posteingang"
    inbox.mkdir()
    source = inbox / "2026-11-07-original.pdf"
    pages = ["Rechnung A\nSeite 1 von 2", "Seite 2 von 2\nRechnung A",
             "Vertrag B\nSeite 1 von 2", "Seite 2 von 2\nVertrag B", "Betreff: Brief C"]
    make_pdf(source, pages)
    with pdf.open(source) as doc:
        assert scan_breaks(doc) == [2, 4]
        assert scan_ranges(len(doc), [4, 0, 2, 2, 10]) == [(0, 2), (2, 4), (4, 5)]
    window = Window()
    original = source.read_bytes()
    with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Rejected):
        assert not window.split_scan_draft(source)
    assert source.read_bytes() == original
    with patch.object(window, "review_scan_breaks", return_value=[(0, 2), (2, 5)]), patch(
            "pdf_werkbank.save_copy", side_effect=[None, OSError("Schreibfehler")]):
        try:
            window.split_scan_draft(source)
        except OSError:
            pass
        else:
            raise AssertionError("Schreibfehler wurde verschluckt")
    assert source.read_bytes() == original and list(inbox.glob("*.pdf")) == [source]

    def accept_and_adjust(dialog):
        rows = dialog.findChild(QListWidget)
        assert rows.count() == 5
        assert rows.item(2).checkState() == Qt.CheckState.Checked
        assert rows.item(4).checkState() == Qt.CheckState.Checked
        rows.item(4).setCheckState(Qt.CheckState.Unchecked)
        return QDialog.DialogCode.Accepted

    with patch.object(QDialog, "exec", accept_and_adjust):
        parts = window.split_scan_draft(source)
    assert len(parts) == 2 and not source.exists()
    actual = []
    for part in parts:
        with pdf.open(part) as doc:
            actual.extend(page.get_text().strip() for page in doc)
    assert actual == pages

    archive = root / "Archiv"
    energy = archive / "Energie"
    energy.mkdir(parents=True)
    filename = energy / "Stromrechnung.pdf"
    make_pdf(filename, ["Stromvertrag", "Rechnungsnummer 4711"])
    broken = archive / "defekt.pdf"
    broken.write_bytes(b"kein PDF")
    database = root / "Suchindex" / "Index.sqlite"
    result, skipped, updated, state = indexed_matches(database, archive, "4711", True, proceed)
    assert [(path, index) for path, index, _ in result] == [(filename, 1)]
    assert (skipped, updated, state) == (1, 2, "")
    with patch("filing.pdf.open", side_effect=AssertionError("Unveränderte PDFs erneut geöffnet")):
        assert indexed_matches(database, archive, "4711", True, proceed)[2] == 0
        assert indexed_matches(database, archive, "4711", True, proceed, filters=("Wasser", "", 0))[0] == []
        assert indexed_matches(database, archive, "4711", True, proceed, filters=("STROM", "energie", 0))[0]
        assert not indexed_matches(database, archive, "4711", True, proceed,
                                   filters=("", "", filename.stat().st_mtime_ns + 1))[0]
    assert not indexed_matches(database, archive, "4711", False, proceed)[0]
    assert indexed_matches(database, archive, "4711", True, lambda _: False)[3] == "abgebrochen"
    newer = energy / "neu.pdf"
    make_pdf(newer, ["Rechnungsnummer 4812"])
    newer.replace(filename)
    assert not indexed_matches(database, archive, "4711", True, proceed)[0]
    assert indexed_matches(database, archive, "4812", True, proceed)[0][0][:2] == (filename, 0)
    filename.unlink()
    assert not indexed_matches(database, archive, "4812", True, proceed)[0]
    clear_index(database, archive)
    assert indexed_matches(database, archive, "4812", True, proceed)[2] == 1

print("Stapelvorschläge, Korrektur, sichere Aufteilung und inkrementeller Suchindex geprüft.")

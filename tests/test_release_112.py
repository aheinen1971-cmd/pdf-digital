# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTableWidget

from pdf_standards import validate_standard
from pdf_werkbank import Window


def draft(path, label):
    with pdf.open() as doc:
        doc.new_page().insert_text((60, 100), label)
        doc.save(path)


app = QApplication([])
settings = QSettings("PDF Werkbank", "PDF Werkbank")
keys = ("toolbar/favorites", "scan/archive-folder")
previous = {key: settings.value(key) for key in keys}
try:
    settings.remove("toolbar/favorites")
    window = Window()
    assert [action.text() for action in window.favorite_actions] == ["Scannen", "Ordnersuche", "Samsung-Duplex"]
    window.pin_favorite("duplex", False)
    window.pin_favorite("inbox", True)
    reopened = Window()
    assert [action.text() for action in reopened.favorite_actions] == ["Scannen", "Ordnersuche", "Scan-Posteingang"]
    for key in list(reopened.favorite_keys):
        reopened.pin_favorite(key, False)
    assert not Window().favorite_actions
    clicked = []
    window.favorite_commands["inbox"] = ("Scan-Posteingang", lambda: clicked.append(True))
    window.refresh_favorites()
    window.favorite_actions[-1].trigger()
    assert clicked == [True]

    with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        root = Path(folder)
        inbox, archive = root / "Posteingang", root / "Archiv"
        inbox.mkdir()
        archive.mkdir()
        first, second, third = [inbox / f"2026-11-07-{index}.pdf" for index in range(3)]
        for path, label in zip((first, second, third), ("Rechnung Alpha", "Rechnung Beta", "Rechnung Gamma")):
            draft(path, label)
        jobs = []

        def review(dialog):
            table = dialog.findChild(QTableWidget)
            assert table.rowCount() == 3
            assert not table.item(0, 1).icon().isNull()
            assert table.item(0, 3).text() != table.item(1, 3).text()
            table.item(0, 3).setText("Eigener Name.pdf")
            table.item(0, 4).setText("Geprüft")
            table.item(2, 0).setCheckState(Qt.CheckState.Unchecked)
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", review), patch.object(window, "archive_batch", side_effect=jobs.extend):
            window.review_archive_batch([first, second, third], str(archive))
        assert len(jobs) == 2 and jobs[0] == (first, archive / "Geprüft" / "Eigener Name.pdf")
        assert jobs[1][0] == second
        assert all(path.exists() for path in (first, second, third))

        reports = []
        def capture(report):
            reports.append((report.text(), report.detailedText()))
            return 0

        java, jar = (os.environ.get(f"PDF_WERKBANK_QA_{key}") for key in ("JAVA", "VERA"))
        original = window.build_archive
        def build(source, candidate, recognize, tools):
            if "Beta" in source[0].get_text():
                raise ValueError("Abgelehnter Testentwurf")
            if java and jar:
                return original(source, candidate, recognize, tools)
            draft(candidate, "Ersatz für Test ohne veraPDF")
            return "PDF/A-2b von veraPDF bestätigt."

        with patch.object(window, "standard_tools", return_value=(java, jar)), patch.object(
                window, "build_archive", side_effect=build), patch.object(QMessageBox, "exec", capture):
            window.archive_batch(jobs)
        assert not first.exists() and jobs[0][1].is_file()
        assert second.exists() and not jobs[1][1].exists() and third.exists()
        assert "1 Fehler" in reports[-1][0] and "Abgelehnter Testentwurf" in reports[-1][1]
        if java and jar:
            assert validate_standard(java, jar, jobs[0][1], "2b")[0]

        with patch.object(window, "standard_tools", return_value=(java, jar)), patch.object(
                window, "build_archive", return_value=None), patch.object(QMessageBox, "exec", capture):
            window.archive_batch([jobs[1], (third, archive / "Dritte.pdf")])
        assert second.exists() and third.exists() and not (archive / "Dritte.pdf").exists()
        assert "abgebrochen" in reports[-1][0]
finally:
    for key, value in previous.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()

print("Favoriten, gemeinsame Freigabe, PDF/A-Prüfung, Fehler und Abbruch geprüft.")

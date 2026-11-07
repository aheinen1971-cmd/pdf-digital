# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QFileDialog, QInputDialog, QMessageBox

from archive_run import ArchiveRun
from filing import folder_matches, indexed_matches
from pdf_standards import validate_standard
from pdf_werkbank import Window
from preferences import export_package, import_groups, read_package
from update_worker import digest_file


def make_pdf(path, text):
    with pdf.open() as doc:
        doc.new_page().insert_text((50, 100), text)
        doc.save(path)


app = QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    root = Path(folder)
    library, inbox, output = (root / name for name in ("Ablage", "Posteingang", "Archiv"))
    for path in (library, inbox, output):
        path.mkdir()
    similar, exact, false_match = (library / name for name in ("A.pdf", "B.pdf", "C.pdf"))
    for path, value in ((similar, "Rechnungsnummer 47I8"), (exact, "Rechnungsnummer 4718"),
                        (false_match, "Rechnungsnummer 947I88")):
        make_pdf(path, value)
    index = root / "index.sqlite"
    for run in (lambda **kw: folder_matches(library, "4718", True, lambda _: True, **kw),
                lambda **kw: indexed_matches(index, library, "4718", True, lambda _: True, **kw)):
        hits, *_ = run(fuzzy=True)
        assert [(hit[0], hit[3]) for hit in hits] == [(exact, None), (similar, "47i8")]
        assert [hit[0] for hit in run(fuzzy=False)[0]] == [exact]
        assert run(fuzzy=True, limit=1)[0][0][0] == exact

    source_settings = QSettings(str(root / "Einstellungen-Quelle.ini"), QSettings.Format.IniFormat)
    target_settings = QSettings(str(root / "Einstellungen-Ziel.ini"), QSettings.Format.IniFormat)
    source_settings.setValue("scan/a4", True)
    source_settings.setValue("scan/archive-folder", "C:/private")
    source_settings.setValue("workspace/maps", '{"Persönlich":[{"path":"C:/private.pdf"}]}')
    source_settings.setValue("batch/profiles", '{"Versand":["ocr","compress"]}')
    package = root / "Einstellungen.json"
    package.write_bytes(export_package(source_settings))
    assert b"private" not in package.read_bytes()
    groups = read_package(package)
    target_settings.setValue("scan/a4", False)
    import_groups(target_settings, {"workflows": groups["workflows"]})
    assert not target_settings.value("scan/a4", True, bool)
    assert json.loads(target_settings.value("batch/profiles", "", str)) == {"Versand": ["ocr", "compress"]}
    malformed = root / "Ungueltig.json"
    bad = json.loads(package.read_bytes())
    bad["groups"]["scan"]["ocr"] = "true"
    malformed.write_text(json.dumps(bad), encoding="utf-8")
    try:
        read_package(malformed)
    except ValueError:
        pass
    else:
        raise AssertionError("Ungültiges Paket akzeptiert")

    window = Window()
    window.error = lambda exc: (_ for _ in ()).throw(AssertionError(exc))
    actual = QSettings("PDF Werkbank", "PDF Werkbank")
    old_scan = actual.value("scan/a4")
    try:
        def select_import(dialog):
            for check in dialog.findChildren(QCheckBox):
                check.setChecked(check.text() == "Scanprofil")
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, "exec", select_import), patch.object(
                QInputDialog, "getItem", return_value=("Einstellungen importieren", True)), patch.object(
                QFileDialog, "getOpenFileName", return_value=(str(package), "")):
            window.transfer_preferences()
        assert actual.value("scan/a4", False, bool)
    finally:
        if old_scan is None:
            actual.remove("scan/a4")
        else:
            actual.setValue("scan/a4", old_scan)

    first, second = inbox / "A.pdf", inbox / "B.pdf"
    target_a, target_b = output / "A.pdf", output / "B.pdf"
    make_pdf(first, "Rechnung A")
    make_pdf(second, "Rechnung B")
    journal = ArchiveRun(inbox)
    journal.begin([(first, target_a), (second, target_b)])
    java, jar = (os.environ.get(f"PDF_WERKBANK_QA_{key}") for key in ("JAVA", "VERA"))
    original_builder = window.build_archive
    def build(source, candidate, recognize, tools):
        if java and jar:
            return original_builder(source, candidate, recognize, tools)
        make_pdf(candidate, source[0].get_text().strip())
        return "PDF/A-2b bestätigt."
    # Simulate exit after a verified target was written but before its draft was removed.
    with pdf.open(first) as source:
        build(source, target_a, True, (java, jar))
    journal.jobs[0]["sha256"] = digest_file(target_a)
    journal.write()
    resumed = ArchiveRun(inbox)
    resumed.load()
    with patch.object(window, "standard_tools", return_value=(java, jar)), patch.object(
            window, "build_archive", side_effect=build) as checked, patch.object(QMessageBox, "exec", return_value=0):
        window.archive_batch([resumed.paths(job) for job in resumed.jobs], resumed)
        assert checked.call_count == 1
    assert not first.exists() and not second.exists() and not resumed.path.exists()
    assert digest_file(target_a) == journal.jobs[0]["sha256"]
    if java and jar:
        assert all(validate_standard(java, jar, path, "2b")[0] for path in (target_a, target_b))

    editable, fixed = root / "Formular.pdf", root / "Feste Kopie.pdf"
    with pdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 70), "Versandformular")
        for index, (label, kind, value) in enumerate(
                [("Name", pdf.PDF_WIDGET_TYPE_TEXT, "Musterwert"),
                 ("Auswahl", pdf.PDF_WIDGET_TYPE_CHECKBOX, True),
                 ("Ort", pdf.PDF_WIDGET_TYPE_COMBOBOX, "Berlin")]):
            widget = pdf.Widget()
            widget.field_type = kind
            widget.field_name = label
            widget.field_value = value
            y = 95 + index * 36
            widget.rect = pdf.Rect(50, y, 70 if label == "Auswahl" else 210, y + 24)
            if label == "Ort":
                widget.choice_values = ["Berlin", "Köln"]
            page.add_widget(widget)
        doc.save(editable)
    before = editable.read_bytes()
    window.open_path(str(editable))
    with patch.object(window, "preview_send", return_value=True), patch.object(window, "output_path", return_value=fixed):
        window.flatten_form_copy()
    with pdf.open(fixed) as doc:
        assert not any(doc[0].widgets())
        assert doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")[1] in ("null", "[]")
        text = doc[0].get_text()
        assert text.count("Musterwert") == 1 and text.count("Berlin") == 1
        assert any(doc.xref_get_key(font[0], "FontDescriptor/FontFile3")[0] == "xref"
                   for font in doc[0].get_fonts(full=True) if font[3] == "ZapfDingbats")
    assert editable.read_bytes() == before and len(list(window.current.doc[0].widgets())) == 3
    if os.environ.get("PDF_WERKBANK_QA_OUTPUT"):
        (Path(os.environ["PDF_WERKBANK_QA_OUTPUT"]) / "Formularfest-116.pdf").write_bytes(fixed.read_bytes())
    window.close_tab(0)

print("Fehlertolerante Suche, Einstellungsgruppen, Archivlauf-Fortsetzung und feste Formularkopie geprüft.")

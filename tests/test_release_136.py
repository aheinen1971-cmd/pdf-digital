# SPDX-License-Identifier: AGPL-3.0-only
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QProgressDialog, QPushButton, QTableWidget

from form_run import FormRun
from office_export import export_linked_sheet, read_form_values
from pdf_werkbank import Window
from preferences import FORM_TEMPLATES
from update_worker import digest_file


def write_workbook(path, second, first="A"):
    export_linked_sheet([(first, "Erste"), (second, "Zweite"), ("C", "Dritte")],
                        path, "Eingabe", ["Name", "Dateiname"], [25, 25])


def phase(root, stage):
    app = QApplication([])
    window = Window()
    window.favorite_settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
    target, book, source = root / "PDFs", root / "Liste.xlsx", root / "Vorlage.pdf"
    report = root / "Bericht.xlsx"

    def preview(dialog):
        table = dialog.findChild(QTableWidget)
        states = [table.item(row, 3).text() for row in range(3)]
        expected = {"cancel": ["Bereit", "Pflichtwert fehlt: Name · Keine Formularwerte zugeordnet", "Bereit"],
                    "crash": ["Gespeichert", "Bereit", "Bereit"],
                    "resume": ["Gespeichert", "Gespeichert", "Bereit"]}
        assert states == expected[stage], states
        next(button for button in dialog.findChildren(QPushButton)
             if button.text() == "Auswahl als PDFs speichern").click()
        return dialog.result()

    def result(dialog):
        next(button for button in dialog.buttons() if button.text() == "Excel-Bericht speichern").click()
        return dialog.result()

    def interrupted(run):
        if run.records.get("3", {}).get("done"):
            os._exit(37)
        write(run)

    write = FormRun.write
    with patch.object(window, "error", side_effect=AssertionError), \
            patch.object(QDialog, "exec", preview), patch.object(QMessageBox, "exec", result), \
            patch("pdf_werkbank.QFileDialog.getSaveFileName", return_value=(str(report), "")):
        if stage == "cancel":
            window.favorite_settings.setValue(FORM_TEMPLATES, '{"Namen":[["Name",["Name"],true]]}')
            with patch("pdf_werkbank.QFileDialog.getOpenFileName",
                       side_effect=[(str(source), ""), (str(book), "")]), \
                    patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(target)), \
                    patch("pdf_werkbank.QInputDialog.getItem", return_value=("Namen", True)), \
                    patch.object(QProgressDialog, "wasCanceled", side_effect=lambda: (target / "Erste.pdf").exists()):
                window.fill_form_batch()
        else:
            journal = next(target.glob("*.werkbanklauf"))
            with patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(journal), "")):
                if stage == "crash":
                    with patch.object(FormRun, "write", interrupted):
                        window.resume_form_fill()
                    raise AssertionError("Der Prozess hätte nach der PDF-Veröffentlichung stoppen sollen.")
                window.resume_form_fill()
    window.close()


def report_rows(path):
    with ZipFile(path) as archive:
        sheet = fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return [[cell.findtext("{*}v") or cell.findtext("{*}is/{*}t") for cell in row]
            for row in sheet.findall("{*}sheetData/{*}row")]


if len(sys.argv) > 3 and sys.argv[-2] == "--phase":
    phase(Path(sys.argv[-3]), sys.argv[-1])
else:
    with TemporaryDirectory() as folder:
        root = Path(folder)
        target = root / "PDFs"
        target.mkdir()
        source, book = root / "Vorlage.pdf", root / "Liste.xlsx"
        with pdf.open() as doc:
            page = doc.new_page()
            field = pdf.Widget()
            field.field_name = "Name"
            field.field_type = pdf.PDF_WIDGET_TYPE_TEXT
            field.rect = pdf.Rect(40, 50, 180, 75)
            page.add_widget(field)
            doc.save(source)
        write_workbook(book, "")
        script = str(Path(__file__).resolve())
        command = [sys.executable, "-B", "-X", "utf8", "-c",
                   "import os,runpy,sys;sys.path.insert(0,os.getcwd());runpy.run_path(sys.argv[1])",
                   script, str(root), "--phase"]
        assert subprocess.run([*command, "cancel"], timeout=30).returncode == 0
        journal = next(target.glob("*.werkbanklauf"))
        first = digest_file(target / "Erste.pdf")
        assert list(FormRun.load(journal).records) == ["2"]
        assert report_rows(root / "Bericht.xlsx")[-1] == ["4", "Dritte.pdf", "Offen"]
        assert not (target / "Dritte.pdf").exists()

        write_workbook(book, "B")
        assert subprocess.run([*command, "crash"], timeout=30).returncode == 37
        assert not FormRun.load(journal).records["3"]["done"]
        second = digest_file(target / "Zweite.pdf")
        assert subprocess.run([*command, "resume"], timeout=30).returncode == 0
        assert digest_file(target / "Erste.pdf") == first and digest_file(target / "Zweite.pdf") == second
        assert read_form_values(target / "Dritte.pdf") == {"Name": "C"}
        assert all(record["done"] for record in FormRun.load(journal).records.values())
        assert report_rows(root / "Bericht.xlsx")[1:] == [
            ["2", "Erste.pdf", "Gespeichert"], ["3", "Zweite.pdf", "Gespeichert"], ["4", "Dritte.pdf", "Gespeichert"]]
        run = FormRun.load(journal)
        try:
            run.reconcile([(2, "Erste.pdf", target / "Erste.pdf", {"Name": "Verändert"}, [])])
        except ValueError as exc:
            assert "Zeile 2" in str(exc)
        else:
            raise AssertionError("Veränderte fertige Zeile wurde akzeptiert.")
    print("Abbruch, Korrektur, Neustart, Prozessausfall beim Speichern und Excel-Bericht geprüft.")

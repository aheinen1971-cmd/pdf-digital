# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QListWidget, QPushButton, QTableWidget

import pdf_werkbank as app_module
from filing import file_signature
from form_run import FormRun
from office_export import export_linked_sheet


def settings_for():
    return app_module.QSettings("PDF Werkbank", "PDF Werkbank")


def seed(root):
    app = QApplication([])
    settings = settings_for()
    window = app_module.Window()
    template = root / "Vorlage.pdf"
    workbook = root / "Liste.xlsx"
    target = root / "Formularlauf"
    target.mkdir()
    with pdf.open() as doc:
        page = doc.new_page()
        field = pdf.Widget()
        field.field_name = "Name"
        field.field_type = pdf.PDF_WIDGET_TYPE_TEXT
        field.rect = pdf.Rect(30, 30, 140, 60)
        page.add_widget(field)
        doc.save(template)
        window.queue_scan(doc)
        window.queue_scan(doc)
    export_linked_sheet([("A",)], workbook, "Eingabe", ["Name"], [20])
    source_hash = hashlib.sha256(template.read_bytes()).hexdigest()
    columns = [["Name", ["Name"], True]]
    run = FormRun.begin(target, template, source_hash, workbook, "Eingabe", columns)
    run.remember_overview(1, file_signature(workbook))
    completed = FormRun.begin(target, template, source_hash, workbook, "Eingabe", columns)
    completed.remember_overview(1, file_signature(workbook))
    (target / "Kopie.pdf").write_bytes(template.read_bytes())
    completed.records["2"] = {"done": True, "name": "Kopie.pdf", "row": source_hash, "pdf": source_hash}
    completed.write()
    settings.setValue("form-export/runs", [str(run.path), str(completed.path)])
    incoming = root / "Rechnungen"
    incoming.mkdir()
    for name, offset in (("Heute", 0), ("Überfällig", -1), ("Später", 1)):
        content = f"<Test>{name}</Test>".encode()
        (incoming / (name + ".xml")).write_bytes(content)
        key = "invoice-watch/reminder/" + hashlib.sha256(content).hexdigest()
        settings.setValue(key, (date.today() + timedelta(days=offset)).isoformat() + "|Test")
    settings.setValue("invoice-watch/folder", str(incoming))
    settings.setValue("work-overview/start", True)
    settings.sync()
    window.close()


def verify_restart(root):
    settings = settings_for()
    checked, errors = [], []
    windows = []

    class ObservedWindow(app_module.Window):
        def __init__(self):
            super().__init__()
            windows.append(self)
            self.probe = QTimer(self, interval=100)
            self.probe.timeout.connect(check)
            self.probe.start()
            QTimer.singleShot(16000, QApplication.quit)

    def check():
        if not windows or windows[0].open_work_dialog is None:
            return
        window = windows[0]
        dialog = window.open_work_dialog
        table = dialog.findChild(QTableWidget)
        if table.rowCount() < 5:
            return
        window.probe.stop()
        try:
            assert [table.item(row, 0).text() for row in range(5)] == [
                "Formularlauf", "Scan", "Scan", "Rechnung", "Rechnung"]
            assert table.item(3, 1).text() == "Überfällig.xml" and "Überfällig" in table.item(3, 2).text()
            assert table.item(4, 1).text() == "Heute.xml" and "Heute fällig" in table.item(4, 2).text()
            assert "1 Zeile" in table.item(0, 2).text()
            scans = window.scan_drafts()
            with patch.object(QDialog, "exec", lambda inbox: checked.append(inbox.findChild(QListWidget).currentRow())):
                table.cellDoubleClicked.emit(2, 1)
            assert checked == [1] and scans[1].name in table.item(2, 1).toolTip()
            with patch.object(window, "review_form_fill") as review:
                table.selectRow(0)
                next(button for button in dialog.findChildren(QPushButton) if button.text() == "Zum Eintrag").click()
                review.assert_called_once()
                assert review.call_args.args[2].path.name in table.item(0, 1).toolTip()
            table.cellDoubleClicked.emit(4, 1)
            watcher = window._invoice_folder_window
            invoices = watcher.findChild(QTableWidget)
            assert invoices.item(invoices.currentRow(), 0).text() == "Heute.xml"
            assert watcher.findChild(QComboBox).currentText() == "Wiedervorlage fällig"
            window.show_open_work()
            dialog.findChild(QCheckBox).setChecked(False)
            settings.sync()
            assert settings.value("work-overview/start", True, bool) is False
            checked.append("Neustart")
        except BaseException as exc:
            errors.append(exc)
        finally:
            window.close()
            QApplication.quit()

    with patch.object(app_module, "Window", ObservedWindow), \
            patch.object(app_module.ReleaseCheck, "check"), \
            patch.object(sys, "argv", [sys.argv[0]]):
        app_module.main()
    if errors:
        raise errors[0]
    assert checked == [1, "Neustart"]


if len(sys.argv) > 3 and sys.argv[-2] == "--phase":
    root = Path(sys.argv[-3])
    os.environ["LOCALAPPDATA"] = str(root / "Local")

    class LocalSettings(QSettings):
        def __init__(self, *_):
            super().__init__(str(root / "settings.ini"), QSettings.Format.IniFormat)

    with patch.object(app_module, "QSettings", LocalSettings):
        (seed if sys.argv[-1] == "seed" else verify_restart)(root)
else:
    with TemporaryDirectory() as folder:
        script = str(Path(__file__).resolve())
        command = [sys.executable, "-B", "-X", "utf8", "-c",
                   "import os,runpy,sys;sys.path.insert(0,os.getcwd());runpy.run_path(sys.argv[1])",
                   script, folder, "--phase"]
        for stage in ("seed", "restart"):
            assert subprocess.run([*command, stage], timeout=35).returncode == 0
    print("Übersicht nach Neustart, Scan-Auswahl, direkter Formularlauf und fälliger Beleg geprüft.")

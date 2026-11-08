# SPDX-License-Identifier: AGPL-3.0-only
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QEventLoop, QPoint, QSettings, Qt, QTimer
from PySide6.QtNetwork import QNetworkProxy
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from office_export import export_linked_sheet, read_form_values
from online_updates import ReleaseCheck
from pdf_werkbank import ScanFieldPreview, Window, save_copy
from preferences import FORM_TEMPLATES
from scan_form import capture_scan_fields, check_scan_target, scan_form_templates
from update_worker import package_url
from work_backup import read_backup, restore_backup, write_backup


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    source, scan, output = root / "Original.pdf", root / "Scan.pdf", root / "Ausfüllbar.pdf"
    with pdf.open() as sample:
        page = sample.new_page(width=400, height=500)
        page.insert_text((40, 85), "Kunde und Auswahl", fontsize=17)
        with pdf.open() as image:
            scanned = image.new_page(width=480, height=600)
            scanned.insert_image(scanned.rect, pixmap=page.get_pixmap())
            image.save(scan)
        for index, (name, kind) in enumerate((("Kunde", pdf.PDF_WIDGET_TYPE_TEXT),
                                               ("Auswahl", pdf.PDF_WIDGET_TYPE_COMBOBOX),
                                               ("Ja", pdf.PDF_WIDGET_TYPE_CHECKBOX))):
            field = pdf.Widget()
            field.field_name, field.field_type = name, kind
            field.rect = pdf.Rect(40, 100 + index * 50, 180, 125 + index * 50)
            field.field_flags = pdf.PDF_FIELD_IS_REQUIRED if index == 0 else 0
            if kind == pdf.PDF_WIDGET_TYPE_COMBOBOX:
                field.choice_values = ["A", "B"]
                field.field_value = "A"
            if kind == pdf.PDF_WIDGET_TYPE_TEXT:
                field.field_value = "Alter Inhalt"
                field.text_maxlen = 30
            page.add_widget(field)
        sample.save(source)
    window = Window()
    window.favorite_settings = QSettings(str(root / "prefs.ini"), QSettings.Format.IniFormat)
    window.open_path(str(source))
    with patch("pdf_werkbank.QInputDialog.getText", return_value=("Papier", True)), \
            patch.object(window, "error", side_effect=AssertionError):
        window.save_scan_field_template()
    saved = scan_form_templates(window.favorite_settings)["Papier"]
    assert saved[0]["required"] and saved[0]["maximum"] == 30
    assert saved[1]["choices"] == ["A", "B"] and "Alter Inhalt" not in repr(saved)
    backup = root / "Backup.werkbankbackup"
    write_backup(backup, window.favorite_settings, root / "measurements")
    imported = QSettings(str(root / "imported.ini"), QSettings.Format.IniFormat)
    restore_backup(read_backup(backup), {"settings"}, imported, root / "measurements")
    assert scan_form_templates(imported)["Papier"] == saved
    window.favorite_settings = imported
    session = window.open_path(str(scan))

    def drag(dialog):
        dialog.show()
        app.processEvents()
        view = dialog.findChild(ScanFieldPreview)
        begin = view.bounds(view.fields[0]).center().toPoint()
        end = begin + QPoint(40, 30)
        QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=begin)
        QTest.mouseMove(view, end)
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=end)
        assert view.fields[0]["box"][0] > saved[0]["box"][0]
        assert view.fields[1]["box"] == saved[1]["box"]
        dialog.accept()
        return dialog.result()

    with patch("pdf_werkbank.QInputDialog.getItem", return_value=("Papier", True)), \
            patch.object(QDialog, "exec", drag), patch.object(window, "error", side_effect=AssertionError):
        window.use_scan_field_template()
    assert session.changed
    positioned = capture_scan_fields(session.doc)
    assert positioned[0]["box"][0] > saved[0]["box"][0] and positioned[0]["required"]
    assert scan_form_templates(imported)["Papier"] == saved
    try:
        check_scan_target(session.doc, saved)
    except ValueError as exc:
        assert "bereits" in str(exc)
    else:
        raise AssertionError("Doppelte Felder wurden akzeptiert.")
    save_copy(session.doc, output)
    assert read_form_values(output)["Kunde"] == ""
    with pdf.open() as rotated:
        rotated.new_page(width=500, height=400).set_rotation(90)
        from scan_form import apply_scan_fields
        apply_scan_fields(rotated, saved)
        assert abs(capture_scan_fields(rotated)[0]["box"][0] - saved[0]["box"][0]) < 0.0001
    workbook, result = root / "Liste.xlsx", root / "Ergebnis"
    result.mkdir()
    export_linked_sheet([("Kopie", "Musterkunde", "B", "Ja")], workbook, "Daten",
                        ["Dateiname", "Kunde", "Auswahl", "Ja"], [20] * 4)
    imported.setValue(FORM_TEMPLATES, '{"Kunden":[["Kunde",["Kunde"],true],["Auswahl",["Auswahl"],false],["Ja",["Ja"],false]]}')

    def batch(dialog):
        dialog.accept()
        return dialog.result()

    with patch("pdf_werkbank.QFileDialog.getOpenFileName", side_effect=[(str(output), ""), (str(workbook), "")]), \
            patch("pdf_werkbank.QInputDialog.getItem", return_value=("Kunden", True)), \
            patch("pdf_werkbank.QFileDialog.getExistingDirectory", return_value=str(result)), \
            patch.object(QDialog, "exec", batch), patch.object(QMessageBox, "exec", return_value=0), \
            patch.object(window, "error", side_effect=AssertionError):
        window.fill_form_batch()
    assert read_form_values(result / "Kopie.pdf") == {"Kunde": "Musterkunde", "Auswahl": "B", "Ja": "Yes"}
    session.changed = False
    while window.sessions:
        window.close_tab(0)
    window.close()


statuses = [503, 200, 503, 503]


class Server(BaseHTTPRequestHandler):
    def do_GET(self):
        status = statuses.pop(0)
        data = {"tag_name": "v1.47.0", "assets": [{"name": "PDF-Werkbank-1.47.0-Windows.zip",
                 "state": "uploaded", "browser_download_url": package_url("1.47.0"),
                 "digest": "sha256:" + "a" * 64, "size": 123}]}
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


with ThreadingHTTPServer(("127.0.0.1", 0), Server) as server:
    Thread(target=server.serve_forever, daemon=True).start()
    checker = ReleaseCheck((1, 46, 0), app)
    checker.network.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
    checker.retry.setInterval(20)
    results = []
    loop = QEventLoop()
    checker.finished.connect(lambda *values: (results.append(values), loop.quit()))
    with patch("online_updates.LATEST", f"http://127.0.0.1:{server.server_port}"):
        for _ in range(2):
            checker.check(manual=True)
            QTimer.singleShot(2000, loop.quit)
            loop.exec()
    server.shutdown()
assert results == [(("1.47.0", "a" * 64, 123), True, True), (None, False, True)]
assert not statuses and "HTTP 503" in checker.error_detail

print("Scan-Feldvorlage, Verschieben, PDF-/Excel-Serienlauf und Wiederholung bei GitHub-Fehler geprüft.")

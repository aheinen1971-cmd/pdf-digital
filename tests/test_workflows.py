# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtGui import QPageSize
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QLineEdit, QPushButton

from pdf_werkbank import Window, print_sheets, redact_area, redaction_findings, save_copy


app = QApplication([])
here = Path(__file__).parent
with TemporaryDirectory() as temp:
    root = Path(temp)
    source = root / "Original.pdf"
    target = root / "Versand.pdf"
    with pdf.open() as scan_source, pdf.open() as doc:
        scan = scan_source.new_page()
        scan.insert_text((50, 100), "Prüfung für die PDF Werkbank", fontsize=23)
        raster = scan.get_pixmap(matrix=pdf.Matrix(4, 4))
        page = doc.new_page()
        page.insert_image(page.rect, pixmap=raster)
        doc.new_page().insert_text((50, 80), "Ausgelassene Seite")
        doc.new_page().insert_text((50, 80), "Versand zuerst")
        doc.new_page()
        doc.save(source)
    original = source.read_bytes()
    window = Window()
    window.error = lambda error: (_ for _ in ()).throw(AssertionError(error))
    window.open_path(str(source))
    window.output_path = lambda *args: target
    real_exec = QDialog.exec

    def send_dialog(dialog):
        if dialog.windowTitle() == "Für den Versand fertig machen":
            dialog.findChild(QLineEdit).setText("3, 1, 4")
            dialog.findChildren(QComboBox)[1].setCurrentText("Kleine Datei")
            return QDialog.DialogCode.Accepted
        assert dialog.windowTitle() == "Versandkopie prüfen"
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert any("3 Seiten" in text and "4 (Originalseiten)" in text and "KB" in text for text in labels), labels
        dialog.findChild(QComboBox).setCurrentIndex(1)
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(here / "send-v140.png"))
        return QDialog.DialogCode.Accepted

    QDialog.exec = send_dialog
    try:
        window.prepare_send()
    finally:
        QDialog.exec = real_exec
    assert source.read_bytes() == original
    with pdf.open(target) as result:
        assert len(result) == 3
        assert result[0].get_text().strip() == "Versand zuerst"
        assert result[1].search_for("Prüfung")
        assert not result[2].get_text().strip()
        assert result[1].get_images(full=True)[0][2] < window.current.doc[0].get_images(full=True)[0][2]

    with pdf.open() as doc:
        page = doc.new_page()
        page.insert_text((45, 100), "Nur hier ABC987")
        redact_area(page, pdf.Rect(42, 80, 190, 110))
        page = doc.new_page()
        page.insert_text((50, 100), "Kunden-Nummer 123456")
        page.add_text_annot((70, 150), "Interner Kommentar")
        widget = pdf.Widget()
        widget.rect = pdf.Rect(100, 170, 300, 205)
        widget.field_type = pdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = "Kundenfeld"
        widget.field_value = "Konto 789"
        page.add_widget(widget)
        doc.set_metadata({"title": "Auftrag Gamma"})
        doc.set_toc([[1, "Register Sigma", 2]])
        doc.embfile_add("Beleg Omega.txt", b"Anlage Delta")
        checked = root / "Geschwaerzt.pdf"
        save_copy(doc, checked)
    with pdf.open(checked) as doc:
        found = redaction_findings(doc, ["ABC987", "123456", "Gamma", "Kommentar", "Konto 789", "Sigma", "Omega", "Delta"])
        assert found == {"123456": ["Seite 2 · Text / OCR"], "Gamma": ["Dokumenteigenschaften"],
                         "Kommentar": ["Seite 2 · Kommentar"], "Konto 789": ["Seite 2 · Text / OCR", "Seite 2 · Formularfeld"],
                         "Sigma": ["Lesezeichen"], "Omega": ["Dateiname einer Anlage"]}, found

    assert print_sheets([2, 1, 0], True) == [(2, 1), (0, None)]
    assert print_sheets([2, 0], False) == [(2,), (0,)]
    samsung = next((device for device in QPrinterInfo.availablePrinters() if "clx-3300" in device.printerName().casefold()), None)
    if samsung:
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        previous = settings.value("samsung_printer")
        native_exec = QPrintDialog.exec
        observed = []

        def capture_profile(dialog):
            printer = dialog.printer()
            assert printer.printerName() == samsung.printerName()
            assert printer.pageLayout().pageSize().id() == QPageSize.PageSizeId.A4
            assert printer.duplex() == QPrinter.DuplexMode.DuplexLongSide
            observed.append(printer.printerName())
            return QDialog.DialogCode.Rejected

        QPrintDialog.exec = capture_profile
        try:
            window.print_dialog(samsung=True)
        finally:
            QPrintDialog.exec = native_exec
            if previous is None:
                settings.remove("samsung_printer")
            else:
                settings.setValue("samsung_printer", previous)
        assert observed

        def capture_duplex(dialog):
            def texts():
                return [label.text() for label in dialog.findChildren(QLabel)]
            assert "Vorderseite · PDF-Seite 3" in texts() and "Rückseite · PDF-Seite 2" in texts()
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "›").click()
            assert "Vorderseite · PDF-Seite 1" in texts() and "Rückseite · bleibt leer" in texts()
            dialog.show()
            app.processEvents()
            assert dialog.grab().save(str(here / "duplex-v140.png"))
            return QDialog.DialogCode.Rejected

        printer = QPrinter(samsung)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide)
        QDialog.exec = capture_duplex
        try:
            assert not window.confirm_print(window.current.doc, [2, 1, 0], printer)
        finally:
            QDialog.exec = real_exec
    window.close_tab(0)
print("Versandkopie mit OCR und Komprimierung, Schwärzungsprüfung und Blattvorschau geprüft.")

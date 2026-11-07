from pathlib import Path
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QLabel
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from pdf_werkbank import Window
import pymupdf as pdf

app = QApplication([])
window = Window()
path = Path(__file__).parent / "test-input.pdf"
window.open_path(str(path))
with pdf.open(path) as doc:
    original_exec = QDialog.exec

    def cancel_after_review(dialog):
        assert dialog.windowTitle() == "Druckvorschau"
        assert any("PDF-Seite 3" in label.text() for label in dialog.findChildren(QLabel))
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "›").click()
        assert any("PDF-Seite 1" in label.text() for label in dialog.findChildren(QLabel))
        return QDialog.DialogCode.Rejected

    QDialog.exec = cancel_after_review
    try:
        assert not window.confirm_print(doc, [2, 0], QPrinter())
    finally:
        QDialog.exec = original_exec

for device in QPrinterInfo.availablePrinters():
    if "Samsung" in device.printerName():
        printer = QPrinter(device, QPrinter.PrinterMode.HighResolution)
        for mode in (QPrinter.DuplexMode.DuplexLongSide, QPrinter.DuplexMode.DuplexShortSide):
            assert mode in device.supportedDuplexModes()
            printer.setDuplex(mode)
            assert printer.duplex() == mode
        print(f"Windows-Treiber akzeptiert lange und kurze Kante: {device.printerName()}")
print("Druckvorschau, Seitenwechsel und Abbrechen OK")


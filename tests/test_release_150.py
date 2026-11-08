# SPDX-License-Identifier: AGPL-3.0-only
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QVBoxLayout

from pdf_werkbank import Window
from work_backup import read_backup, restore_backup, write_backup


app = QApplication.instance() or QApplication([])
if os.environ.get("PDF_WERKBANK_QA_DARK"):
    font_path = Path(os.environ["WINDIR"]) / "Fonts/arial.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
with TemporaryDirectory() as directory:
    root = Path(directory)
    config = root / "settings.ini"

    class LocalSettings(QSettings):
        def __init__(self, *_):
            super().__init__(str(config), QSettings.Format.IniFormat)

    path = root / "Weiss.pdf"
    with pdf.open() as document:
        page = document.new_page(width=300, height=300)
        page.insert_text((45, 45), "Testseite")
        document.save(path)

    with patch("pdf_werkbank.QSettings", LocalSettings):
        window = Window()
        assert not window.dark_mode
        window.show()
        window.open_path(str(path))
        before = window.canvas.pixmap().toImage()
        tabs = [window.tools.tabText(i) for i in range(window.tools.count())]
        window.tools.setCurrentIndex(tabs.index("Ansicht"))
        button = next(widget for widget in window.findChildren(QPushButton) if widget.text() == "Dunkelmodus")
        with patch("pdf_werkbank.QInputDialog.getItem", return_value=("Dunkel", True)):
            button.click()
        app.processEvents()
        assert window.dark_mode and LocalSettings().value("view/dark-mode", False, bool)
        window.render()
        assert window.canvas.pixmap().toImage() == before
        dialog = QDialog(window)
        dialog.setWindowTitle("Vorschau Dunkelmodus")
        layout = QVBoxLayout(dialog)
        entry = QLineEdit("Lesbarer Text")
        layout.addWidget(entry)
        layout.addWidget(QPushButton("Schließen"))
        dialog.resize(380, 130)
        dialog.show()
        app.processEvents()
        assert entry.palette().color(QPalette.ColorRole.Base).name() == "#202832"
        assert entry.palette().color(QPalette.ColorRole.Text).name() == "#edf3fa"
        assert dialog.grab().toImage().pixelColor(3, 3).name() == "#181d25"
        if os.environ.get("PDF_WERKBANK_QA_DARK"):
            window.grab().save(str(Path(os.environ["PDF_WERKBANK_QA_DARK"]) / "Dunkelmodus.png"))
            dialog.grab().save(str(Path(os.environ["PDF_WERKBANK_QA_DARK"]) / "Dunkelmodus-Dialog.png"))
        dialog.close()
        window.close_tab(0)
        window.close()
        backup = root / "Backup.werkbankbackup"
        write_backup(backup, LocalSettings(), root / "Messungen")
        fresh = QSettings(str(root / "fresh.ini"), QSettings.Format.IniFormat)
        restore_backup(read_backup(backup), {"settings"}, fresh, root / "Messungen")
        assert fresh.value("view/dark-mode", False, bool)
        window = Window()
        assert window.dark_mode
        with patch("pdf_werkbank.QInputDialog.getItem", return_value=("Hell", True)):
            window.choose_appearance()
        assert not window.dark_mode and not LocalSettings().value("view/dark-mode", True, bool)
        assert app.palette().color(QPalette.ColorRole.Window) == app._werkbank_default_palette.color(QPalette.ColorRole.Window)
        window.close()

print("Dunkelmodus, Dialogkontrast, unveränderte PDF-Ansicht, Speicherung und Rückschalten geprüft.")

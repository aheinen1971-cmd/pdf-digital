# SPDX-License-Identifier: AGPL-3.0-only
import json
import string
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QPushButton, QDialog, QLineEdit, QListWidget

import pdf_werkbank
from pdf_werkbank import UI_LANGUAGES, Window


app = QApplication.instance() or QApplication([])
catalog = json.loads(Path(pdf_werkbank.__file__).with_name("ui_languages.json").read_text(encoding="utf-8"))
assert set(catalog) == set(UI_LANGUAGES) - {"de"}
alias_keys = {
    "PDF anhängen": "zusammenfügen zusammenfuegen zusammenführen verbinden merge combine pdf",
    "Scan-PDF verkleinern": "komprimieren dateigröße dateigroesse reduzieren compress shrink pdf",
    "Seiten extrahieren": "aufteilen trennen split extract pdf",
    "Schwärzen": "unkenntlich entfernen redigieren redact",
    "OCR für das Dokument": "texterkennung durchsuchbar text recognition searchable pdf",
}
def fields(text):
    return Counter(field for _, field, _, _ in string.Formatter().parse(text) if field is not None)
fixed_templates = ["Erwartet ein Excel-Datum, TT.MM.JJJJ oder JJJJ-MM-TT.",
    "Quellcode unter AGPL-3.0-only.",
    "{0} von {1} ausgewählten PDF-Kopien gespeichert. Der Lauf kann mit {2} fortgesetzt werden.",
    "Version {0} ist bereit. Schließe die Werkbank und öffne sie erneut über deinen bisherigen Starter.",
    "Mindestens {0} Zeichen. Bewahre das Passwort sicher auf. Es kann später nicht zurückgesetzt werden."]
for code, words in catalog.items():
    for source in fixed_templates:
        assert words[source] != source, (code, source)
        assert fields(words[source]) == fields(source), (code, source)
    assert "AGPL-3.0-only" in words[fixed_templates[1]]
with TemporaryDirectory() as directory:
    path = str(Path(directory) / "language.ini")
    for code in UI_LANGUAGES:
        settings = QSettings(path, QSettings.Format.IniFormat)
        settings.setValue("view/language", code)
        settings.sync()
        with patch("pdf_werkbank.QSettings", wraps=QSettings) as settings_factory:
            settings_factory.return_value = QSettings(path, QSettings.Format.IniFormat)
            window = Window()
            assert window.ui_language == code
            words = catalog.get(code, {})
            expected = Qt.LayoutDirection.RightToLeft if code in ("ar", "he") else Qt.LayoutDirection.LeftToRight
            assert window.layoutDirection() == expected
            for index, (tab, commands) in enumerate(window.groups):
                if code != "de":
                    assert tab in words
                    assert all(label in words for label, _ in commands)
                assert window.tools.tabText(index) == words.get(tab, tab)
                window.tools.setCurrentIndex(index)
                assert all(button.text() for button in window.ribbon_scroll.findChildren(QPushButton))
            assert window.ui("PDF öffnen") == words.get("PDF öffnen", "PDF öffnen")
            assert window.ui("An untranslated string") == "An untranslated string"
            template = "Blatt {0} von {1}"
            assert window.ui_format(template, 93, 98) == words.get(template, template).format(93, 98)
            if code == "en-US":
                assert window.ui_columns(("Datei", "Name")) == ("File", "Name")
            for label, alias in alias_keys.items():
                phrase = words.get(alias, alias).split()[0]
                def search(dialog):
                    dialog.findChild(QLineEdit).setText(phrase)
                    found = dialog.findChild(QListWidget)
                    expected_label = words.get(label, label) + " · "
                    assert any(found.item(i).text().startswith(expected_label)
                               for i in range(found.count())), (code, phrase, label)
                    return QDialog.DialogCode.Rejected
                with patch.object(QDialog, "exec", search):
                    window.command_palette()
        if code == "de":
            with patch("pdf_werkbank.QInputDialog.getItem", return_value=("English (en-US)", True)), \
                    patch("pdf_werkbank.QMessageBox.information"):
                window.choose_language()
            assert QSettings(path, QSettings.Format.IniFormat).value("view/language") == "en-US"
        window.close()
        window.deleteLater()
        app.processEvents()

print("20 Sprachen, Menütexte, Fallback und Rechts-nach-links-Layout geprüft.")

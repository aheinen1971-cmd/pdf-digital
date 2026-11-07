# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QDialogButtonBox, QLineEdit

from pdf_werkbank import Window
from work_backup import (ENCRYPTED_PREFIX, BackupPasswordRequired, automatic_backup,
                         read_backup, write_backup)


def rejects(path, password, error_type, message):
    try:
        read_backup(path, password)
    except error_type as exc:
        assert message in str(exc), exc
    else:
        raise AssertionError("Ungültiges Backup wurde gelesen.")


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as folder:
    root = Path(folder)
    window = Window()
    settings = QSettings(str(root / "test.ini"), QSettings.Format.IniFormat)
    window.favorite_settings = settings
    key = "invoice-watch/note/" + "c" * 64
    settings.setValue(key, "Vertrauliche Testnotiz")
    plans = root / "plans"
    plan = plans / ("a" * 64) / ("b" * 64 + ".json")
    plan.parent.mkdir(parents=True)
    plan.write_text('{"version":1,"pages":[]}', encoding="utf-8")
    password = "Prüfpasswort mit Leerzeichen "
    plain = root / "plain.werkbankbackup"
    protected = root / "protected.werkbankbackup"
    second = root / "second.werkbankbackup"
    altered = root / "altered.werkbankbackup"
    write_backup(plain, settings, plans)
    write_backup(protected, settings, plans, password)
    write_backup(second, settings, plans, password)
    expected = read_backup(plain)
    assert expected["archive"][key] == "Vertrauliche Testnotiz" and len(expected["plans"]) == 1
    assert read_backup(protected, password) == expected
    assert read_backup(second, password) == expected
    data = protected.read_bytes()
    start = len(ENCRYPTED_PREFIX) + 1
    assert data.startswith(ENCRYPTED_PREFIX) and b"settings.json" not in data
    assert data[start:start + 16] != second.read_bytes()[start:start + 16]
    assert data[start + 16:start + 28] != second.read_bytes()[start + 16:start + 28]
    rejects(protected, None, BackupPasswordRequired, "Passwort")
    rejects(protected, password.rstrip(), ValueError, "Passwort ist falsch")
    for offset in (start, start + 16, start + 28, len(data) - 1):
        damaged = bytearray(data)
        damaged[offset] ^= 1
        altered.write_bytes(damaged)
        rejects(altered, password, ValueError, "beschädigt")
    altered.write_bytes(data[:len(ENCRYPTED_PREFIX)] + b"\x02" + data[start:])
    rejects(altered, password, ValueError, "nicht unterstützt")
    altered.write_bytes(data[:start + 12])
    rejects(altered, password, ValueError, "beschädigt")
    automatic = automatic_backup(settings, plans, "1.29.1", "1.30.0", root / "auto")
    assert automatic.read_bytes().startswith(b"PK") and read_backup(automatic) == expected

    def fill_password_dialog():
        dialog = app.activeModalWidget()
        dialog.findChild(QCheckBox).setChecked(True)
        first, repeat = dialog.findChildren(QLineEdit)
        save = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)
        first.setText("kurz")
        save.click()
        assert dialog.isVisible()
        first.setText(password)
        repeat.setText("abweichendes Passwort")
        save.click()
        assert dialog.isVisible()
        repeat.setText(password)
        save.click()

    with patch.object(window, "measurement_store", return_value=plans), \
            patch("pdf_werkbank.QInputDialog.getItem", return_value=("Backup speichern", True)), \
            patch("pdf_werkbank.QFileDialog.getSaveFileName", return_value=(str(second), "")), \
            patch.object(window, "error") as error:
        QTimer.singleShot(0, fill_password_dialog)
        window.work_backup()
        error.assert_not_called()
        assert read_backup(second, password) == expected

    with patch("pdf_werkbank.QInputDialog.getItem", return_value=("Backup wiederherstellen", True)), \
            patch("pdf_werkbank.QFileDialog.getOpenFileName", return_value=(str(second), "")), \
            patch("pdf_werkbank.QInputDialog.getText", return_value=("falsch", True)) as prompt, \
            patch.object(window, "restore_backup_dialog") as preview, patch.object(window, "error") as error:
        window.work_backup()
        preview.assert_not_called()
        error.assert_called_once()
        prompt.return_value = ("", False)
        window.work_backup()
        preview.assert_not_called()
        prompt.return_value = (password, True)
        window.work_backup()
        preview.assert_called_once_with(expected, settings)
        assert prompt.call_args.args[3] == QLineEdit.EchoMode.Password
    window.close()

print("Passwortschutz, alte Backups, Manipulationsprüfung und Sicherungsdialog geprüft.")

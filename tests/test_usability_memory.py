import gc
import os
import weakref
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget
from pdf_werkbank import Session, Window

app = QApplication([])
window = Window()
for phrase, expected in [("zusammenfügen", "PDF anhängen"),
                         ("zusammenfuegen", "PDF anhängen"),
                         ("komprimieren", "Scan-PDF verkleinern"),
                         ("PDF verkleinern", "Scan-PDF verkleinern"),
                         ("aufteilen", "Seiten extrahieren")]:
    def search(dialog):
        dialog.findChild(QLineEdit).setText(phrase)
        found = dialog.findChild(QListWidget)
        assert any(expected in found.item(i).text() for i in range(found.count())), phrase
        return QDialog.DialogCode.Rejected
    with patch.object(QDialog, "exec", search):
        window.command_palette()

with TemporaryDirectory() as folder:
    root = Path(folder)
    for encrypted in (False, True):
        path = root / f"test-{encrypted}.pdf"
        with pdf.open() as doc:
            page = doc.new_page()
            image = pdf.Pixmap(pdf.csRGB, 1500, 1500, os.urandom(6750000), False)
            page.insert_image(page.rect, pixmap=image)
            options = dict(encryption=pdf.PDF_ENCRYPT_AES_256, owner_pw="audit", user_pw="audit") if encrypted else {}
            doc.save(path, **options)
        session = Session(path, password="audit" if encrypted else None)
        for number in range(10):
            session.checkpoint(str(number))
            session.doc[0].set_rotation(((number+1)*90) % 360)
        assert len(session.history) == 8
        assert all(hasattr(entry[0], "read") for entry in session.history)
        previous = session.history[-1]
        with patch("pdf_werkbank.tempfile.TemporaryFile", side_effect=OSError("disk full")):
            try:
                session.checkpoint("failed")
            except OSError:
                pass
            else:
                raise AssertionError("Storage error was ignored")
        assert session.history[-1] is previous
        del previous
        for number in reversed(range(2, 10)):
            assert session.restore_edit() == str(number)
            assert session.doc[0].rotation == (number*90) % 360
        assert len(session.redo_stack) == 8
        for number in range(2, 10):
            session.restore_edit(forward=True)
            assert session.doc[0].rotation == ((number+1)*90) % 360
        handles = [entry[0] for entry in session.history]
        for handle in handles:
            handle.seek(0)
            with pdf.open(stream=handle.read(), filetype="pdf") as check:
                assert bool(check.needs_pass) == encrypted
        refs = [weakref.ref(handle) for handle in handles]
        del handle, handles
        session.history.clear()
        session.redo_stack.clear()
        session.doc.close()
        gc.collect()
        assert all(ref() is None for ref in refs)
        if not encrypted:
            opened = window.open_path(str(path))
            opened.checkpoint("close cleanup")
            ref = weakref.ref(opened.history[-1][0])
            window.close_tab(0)
            gc.collect()
            assert ref() is None and not opened.history
print("Search aliases and eight disk-backed undo/redo levels, including encrypted PDFs: passed")

# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import string
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf as pdf
from PySide6.QtCore import QSettings, QRectF, Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox, QPushButton, QLineEdit, QPlainTextEdit, QComboBox, QSpinBox
import pdf_werkbank as module
from pdf_werkbank import Window, CompareDialog, UI_LANGUAGES

app = QApplication.instance() or QApplication([])
def button(dialog, label):
    return next(b for b in dialog.findChildren(QPushButton) if b.text() == label)
def fail(error):raise AssertionError(str(error))
def close(window):
    for session in window.sessions:session.changed=False
    window.close()
    for session in window.sessions:session.doc.close()
    window.sessions.clear()

def detach(session):
    # Simulate an externally replaceable source while retaining the editing state.
    doc=pdf.open(stream=session.doc.tobytes(),filetype='pdf')
    session.doc.close();session.doc=doc

with TemporaryDirectory(ignore_cleanup_errors=True) as directory:
    root=Path(directory);source=root/'Original.pdf';target=root/'Copy.pdf'
    test_settings=QSettings(str(root/'settings.ini'),QSettings.Format.IniFormat)
    factory=patch.object(module,'QSettings',wraps=QSettings)
    settings_factory=factory.start();settings_factory.return_value=test_settings;settings_factory.Status=QSettings.Status
    with pdf.open() as d:
        for n in range(3):d.new_page().insert_text((40,60),f'Page {n+1} original')
        d.save(source)
    original=source.read_bytes()
    w=Window();w.error=fail;w.open_path(str(source));w.current.doc[0].insert_text((40,90),'Unsaved edit');w.changed()
    def review(dialog):
        assert isinstance(dialog,CompareDialog)
        assert 'Unsaved edit' not in dialog.docs[0][0].get_text()
        assert 'Unsaved edit' in dialog.docs[1][0].get_text()
        assert any(pair[2]!='equal' for pair in dialog.pairs)
        return QDialog.DialogCode.Rejected
    with patch.object(QDialog,'exec',review),patch.object(w,'save') as save:
        assert not w.compare_and_save();save.assert_not_called()
    assert w.current.changed and source.read_bytes()==original
    with patch.object(QDialog,'exec',lambda d: QDialog.DialogCode.Accepted),patch.object(QFileDialog,'getSaveFileName',return_value=('', '')):
        assert not w.compare_and_save()
    assert w.current.changed
    with patch.object(QDialog,'exec',lambda d: QDialog.DialogCode.Accepted),patch.object(QFileDialog,'getSaveFileName',return_value=(str(target),'PDF')):
        assert w.compare_and_save()
    assert source.read_bytes()==original
    with pdf.open(target) as d:assert 'Unsaved edit' in d[0].get_text()
    # Missing and externally replaced originals never close the current editing session.
    detach(w.current)
    source.rename(root/'Moved.pdf');errors=[];w.error=lambda e:errors.append(str(e))
    assert not w.compare_and_save() and errors and w.current is not None
    (root/'Moved.pdf').rename(source)
    with pdf.open() as d:d.new_page().insert_text((40,60),'New original');d.save(root/'Replacement.pdf')
    source.write_bytes((root/'Replacement.pdf').read_bytes())
    with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.No),patch.object(QDialog,'exec') as dialog:
        assert not w.compare_and_save();dialog.assert_not_called()
    source.write_bytes(original);w.error=fail
    # Reading places use the saved copy and retain notes without modifying the PDF.
    w.current.page=2
    assert w.save_reading_place('Read this next\nPrivate note')
    marks=w.personal_items('personal/reading');assert len(marks)==1 and marks[0]['path']==str(target.resolve())
    assert marks[0]['page']==2 and marks[0]['note'].endswith('Private note') and 'password' not in marks[0]
    w2=Window();w2.error=fail
    restored=w2.personal_items('personal/reading');assert restored==marks
    opened=w2.open_reading_place(restored[0]);assert opened.page==2
    detach(opened)
    before=target.read_bytes();assert w.save_reading_place('Revised note')
    assert len(w.personal_items('personal/reading'))==1 and target.read_bytes()==before
    target.rename(root/'Missing.pdf')
    try:w2.open_reading_place(marks[0]);raise AssertionError('Missing PDF accepted')
    except ValueError:pass
    assert w2.personal_items('personal/reading')
    (root/'Missing.pdf').rename(target)
    target.write_bytes((root/'Replacement.pdf').read_bytes())
    with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.No):assert w2.open_reading_place(marks[0]) is None
    with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):
        changed=w2.open_reading_place(marks[0]);assert changed.page==0;detach(changed)
    target.write_bytes(before)
    w.changed()
    with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.No),patch.object(w,'save') as save:
        assert not w.save_reading_place('not saved');save.assert_not_called()
    assert w.personal_items('personal/reading')[0]['note']=='Revised note'
    for s in w.sessions:s.changed=False
    # Actual text-template dialog: create, update, persist, select for insertion.
    def templates(dialog):
        fields=dialog.findChildren(QLineEdit);name=fields[0]
        text=dialog.findChild(QPlainTextEdit);combos=dialog.findChildren(QComboBox)
        name.setText('Address');text.setPlainText('Ada Example\nMain Street 1\n12345 City')
        dialog.findChild(QSpinBox).setValue(16)
        button(dialog,w.ui('Vorlage speichern')).click()
        assert len(w.personal_items('personal/snippets'))==1
        text.setPlainText('Ada Example\nSecond Street 2');button(dialog,w.ui('Vorlage speichern')).click()
        assert w.personal_items('personal/snippets')[0]['text'].endswith('Street 2')
        button(dialog,w.ui('Einfügen')).click()
        assert w.mode=='Textbausteine'
        return QDialog.DialogCode.Accepted
    with patch.object(QDialog,'exec',templates):w.text_snippets()
    assert w2.personal_items('personal/snippets')[0]['size']==16
    persisted=subprocess.check_output([sys.executable,'-c',
        'import sys,json;from PySide6.QtCore import QSettings;s=QSettings(sys.argv[1],QSettings.Format.IniFormat);print(json.dumps([json.loads(s.value("personal/snippets","[]")),json.loads(s.value("personal/reading","[]"))]))',str(root/'settings.ini')])
    snippets,places=json.loads(persisted)
    assert snippets[0]['name']=='Address' and places[0]['note']=='Revised note'
    s=w.current;page=s.doc[0];before_text=page.get_text();before_history=len(s.history)
    assert w.put_snippet(page,pdf.Rect(40,150,400,350));w.changed()
    assert 'Second Street 2' in s.doc[0].get_text().replace(chr(160), chr(32))
    w.undo();assert s.doc[0].get_text()==before_text
    w.redo();assert 'Second Street 2' in s.doc[0].get_text().replace(chr(160), chr(32))
    history=len(s.history);content=s.doc[0].get_text()
    try:w.put_snippet(s.doc[0],pdf.Rect(10,10,21,21));raise AssertionError('Overflow accepted')
    except ValueError:pass
    assert len(s.history)==history and s.doc[0].get_text()==content
    # Untrusted snippet input stays literal text, not executable HTML or remote images.
    w.active_snippet=dict(name='literal',text='<img src="https://example.invalid/a"> & safe',font=0,color=0,size=9)
    assert w.put_snippet(s.doc[0],pdf.Rect(30,400,550,500))
    assert '<img' in s.doc[0].get_text().replace(chr(160), chr(32)) and not s.doc[0].get_images()
    close(w);close(w2)
    # Password-protected originals: comparison and saving preserve encryption.
    protected=root/'Protected.pdf';out=root/'Protected-copy.pdf'
    with pdf.open(source) as d:d.save(protected,encryption=pdf.PDF_ENCRYPT_AES_256,user_pw='reader',owner_pw='owner')
    w=Window();w.error=fail
    with patch.object(QInputDialog,'getText',return_value=('owner',True)):w.open_path(str(protected))
    w.current.doc[0].insert_text((40,120),'Protected edit');w.changed()
    with patch.object(QDialog,'exec',lambda d: QDialog.DialogCode.Accepted),patch.object(QFileDialog,'getSaveFileName',return_value=(str(out),'PDF')):
        assert w.compare_and_save()
    with pdf.open(out) as d:assert d.needs_pass and d.authenticate('owner') and 'Protected edit' in d[0].get_text()
    close(w)
    # Every new dialog and snippet content in all 20 languages, including RTL and CJK.
    catalog=json.loads(Path(module.__file__).with_name('ui_languages.json').read_text(encoding='utf-8'))
    labels=['Vergleichen und speichern','Textbausteine','Lesestellen','Lesestelle speichern','Notiz speichern','Originaldatei','Aktuelle Bearbeitung','Farbige Flächen zeigen Unterschiede.','Unterschiede gefunden','Keine sichtbaren Unterschiede','Schwarz','Blau','Rot','Weiter','Schließen']
    for code in UI_LANGUAGES:
        settings=test_settings;settings.setValue('view/language',code);settings.sync()
        w=Window();w.error=fail;w.open_path(str(source))
        for label in labels:
            if code!='de':assert label in catalog[code],(code,label)
        for key,value in catalog.get(code,{}).items():
            fields=lambda t:Counter(field for _,field,_,_ in string.Formatter().parse(t) if field is not None)
            if key in labels or key=='{0} · Seite {1}':assert fields(key)==fields(value),(code,key)
        def inspect(dialog):
            assert dialog.layoutDirection()==(Qt.LayoutDirection.RightToLeft if code in ('ar','he') else Qt.LayoutDirection.LeftToRight)
            assert dialog.windowTitle() in [w.ui('Textbausteine'),w.ui('Lesestellen'),w.ui('PDF-Versionen vergleichen')]
            dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            dialog.show();app.processEvents()
            if code in ('de','ja','ar'):
                images=Path(os.environ.get('PDF_PERSONAL_QA_OUTPUT',str(root)));images.mkdir(parents=True,exist_ok=True)
                kind='snippets' if dialog.windowTitle()==w.ui('Textbausteine') else 'reading' if dialog.windowTitle()==w.ui('Lesestellen') else 'compare'
                image=images/f'{code}-{kind}.png'
                assert dialog.grab().save(str(image))
            dialog.hide()
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog,'exec',inspect):
            w.text_snippets();w.reading_places();assert not w.compare_and_save()
        text=w.ui('Notizen werden lokal gespeichert, nicht in der PDF.')
        w.active_snippet=dict(name='Unicode',text=text+'\n12345',font=0,color=0,size=12)
        assert w.put_snippet(w.current.doc[0],pdf.Rect(30,180,570,500))
        extracted=w.current.doc[0].get_text()
        assert '12345' in extracted and '\ufffd' not in extracted,(code,extracted)
        letters=lambda t:Counter(c for c in unicodedata.normalize('NFKC',t) if c.isalpha())
        assert not (letters(text)-letters(extracted)),(code,text,extracted)
        if code in ('ja','ar'):
            images=Path(os.environ.get('PDF_PERSONAL_QA_OUTPUT',str(root)));images.mkdir(parents=True,exist_ok=True)
            w.current.doc[0].get_pixmap().save(str(images/f'{code}-inserted.png'))
        close(w)
    settings.setValue('view/language','de');settings.sync()
    factory.stop()
print('Original comparison/save/cancel/encryption; persistent snippets, undo/redo/overflow; reading places/missing/changed files; 20 dialogs and Unicode insertion passed.')

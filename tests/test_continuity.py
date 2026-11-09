# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QCheckBox, QMessageBox, QTableWidget, QFileDialog
import pdf_werkbank as appmod
from work_backup import (write_backup, read_backup, restore_backup, backup_preview, backup_check_rows,
                         capture_personal, validate_personal, merge_personal, PERSONAL_KEYS)

app = QApplication.instance() or QApplication([])

def close(window):
    for session in window.sessions: session.changed = False
    window.close()
    for session in window.sessions: session.doc.close()
    window.sessions.clear()

with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    root = Path(folder)
    source = QSettings(str(root/'source.ini'), QSettings.Format.IniFormat)
    target = QSettings(str(root/'target.ini'), QSettings.Format.IniFormat)
    first, second, locked = root/'first.pdf', root/'second.pdf', root/'locked.pdf'
    with pdf.open() as doc:
        for i in range(3): doc.new_page().insert_text((30, 50), f'page {i}')
        doc.save(first)
        doc.save(locked, encryption=pdf.PDF_ENCRYPT_AES_256, user_pw='reader', owner_pw='owner')
    with pdf.open() as doc:
        doc.new_page(width=800, height=400).insert_text((30,50),'another')
        doc.save(second)
    snippet = dict(name='Gruß', text='Grüße\nمرحبا', size=12, font=0, color=1)
    reading = dict(path=str(first),page=2,note='Notiz',digest='a'*64)
    personal = dict(snippets=[snippet], reading=[reading], workspaces={'Mappe':[dict(path=str(first),page=2,active=True)]})
    for key,path in PERSONAL_KEYS.items(): source.setValue(path,json.dumps(personal[key]))
    source.sync()
    backup=root/'test.backup'
    write_backup(backup,source,root/'measurements',password='test-password')
    data=read_backup(backup,password='test-password')
    assert data['personal']==personal
    restore_backup(data,{'personal'},target,root/'restored')
    assert capture_personal(target)==personal
    # No overwrite of local conflicts, no duplicates on repeated restore.
    target.setValue('personal/snippets',json.dumps([{**snippet,'text':'local'}]))
    before=capture_personal(target)
    assert any(row[2]=='Lokaler Eintrag bleibt erhalten' for row in backup_preview(data,target,root))
    restore_backup(data,{'personal'},target,root)
    assert capture_personal(target)==before
    restore_backup(data,{'plans'},target,root)
    assert capture_personal(target)==before
    assert any('first.pdf' in row[1] for row in backup_check_rows(data))
    # Old metadata without personal group remains readable and does not clear data.
    plain=root/'plain.backup'; write_backup(plain,source,root)
    old=root/'old.backup'
    with ZipFile(plain) as z, ZipFile(old,'w',ZIP_DEFLATED) as out:
        for name in z.namelist():
            content=z.read(name)
            if name=='meta.json':
                meta=json.loads(content); meta.pop('personal'); content=json.dumps(meta).encode()
            out.writestr(name,content)
    restore_backup(read_backup(old),{'personal'},target,root)
    assert capture_personal(target)==before
    for bad in ({'snippets':[{**snippet,'font':-1}]}, {'reading':[{**reading,'path':'../secret.pdf'}]},
                {'workspaces':{'bad':[dict(path=str(first),page=True)]}}, {'password':'no'}):
        try: validate_personal(bad)
        except ValueError: pass
        else: raise AssertionError(bad)
    many={'snippets':[{**snippet,'name':str(i)} for i in range(100)]}
    try: merge_personal(many,{'snippets':[snippet]})
    except ValueError: pass
    else: raise AssertionError('overflow accepted')

    def settings(*_): return target
    settings.Status=QSettings.Status
    with patch.object(appmod,'QSettings',settings):
        window=appmod.Window(); window.error=lambda e: (_ for _ in ()).throw(AssertionError(str(e)))
        a=window.open_path(str(first),page=2); b=window.open_path(str(second),page=0)
        window.tabs.setCurrentIndex(0)
        target.setValue('session/resume',True)
        window.remember_session(a)
        saved=json.loads(target.value('session/tabs'))
        assert saved[0]['active'] and saved[0]['page']==2
        assert not any('password' in item for item in saved)
        # A second process proves persistence without sharing Qt's settings cache.
        child = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
import pdf_werkbank as m
app=QApplication([])
settings=QSettings(sys.argv[2],QSettings.Format.IniFormat)
def factory(*args): return settings
factory.Status=QSettings.Status
m.QSettings=factory
w=m.Window(); w.resume_session()
assert len(w.sessions)==2 and w.current.page==2
assert str(w.current.path)==sys.argv[3]
w.close()
print('Fresh process restored saved tabs and active page.')
'''
        result=subprocess.run([sys.executable,'-X','utf8','-c',child,str(Path(appmod.__file__).parent),target.fileName(),str(first)],capture_output=True,timeout=30)
        assert result.returncode==0,(result.stdout,result.stderr)
        reopened=appmod.Window(); reopened.resume_session()
        assert len(reopened.sessions)==2 and reopened.current.page==2
        reopened.resume_session(); assert len(reopened.sessions)==2
        # Closing cancellation must not change the saved session.
        original=target.value('session/tabs')
        a.changed=True
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel):
            event=QCloseEvent(); window.closeEvent(event); assert not event.isAccepted()
        assert target.value('session/tabs')==original
        a.changed=False
        # Saving during normal close remembers the saved copy, not the old source.
        a.doc[0].insert_text((30,90),'saved during close');a.changed=True
        copy=root/'saved-copy.pdf'
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Save), \
                patch.object(QFileDialog,'getSaveFileName',return_value=(str(copy),'')):
            event=QCloseEvent();window.closeEvent(event);assert event.isAccepted()
        assert json.loads(target.value('session/tabs'))[0]['path']==str(copy)
        with pdf.open(copy) as doc:assert 'saved during close' in doc[0].get_text()
        # A missing file is skipped and reported; out-of-range pages are clamped.
        target.setValue('session/tabs',json.dumps([dict(path=str(root/'gone.pdf'),page=0),dict(path=str(first),page=999)]))
        missing=appmod.Window()
        with patch.object(QMessageBox,'information') as info:
            missing.resume_session(); assert info.called
        assert len(missing.sessions)==1 and missing.current.page==2
        # Protected files request their password again.
        target.setValue('session/tabs',json.dumps([dict(path=str(locked),page=1)]))
        protected=appmod.Window()
        with patch.object(appmod.QInputDialog,'getText',return_value=('reader',True)) as ask:
            protected.resume_session(); assert ask.called
        assert protected.current.page==1
        # Disabled means no session read, and disabling through the dialog removes paths.
        def disable(dialog):
            dialog.findChild(QCheckBox).setChecked(False)
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog,'exec',disable):window.session_options()
        assert not target.value('session/resume',True,bool) and not target.contains('session/tabs')
        disabled=appmod.Window(); disabled.resume_session(); assert not disabled.sessions

        compare=appmod.CompareDialog(window,a.doc,b.doc,('A','B'),[(0,0,'replace'),(2,None,'delete')],True)
        compare.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True)
        compare.resize(700,500);compare.show();app.processEvents()
        compare.zoom_control.setValue(200);app.processEvents()
        left,right=[area.verticalScrollBar() for area in compare.areas]
        assert left.maximum()>0 and right.maximum()>0
        left.setValue(left.maximum()//2)
        assert abs(left.value()/left.maximum()-right.value()/right.maximum())<0.02
        right.setValue(right.maximum());assert left.value()==left.maximum()
        compare.sync_scroll.setChecked(False);right.setValue(0);assert left.value()==left.maximum()
        compare.sync_scroll.setChecked(True);assert right.value()==right.maximum()
        compare.pages.setCurrentIndex(1);app.processEvents();assert compare.images[1].text()=='—'
        compare.zoom_control.setValue(50);app.processEvents()
        compare.close()
        # New controls and backup group are translated in all 20 languages, with RTL.
        keys=['Textbausteine, Lesestellen und Arbeitsmappen','Gemeinsam scrollen','Gemeinsamer Zoom',
              'Sitzung fortsetzen','Geöffnete PDFs beim nächsten Start wieder öffnen']
        for lang in appmod.UI_LANGUAGES:
            target.setValue('view/language',lang)
            translated=appmod.Window()
            if lang!='de':assert all(key in translated.ui_words and translated.ui(key)!=key for key in keys)
            def inspect(dialog):
                assert dialog.windowTitle()==translated.ui('Sitzung fortsetzen')
                assert dialog.layoutDirection()==translated.layoutDirection()
                if lang in ('de','ar','ja') and os.environ.get('PDF_CONTINUITY_QA'):
                    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True);dialog.show();app.processEvents()
                    dialog.grab().save(str(Path(os.environ['PDF_CONTINUITY_QA'])/(lang+'-session.png')))
                return QDialog.DialogCode.Rejected
            with patch.object(QDialog,'exec',inspect):translated.session_options()
            before=capture_personal(target)
            def inspect_backup(dialog):
                assert dialog.layoutDirection()==translated.layoutDirection()
                box=next(box for box in dialog.findChildren(QCheckBox)
                         if box.text()==translated.ui(keys[0]))
                table=dialog.findChild(QTableWidget)
                count=table.rowCount();box.setChecked(False);assert table.rowCount()<count
                box.setChecked(True)
                if lang in ('de','ar','ja') and os.environ.get('PDF_CONTINUITY_QA'):
                    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True);dialog.show();app.processEvents()
                    dialog.grab().save(str(Path(os.environ['PDF_CONTINUITY_QA'])/(lang+'-backup.png')))
                return QDialog.DialogCode.Rejected
            with patch.object(QDialog,'exec',inspect_backup):translated.restore_backup_dialog(data,target)
            assert capture_personal(target)==before
            localized=appmod.CompareDialog(translated,a.doc,b.doc,('A','B'),[(0,0,'replace')],True)
            assert localized.sync_scroll.text()==translated.ui('Gemeinsam scrollen')
            assert localized.layoutDirection()==translated.layoutDirection()
            if lang in ('de','ar','ja') and os.environ.get('PDF_CONTINUITY_QA'):
                localized.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True);localized.show();app.processEvents()
                localized.grab().save(str(Path(os.environ['PDF_CONTINUITY_QA'])/(lang+'-compare.png')))
            localized.close()
            close(translated)
        for item in (window,reopened,missing,protected,disabled):close(item)
print('Backup encryption/legacy/conflicts/selection/validation; session resume/cancel/password/missing; synchronized comparison/zoom and 20 languages passed.')

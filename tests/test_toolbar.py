# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt, QModelIndex
from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QLineEdit, QPushButton, QAbstractItemView
import pdf_werkbank as m
from preferences import DEFAULT_FAVORITES, export_package, import_groups, validate_groups
from work_backup import write_backup, read_backup

app=QApplication.instance() or QApplication([])
with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    root=Path(folder)
    settings=QSettings(str(root/'toolbar.ini'),QSettings.Format.IniFormat)
    def factory(*_):return settings
    factory.Status=QSettings.Status
    def button(dialog,label):return next(b for b in dialog.findChildren(QPushButton) if b.text()==label)
    chosen=['tool:Lesestellen','tool:Textbausteine','save']
    calls=[]
    with patch.object(m,'QSettings',factory), patch.object(m.Window,'text_snippets',lambda self:calls.append('snippets')), patch.object(m.Window,'reading_places',lambda self:calls.append('reading')):
        window=m.Window()
        assert window.favorite_keys==DEFAULT_FAVORITES
        assert set(chosen)<=window.favorite_commands.keys()
        assert len(window.favorite_commands)>100
        assert all('tool:'+label in window.favorite_commands or any(callback==run for _,run in window.favorite_commands.values())
                   for _,commands in window.groups for label,callback in commands)
        def edit(dialog):
            rows=dialog.findChild(QListWidget)
            assert rows.dragDropMode()==QAbstractItemView.DragDropMode.InternalMove
            for i in range(rows.count()):
                item=rows.item(i);item.setCheckState(Qt.CheckState.Unchecked)
            # Same model move operation used for internal reordering; identity and check state travel together.
            for pos,key in enumerate(chosen):
                index=next(i for i in range(rows.count()) if rows.item(i).data(Qt.ItemDataRole.UserRole)==key)
                rows.item(index).setCheckState(Qt.CheckState.Checked)
                if index!=pos:assert rows.model().moveRows(QModelIndex(),index,1,QModelIndex(),pos)
            assert [rows.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)]==chosen
            rows.setCurrentRow(0);button(dialog,window.ui('Nach unten')).click()
            assert rows.item(1).data(Qt.ItemDataRole.UserRole)==chosen[0]
            button(dialog,window.ui('Nach oben')).click()
            search=dialog.findChild(QLineEdit);search.setText('Textbausteine')
            assert not rows.item(1).isHidden() and rows.item(0).isHidden()
            # Hidden checked tools must remain selected when saving search results.
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog,'exec',edit):window.customize_toolbar()
        assert window.favorite_keys==chosen
        window.favorite_actions[0].trigger();window.favorite_actions[1].trigger()
        assert calls==['reading','snippets']
        reopened=m.Window();assert reopened.favorite_keys==chosen
        def cancel(dialog):
            button(dialog,window.ui('Standardauswahl')).click()
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog,'exec',cancel):window.customize_toolbar()
        assert window.favorite_keys==chosen and settings.value('toolbar/favorites')==chosen
        package=json.loads(export_package(settings))
        assert package['groups']['favorites']==chosen
        backup=root/'toolbar.backup';write_backup(backup,settings,root/'plans')
        data=read_backup(backup)
        window.save_favorites(DEFAULT_FAVORITES)
        with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Accepted):window.restore_backup_dialog(data,settings)
        assert window.favorite_keys==chosen
        imported=QSettings(str(root/'imported.ini'),QSettings.Format.IniFormat)
        import_groups(imported,{'favorites':chosen});assert imported.value('toolbar/favorites')==chosen
        # Only registered callbacks are shown; imported unknown tool ids never execute anything.
        settings.setValue('toolbar/favorites',chosen+['tool:future-command'])
        future=m.Window();assert future.favorite_keys==chosen
        future.close();window.save_favorites(chosen)
        for invalid in ('tool:', 'tool:\nmalformed','arbitrary-command'):
            try:validate_groups({'favorites':[invalid]})
            except ValueError:pass
            else:raise AssertionError(invalid)
        before=list(window.favorite_keys)
        with patch.object(settings,'status',side_effect=[QSettings.Status.AccessError,QSettings.Status.NoError]):
            try:window.save_favorites(['save'])
            except OSError:pass
            else:raise AssertionError('write failure ignored')
        assert window.favorite_keys==before and settings.value('toolbar/favorites')==before
        try:window.save_favorites(list(window.favorite_commands)[:101])
        except ValueError:pass
        else:raise AssertionError('limit ignored')
        # A separate process reloads order and new tools from the actual INI file.
        child='''import sys
sys.path.insert(0,sys.argv[1])
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
import pdf_werkbank as m
a=QApplication([]);s=QSettings(sys.argv[2],QSettings.Format.IniFormat)
def settings(*args):return s
settings.Status=QSettings.Status;m.QSettings=settings
w=m.Window();assert w.favorite_keys==['tool:Lesestellen','tool:Textbausteine','save'];w.close()
'''
        result=subprocess.run([sys.executable,'-X','utf8','-c',child,str(Path(m.__file__).parent),settings.fileName()],capture_output=True,timeout=30)
        assert result.returncode==0,(result.stdout,result.stderr)
        # Empty toolbars are allowed and can still be edited via the permanent Favorites menu.
        window.save_favorites([]);assert not window.favorite_actions and window.favorite_menu.actions()
        settings.setValue('batch/profiles',json.dumps({'My task':['compress']}))
        window.profile_commands();window.save_favorites(['profile:My task']);assert len(window.favorite_actions)==1
        window.save_favorites(chosen)
        keys=['Werkzeugleiste anpassen','Werkzeuge anhaken und durch Ziehen sortieren. Nur Speichern übernimmt die Auswahl.',
              'Höchstens 100 Werkzeuge auswählen.','Nach oben','Nach unten','Standardauswahl']
        for lang in m.UI_LANGUAGES:
            settings.setValue('view/language',lang)
            localized=m.Window()
            if lang!='de':assert all(k in localized.ui_words and localized.ui(k)!=k for k in keys)
            if lang!='de':assert all(label in localized.ui_words for key,(label,_) in localized.favorite_commands.items() if not key.startswith('profile:'))
            assert localized.favorite_actions[0].text()==localized.ui('Lesestellen')
            def inspect(dialog):
                assert dialog.layoutDirection()==localized.layoutDirection()
                assert dialog.windowTitle()==localized.ui(keys[0])
                if lang in ('de','ar','ja') and os.environ.get('PDF_TOOLBAR_QA'):
                    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True);dialog.show();app.processEvents()
                    dialog.grab().save(str(Path(os.environ['PDF_TOOLBAR_QA'])/(lang+'-toolbar.png')))
                return QDialog.DialogCode.Rejected
            with patch.object(QDialog,'exec',inspect):localized.customize_toolbar()
            localized.close()
        window.close();reopened.close()
print('Custom toolbar: existing favorites, selection/order/filter/cancel/reset, callbacks, persistence in second process, backup/import, failure rollback, limits and 20 languages passed.')

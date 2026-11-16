import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication,QPushButton,QFileDialog
import pdf_werkbank as m
from online_updates import UpdateWindow
app=QApplication.instance() or QApplication([])
out=Path(os.environ['APPDATA'])/'center';out.mkdir(exist_ok=True)
with TemporaryDirectory() as temp:
 for lang in m.UI_LANGUAGES:
  settings=QSettings(str(Path(temp)/(lang+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',lang)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  i=next(i for i,(name,_) in enumerate(w.groups) if name=='Info')
  w.tools.setCurrentIndex(i);w.show();app.processEvents()
  buttons=[w.ribbon.itemAt(n).widget() for n in range(w.ribbon.count()) if isinstance(w.ribbon.itemAt(n).widget(),QPushButton)]
  assert len(buttons)==2
  update=next(b for b in buttons if b.objectName()=='singleUpdateButton');assert update.text()==w.ui('Update')
  menu=next(b for b in buttons if b.objectName()=='infoMenuButton').menu()
  assert len(menu.actions())==2
  assert not any('Update' in name for _,commands in w.groups for name,_ in commands)
  with patch.object(w.release_check,'check') as check:
   update.click();p=w.single_update_window;assert not check.called
   assert p.isVisible() and not p.more.isVisible() and p.versions.text()
   assert p.message.text()==w.ui('Noch nicht geprüft')
   p.check_button.click();check.assert_called_once_with(manual=True)
   assert not p.check_button.isEnabled() and all(not b.isEnabled() for b in p.extra_buttons)
   update.click();assert w.single_update_window is p and p.progress.isVisible()
  w.show_online_update(None,True,True)
  assert p.check_button.isEnabled()
  p.more_button.click();assert p.more.isVisible()
  assert [b.text() for b in p.extra_buttons]==[w.ui(s) for s in ('Update vom USB-Stick','Update für USB speichern','Update-Info','Update-Ordner')]
  with patch.object(QFileDialog,'getOpenFileName',return_value=('','')) as file:
   p.extra_buttons[0].click();file.assert_called_once()
  with patch.object(QFileDialog,'getExistingDirectory',return_value='') as folder:
   p.extra_buttons[1].click();folder.assert_called_once()
  with patch.object(m,'prepared_update',return_value=None):
   p.extra_buttons[2].click();assert p.details.toPlainText() and w.single_update_window is p
  with patch.object(QFileDialog,'getExistingDirectory',return_value='') as folder:
   p.extra_buttons[3].click();folder.assert_called_once()
  called=[];p.display('Confirm',confirm=lambda:called.append(1))
  assert not p.check_button.isEnabled() and all(not b.isEnabled() for b in p.extra_buttons)
  p.action.click();p.action.click();assert called==[1]
  p.display('Working',busy=True,percent=37);w.usb_update_busy=True
  update.click();assert p.progress.value()==37 and p.message.text()=='Working'
  p.close_button.click();update.click();assert p.isVisible() and p.progress.value()==37
  w.usb_update_busy=False;p.display(w.ui('Du hast die aktuelle Version.'))
  assert p.check_button.isEnabled() and all(b.isEnabled() for b in p.extra_buttons)
  assert len(w.findChildren(UpdateWindow))==1 and p.layoutDirection()==w.layoutDirection()
  if lang in ('de','ar','ja'):
   p.grab().save(str(out/(lang+'-expanded.png')))
   p.more_button.setChecked(False);app.processEvents();p.grab().save(str(out/(lang+'-collapsed.png')))
  p.hide();w.close();w.deleteLater();app.processEvents()
print('PASS 20 languages: single entry, collapsed extras, all four extra actions, online search, busy/confirmation locks, progress preserved on reopen, no duplicate window, RTL; no real update or profile changes.')

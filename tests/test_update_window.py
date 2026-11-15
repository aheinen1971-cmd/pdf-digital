import os,json,sys,time
from pathlib import Path
from unittest.mock import patch
from tempfile import TemporaryDirectory
from PySide6.QtCore import QSettings,QProcess,Qt
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtGui import QCloseEvent,QFont,QFontDatabase
import pdf_werkbank as m
from online_updates import UpdateWindow
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
app.setFont(QFont('Segoe UI',10))
catalog=json.loads(Path(m.__file__).with_name('ui_languages.json').read_text(encoding='utf-8'))
out=Path(os.environ['APPDATA'])/'images';out.mkdir(exist_ok=True)
with TemporaryDirectory() as temp:
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(Path(temp)/'settings.ini'),QSettings.Format.IniFormat)
  settings.setValue('view/language',code);settings.sync()
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  with patch.object(QMessageBox,'information',side_effect=AssertionError('extra popup')),patch.object(QMessageBox,'question',side_effect=AssertionError('extra popup')),patch.object(QMessageBox,'critical',side_effect=AssertionError('extra popup')),patch.object(m,'prepared_update',return_value=None):
   with patch.object(w.release_check,'check') as check:
    w.check_online();p=w.single_update_window
    assert p.isVisible() and p.progress.maximum()==0
    w.check_online();assert w.single_update_window is p
   w.show_online_update(None,True,True)
   assert p.message.text()==w.ui('Du hast die aktuelle Version.') and not p.progress.isVisible()
   w.release_check.error_detail='HTTP 503 <b>offline</b>'
   w.show_online_update(None,False,True)
   assert '<b>offline</b>' in p.details.toPlainText()
   # Progress is actual downloaded bytes, never an installation time prediction.
   process=QProcess(w);w.update_process=process;w.update_output=b''
   process.start(sys.executable,['-c','import json; print(json.dumps(dict(phase="download",percent=37)))'])
   assert process.waitForFinished(10000)
   w.read_update_progress();assert p.progress.value()==37
   event=QCloseEvent();w.closeEvent(event);assert not event.isAccepted()
   p.close_button.click();assert not p.isVisible()
   assert w.update_panel() is p and p.isVisible()
   w.finish_online_update(3)
   assert p.message.text()==w.ui('Update abgelehnt. Die Projektsignatur fehlt oder ist ungültig.')
   for result in (0,1,2,4):
    w.update_process=QProcess(w);w.finish_online_update(result)
    assert not p.progress.isVisible() and p.callback is None
   if code=='de':
    # Exercise the real asynchronous process signals without network or packages.
    real_start=QProcess.start
    def fake_start(proc,*args):
     return real_start(proc,sys.executable,['-c','import json,time; print(json.dumps(dict(phase="download",percent=43)),flush=True); time.sleep(.2)'])
    with patch.object(QProcess,'start',fake_start):
     w.show_online_update(('9.0.0','0'*64,10),True,True)
    deadline=time.monotonic()+10
    while w.update_process is not None and time.monotonic()<deadline:
     app.processEvents();time.sleep(.01)
    assert w.update_process is None
    assert p.message.text()==w.ui('Update bereit. Beim nächsten Öffnen startet die neue Version.')
    with patch.object(QProcess,'start',lambda proc,*args:real_start(proc,'Z:/missing-update-python.exe',[])):
     w.show_online_update(('9.0.0','0'*64,10),True,True)
    deadline=time.monotonic()+5
    while w.update_process is not None and time.monotonic()<deadline:
     app.processEvents();time.sleep(.01)
    assert w.update_process is None and not p.progress.isVisible()
   calls=[]
   p.display('Confirm',confirm=lambda:calls.append(1));p.action.click();p.action.click();assert calls==[1]
   # USB preparation uses this same window, with no progress/completion popups.
   with patch.object(m.QFileDialog,'getOpenFileName',return_value=('fake-Windows.zip','')),patch.object(m,'usb_update_info',return_value=('9.0.0',None,None)),patch.object(m,'stage_usb_update',return_value='9.0.0'),patch.object(m,'automatic_backup'):
    w.usb_update_dialog();assert p.callback is not None
    p.action.click();assert '9.0.0' in p.message.text() and not w.usb_update_busy
   with patch.object(m.QFileDialog,'getOpenFileName',return_value=('fake-Windows.zip','')),patch.object(m,'usb_update_info',side_effect=ValueError('invalid signature')):
    w.usb_update_dialog();assert p.details.toPlainText()=='invalid signature'
   # Info and busy re-entry never create another window or reset progress.
   w.update_info();assert w.single_update_window is p
   w.usb_update_busy=True;p.display('Busy',busy=True);w.check_online();assert p.message.text()=='Busy';w.usb_update_busy=False
   p.display(w.ui('Update wird heruntergeladen …'),busy=True,percent=37)
   app.processEvents()
   assert len(w.findChildren(UpdateWindow))==1
   assert p.layoutDirection()==w.layoutDirection()
   if code!='de':
    for key in ['Aktualisierung','Vorbereiten','Im Hintergrund weiter','Signatur wird geprüft …','Update wird heruntergeladen …','Update wird eingerichtet …','Du hast die aktuelle Version.','Die Update-Suche ist fehlgeschlagen.']:
     assert catalog[code][key]!=key,(code,key)
   if code in ('de','ar','ja'):p.grab().save(str(out/(code+'.png')))
   p.hide();w.close();w.deleteLater();app.processEvents()
print('PASS single window: 20 languages/RTL, download progress, failure codes, safe close/reopen, USB confirmation/success/error, info, no extra popups.')

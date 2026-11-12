import json,subprocess,sys,os,queue,threading,time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch,PropertyMock
from types import SimpleNamespace
import pymupdf as pdf
from PySide6 import QtCore
from PySide6.QtCore import QSettings,QObject,Signal,QByteArray
from PySide6.QtGui import QFont,QFontDatabase,QTextCursor
from PySide6.QtWidgets import QApplication,QDialog,QPushButton,QCheckBox,QLabel,QSpinBox,QTableWidget,QPlainTextEdit
import pdf_werkbank as m
import ocr_confidence as c
import ocr_review_state as store
import read_aloud as speech

app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 p=Path('C:/Windows/Fonts')/name
 if p.exists():QFontDatabase.addApplicationFont(str(p))
app.setFont(QFont('Segoe UI',10))
root=Path(m.__file__).parent;output=root/'tests/resume-results';output.mkdir(exist_ok=True)
def button(dialog,window,key):return next(b for b in dialog.findChildren(QPushButton) if b.text()==window.ui(key))
with TemporaryDirectory() as folder,pdf.open() as doc:
 doc.new_page(width=300,height=300).insert_text((20,40),'Review fixture')
 doc.new_page()
 page=doc[0];pix=page.get_pixmap();raw=doc.tobytes(no_new_id=True)
 words=[dict(text='private word',score=20,box=[20,20,70,40]),dict(text='other',score=60,box=[30,90,80,110])]
 fingerprint=store.document_key(doc)
 with pdf.open(stream=raw,filetype='pdf') as reopened:assert store.document_key(reopened)==fingerprint
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):window=m.Window()
  with patch.object(store,'settings',return_value=settings):
   def save(dialog):
    assert store.read()==[]
    dialog.findChild(QCheckBox,'reviewedWord').click()
    button(dialog,window,'Prüfstand speichern').click()
    assert store.read()[0]['reviewed']==[0]
    assert 'private word' not in settings.value(store.KEY) and str(folder) not in settings.value(store.KEY)
    return QDialog.DialogCode.Rejected
   with patch.object(c,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',save):c.show_review(window,page,'eng',root/'tessdata')
   def resume(dialog):
    assert dialog.findChild(QLabel,'reviewCount').text()==window.ui('{0} von {1} geprüft').format(0,2)
    button(dialog,window,'Prüfung fortsetzen').click()
    assert dialog.findChild(QLabel,'reviewCount').text()==window.ui('{0} von {1} geprüft').format(1,2)
    assert dialog.findChild(QCheckBox,'reviewedWord').isChecked()
    dialog.show();app.processEvents()
    if code in ('de','ar','ja'):dialog.grab().save(str(output/('resume-'+code+'.png')))
    return QDialog.DialogCode.Rejected
   with pdf.open(stream=raw,filetype='pdf') as reopened:
    with patch.object(c,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',resume):c.show_review(window,reopened[0],'eng',root/'tessdata')
   def overview(dialog):
    table=dialog.findChild(QTableWidget)
    assert table.item(0,1).text()==window.ui('{0} von {1} geprüft').format(1,2)
    assert table.item(0,2).text()=='1' and table.item(1,1).text()==window.ui('Nicht geprüft')
    dialog.show();app.processEvents()
    if code in ('de','ar','ja'):dialog.grab().save(str(output/('summary-'+code+'.png')))
    return QDialog.DialogCode.Rejected
   with patch.object(QDialog,'exec',overview):assert not store.summary(window,doc)
   with patch.object(type(window),'current',new_callable=PropertyMock,return_value=SimpleNamespace(doc=doc)),patch.object(QDialog,'exec',overview),patch.object(m.QFileDialog,'getSaveFileName') as target:
    assert not window.save();target.assert_not_called()
   with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Accepted):assert store.summary(window,doc)
   def mismatch(dialog):
    button(dialog,window,'Prüfung fortsetzen').click()
    assert dialog.findChild(QLabel,'reviewCount').text()==window.ui('{0} von {1} geprüft').format(0,2)
    assert any(l.text()==window.ui('Kein passender Prüfstand. Dokument oder Erkennung können verändert sein.') for l in dialog.findChildren(QLabel))
    return QDialog.DialogCode.Rejected
   altered=[dict(w) for w in words];altered[0]['text']='changed'
   with patch.object(c,'recognize',return_value=(altered,pix)),patch.object(QDialog,'exec',mismatch):c.show_review(window,page,'eng',root/'tessdata')
   with pdf.open(stream=raw,filetype='pdf') as changed:
    changed[0].set_rotation(90)
    with patch.object(c,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',mismatch):c.show_review(window,changed[0],'eng',root/'tessdata')
    window._ocr_review_memory={}
    def changed_summary(dialog):
     assert dialog.findChild(QTableWidget).item(0,1).text()==window.ui('Nicht geprüft')
     return QDialog.DialogCode.Rejected
    with patch.object(QDialog,'exec',changed_summary):assert not store.summary(window,changed)
   checkpoint=store.read()[0]
   store.write(checkpoint,remove=True);assert store.read()==[]
   settings.setValue(store.KEY,'invalid')
   with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Accepted):assert store.summary(window,doc)
   try:store.read()
   except ValueError:pass
   else:raise AssertionError('Corrupt state accepted')
   settings.setValue(store.KEY,'[]')
   for bad in (-1,2,True):
    invalid=dict(checkpoint,reviewed=[bad]);settings.setValue(store.KEY,json.dumps([invalid]))
    try:store.read()
    except ValueError:pass
    else:raise AssertionError('Invalid index accepted')
   settings.setValue(store.KEY,'[]')
  window.close();window.deleteLater();app.processEvents()
 assert doc.tobytes(no_new_id=True)==raw
 # Independent process can read the saved checkpoint without document text/path.
 settings=QSettings(str(Path(folder)/'persist.ini'),QSettings.Format.IniFormat)
 with patch.object(store,'settings',return_value=settings):store.write(checkpoint)
 code='import sys;sys.path.insert(0,'+repr(str(root))+');import ocr_review_state as s;from PySide6.QtCore import QSettings;s.settings=lambda:QSettings('+repr(settings.fileName())+',QSettings.Format.IniFormat);assert s.read()[0]["reviewed"]==[0]'
 subprocess.run([sys.executable,'-c',code],check=True)
 class Broken:
  Status=QSettings.Status
  raw='[]'
  def value(self,*args):return self.raw
  def setValue(self,key,value):self.raw=value
  def sync(self):pass
  def status(self):return self.Status.AccessError
 broken=Broken()
 with patch.object(store,'settings',return_value=broken):
  try:store.write(checkpoint)
  except OSError:pass
  else:raise AssertionError('Write failure ignored')
  assert broken.raw=='[]'

Real=QtCore.QProcess
class Fake(QObject):
 readyReadStandardOutput=Signal();errorOccurred=Signal(int);finished=Signal(int,int)
 ProcessState=Real.ProcessState;ProcessChannelMode=Real.ProcessChannelMode
 latest=None
 def __init__(self,*args):super().__init__(*args);Fake.latest=self;self.pending=[];self.running=False;self.commands=[]
 def setProcessChannelMode(self,*args):pass
 def state(self):return self.ProcessState.Running if self.running else self.ProcessState.NotRunning
 def reply(self,**v):self.pending.append(json.dumps(v).encode());self.readyReadStandardOutput.emit()
 def start(self,*args):self.running=True;self.reply(voices=[dict(name='fixture',culture='en-US')],state='Ready')
 def write(self,raw):
  d=json.loads(bytes(raw));self.commands.append(d)
  if d['action']=='quit':self.running=False
  self.reply(state='Speaking' if d['action']=='start' else 'Ready');return len(raw)
 def canReadLine(self):return bool(self.pending)
 def readLine(self):return QByteArray(self.pending.pop(0))
 def closeWriteChannel(self):pass
 def waitForFinished(self,*args):return not self.running
 def kill(self):self.running=False
with TemporaryDirectory() as folder:
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):window=m.Window()
  def inspect(dialog):
   view=dialog.findChild(QPlainTextEdit);cursor=view.textCursor();cursor.setPosition(3);cursor.setPosition(8,QTextCursor.MoveMode.KeepAnchor);view.setTextCursor(cursor)
   button(dialog,window,'Vorlesen').click();p=Fake.latest;generation=p.commands[-1]['id']
   p.reply(state='Speaking',id=generation,position=0,length=5)
   marks=view.extraSelections();assert marks[0].cursor.selectedText()=='Hallo'
   dialog.show();app.processEvents()
   if code in ('de','ar','ja'):dialog.grab().save(str(output/('highlight-'+code+'.png')))
   p.reply(state='Speaking',id=generation-1,position=0,length=1)
   marks=view.extraSelections();assert marks[0].cursor.selectedText()=='Hallo'
   p.reply(state='Ready',id=generation-1)
   assert view.extraSelections()
   p.reply(state='Paused',id=generation,position=0,length=5);assert view.extraSelections()
   p.reply(state='Ready');assert not view.extraSelections()
   button(dialog,window,'Vorlesen').click();assert p.commands[-1]['text']=='😀 Hallo العربية עברית 日本語'
   return QDialog.DialogCode.Rejected
  with patch.object(QtCore,'QProcess',Fake),patch.object(QDialog,'exec',inspect):speech.show_reader(window,'😀 Hallo العربية עברית 日本語')
  window.close();window.deleteLater();app.processEvents()
print('PASS: persisted/reopened/cross-process review, document and OCR mismatch, corrupt state, no text/path storage, 20 UI summaries/cancel, speech UTF16 offsets/stale events/pause/stop/restart')


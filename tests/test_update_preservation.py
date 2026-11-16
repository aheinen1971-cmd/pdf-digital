import json, os, subprocess, sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED
import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QSpinBox
from PySide6.QtTest import QTest
import pdf_werkbank as m
import work_backup as b

app=QApplication.instance() or QApplication([])
out=Path(os.environ['APPDATA'])/'views';out.mkdir(exist_ok=True)
class Device:
 def printerName(self):return 'Test printer'
 def supportedDuplexModes(self):return [QPrinter.DuplexMode.DuplexNone]

with TemporaryDirectory() as temp:
 root=Path(temp);plans=root/'plans';plans.mkdir()
 source=root/'fixture.pdf'
 with pdf.open() as doc:
  doc.new_page().insert_text((50,50),'Synthetic fixture');doc.save(source)
 for lang in m.UI_LANGUAGES:
  path=root/(lang+'.ini');settings=QSettings(str(path),QSettings.Format.IniFormat)
  values={'view/language':lang,'toolbar/favorites':['open','save','tool:Datei/Drucken'],
   'print/preset/'+'a'*64:json.dumps({'version':1,'copies':3}),
   'print/named/'+'a'*64:json.dumps({'Prüfung 日本語':{'version':1,'copies':2}}),
   'personal/reading':json.dumps([{'path':str(source),'page':0,'note':'Lesestelle 🌹','digest':'a'*64}])}
  for key,value in values.items():settings.setValue(key,value)
  settings.sync()
  backup=b.automatic_backup(settings,plans,'1.78.0','1.79.0',root=root/lang,refresh=True)
  # A separate process sees the same persisted preferences after application restart.
  code='from PySide6.QtCore import QSettings;import sys,json;s=QSettings(sys.argv[1],QSettings.Format.IniFormat);print(json.dumps({k:s.value(k) for k in s.allKeys()},ensure_ascii=True))'
  observed=json.loads(subprocess.check_output([sys.executable,'-c',code,str(path)],text=True))
  assert all(observed[k]==v for k,v in values.items()),lang
  restored=QSettings(str(root/(lang+'-restored.ini')),QSettings.Format.IniFormat)
  b.restore_backup(b.read_backup(backup),{'settings','personal'},restored,plans)
  for key,value in values.items():
   actual=restored.value(key)
   assert (json.loads(actual)==json.loads(value) if key=='personal/reading' else actual==value),(lang,key,actual)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  for key in ('Häufige Aufgaben','Eine weitere PDF an das geöffnete Dokument anhängen.','Sprache, Werkzeugleiste, Druckvorlagen und Lesestellen werden vor dem Versionswechsel gesichert.'):
   if lang!='de':assert key in w.ui_words and w.ui(key)!=key,(lang,key)
  # Backup failure prevents download/preparation and leaves settings intact.
  with patch.object(m,'prepared_update',return_value=None),patch.object(m,'automatic_backup',side_effect=OSError('fixture backup failed')),patch.object(m.QProcess,'start') as start:
   w.show_online_update(('1.79.0','a'*64,100),True,True)
   start.assert_not_called();assert 'fixture backup failed' in w.single_update_window.details.toPlainText()
  w.single_update_window.hide()
  # Every shortcut delegates exactly once; cancelling file selection does nothing.
  for index,name in enumerate(('append','extract','ocr','print_dialog')):
   with patch.object(w,name) as action,patch.object(w,'open_files') as choose:
    def select(d):d.findChild(QPushButton,'commonTask'+str(index)).click();return QDialog.DialogCode.Accepted
    with patch.object(QDialog,'exec',select):w.common_tasks()
    choose.assert_called_once();action.assert_not_called()
  w.open_path(str(source))
  for index,name in enumerate(('append','extract','ocr','print_dialog')):
   with patch.object(w,name) as action:
    def select(d):
     d.show();app.processEvents()
     if index==0 and lang in ('de','ar','ja'):d.grab().save(str(out/(lang+'-tasks.png')))
     d.findChild(QPushButton,'commonTask'+str(index)).click();return QDialog.DialogCode.Accepted
    with patch.object(QDialog,'exec',select):w.common_tasks()
    action.assert_called_once()
  printer=QPrinter();printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
  sends=[]
  def submit(p,indices):
   sends.append((p.copyCount(),list(indices)))
   if len(sends)==1:raise OSError('Synthetic spooler error <b>plain</b>')
  def inspect(d):
   d.show();app.processEvents();copies=d.findChild(QSpinBox,'printCopies');copies.setValue(3)
   button=d.findChild(QPushButton,'printSubmit');button.click()
   assert d.result()!=QDialog.DialogCode.Accepted and copies.value()==3 and not button.isEnabled()
   failure=d.findChild(QLabel,'printFailure');assert 'Synthetic spooler error' in failure.text() and failure.textFormat()==Qt.TextFormat.PlainText
   for _ in range(40):
    if button.isEnabled():break
    QTest.qWait(50)
   assert 'Synthetic spooler error' in failure.text() and button.isEnabled()
   if lang in ('de','ar','ja'):d.grab().save(str(out/(lang+'-print.png')))
   button.click();assert d.result()==QDialog.DialogCode.Accepted
   return d.result()
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',inspect):
   assert w.print_preview_setup(w.current.doc,printer,[Device()],submit=submit)
  assert sends==[(3,[0]),(3,[0])]
  w.current.doc.close();w.close()
 # Older backups remain readable and do not reset an existing language/preset.
 old=root/'old.zip'
 with ZipFile(backup) as z,ZipFile(old,'w',ZIP_DEFLATED) as target:
  for name in z.namelist():
   raw=z.read(name)
   if name=='meta.json':
    data=json.loads(raw);data.pop('update_preferences');raw=json.dumps(data).encode()
   target.writestr(name,raw)
 assert b.read_backup(old)['update_preferences']=={}
 for bad in ({'updates':'https://example.invalid'},{'view/language':'invalid'},{'print/preset/'+'a'*64:'[]'},{'view/language':True}):
  try:b.validate_update_preferences(bad)
  except ValueError:pass
  else:raise AssertionError(bad)
print('PASS 20 languages: restart persistence, backup/restore, old format, invalid input, backup failure blocks update, four shortcuts/cancel, inline print failure/retry retains selection; no physical print')

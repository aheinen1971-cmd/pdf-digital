import os,json,hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtPrintSupport import QPrinter,QPrinterInfo
from PySide6.QtWidgets import QApplication,QDialog,QDialogButtonBox,QSpinBox,QPushButton,QLabel,QInputDialog,QFileDialog
from PySide6.QtTest import QTest
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
out=Path(os.environ['APPDATA'])/'views';out.mkdir(exist_ok=True)
class Device:
 def printerName(self):return 'Guard test'
 def supportedDuplexModes(self):return [QPrinter.DuplexMode.DuplexNone]
class Printer(QPrinter):
 def __init__(self):super().__init__();self.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
 def printerName(self):return 'Guard test'
with TemporaryDirectory() as temp:
 root=Path(temp);source=root/'source.pdf'
 with pdf.open() as doc:
  for i in range(3):doc.new_page().insert_text((40,60),str(i+1))
  doc.save(source)
 for lang in m.UI_LANGUAGES:
  settings=QSettings(str(root/(lang+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',lang)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  w.open_path(str(source));w.show();app.processEvents()
  extracted=root/(lang+'-extract.pdf')
  with patch.object(QInputDialog,'getText',return_value=('3,1',True)),patch.object(w,'output_path',return_value=extracted):w.extract()
  assert w.ui_format('Seiten entnommen: {0}',2) in w.result_label.text()
  saved=root/(lang+'-copy.pdf')
  with patch.object(QFileDialog,'getSaveFileName',return_value=(str(saved),'')):assert w.save()
  assert w.ui_format('PDF-Kopie gespeichert · Seiten: {0}',3) in w.result_label.text()
  with patch.object(QFileDialog,'getSaveFileName',return_value=('','')):assert not w.save()
  assert w.result_path==saved
  if lang in ('de','ar','ja'):w.grab().save(str(out/(lang+'-summary.png')))
  calls=[];active=[]
  def send(p,indices):
   calls.append(list(indices));d=active[0];buttons=d.findChild(QDialogButtonBox)
   assert not d.findChild(QPushButton,'printSubmit').isEnabled()
   # Nested signals and a timer-triggered re-render while the backend is busy.
   buttons.accepted.emit()
   d.findChild(QSpinBox,'printCopies').setValue(2);QTest.qWait(100)
   assert not d.findChild(QPushButton,'printSubmit').isEnabled()
   buttons.accepted.emit()
  def inspect(d):
   active[:]=[d];buttons=d.findChild(QDialogButtonBox)
   buttons.accepted.emit();buttons.accepted.emit()
   assert d.result()==QDialog.DialogCode.Accepted
   assert not d.findChild(QPushButton,'printSubmit').isEnabled()
   assert d.findChild(QPushButton,'printSubmit').text()==w.ui('Druckauftrag übergeben.')
   return d.result()
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',inspect):
   assert w.print_preview_setup(w.current.doc,Printer(),[Device()],submit=send)
  assert calls==[[0,1,2]],(lang,calls)
  attempts=[]
  def fail_once(p,indices):
   attempts.append(list(indices))
   if len(attempts)==1:raise OSError('partial output possible')
  def failed_double(d):
   buttons=d.findChild(QDialogButtonBox);buttons.accepted.emit();buttons.accepted.emit()
   assert len(attempts)==1 and not d.findChild(QPushButton,'printSubmit').isEnabled()
   QTest.qWait(max(400,app.doubleClickInterval())+100)
   assert d.findChild(QPushButton,'printSubmit').isEnabled()
   buttons.accepted.emit();assert len(attempts)==2
   return d.result()
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',failed_double):
   assert w.print_preview_setup(w.current.doc,Printer(),[Device()],submit=fail_once)
  # A second request to open a print window during one active request is ignored.
  w.print_dialog_active=True
  with patch.object(w,'print_preview_setup') as setup:w.print_dialog();setup.assert_not_called()
  w.print_dialog_active=False
  stem=hashlib.sha256(b'Guard test').hexdigest();recent='print/recent/'+stem;default='print/preset/'+stem;named='print/named/'+stem
  settings.setValue(named,json.dumps({'Keep':{'version':1,'copies':7}}))
  settings.setValue('print/recent/'+'b'*64,json.dumps({'version':1,'copies':8}))
  for with_default in (True,False):
   settings.setValue(recent,json.dumps({'version':1,'copies':3}))
   if with_default:settings.setValue(default,json.dumps({'version':1,'copies':2}))
   else:settings.remove(default)
   settings.sync()
   before_named=settings.value(named)
   def reset(d):
    d.show();app.processEvents()
    assert d.findChild(QSpinBox,'printCopies').value()==3
    d.findChild(QSpinBox,'printFrom').setValue(2)
    d.findChild(QSpinBox,'printTo').setValue(2)
    d.findChild(QPushButton,'printResetRemembered').click()
    assert d.findChild(QSpinBox,'printCopies').value()==(2 if with_default else 1)
    assert d.findChild(QSpinBox,'printFrom').value()==2 and d.findChild(QSpinBox,'printTo').value()==2
    assert not settings.contains(recent) and settings.value(named)==before_named
    assert settings.contains('print/recent/'+'b'*64)
    assert w.ui('Gemerkte Werte zurückgesetzt. Standardvorgaben geladen; Seitenauswahl und benannte Vorlagen bleiben erhalten.')==d.findChild(QLabel,'printSavedNotice').text()
    if with_default and lang in ('de','ar','ja'):
     d.findChild(QPushButton,'printResetRemembered').setFocus()
     for area in d.findChildren(m.QScrollArea):
      if area.isAncestorOf(d.findChild(QPushButton,'printResetRemembered')):area.ensureWidgetVisible(d.findChild(QLabel,'printSavedNotice'))
     app.processEvents();d.grab().save(str(out/(lang+'-reset.png')))
    return QDialog.DialogCode.Rejected
   with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',reset),patch.object(m,'print_pages',side_effect=AssertionError('No physical printing')):
    assert w.print_preview_setup(w.current.doc,Printer(),[Device()]) is None
  for s in w.sessions:s.changed=False;s.doc.close()
  w.sessions.clear();w.close()
print('PASS 20 languages: duplicate/reentrant/busy/success signals submit exactly once; window guard; saved result counts/cancel; reset remembered only, default/driver fallback, page range/named/other printer preserved; no physical jobs')

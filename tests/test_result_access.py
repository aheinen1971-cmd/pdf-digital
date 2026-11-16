import os,json,hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtPrintSupport import QPrinter,QPrinterInfo
from PySide6.QtWidgets import QApplication,QDialog,QPushButton,QSpinBox,QLabel,QFileDialog,QInputDialog
from PySide6.QtTest import QTest
import pdf_werkbank as m
import work_backup as backup
app=QApplication.instance() or QApplication([])
out=Path(os.environ['APPDATA'])/'views';out.mkdir(exist_ok=True)
class Device:
 def __init__(self,name='Fixture printer'):self.name=name
 def printerName(self):return self.name
 def supportedDuplexModes(self):return [QPrinter.DuplexMode.DuplexNone]
class Printer(QPrinter):
 def __init__(self):
  super().__init__();self.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
 def printerName(self):return 'Fixture printer'
with TemporaryDirectory() as temp:
 root=Path(temp);source=root/'source.pdf';addition=root/'additional.pdf'
 for path,text in ((source,'Original'),(addition,'Appended')):
  with pdf.open() as doc:doc.new_page().insert_text((50,60),text);doc.save(path)
 original=source.read_bytes()
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(root/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  w.show();app.processEvents()
  assert not w.result_bar.isVisible()
  # Toolbar entry survives favorite changes and does not change their stored order.
  before=list(w.favorite_keys)
  with patch.object(m.QDialog,'exec',return_value=QDialog.DialogCode.Rejected) as opened:
   w.common_tasks_action.trigger();opened.assert_called_once()
  w.refresh_favorites();assert w.common_tasks_action in w.favorite_toolbar.actions() and w.favorite_keys==before
  for key in ('Ergebnis öffnen','Ordner anzeigen','Ordner konnte nicht geöffnet werden.','Zuletzt verwendete Druckeinstellungen'):
   if code!='de':assert w.ui(key)!=key and key in w.ui_words,(code,key)
  w.open_path(str(source))
  target=root/(code+'-extract.pdf')
  with patch.object(QInputDialog,'getText',return_value=('1',True)),patch.object(w,'output_path',return_value=target):w.extract()
  assert target.is_file() and w.result_path==target and w.result_bar.isVisible()
  assert len(w.sessions)==1 # No unsolicited switch or second tab.
  with patch.object(m.QDesktopServices,'openUrl',return_value=True) as folder:
   w.result_folder.click();assert Path(folder.call_args.args[0].toLocalFile())==root
  with patch.object(m.QDesktopServices,'openUrl',return_value=False),patch.object(w,'error') as error:
   w.result_folder.click();error.assert_called_once()
  w.result_open.click();assert len(w.sessions)==2 and w.current.path==target
  # Cancellation and failed output never advertise a new result.
  with patch.object(QInputDialog,'getText',return_value=('',False)):w.extract()
  assert w.result_path==target
  with patch.object(QInputDialog,'getText',return_value=('1',True)),patch.object(w,'output_path',return_value=root/'failed.pdf'),patch.object(m,'save_copy',side_effect=OSError('disk full')),patch.object(w,'error') as error:
   w.extract();error.assert_called_once()
  assert w.result_path==target
  # Real append and save: result is a new PDF; source unchanged.
  with patch.object(QFileDialog,'getOpenFileName',return_value=(str(addition),'')):w.append()
  saved=root/(code+'-combined.pdf')
  with patch.object(QFileDialog,'getSaveFileName',return_value=(str(saved),'')):assert w.save()
  assert w.result_path==saved
  w.result_open.click();assert len(w.current.doc)==2
  assert source.read_bytes()==original
  if code in ('de','ar','ja'):w.grab().save(str(out/(code+'-result.png')))
  # Deleted output is reported, never opened and never guessed.
  w.offer_saved_result(root/'missing.pdf')
  with patch.object(w,'open_path') as opened,patch.object(w,'error') as error:
   w.result_open.click();opened.assert_not_called();error.assert_called_once()
  w.result_bar.hide()
  # A successful print remembers driver settings but never ranges/excluded pages.
  sent=[]
  def submit(p,indices):sent.append(list(indices))
  def initial(d):
   d.findChild(QSpinBox,'printCopies').setValue(3)
   d.findChild(QSpinBox,'printFrom').setValue(2)
   d.findChild(QPushButton,'printSubmit').click();return d.result()
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',initial):
   assert w.print_preview_setup(w.current.doc,Printer(),[Device()],submit=submit)
  assert sent==[[1]] and settings.value('print/last-printer')=='Fixture printer'
  key='print/recent/'+hashlib.sha256(b'Fixture printer').hexdigest()
  recent=json.loads(settings.value(key));assert recent['copies']==3 and 'first' not in recent
  def reopened(d):
   assert d.findChild(QSpinBox,'printCopies').value()==3
   assert d.findChild(QSpinBox,'printFrom').value()==1
   assert d.findChild(QSpinBox,'printTo').value()==2
   return QDialog.DialogCode.Rejected
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',reopened):
   assert w.print_preview_setup(w.current.doc,Printer(),[Device()],submit=submit) is None
  assert sent==[[1]]
  prefs=backup.capture_update_preferences(settings);assert prefs[key]==settings.value(key) and prefs['print/last-printer']=='Fixture printer'
  settings.setValue(key.replace('/recent/','/preset/'),json.dumps(recent))
  settings.setValue(key,'broken')
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',reopened):
   assert w.print_preview_setup(w.current.doc,Printer(),[Device()]) is None
  settings.setValue(key,json.dumps(recent))
  # Last printer wins when available; a missing last printer falls back safely.
  for available in (True,False):
   settings.setValue('print/last-printer','Fixture printer' if available else 'Disconnected')
   with patch.object(m.QPrinterInfo,'availablePrinters',return_value=[Device()]),patch.object(m,'QPrinter',wraps=QPrinter) as factory,patch.object(w,'print_preview_setup',return_value=None):
    # Avoid constructing a native QPrinter from a synthetic QPrinterInfo object.
    factory.side_effect=lambda *args:Printer()
    w.print_dialog()
    assert any(args.args and isinstance(args.args[0],Device) for args in factory.call_args_list)==available
  for session in w.sessions:session.changed=False;session.doc.close()
  w.sessions.clear();w.close()
print('PASS 20 languages: real extract/append/save/open, folders/error/cancel/missing output, original preserved, toolbar access, print memory/no stale range/cancel, disconnected printer and backup integration; no physical printing')

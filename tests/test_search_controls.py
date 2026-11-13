import json,time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings,Qt,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QComboBox,QSpinBox,QCheckBox,QPushButton,QLineEdit,QLabel,QListWidget,QTableWidget,QProgressDialog
from PySide6.QtGui import QFontDatabase,QFont
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 p=Path('C:/Windows/Fonts')/name
 if p.exists():QFontDatabase.addApplicationFont(str(p))
app.setFont(QFont('Segoe UI',10))
out=Path(__file__).parent/'search-controls-results';out.mkdir(exist_ok=True)
def fixture(count):
 d=pdf.open()
 with pdf.open() as source:
  p=source.new_page();p.insert_text((30,50),'Needle needles NEEDLE\nneedle\nStraße STRASSE',render_mode=3)
  # OCR correction lines require a scan image beneath invisible text.
  with pdf.open() as raster:
   raster.new_page().insert_text((30,50),'Scan');p.insert_image(p.rect,pixmap=raster[0].get_pixmap(),overlay=False)
  for _ in range(count):d.insert_pdf(source)
 return d
def wait(d,w):
 until=time.monotonic()+40
 while d.findChild(QLabel,'ocrSearchStatus').text()==w.ui('Suche läuft …'):
  app.processEvents();QTest.qWait(1);assert time.monotonic()<until
with TemporaryDirectory() as tmp,fixture(12) as doc:
 before=doc.tobytes(no_new_id=True)
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(Path(tmp)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  def run(d):
   find=lambda cls,name:d.findChild(cls,name)
   search=find(QLineEdit,'ocrLineSearch');scope=find(QComboBox,'ocrSearchScope');whole=find(QCheckBox,'ocrSearchWhole');case=find(QCheckBox,'ocrSearchCase')
   preview=find(QListWidget,'ocrSearchResults');status=find(QLabel,'ocrSearchStatus');rows=d.findChild(QTableWidget)
   for key in ('Ganze Wörter','Suche abbrechen','Vorschau: {0} von {1} passenden Zeilen','Nach aktueller Seite abbrechen'):
    assert code=='de' or w.ui(key)!=key
   search.setText('needle');wait(d,w);assert preview.count()==24
   scope.setCurrentIndex(1);wait(d,w);assert preview.count()==2
   case.setChecked(True);search.setText('Needle');wait(d,w);assert preview.count()==1
   search.setText('need');whole.setChecked(True);wait(d,w);assert preview.count()==0
   case.setChecked(False);search.setText('STRASSE');wait(d,w);assert preview.count()==1
   scope.setCurrentIndex(2);find(QSpinBox,'ocrSearchFrom').setValue(3);find(QSpinBox,'ocrSearchTo').setValue(5);wait(d,w)
   assert preview.count()==3;assert preview.item(0).data(Qt.ItemDataRole.UserRole)[0]==2
   preview.itemClicked.emit(preview.item(2));assert '5' in status.text()
   rows.item(0,0).setText('Draft UNIQUE');search.setText('UNIQUE');wait(d,w);assert preview.count()==1
   scope.setCurrentIndex(0);search.setText('needle');search.returnPressed.emit()
   find(QPushButton,'ocrSearchStop').click();assert status.text()==w.ui('Suche abgebrochen')
   QTest.qWait(300);assert preview.count()==0;assert not find(QPushButton,'ocrSearchNext').isEnabled()
   search.setText('UNIQUE');wait(d,w);assert preview.count()==1,'Draft lost on cancellation'
   d.show();app.processEvents()
   if code in ('de','ar','ja'):d.grab().save(str(out/(code+'.png')))
   d.reject();return QDialog.DialogCode.Rejected
  with patch.object(QDialog,'exec',run):assert w.review_ocr(doc,0)=={}
  assert doc.tobytes(no_new_id=True)==before
  # Cancellation after a completed page preserves that work, leaves remaining pages
  # untouched and never overwrites the original file. Undo remains available.
  path=Path(tmp)/('cancel-'+code+'.pdf')
  with pdf.open() as blank:
   for _ in range(3):blank.new_page()
   blank.save(path)
  original_bytes=path.read_bytes();session=w.open_path(str(path))
  def recognize(page,*_):page.insert_text((30,50),'Completed OCR')
  with patch.object(w,'ask_ocr_language',return_value=('eng',True)),patch.object(m,'recognize_page',side_effect=recognize),patch.object(QProgressDialog,'wasCanceled',side_effect=[False,True]),patch.object(w,'show_ocr_report') as report:
   w.ocr(True)
  assert [r[1] for r in report.call_args.args[1]]==['added','cancelled','cancelled']
  assert 'Completed OCR' in session.doc[0].get_text() and not session.doc[1].get_text()
  assert path.read_bytes()==original_bytes
  w.undo();assert not session.doc[0].get_text()
  session.doc.close();w.sessions.clear()
  w.deleteLater();app.processEvents()
 metrics=[]
 for count in (500,1500):
  with fixture(count) as large:
   settings=QSettings(str(Path(tmp)/'large.ini'),QSettings.Format.IniFormat)
   with patch.object(m,'QSettings',return_value=settings):w=m.Window()
   def run_large(d):
    search=d.findChild(QLineEdit,'ocrLineSearch');preview=d.findChild(QListWidget,'ocrSearchResults');stop=d.findChild(QPushButton,'ocrSearchStop')
    original=m.ocr_lines
    with patch.object(m,'ocr_lines',wraps=original) as extract:
     search.setText('needle');search.returnPressed.emit();calls=extract.call_count;stop.click();QTest.qWait(50)
     assert extract.call_count==calls and calls<count,'Cancelled search kept reading'
     start=time.monotonic();search.setText('STRASSE');wait(d,w)
     assert preview.count()==200,'Preview must be bounded'
     assert d.findChild(QLabel,'ocrSearchPreviewNote').text()==w.ui('Vorschau: {0} von {1} passenden Zeilen').format(200,count)
     assert d.findChild(QLabel,'ocrSearchProgress').text()==w.ui('Seite {0} von {1}').format(count,count)
     metrics.append(dict(pages=count,seconds=round(time.monotonic()-start,3),previewRows=200,cancelledAfterPages=calls))
    return QDialog.DialogCode.Rejected
   with patch.object(QDialog,'exec',run_large):w.review_ocr(large,0)
   w.deleteLater();app.processEvents()
 (out/'large.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
print('PASS: 20 languages, scope/range/case/whole words/Unicode/previews/drafts/cancel/restart/unchanged PDF and 500/1500 pages')

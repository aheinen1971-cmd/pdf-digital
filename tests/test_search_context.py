import json,time,sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch,MagicMock
import pymupdf as pdf
from PySide6.QtCore import QSettings,Qt
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QLineEdit,QLabel,QListWidget,QPushButton,QCheckBox,QSpinBox,QPlainTextEdit,QTableWidget
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 p=Path('C:/Windows/Fonts')/name
 if p.exists():QFontDatabase.addApplicationFont(str(p))
app.setFont(QFont('Segoe UI',10));out=Path(__file__).parent/'search-context-results';out.mkdir(exist_ok=True)
def fixture(count):
 d=pdf.open()
 with pdf.open() as seed,pdf.open() as raster:
  raster.new_page().insert_text((30,50),'Scan');p=seed.new_page();p.insert_image(p.rect,pixmap=raster[0].get_pixmap())
  for y,t in [(50,'before'),(80,'needle'),(110,'after')]:p.insert_text((30,y),t,render_mode=3)
  for _ in range(count):d.insert_pdf(seed)
 return d
def wait(d,w):
 until=time.monotonic()+45
 while d.findChild(QLabel,'ocrSearchStatus').text()==w.ui('Suche läuft …'):
  app.processEvents();QTest.qWait(1);assert time.monotonic()<until
with TemporaryDirectory() as tmp,fixture(205) as doc:
 original=doc.tobytes(no_new_id=True)
 for code in (('de','ar','ja') if '--visual-only' in sys.argv else m.UI_LANGUAGES):
  settings=QSettings(str(Path(tmp)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  def inspect(d):
   find=lambda c,n:d.findChild(c,n)
   search=find(QLineEdit,'ocrLineSearch');listing=find(QListWidget,'ocrSearchResults');context=find(QCheckBox,'ocrPreviewContext');back=find(QPushButton,'ocrLocationBack');copy=find(QPushButton,'ocrResultsCopy');full=find(QPlainTextEdit,'ocrPreviewFullText');table=d.findChild(QTableWidget);page=d.findChild(QSpinBox)
   assert not context.isEnabled() and not back.isEnabled() and not copy.isEnabled()
   draft='needle <tag> & العربية 日本語';table.item(1,0).setText(draft)
   search.setText('needle');wait(d,w);context.setChecked(True)
   assert full.toPlainText()=='before\n'+draft+'\nafter' and full.isReadOnly() and not full.isHidden()
   listing.clearSelection();listing.item(0).setSelected(True);listing.item(2).setSelected(True)
   cb=MagicMock()
   with patch.object(QApplication,'clipboard',return_value=cb):copy.click()
   expected=w.ui('Seite {0}').format(1)+' · '+draft+'\n'+w.ui('Seite {0}').format(3)+' · needle'
   cb.setText.assert_called_once_with(expected)
   assert find(QLabel,'ocrCopyStatus').text()==w.ui('{0} Treffer kopiert').format(2)
   listing.clearSelection();assert not copy.isEnabled()
   find(QSpinBox,'ocrPreviewGroup').setValue(2);find(QPushButton,'ocrPreviewGroupGo').click()
   listing.itemActivated.emit(listing.item(0));assert page.value()==201 and table.currentRow()==1
   back.click();assert page.value()==1 and table.currentRow()==1 and table.item(1,0).text()==draft
   assert not copy.isEnabled() and not full.toPlainText()
   page.setValue(2);back.click();assert page.value()==1 and table.currentRow()==1
   search.setText('before');wait(d,w);assert full.toPlainText()=='before\n'+draft
   search.setText('after');wait(d,w);assert full.toPlainText()==draft+'\nafter'
   search.setText('missing');wait(d,w);assert not context.isEnabled() and full.isHidden() and not copy.isEnabled()
   search.setText('needle');wait(d,w);assert full.toPlainText()=='before\n'+draft+'\nafter'
   if code in ('de','ar','ja'):
    d.show();app.processEvents();d.grab().save(str(out/(code+'.png')))
   d.reject();return QDialog.DialogCode.Rejected
  with patch.object(QDialog,'exec',inspect):assert w.review_ocr(doc,0)=={}
  assert doc.tobytes(no_new_id=True)==original
  assert not any('history' in k or 'context' in k for k in settings.allKeys())
  w.deleteLater();app.processEvents()
 metrics=[]
 for count in (() if '--visual-only' in sys.argv else (500,1500)):
  with fixture(count) as large:
   with patch.object(m,'QSettings',return_value=QSettings(str(Path(tmp)/'large.ini'),QSettings.Format.IniFormat)):w=m.Window()
   def big(d):
    search=d.findChild(QLineEdit,'ocrLineSearch');search.setText('needle');wait(d,w)
    group=d.findChild(QSpinBox,'ocrPreviewGroup');group.setValue(group.maximum());d.findChild(QPushButton,'ocrPreviewGroupGo').click()
    listing=d.findChild(QListWidget,'ocrSearchResults');assert listing.count()<=200
    started=time.monotonic();listing.setCurrentRow(listing.count()-1);d.findChild(QCheckBox,'ocrPreviewContext').setChecked(True)
    assert d.findChild(QPlainTextEdit,'ocrPreviewFullText').toPlainText()=='before\nneedle\nafter'
    listing.selectAll();cb=MagicMock()
    with patch.object(QApplication,'clipboard',return_value=cb):d.findChild(QPushButton,'ocrResultsCopy').click()
    assert len(cb.setText.call_args.args[0].splitlines())==listing.count()
    listing.itemActivated.emit(listing.currentItem());assert d.findChild(QSpinBox).value()==count
    d.findChild(QPushButton,'ocrLocationBack').click();assert d.findChild(QSpinBox).value()==1
    metrics.append(dict(pages=count,contextCopyReturnSeconds=time.monotonic()-started))
    return QDialog.DialogCode.Rejected
   with patch.object(QDialog,'exec',big):w.review_ocr(large,0)
   w.deleteLater();app.processEvents()
 if metrics:(out/'large.json').write_text(json.dumps(metrics,indent=2))
print('PASS context with drafts/Unicode/edge lines/empty states; selected copy in document order; cross-page and same-page return; no persistence/PDF changes; 20 languages and 500/1500 pages')

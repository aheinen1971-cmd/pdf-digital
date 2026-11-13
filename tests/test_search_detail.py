import json,time,sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings,Qt
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QLineEdit,QLabel,QListWidget,QPushButton,QCheckBox,QComboBox,QSpinBox,QPlainTextEdit,QTableWidget
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 p=Path('C:/Windows/Fonts')/name
 if p.exists():QFontDatabase.addApplicationFont(str(p))
app.setFont(QFont('Segoe UI',10));out=Path(__file__).parent/'search-detail-results';out.mkdir(exist_ok=True)
def fixture(count):
 d=pdf.open()
 with pdf.open() as seed,pdf.open() as raster:
  raster.new_page().insert_text((30,50),'Scan');p=seed.new_page();p.insert_image(p.rect,pixmap=raster[0].get_pixmap());p.insert_text((30,50),'needle',render_mode=3)
  for _ in range(count):d.insert_pdf(seed)
 return d
def wait(d,w):
 until=time.monotonic()+40
 while d.findChild(QLabel,'ocrSearchStatus').text()==w.ui('Suche läuft …'):
  app.processEvents();QTest.qWait(1);assert time.monotonic()<until
with TemporaryDirectory() as tmp,fixture(205) as doc:
 original=doc.tobytes(no_new_id=True)
 for code in (('de','ar','ja') if '--visual-only' in sys.argv else m.UI_LANGUAGES):
  settings=QSettings(str(Path(tmp)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  def inspect(d):
   find=lambda cls,name:d.findChild(cls,name)
   search=find(QLineEdit,'ocrLineSearch');listing=find(QListWidget,'ocrSearchResults');expand=find(QPushButton,'ocrPreviewExpand');full=find(QPlainTextEdit,'ocrPreviewFullText');group=find(QSpinBox,'ocrPreviewGroup');go=find(QPushButton,'ocrPreviewGroupGo');reset=find(QPushButton,'ocrSearchReset');table=d.findChild(QTableWidget)
   assert not expand.isEnabled() and not group.isEnabled()
   long='Anfang '+('viel Text '*160)+'needle <tag> & العربية 日本語 Ende'
   table.item(0,0).setText(long);search.setText('needle');wait(d,w)
   expand.click();assert full.toPlainText()==long and full.isReadOnly() and not full.isHidden()
   assert '<tag>' in full.toPlainText(),'Full text must be literal, not HTML'
   full.selectAll();assert full.textCursor().selectedText()==long
   expand.click();assert full.isHidden();expand.click()
   group.setValue(2);go.click();assert listing.count()==5 and full.toPlainText()=='needle'
   assert listing.item(0).data(Qt.ItemDataRole.UserRole)==(200,0)
   group.setValue(1)
   with patch.object(d,'accept') as accepted:
    QTest.keyClick(group,Qt.Key.Key_Return);assert not accepted.called,'Enter applied corrections'
   assert listing.count()==200 and full.toPlainText()==long
   scope=find(QComboBox,'ocrSearchScope');scope.setCurrentIndex(2)
   find(QSpinBox,'ocrSearchFrom').setValue(3);find(QSpinBox,'ocrSearchTo').setValue(8)
   find(QCheckBox,'ocrSearchWhole').setChecked(True);find(QCheckBox,'ocrSearchCase').setChecked(True);wait(d,w)
   reset.click();wait(d,w)
   assert scope.currentIndex()==0 and find(QSpinBox,'ocrSearchFrom').value()==1 and find(QSpinBox,'ocrSearchTo').value()==205
   assert not find(QCheckBox,'ocrSearchWhole').isChecked() and not find(QCheckBox,'ocrSearchCase').isChecked()
   assert search.text()=='needle' and full.toPlainText()==long and group.maximum()==2
   if code in ('de','ar','ja'):
    d.show();app.processEvents();d.grab().save(str(out/(code+'.png')))
   search.setText('ABSENT');wait(d,w);assert not expand.isEnabled() and full.isHidden() and not full.toPlainText() and not go.isEnabled()
   search.setText('needle');wait(d,w);assert group.value()==1 and full.toPlainText()==long
   d.reject();return QDialog.DialogCode.Rejected
  with patch.object(QDialog,'exec',inspect):assert w.review_ocr(doc,0)=={}
  settings.sync();assert str(settings.value('ocr/search-whole')).lower()=='false' and str(settings.value('ocr/search-case')).lower()=='false'
  assert doc.tobytes(no_new_id=True)==original
  w.deleteLater();app.processEvents()
 metrics=[]
 for count in (() if '--visual-only' in sys.argv else (500,1500)):
  with fixture(count) as large:
   with patch.object(m,'QSettings',return_value=QSettings(str(Path(tmp)/'large.ini'),QSettings.Format.IniFormat)):w=m.Window()
   def big(d):
    search=d.findChild(QLineEdit,'ocrLineSearch');search.setText('needle');wait(d,w)
    group=d.findChild(QSpinBox,'ocrPreviewGroup');go=d.findChild(QPushButton,'ocrPreviewGroupGo');listing=d.findChild(QListWidget,'ocrSearchResults')
    start=time.monotonic();group.setValue(group.maximum());go.click()
    assert listing.count()==count%200 and listing.item(listing.count()-1).data(Qt.ItemDataRole.UserRole)==(count-1,0)
    if count==1500:
     group.setValue(5);go.click();assert listing.item(0).data(Qt.ItemDataRole.UserRole)==(800,0)
    d.findChild(QPushButton,'ocrPreviewExpand').click();assert d.findChild(QPlainTextEdit,'ocrPreviewFullText').toPlainText()=='needle'
    metrics.append(dict(pages=count,groupJumpSeconds=time.monotonic()-start,rows=listing.count()))
    return QDialog.DialogCode.Rejected
   with patch.object(QDialog,'exec',big):w.review_ocr(large,0)
   w.deleteLater();app.processEvents()
 if metrics:(out/'large.json').write_text(json.dumps(metrics,indent=2))
print('PASS full Unicode/literal long-line preview, expand/collapse, direct groups/Enter safety, empty reset, filter and persisted flag reset, draft/PDF preservation; '+('3 visual languages' if '--visual-only' in sys.argv else '20 languages plus 500/1500 pages'))

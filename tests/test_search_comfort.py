import time,json,sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings,Qt
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QLabel,QListWidget,QPushButton,QCheckBox,QLineEdit,QTableWidget,QSpinBox
import pdf_werkbank as m
import ocr_confidence as c
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 p=Path('C:/Windows/Fonts')/name
 if p.exists():QFontDatabase.addApplicationFont(str(p))
app.setFont(QFont('Segoe UI',10));out=Path(__file__).parent/'search-comfort-results';out.mkdir(exist_ok=True)
def wait(d,w):
 until=time.monotonic()+45
 while d.findChild(QLabel,'ocrSearchStatus').text()==w.ui('Suche läuft …'):
  app.processEvents();QTest.qWait(1);assert time.monotonic()<until
with TemporaryDirectory() as tmp,pdf.open() as doc:
 with pdf.open() as seed,pdf.open() as raster:
  raster.new_page().insert_text((30,50),'Scan');p=seed.new_page();p.insert_image(p.rect,pixmap=raster[0].get_pixmap())
  p.insert_text((30,50),'Straße <needle> & NEEDLE',render_mode=3)
  for _ in range(405):doc.insert_pdf(seed)
 original=doc.tobytes(no_new_id=True)
 for code in (('de','ar','ja') if '--visual-only' in sys.argv else m.UI_LANGUAGES):
  settings=QSettings(str(Path(tmp)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  def inspect(d):
   search=d.findChild(QLineEdit,'ocrLineSearch');listing=d.findChild(QListWidget,'ocrSearchResults');more=d.findChild(QPushButton,'ocrPreviewMore');back=d.findChild(QPushButton,'ocrPreviewEarlier')
   search.setText('STRASSE');wait(d,w);assert listing.count()==200
   label=listing.itemWidget(listing.item(0));assert '>Straße</b>' in label.text() and '&lt;needle&gt; &amp;' in label.text()
   assert not listing.item(0).text(),'Plain and rich text must not be drawn twice'
   assert listing.item(0).data(Qt.ItemDataRole.AccessibleTextRole)
   assert listing.item(0).sizeHint().height()>=label.fontMetrics().height()+6
   more.click();assert listing.count()==200 and listing.item(0).data(Qt.ItemDataRole.UserRole)==(200,0)
   more.click();assert listing.count()==5 and not more.isEnabled()
   listing.itemActivated.emit(listing.item(4));assert d.findChild(QLabel,'ocrSearchStatus').text()==w.ui('Fundstelle {0} von {1} · Seite {2}').format(405,405,405)
   back.click();assert listing.count()==200
   search.setText('needle');wait(d,w);assert not back.isEnabled() and listing.item(0).data(Qt.ItemDataRole.UserRole)==(0,0)
   assert listing.itemWidget(listing.item(0)).text().count('background-color')==2
   d.findChild(QCheckBox,'ocrSearchWhole').setChecked(True);d.findChild(QCheckBox,'ocrSearchCase').setChecked(True);wait(d,w)
   if code in ('de','ar','ja'):
    d.show();app.processEvents();d.grab().save(str(out/(code+'.png')))
   d.reject();return QDialog.DialogCode.Rejected
  with patch.object(QDialog,'exec',inspect):w.review_ocr(doc,0)
  # Recreate window/settings access to verify persistence rather than widget memory.
  settings.sync()
  w.deleteLater();app.processEvents()
  settings=QSettings(str(Path(tmp)/(code+'.ini')),QSettings.Format.IniFormat)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  def reopen(d):
   assert d.findChild(QCheckBox,'ocrSearchWhole').isChecked() and d.findChild(QCheckBox,'ocrSearchCase').isChecked()
   assert not d.findChild(QLineEdit,'ocrLineSearch').text()
   return QDialog.DialogCode.Rejected
  with patch.object(QDialog,'exec',reopen):w.review_ocr(doc,0)
  assert set(k for k in settings.allKeys() if k.startswith('ocr/search'))=={'ocr/search-whole','ocr/search-case'}
  assert 'needle' not in Path(settings.fileName()).read_text(encoding='utf-8').lower()
  words=[dict(text='one',score=20,box=[20,30,80,50]),dict(text='two',score=40,box=[20,60,80,80])]
  def keyboard(d):
   table=d.findChild(QTableWidget);mark=d.findChild(QCheckBox,'reviewedWord')
   d.show();d.activateWindow();table.setFocus();QTest.qWait(40)
   assert table.currentRow()==0
   QTest.keyClick(table,Qt.Key.Key_J,Qt.KeyboardModifier.ControlModifier);assert table.currentRow()==1
   QTest.keyClick(table,Qt.Key.Key_M,Qt.KeyboardModifier.ControlModifier);assert mark.isChecked()
   QTest.keyClick(table,Qt.Key.Key_M,Qt.KeyboardModifier.ControlModifier);assert not mark.isChecked()
   d.findChild(QSpinBox).setValue(0);QTest.keyClick(table,Qt.Key.Key_M,Qt.KeyboardModifier.ControlModifier);QTest.keyClick(table,Qt.Key.Key_J,Qt.KeyboardModifier.ControlModifier)
   assert not mark.isEnabled()
   assert 'Ctrl+J' in d.findChild(QLabel,'reviewShortcuts').text()
   d.hide();return QDialog.DialogCode.Rejected
  with patch.object(c,'recognize',return_value=(words,doc[0].get_pixmap())),patch.object(QDialog,'exec',keyboard):c.show_review(w,doc[0],'eng',Path(m.__file__).parent/'tessdata')
  w.deleteLater();app.processEvents()
 assert doc.tobytes(no_new_id=True)==original
print('PASS '+('3 visual UI' if '--visual-only' in sys.argv else '20 UI')+': Unicode/HTML-safe highlighting, 200/200/5 paging, reset, activation, persistent flags only, actual Ctrl+J/M, reversible/empty review and original PDF preserved')

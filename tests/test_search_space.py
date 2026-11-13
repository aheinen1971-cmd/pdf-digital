import time,json,sys,os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QLineEdit,QLabel,QListWidget,QPushButton,QCheckBox,QPlainTextEdit,QTableWidget,QSplitter,QScrollArea
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 p=Path('C:/Windows/Fonts')/name
 if p.exists():QFontDatabase.addApplicationFont(str(p))
app.setFont(QFont('Segoe UI',10))
out=Path(__file__).parent/('search-space-native-results' if os.environ.get('PDF_NATIVE') else 'search-space-results');out.mkdir(exist_ok=True)
def fixture(count):
 d=pdf.open()
 with pdf.open() as seed,pdf.open() as raster:
  raster.new_page().insert_text((30,50),'Scan');p=seed.new_page();p.insert_image(p.rect,pixmap=raster[0].get_pixmap())
  for y,t in [(50,'before'),(80,'needle'),(110,'after')]:p.insert_text((30,y),t,render_mode=3)
  for _ in range(count):d.insert_pdf(seed)
 return d
def wait(d,w):
 end=time.monotonic()+45
 while d.findChild(QLabel,'ocrSearchStatus').text()==w.ui('Suche läuft …'):
  app.processEvents();QTest.qWait(1);assert time.monotonic()<end
with TemporaryDirectory() as tmp,fixture(3) as doc:
 original=doc.tobytes(no_new_id=True)
 for code in (('de','ar','ja') if '--visual-only' in sys.argv else m.UI_LANGUAGES):
  settings=QSettings(str(Path(tmp)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  def inspect(d):
   find=lambda cls,name:d.findChild(cls,name)
   table=d.findChild(QTableWidget);search=find(QLineEdit,'ocrLineSearch');context=find(QCheckBox,'ocrPreviewContext');full=find(QPlainTextEdit,'ocrPreviewFullText')
   tools=find(QPushButton,'ocrToolsToggle');help_button=find(QPushButton,'ocrToolsHelp');help_note=find(QLabel,'ocrToolsHelpText');scroll=find(QScrollArea,'ocrSearchToolsScroll')
   line='😀 Straße <tag> & STRASSE العربية 日本語';table.item(1,0).setText(line);search.setText('strasse');wait(d,w);context.setChecked(True)
   assert full.toPlainText()=='before\n'+line+'\nafter'
   spans=full.extraSelections();assert [s.cursor.selectedText() for s in spans[1:]]==['Straße','STRASSE']
   assert spans[0].cursor.selectedText()==line and full.isReadOnly()
   d.show();app.processEvents();split=d.findChild(QSplitter);old=split.height();selected=find(QListWidget,'ocrSearchResults').currentRow()
   tools.click();app.processEvents();assert not scroll.isVisible() and split.height()>old+150
   assert tools.text()==w.ui('Suchwerkzeuge anzeigen')
   if code in ('de','ar','ja'):d.grab().save(str(out/(code+'-collapsed.png')))
   assert search.isVisible() and d.findChild(QPushButton,'ocrSearchStop').isVisible()
   assert search.text()=='strasse' and table.item(1,0).text()==line
   tools.click();app.processEvents();assert scroll.isVisible() and full.isVisible()
   assert selected==find(QListWidget,'ocrSearchResults').currentRow()
   help_button.click();app.processEvents();assert help_note.isVisible() and help_note.text().strip()
   if code in ('de','ar','ja'):d.grab().save(str(out/(code+'-help.png')))
   assert help_note.text().count('\n\n')==2
   for name in ('ocrResultsCopy','ocrLocationBack'):
    assert find(QPushButton,name).toolTip() in help_note.text()
   assert context.toolTip() in help_note.text()
   if code!='de':assert tools.text()!='Suchwerkzeuge anzeigen' and help_button.text()!='Kurzhilfe'
   tools.click();help_button.click();help_button.click();app.processEvents();assert tools.isChecked() and scroll.isVisible() and help_note.isVisible()
   help_button.click();app.processEvents();assert help_note.isHidden()
   if code in ('de','ar','ja'):
    d.grab().save(str(out/(code+'.png')))
   find(QCheckBox,'ocrSearchCase').setChecked(True);wait(d,w);assert not full.extraSelections()
   find(QCheckBox,'ocrSearchCase').setChecked(False);wait(d,w);assert len(full.extraSelections())==3
   search.setText('missing');wait(d,w);assert not full.extraSelections()
   return QDialog.DialogCode.Rejected
  with patch.object(QDialog,'exec',inspect):assert w.review_ocr(doc,0)=={}
  assert doc.tobytes(no_new_id=True)==original
  w.deleteLater();app.processEvents()
 metrics=[]
 for count in (() if '--visual-only' in sys.argv else (500,1500)):
  with fixture(count) as large:
   with patch.object(m,'QSettings',return_value=QSettings(str(Path(tmp)/'large.ini'),QSettings.Format.IniFormat)):w=m.Window()
   def big(d):
    tools=d.findChild(QPushButton,'ocrToolsToggle');tools.setChecked(False)
    d.findChild(QLineEdit,'ocrLineSearch').setText('needle');wait(d,w)
    listing=d.findChild(QListWidget,'ocrSearchResults');assert listing.count()==200
    d.findChild(QCheckBox,'ocrPreviewContext').setChecked(True)
    full=d.findChild(QPlainTextEdit,'ocrPreviewFullText');selections=full.extraSelections();assert selections[1].cursor.selectedText()=='needle'
    start=time.monotonic()
    for _ in range(20):tools.click()
    metrics.append(dict(pages=count,twentyTogglesSeconds=time.monotonic()-start))
    assert len(full.extraSelections())==2
    return QDialog.DialogCode.Rejected
   with patch.object(QDialog,'exec',big):w.review_ocr(large,0)
   w.deleteLater();app.processEvents()
 if metrics:(out/'large.json').write_text(json.dumps(metrics,indent=2))
print('PASS document space gain/state preservation, help controls/translations, Unicode UTF16/casefold/literal word+line highlights/clear; '+('3 visual UI languages' if '--visual-only' in sys.argv else '20 UI languages and 500/1500 pages'))

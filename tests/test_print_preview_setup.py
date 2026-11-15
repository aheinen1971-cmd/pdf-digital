import json, os, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPageLayout, QFont, QFontDatabase
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo, QPrintDialog
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QComboBox, QSpinBox, QCheckBox
from PySide6.QtTest import QTest
import pdf_werkbank as m

app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
app.setFont(QFont('Segoe UI',10))
out=Path(__file__).parent/'print-preview-results';out.mkdir(exist_ok=True)
class Device:
 def printerName(self):return 'Test printer'
 def supportedDuplexModes(self):return [QPrinter.DuplexMode.DuplexNone,QPrinter.DuplexMode.DuplexLongSide,QPrinter.DuplexMode.DuplexShortSide]

class PreviewPrinter(QPrinter):
 # PDF output deliberately ignores hardware duplex; emulate a duplex driver for preview tests.
 def __init__(self):
  super().__init__();self.setOutputFormat(QPrinter.OutputFormat.PdfFormat);self.mode=QPrinter.DuplexMode.DuplexNone
 def setDuplex(self,mode):self.mode=mode
 def duplex(self):return self.mode
def printer():return PreviewPrinter()

with TemporaryDirectory() as tmp,pdf.open() as doc:
 for i in range(5):
  p=doc.new_page(width=420 if i%2 else 595,height=595 if i%2 else 842)
  p.insert_text((30,60),'Page '+str(i+1),fontsize=24)
  p.draw_rect(pdf.Rect(30,100,220,260),fill=(0.1,0.5,0.8))
 original=doc.tobytes(no_new_id=True)
 for lang in m.UI_LANGUAGES:
  settings=QSettings(str(Path(tmp)/(lang+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',lang)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  if lang!='de':
   for key in ['Alle ausgewählten Seiten','Nur ungerade Seiten','Nur gerade Seiten','Vorwärts','Rückwärts','Einseitig','Lange Kante (wie ein Buch)','Kurze Kante (wie ein Kalender)','Drucker','Druckereinstellungen …','Exemplare','Sortieren','Hochformat','Querformat','Ausrichtung','PDF-Seite {0}','Druckvorschau konnte nicht erstellt werden.','Vorherige Seite','Nächste Seite']:
    assert key in w.ui_words and w.ui(key)!=key,(lang,key)
  def inspect(d):
   f=lambda cls,name:d.findChild(cls,name)
   d.show();app.processEvents()
   assert not f(QLabel,'printImage0').pixmap().isNull()
   assert f(QLabel,'printImage0').pixmap().toImage().pixelColor(45,90)!=f(QLabel,'printImage0').pixmap().toImage().pixelColor(0,0)
   assert f(QPushButton,'printSubmit').text()==w.ui('Jetzt drucken')
   assert d.layoutDirection()==(Qt.LayoutDirection.RightToLeft if lang in ('ar','he') else Qt.LayoutDirection.LeftToRight)
   f(QPushButton,'printNext').click();assert '2' in f(QLabel,'printCount').text()
   f(QSpinBox,'printFrom').setValue(2);f(QSpinBox,'printTo').setValue(5)
   f(QComboBox,'printSubset').setCurrentIndex(1);f(QComboBox,'printOrder').setCurrentIndex(1)
   f(QComboBox,'printDuplex').setCurrentIndex(1);f(QComboBox,'printOrientation').setCurrentIndex(1)
   f(QSpinBox,'printCopies').setValue(2);f(QCheckBox,'printCollate').setChecked(True)
   QTest.qWait(110);app.processEvents()
   assert '5' in f(QLabel,'printHeading0').text()
   if lang in ('de','ar','ja'):d.grab().save(str(out/(lang+'.png')))
   assert f(QPushButton,'printSubmit').isEnabled()
   return QDialog.DialogCode.Accepted
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',inspect):
   p,indices=w.print_preview_setup(doc,printer(),[Device()])
  assert indices==[4,2] and p.copyCount()==2 and p.collateCopies()
  assert p.pageLayout().orientation()==QPageLayout.Orientation.Landscape
  assert p.duplex()==QPrinter.DuplexMode.DuplexLongSide
  assert doc.tobytes(no_new_id=True)==original
  w.close();w.deleteLater();app.processEvents()

 with patch.object(m,'QSettings',return_value=QSettings(str(Path(tmp)/'extra.ini'),QSettings.Format.IniFormat)):w=m.Window()
 # Advanced driver dialog: cancelled changes discarded; accepted settings reflected.
 real_printer=m.QPrinter
 def create_candidate(*args):return printer()
 # Expose enum attributes used by the product while replacing only construction.
 for name in ('PrinterMode','DuplexMode','PageOrder','PrintRange'):
  setattr(create_candidate,name,getattr(real_printer,name))
 for accepted in (False,True):
  def native(n):
   p=n.printer();p.setCopyCount(3);p.setPageOrientation(QPageLayout.Orientation.Landscape)
   p.setPrintRange(QPrinter.PrintRange.PageRange);p.setFromTo(2,4)
   return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
  def advanced(d):
   d.findChild(QPushButton,'printAdvanced').click()
   assert d.findChild(QSpinBox,'printCopies').value()==(3 if accepted else 1)
   assert d.findChild(QSpinBox,'printFrom').value()==(2 if accepted else 1)
   return QDialog.DialogCode.Accepted
  with patch.object(m,'QPrinter',create_candidate),patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QPrintDialog,'exec',native),patch.object(QDialog,'exec',advanced):
   result=w.print_preview_setup(doc,printer(),[Device()])
   assert result[1]==([1,2,3] if accepted else [0,1,2,3,4])
 for empty in ('range','parity','no-printer','render-error'):
  def invalid(d):
   if empty=='range':d.findChild(QSpinBox,'printFrom').setValue(5);d.findChild(QSpinBox,'printTo').setValue(1)
   if empty=='parity':d.findChild(QSpinBox,'printTo').setValue(1);d.findChild(QComboBox,'printSubset').setCurrentIndex(2)
   return QDialog.DialogCode.Accepted
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',invalid):
   if empty=='render-error':
    with patch.object(m,'print_preview_image',side_effect=RuntimeError('fixture')):assert w.print_preview_setup(doc,printer(),[Device()]) is None
   else:assert w.print_preview_setup(doc,printer(),[] if empty=='no-printer' else [Device()]) is None
 # No rendering or printing of all 1500 pages just to open the preview.
 with pdf.open() as large:
  for _ in range(1500):large.new_page()
  start=time.monotonic()
  with patch.object(m,'print_preview_image',wraps=m.print_preview_image) as image,patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Rejected):
   assert w.print_preview_setup(large,printer(),[Device()]) is None
   assert image.call_count<=4
  elapsed=time.monotonic()-start
 # Real PDF output, correct page order and original preserved (no physical print).
 target=Path(tmp)/'printed.pdf';p=printer();p.setOutputFileName(str(target));p.setResolution(96)
 m.print_pages(doc,p,[4,2])
 with pdf.open(target) as result:assert len(result)==2 and result[0].get_pixmap().samples!=result[1].get_pixmap().samples
 assert doc.tobytes(no_new_id=True)==original
 # Full entry point opens our preview first. Cancel never invokes print_pages.
 src=Path(tmp)/'source.pdf';doc.save(src);w.open_path(str(src))
 with patch.object(QPrinterInfo,'availablePrinters',return_value=[]),patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Rejected),patch.object(m,'print_pages') as send,patch.object(QPrintDialog,'exec',side_effect=AssertionError('native dialog opened first')):
  w.print_dialog();send.assert_not_called()
 w.current.doc.close();w.close()
 (out/'result.json').write_text(json.dumps({'passed':True,'languages':list(m.UI_LANGUAGES),'largePages':1500,'largePreviewSeconds':elapsed,'physicalPrintSent':False},indent=2))
print('PASS immediate preview, 20 languages/RTL, range/parity/reverse/duplex/orientation/copies/collation, invalid selections, render failure, cancellation, 1500 pages, real PDF print output; no physical jobs')

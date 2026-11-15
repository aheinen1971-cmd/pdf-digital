import json, os, time, math, hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt, QPoint
from PySide6.QtGui import QPageSize, QFont, QFontDatabase
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QComboBox, QSpinBox, QLineEdit, QListWidget, QMessageBox, QScrollArea
from PySide6.QtTest import QTest
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
app.setFont(QFont('Segoe UI',10))
out=Path(os.environ.get('PRINT_SELECTION_RESULTS',str(Path(__file__).parent/'print-selection-results')));out.mkdir(parents=True,exist_ok=True)
class Device:
 def printerName(self):return 'Test printer'
 def supportedDuplexModes(self):return [QPrinter.DuplexMode.DuplexNone,QPrinter.DuplexMode.DuplexLongSide]
 def supportedPageSizes(self):return [QPageSize(QPageSize.PageSizeId.A4)]
class Printer(QPrinter):
 def __init__(self,name='Test printer'):
  super().__init__();self.name=name;self.mode=QPrinter.DuplexMode.DuplexNone
  self.setOutputFormat(QPrinter.OutputFormat.PdfFormat);self.setPageSize(QPageSize(QPageSize.PageSizeId.A4));self.setResolution(144)
 def printerName(self):return self.name
 def setDuplex(self,mode):self.mode=mode
 def duplex(self):return self.mode
summary='PDF-Seiten: {0} · Exemplare: {1} · Blatt gesamt: {2}'
keys=[summary,'Papierbedarf ohne Drucker-Trennblätter.','Einzelne Seiten auswählen','Häkchen entfernen, um Seiten auszuschließen. Seitenbereich und Gerade/Ungerade gelten zusätzlich.','Alle Seiten wieder einschließen','Benannte Druckvorlagen','Vorlagenname','Vorlage „{0}“ ersetzen?','Vorlage „{0}“ löschen?','Name: 1–80 Zeichen. Höchstens 20 Vorlagen je Drucker.']
for pages in range(56):
 for n in (1,2,4):
  for duplex in (False,True):
   for copies in (1,2,3):
    sides,sheets=m.print_totals(pages,n,duplex,copies)
    assert sides==math.ceil(pages/n)*copies
    assert sheets==math.ceil(math.ceil(pages/n)/(2 if duplex else 1))*copies
with TemporaryDirectory() as tmp,pdf.open() as doc:
 for i in range(12):
  page=doc.new_page();page.draw_rect(page.rect,fill=((i+1)/13,0,0),color=None)
 original=doc.tobytes(no_new_id=True)
 for lang in m.UI_LANGUAGES:
  settings=QSettings(str(Path(tmp)/(lang+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',lang)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  if lang!='de':assert all(k in w.ui_words for k in keys),(lang,'translation')
  key='print/named/'+hashlib.sha256(b'Test printer').hexdigest()
  default='print/preset/'+hashlib.sha256(b'Test printer').hexdigest()
  def exercise(d):
   f=lambda c,n:d.findChild(c,n)
   d.show();app.processEvents();assert d.layoutDirection()==w.layoutDirection()
   assert f(QLabel,'printSummary').text()==w.ui_format(summary,12,1,12)
   f(QComboBox,'printPerSide').setCurrentIndex(1);f(QComboBox,'printDuplex').setCurrentIndex(1);f(QSpinBox,'printCopies').setValue(2)
   QTest.qWait(250);assert f(QLabel,'printSummary').text()==w.ui_format(summary,12,2,6)
   f(QPushButton,'printChoosePages').click();app.processEvents()
   choices=f(QListWidget,'printPageChoices');assert choices.isVisible() and choices.count()==12
   # Space toggles the focused checkbox, including with RTL layouts.
   choices.setCurrentRow(0);choices.setFocus();QTest.keyClick(choices,Qt.Key.Key_Space)
   assert choices.item(0).checkState()==Qt.CheckState.Unchecked
   choices.item(11).setCheckState(Qt.CheckState.Unchecked);QTest.qWait(250)
   assert f(QLabel,'printSummary').text()==w.ui_format(summary,10,2,6)
   assert '1, 2' not in f(QLabel,'printHeading0').text()
   f(QLineEdit,'printPresetName').setText('Entwurf 日本語 🌹');f(QPushButton,'printNamedSave').click()
   data=json.loads(settings.value(key));assert len(data)==1
   assert data['Entwurf 日本語 🌹']['pages']==2 and not settings.contains(default)
   assert 'excluded' not in str(data)
   f(QComboBox,'printPerSide').setCurrentIndex(2);f(QLineEdit,'printPresetName').setText('Vier');f(QPushButton,'printNamedSave').click()
   f(QComboBox,'printNamedPresets').setCurrentText('Entwurf 日本語 🌹');f(QPushButton,'printNamedLoad').click()
   assert f(QComboBox,'printPerSide').currentData()==2
   assert choices.item(0).checkState()==Qt.CheckState.Unchecked
   f(QSpinBox,'printCopies').setValue(3)
   with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Cancel):f(QPushButton,'printNamedSave').click()
   assert json.loads(settings.value(key))['Entwurf 日本語 🌹']['copies']==2
   with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes):f(QPushButton,'printNamedSave').click()
   assert json.loads(settings.value(key))['Entwurf 日本語 🌹']['copies']==3
   before=settings.value(key)
   with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Cancel):f(QPushButton,'printNamedDelete').click()
   assert settings.value(key)==before
   with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes):f(QPushButton,'printNamedDelete').click()
   assert list(json.loads(settings.value(key)))==['Vier']
   f(QPushButton,'printResetPages').click();assert all(choices.item(i).checkState()==Qt.CheckState.Checked for i in range(12))
   f(QSpinBox,'printFrom').setValue(2);f(QSpinBox,'printTo').setValue(10);f(QComboBox,'printSubset').setCurrentIndex(2);f(QComboBox,'printOrder').setCurrentIndex(1)
   choices.item(3).setCheckState(Qt.CheckState.Unchecked)
   if lang in ('de','ar','ja'):
    QTest.qWait(250)
    for scroll in d.findChildren(QScrollArea):
     if scroll.widget() and scroll.widget().isAncestorOf(f(QPushButton,'printNamedSave')):scroll.ensureWidgetVisible(f(QPushButton,'printNamedSave'))
    app.processEvents();d.grab().save(str(out/(lang+'.png')))
   return QDialog.DialogCode.Accepted
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',exercise):
   result=w.print_preview_setup(doc,Printer(),[Device()]);assert result[1]==[9,7,5,1],result[1]
  # Restart with the same isolated settings: named presets persist, selection does not.
  w.close();w.deleteLater();app.processEvents()
  disk=QSettings(settings.fileName(),QSettings.Format.IniFormat)
  with patch.object(m,'QSettings',return_value=disk):w=m.Window()
  def reopened(d):
   assert d.findChild(QComboBox,'printNamedPresets').count()==1
   assert d.findChild(QListWidget,'printPageChoices').item(3).checkState()==Qt.CheckState.Checked
   assert d.findChild(QComboBox,'printPerSide').currentData()==1
   return QDialog.DialogCode.Rejected
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',reopened):assert w.print_preview_setup(doc,Printer(),[Device()]) is None
  def other(d):
   assert d.findChild(QComboBox,'printNamedPresets').count()==0
   return QDialog.DialogCode.Rejected
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',other):w.print_preview_setup(doc,Printer('Other printer'),[Device()])
  def exclude_all(d):
   choices=d.findChild(QListWidget,'printPageChoices')
   for i in range(choices.count()):choices.item(i).setCheckState(Qt.CheckState.Unchecked)
   return QDialog.DialogCode.Accepted
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',exclude_all):assert w.print_preview_setup(doc,Printer(),[Device()]) is None
  w.close();w.deleteLater();app.processEvents()
 # Limits and malformed stored data; rejected saves must preserve the store.
 settings=QSettings(str(Path(tmp)/'limits.ini'),QSettings.Format.IniFormat)
 with patch.object(m,'QSettings',return_value=settings):w=m.Window()
 settings.setValue(key,json.dumps({str(i):{'version':1,'pages':1} for i in range(20)}))
 def limit(d):
  before=settings.value(key)
  for name in ('',' ','New','Bad\x01Name'):
   d.findChild(QLineEdit,'printPresetName').setText(name);d.findChild(QPushButton,'printNamedSave').click();assert settings.value(key)==before
  return QDialog.DialogCode.Rejected
 with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',limit):w.print_preview_setup(doc,Printer(),[Device()])
 for raw in ('[]','invalid','x'*65537,json.dumps({'bad':[],'valid':{'version':2}})):
  settings.setValue(key,raw)
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',other):w.print_preview_setup(doc,Printer(),[Device()])
 # Selected pages really reach output, in the requested order.
 p=Printer();p.pdf_pages_per_side=2;target=Path(tmp)/'selected.pdf';p.setOutputFileName(str(target));selected=[9,7,5,1];m.print_pages(doc,p,selected)
 with pdf.open(target) as printed:
  assert len(printed)==2
  for page,side in zip(printed,m.print_sides(selected,p)):
   for index,_,dest,_ in m.print_placements(doc,side,p):
    px=page.get_pixmap().pixel(round(dest.center().x()),round(dest.center().y()));assert abs(px[0]-round((index+1)/13*255))<5 and px[1]<5, (index, px, round((index+1)/13*255))
 assert doc.tobytes(no_new_id=True)==original
 with pdf.open() as large:
  for _ in range(1500):large.new_page()
  def large_setup(d):
   choices=d.findChild(QListWidget,'printPageChoices');assert choices.count()==1500
   choices.item(0).setCheckState(Qt.CheckState.Unchecked);choices.item(1499).setCheckState(Qt.CheckState.Unchecked)
   d.findChild(QComboBox,'printPerSide').setCurrentIndex(2);d.findChild(QComboBox,'printDuplex').setCurrentIndex(1);d.findChild(QSpinBox,'printCopies').setValue(3)
   QTest.qWait(250);assert d.findChild(QLabel,'printSummary').text()==w.ui_format(summary,1498,3,564)
   return QDialog.DialogCode.Accepted
  start=time.monotonic()
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(m,'draw_print_side',wraps=m.draw_print_side) as draw,patch.object(QDialog,'exec',large_setup):
   result=w.print_preview_setup(large,Printer(),[Device()]);assert len(result[1])==1498 and draw.call_count<=6
  elapsed=time.monotonic()-start;assert elapsed<15
 w.close()
 (out/'result.json').write_text(json.dumps({'passed':True,'languages':list(m.UI_LANGUAGES),'largePages':1500,'largeSeconds':elapsed,'physicalPrintSent':False},indent=2))
print('PASS totals/odd duplex copies/1-2-4-up; page inclusion/range/parity/reverse/empty/real output; named presets persistence/isolation/confirmations/limits/corruption; 20 languages/RTL and 1500 pages; no physical jobs')



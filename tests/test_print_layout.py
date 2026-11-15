import json, os, time, math, hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPageSize, QPageLayout, QFont, QFontDatabase
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QComboBox, QSpinBox
from PySide6.QtTest import QTest
import pdf_werkbank as m

app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
app.setFont(QFont('Segoe UI',10))
out=Path(__file__).parent/'print-layout-results';out.mkdir(exist_ok=True)
class Device:
 def printerName(self):return 'Test printer'
 def supportedDuplexModes(self):return [QPrinter.DuplexMode.DuplexNone,QPrinter.DuplexMode.DuplexLongSide]
 def supportedPageSizes(self):return [QPageSize(QPageSize.PageSizeId.A4),QPageSize(QPageSize.PageSizeId.Letter)]
class Printer(QPrinter):
 def __init__(self,name='Test printer'):
  super().__init__();self.name=name;self.mode=QPrinter.DuplexMode.DuplexNone
  self.setOutputFormat(QPrinter.OutputFormat.PdfFormat);self.setPageSize(QPageSize(QPageSize.PageSizeId.A4));self.setResolution(144)
 def printerName(self):return self.name
 def setDuplex(self,mode):self.mode=mode
 def duplex(self):return self.mode
keys=['Papierformat','Anpassen','Originalgröße (100 %)','Skalierung','Seiten pro Blattseite','Gestrichelte Linien: druckbarer Bereich. Randwarnung gilt für die angezeigten Seiten.','Achtung: Seiteninhalt kann am Rand abgeschnitten werden.','Die angezeigten Seiten passen in den druckbaren Bereich.','Druckeinstellungen speichern','Druckeinstellungen gespeichert.','Druckeinstellungen konnten nicht gespeichert werden.']
with TemporaryDirectory() as tmp,pdf.open() as doc:
 colors=[(1,0,0),(0,1,0),(0,0,1),(1,1,0)]
 for i in range(9):
  page=doc.new_page(width=720 if i%2==0 else 300,height=900 if i%2==0 else 500)
  page.draw_rect(page.rect,fill=colors[i%4],color=None)
  page.insert_text((20,40),'Page '+str(i+1),fontsize=20)
 original=doc.tobytes(no_new_id=True)
 for lang in m.UI_LANGUAGES:
  settings=QSettings(str(Path(tmp)/(lang+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',lang)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  if lang!='de':assert all(k in w.ui_words and w.ui(k)!=k for k in keys),(lang,'translation')
  initial=settings.allKeys()
  def edit(d):
   f=lambda cls,n:d.findChild(cls,n)
   d.show();app.processEvents()
   assert d.layoutDirection()==w.layoutDirection()
   f(QComboBox,'printScaling').setCurrentIndex(1);QTest.qWait(250)
   assert f(QLabel,'printMargins').text()==w.ui(keys[6]), (lang, f(QLabel,'printMargins').text(), f(QLabel,'printNotice').text())
   f(QComboBox,'printScaling').setCurrentIndex(0)
   f(QComboBox,'printPerSide').setCurrentIndex(2)
   f(QComboBox,'printDuplex').setCurrentIndex(1)
   f(QComboBox,'printPaper').setCurrentIndex(1)
   f(QSpinBox,'printCopies').setValue(2)
   QTest.qWait(250)
   assert f(QLabel,'printMargins').text()==w.ui(keys[7])
   assert '1, 2, 3, 4' in f(QLabel,'printHeading0').text()
   assert '5, 6, 7, 8' in f(QLabel,'printHeading1').text()
   assert '2' in f(QLabel,'printCount').text()
   f(QPushButton,'printSaveSettings').click()
   assert f(QLabel,'printSavedNotice').text()==w.ui(keys[9])
   if lang in ('de','ar','ja'):
    d.grab().save(str(out/(lang+'.png')))
   f(QSpinBox,'printFrom').setValue(3)
   return QDialog.DialogCode.Rejected
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',edit):
   assert w.print_preview_setup(doc,Printer(),[Device()]) is None
  # Explicit Save persists even if printing is cancelled; ranges are never stored.
  saved_key='print/preset/'+hashlib.sha256(b'Test printer').hexdigest()
  saved=json.loads(settings.value(saved_key));assert saved['pages']==4 and saved['copies']==2
  assert 'first' not in saved and 'last' not in saved
  def reopened(d):
   assert d.findChild(QComboBox,'printPerSide').currentData()==4
   assert d.findChild(QSpinBox,'printCopies').value()==2
   assert d.findChild(QComboBox,'printPaper').currentData().id()==QPageSize.PageSizeId.Letter
   assert d.findChild(QComboBox,'printDuplex').currentData()==QPrinter.DuplexMode.DuplexLongSide
   assert d.findChild(QSpinBox,'printFrom').value()==1
   d.findChild(QComboBox,'printPerSide').setCurrentIndex(1)
   return QDialog.DialogCode.Rejected
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',reopened):w.print_preview_setup(doc,Printer(),[Device()])
  assert json.loads(settings.value(saved_key))==saved
  disk=QSettings(settings.fileName(),QSettings.Format.IniFormat)
  assert json.loads(disk.value(saved_key))==saved
  def other(d):
   assert d.findChild(QComboBox,'printPerSide').currentData()==1
   assert d.findChild(QSpinBox,'printCopies').value()==1
   return QDialog.DialogCode.Rejected
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',other):w.print_preview_setup(doc,Printer('Other printer'),[Device()])
  for bad in ('not json', '[]', json.dumps({'version':1,'pages':999,'copies':-100,'paper':999,'duplex':999,'scale':'invalid'})):
   settings.setValue(saved_key,bad)
   with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(QDialog,'exec',other):w.print_preview_setup(doc,Printer(),[Device()])
  w.close();w.deleteLater();app.processEvents()
 # Geometry and actual PDF output, every layout and orientation, including reverse order.
 for orientation in (QPageLayout.Orientation.Portrait,QPageLayout.Orientation.Landscape):
  for count in (1,2,4):
   p=Printer();p.setPageOrientation(orientation);p.pdf_pages_per_side=count;p.pdf_scale_mode='fit'
   selected=list(range(8,-1,-1));sides=m.print_sides(selected,p)
   assert len(sides)==math.ceil(9/count)
   assert len(m.print_sheets(sides,True))==math.ceil(len(sides)/2)
   for side in sides:
    geometry=m.print_placements(doc,side,p)
    for index,cell,target,clipped in geometry:
     assert not clipped and cell.adjusted(-.1,-.1,.1,.1).contains(target)
    assert all(not a[1].intersects(b[1]) for i,a in enumerate(geometry) for b in geometry[i+1:])
   dest=Path(tmp)/f'output-{orientation.value}-{count}.pdf';p.setOutputFileName(str(dest))
   m.print_pages(doc,p,selected)
   with pdf.open(dest) as printed:
    assert len(printed)==len(sides)
    for page,side in zip(printed,sides):
     for index,cell,target,_ in m.print_placements(doc,side,p):
      # The solid test page's center retains its unique color at the shared placement.
      x,y=int(target.center().x()),int(target.center().y())
      px=page.get_pixmap().pixel(x,y)
      assert all(abs(a-round(b*255))<5 for a,b in zip(px,colors[index%4])),(count,index,px)
 # Original scale preserves physical dimensions and identifies clipping.
 p=Printer();p.pdf_scale_mode='actual'
 g=m.print_placements(doc,0,p)[0];assert g[3] and abs(g[2].width()-doc[0].rect.width)<.01
 g=m.print_placements(doc,1,p)[0];assert not g[3] and abs(g[2].height()-500)<.01
 # Real original-size output: the small green page keeps its 300-point width.
 target=Path(tmp)/'actual.pdf';p.setOutputFileName(str(target));m.print_pages(doc,p,[1,0])
 with pdf.open(target) as actual:
  assert len(actual)==2
  image=actual[0].get_pixmap()
  y=round(m.print_placements(doc,1,p)[0][2].center().y())
  green=[x for x in range(image.width) if image.pixel(x,y)[1]>240 and image.pixel(x,y)[0]<10]
  assert 298<=len(green)<=302,len(green)
 # Rotated/cropped pages follow their displayed dimensions rather than the media box.
 with pdf.open() as rotated:
  page=rotated.new_page(width=600,height=800);page.draw_rect(page.rect,fill=(0,0,1));page.set_cropbox(pdf.Rect(50,50,550,750));page.set_rotation(90)
  p=Printer();p.pdf_pages_per_side=2
  positions=m.print_placements(rotated,(0,),p);assert not positions[0][3]
  target=Path(tmp)/'rotated.pdf';p.setOutputFileName(str(target));m.print_pages(rotated,p,[0])
  with pdf.open(target) as printed:
   center=positions[0][2].center();px=printed[0].get_pixmap().pixel(round(center.x()),round(center.y()));assert px[2]>240 and px[0]<10
 assert doc.tobytes(no_new_id=True)==original
 # Large documents: preview renders only current two sides, not all 1500 pages.
 with pdf.open() as large:
  for i in range(1500):large.new_page()
  settings=QSettings(str(Path(tmp)/'large.ini'),QSettings.Format.IniFormat)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  def large_setup(d):
   d.findChild(QComboBox,'printPerSide').setCurrentIndex(2)
   d.findChild(QComboBox,'printDuplex').setCurrentIndex(1)
   return QDialog.DialogCode.Accepted
  start=time.monotonic()
  with patch.object(QPrinterInfo,'printerInfo',return_value=Device()),patch.object(m,'draw_print_side',wraps=m.draw_print_side) as draw,patch.object(QDialog,'exec',large_setup):
   result=w.print_preview_setup(large,Printer(),[Device()]);assert len(result[1])==1500 and draw.call_count<=4
  elapsed=time.monotonic()-start;assert elapsed<15
  w.close()
 # Very large physical page at 100% is clipped before rasterization.
 with pdf.open() as huge:
  huge.new_page(width=20000,height=20000)
  p=Printer();p.pdf_scale_mode='actual';image=m.print_preview_image(huge,0,p,440);assert image.width()<=440
 (out/'result.json').write_text(json.dumps({'passed':True,'languages':list(m.UI_LANGUAGES),'largePages':1500,'largeSeconds':elapsed,'physicalPrintSent':False},indent=2))
print('PASS 20 languages/RTL, paper/scale/margins, explicit per-printer presets/cancel/corrupt data, 1/2/4-up geometry and real PDF output in both orientations, duplex grouping, 1500 pages and oversized page; no physical jobs')



import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch,MagicMock
import pymupdf as pdf
from PySide6.QtCore import QSettings,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QListWidget,QPushButton,QLineEdit,QLabel,QSpinBox,QTableWidget,QComboBox
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
with TemporaryDirectory() as tmp,pdf.open() as doc,pdf.open() as raster:
 raster.new_page().insert_text((20,30),'Scan')
 for _ in range(2):
  p=doc.new_page();p.insert_image(p.rect,pixmap=raster[0].get_pixmap());p.insert_text((30,50),'needle',render_mode=3)
 before=doc.tobytes(no_new_id=True)
 with patch.object(m,'QSettings',return_value=QSettings(str(Path(tmp)/'settings.ini'),QSettings.Format.IniFormat)):w=m.Window()
 def inspect(d):
  page=d.findChild(QSpinBox);back=d.findChild(QPushButton,'ocrLocationBack');table=d.findChild(QTableWidget)
  for i in range(105):page.setValue(2 if i%2==0 else 1)
  count=0
  while back.isEnabled():back.click();count+=1;assert count<=100
  assert count==100
  page.setValue(1);search=d.findChild(QLineEdit,'ocrLineSearch');search.setText('needle')
  def wait():
   end=time.monotonic()+15
   while d.findChild(QLabel,'ocrSearchStatus').text()==w.ui('Suche läuft …'):
    app.processEvents();QTest.qWait(1);assert time.monotonic()<end
  wait();d.show();app.processEvents();listing=d.findChild(QListWidget,'ocrSearchResults')
  QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,listing.visualItemRect(listing.item(0)).center())
  QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,listing.visualItemRect(listing.item(1)).center())
  assert len(listing.selectedItems())==2 and page.value()==2
  cb=MagicMock()
  with patch.object(QApplication,'clipboard',return_value=cb):d.findChild(QPushButton,'ocrResultsCopy').click()
  assert len(cb.setText.call_args.args[0].splitlines())==2
  table.item(0,0).setText('needle draft');back.click();assert page.value()==1
  page.setValue(2);assert table.item(0,0).text()=='needle draft'
  scope=d.findChild(QComboBox,'ocrSearchScope');scope.setCurrentIndex(1);wait()
  page.setValue(1);back.click();QTest.qWait(300);app.processEvents();assert page.value()==2 and table.item(0,0).text()=='needle draft'
  return QDialog.DialogCode.Rejected
 with patch.object(QDialog,'exec',inspect):assert w.review_ocr(doc,0)=={}
 def reopen(d):
  assert not d.findChild(QPushButton,'ocrLocationBack').isEnabled()
  assert not d.findChild(QLineEdit,'ocrLineSearch').text()
  return QDialog.DialogCode.Rejected
 with patch.object(QDialog,'exec',reopen):w.review_ocr(doc,0)
 assert doc.tobytes(no_new_id=True)==before
print('PASS 100-location history bound, actual Ctrl-click multi-selection, explicit clipboard copy, drafts, return during pending current-page search, empty new dialog and unchanged PDF')

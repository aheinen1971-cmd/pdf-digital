from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings,Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtWidgets import QApplication,QDialog,QPushButton,QTableWidget,QLabel,QLineEdit,QSpinBox
import pdf_werkbank as m
import ocr_review_state as state
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 p=Path('C:/Windows/Fonts')/name
 if p.exists():QFontDatabase.addApplicationFont(str(p))
app.setFont(QFont('Segoe UI',10));root=Path(m.__file__).parent;out=root/'tests/zoom-search-results';out.mkdir(exist_ok=True)
def capture(dialog,code,name):
 dialog.show();app.processEvents()
 if code in ('de','ar','ja'):dialog.grab().save(str(out/(name+'-'+code+'.png')))
with TemporaryDirectory() as folder,pdf.open() as doc,pdf.open() as seed:
 seed.new_page(width=300,height=300).insert_text((20,40),'Scanned line')
 for n in range(3):
  p=doc.new_page(width=300,height=300);p.insert_image(p.rect,pixmap=seed[0].get_pixmap())
  p.insert_text((20,40),'Target line' if n!=1 else 'Other line',render_mode=3)
 key=state.document_key(doc);before=doc.tobytes(no_new_id=True)
 record=dict(doc=key,page=0,language='eng',words='a'*64,scores=[20],reviewed=[],threshold=80,position=0)
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):window=m.Window()
  for text in ('Verkleinern','Vergrößern','Einpassen','OCR-Korrekturzeilen durchsuchen','Vorherige Fundstelle','Nächste Fundstelle','{0} passende Zeilen','Nächste Seite mit offenen Prüfungen','Seiten ohne passenden Prüfstand gelten weiterhin als nicht geprüft.','Keine weiteren erfassten offenen Prüfungen.'):
   assert code=='de' or window.ui(text)!=text,(code,text)
  with patch.object(state,'settings',return_value=settings):
   state.write(record);state.write(dict(record,page=2));state.write(dict(record,page=1,doc='b'*64))
   assert state.next_pending_record(window,doc,0)['page']==2
   assert state.next_pending_record(window,doc,2)['page']==0
   window._ocr_review_memory={(key,2,'eng'):dict(record,page=2,reviewed=[0])}
   assert state.next_pending_record(window,doc,0)['page']==0
   window._ocr_review_memory[(key,0,'eng')]=dict(record,reviewed=[0])
   assert state.next_pending_record(window,doc,0) is None
   window._ocr_review_memory={}
   def editor(dialog):
    search=dialog.findChild(QLineEdit,'ocrLineSearch');table=dialog.findChild(QTableWidget);selector=dialog.findChild(QSpinBox)
    next_=dialog.findChild(QPushButton,'ocrSearchNext');previous=dialog.findChild(QPushButton,'ocrSearchPrevious')
    search.setText('tArGeT');assert selector.value()==1
    dialog.show();search.setFocus();app.processEvents()
    with patch.object(state,'compare_corrections',return_value=False) as compare:
     QTest.keyClick(search,Qt.Key.Key_Return);QTest.keyClick(search,Qt.Key.Key_Enter);compare.assert_not_called()
    selector.setValue(1)
    assert dialog.findChild(QLabel,'ocrSearchStatus').text()==window.ui('{0} passende Zeilen').format(2)
    next_.click();assert selector.value()==3
    next_.click();assert selector.value()==1
    previous.click();assert selector.value()==3
    table.item(0,0).setText('Straße Ω العربية')
    search.setText('STRASSE');assert selector.value()==3 and next_.isEnabled()
    search.setText('العربية');assert next_.isEnabled()
    search.setText('absent');assert not next_.isEnabled()
    search.clear();assert not next_.isEnabled()
    selector.setValue(1);selector.setValue(3);assert table.item(0,0).text()=='Straße Ω العربية'
    with patch.object(state,'open_record') as opened:
     dialog.findChild(QPushButton,'ocrNextPendingPage').click();assert selector.value()==1;assert opened.call_args.args[2]['page']==0
    capture(dialog,code,'search')
    return QDialog.DialogCode.Rejected
   with patch.object(QDialog,'exec',editor):assert window.review_ocr(doc,0)=={}
   def summary(dialog):
    table=dialog.findChild(QTableWidget);assert table.item(1,1).text()==window.ui('Nicht geprüft')
    table.setCurrentCell(0,0);dialog.findChild(QPushButton,'nextPendingPage').click();return QDialog.DialogCode.Rejected
   with patch.object(state,'open_record') as opened,patch.object(QDialog,'exec',summary):assert not state.summary(window,doc);assert opened.call_args.args[2]['page']==2
   for rotation in (0,90,180,270):
    doc[0].set_rotation(rotation)
    frozen=doc.tobytes(no_new_id=True)
    def zoom(dialog):
     dialog.show();app.processEvents()
     preview=dialog.findChild(QLabel,'scanExcerpt');plus=dialog.findChild(QPushButton,'scanZoomIn');minus=dialog.findChild(QPushButton,'scanZoomOut');fit=dialog.findChild(QPushButton,'scanFit')
     fit.click();initial=preview.pixmap().size();plus.click();assert preview.pixmap().width()>initial.width()
     for _ in range(8):plus.click()
     assert not plus.isEnabled();assert preview.pixmap().width()<=4098 and preview.pixmap().height()<=4098
     for _ in range(8):minus.click()
     assert not minus.isEnabled();fit.click();assert preview.pixmap().size()==initial
     table=dialog.findChild(QTableWidget,'correctionComparison');table.setCurrentCell(1,0);assert not preview.pixmap().isNull()
     if rotation==0:capture(dialog,code,'zoom')
     return QDialog.DialogCode.Rejected
    with patch.object(QDialog,'exec',zoom):assert not state.compare_corrections(window,doc,{0:['Corrected'],2:['Second']})
    assert doc.tobytes(no_new_id=True)==frozen
   doc[0].set_rotation(0)
  window.deleteLater();app.processEvents()
 print('20 languages: zoom limits/fit/rotation, cross-page Unicode search/drafts/wrap/empty, pending navigation/unknown/stale records passed')

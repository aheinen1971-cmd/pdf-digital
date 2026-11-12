import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings,Qt
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtWidgets import QApplication,QDialog,QPushButton,QTableWidget,QLabel,QDialogButtonBox
import pdf_werkbank as m
import ocr_confidence as c
import ocr_review_state as state
from page_tools import ocr_lines
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 p=Path('C:/Windows/Fonts')/name
 if p.exists():QFontDatabase.addApplicationFont(str(p))
app.setFont(QFont('Segoe UI',10))
root=Path(m.__file__).parent;out=root/'tests/navigation-results';out.mkdir(exist_ok=True)
def capture(dialog,code,name):
 dialog.show();app.processEvents()
 if code in ('de','ar','ja'):dialog.grab().save(str(out/(name+'-'+code+'.png')))
with TemporaryDirectory() as folder,pdf.open() as doc:
 page=doc.new_page(width=300,height=300)
 with pdf.open() as seed:
  seed.new_page(width=300,height=300).insert_text((20,40),'Original scan')
  page.insert_image(page.rect,pixmap=seed[0].get_pixmap())
 page.insert_text((20,40),'Original OCR',render_mode=3)
 doc.new_page();page=doc[0];pix=page.get_pixmap();before=doc.tobytes(no_new_id=True)
 words=[dict(text='one',score=10,box=[10,10,40,25]),dict(text='two',score=20,box=[10,80,40,95])]
 r=dict(doc=state.document_key(doc),page=0,language='eng',words=state.word_key(words,'eng'),scores=[10,20],reviewed=[0],threshold=80,position=0)
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):window=m.Window()
  for key in ('Zur offenen Stelle','Gespeicherte Prüfstände','Öffnen ist nur für das unveränderte aktuelle Dokument möglich. Alte Prüfstände lassen sich löschen. Es werden keine Dateinamen oder Texte gespeichert.','Gespeichert am','Aktuelles Dokument','OCR-Korrekturen vergleichen','Originaltext','Korrigierter Text','Zurück zur Bearbeitung'):
   assert code=='de' or window.ui(key)!=key,(code,key)
  with patch.object(state,'settings',return_value=settings):
   state.write(r);assert state.read()[0]['saved_at']
   def resumed(dialog):
    assert dialog.findChild(QLabel,'reviewCount').text()==window.ui('{0} von {1} geprüft').format(1,2)
    assert dialog.findChild(QTableWidget).currentRow()==1
    return QDialog.DialogCode.Rejected
   with patch.object(c,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',resumed):state.open_record(window,doc,r,True)
   def summary(dialog):
    table=dialog.findChild(QTableWidget);button=dialog.findChild(QPushButton,'openReviewItem')
    table.setCurrentCell(1,0);assert not button.isEnabled()
    table.setCurrentCell(0,0);assert button.isEnabled();capture(dialog,code,'summary');button.click()
    return QDialog.DialogCode.Rejected
   with patch.object(state,'open_record') as opened,patch.object(QDialog,'exec',summary):
    assert not state.summary(window,doc);assert opened.call_count==1;assert opened.call_args.kwargs['first_open']
   other=dict(r,doc='a'*64);state.write(other)
   def manager(dialog):
    table=dialog.findChild(QTableWidget,'checkpoints');opening=dialog.findChild(QPushButton,'openCheckpoint')
    table.setCurrentCell(0,0);assert not opening.isEnabled()
    table.setCurrentCell(1,0);assert opening.isEnabled();assert table.item(1,3).text()!='—'
    capture(dialog,code,'manager');opening.click()
    dialog.findChild(QPushButton,'deleteCheckpoint').click();assert len(state.read())==1 and state.read()[0]['doc']=='a'*64
    return QDialog.DialogCode.Rejected
   with patch.object(state,'open_record') as opened,patch.object(QDialog,'exec',manager):state.checkpoints(window,doc);assert opened.call_count==1
   # Old checkpoints without timestamps remain readable.
   settings.setValue(state.KEY,json.dumps([r]));assert 'saved_at' not in state.read()[0]
   for invalid in ('invalid',42,'2026-11-13T00:00:00'):
    settings.setValue(state.KEY,json.dumps([dict(r,saved_at=invalid)]))
    try:state.read()
    except (ValueError,TypeError):pass
    else:raise AssertionError('Invalid date accepted')
   settings.setValue(state.KEY,json.dumps([r]))
   with patch.object(window,'error') as error,patch.object(c,'show_review') as review:
    state.open_record(window,doc,other);error.assert_called_once();review.assert_not_called()
   altered=[dict(w) for w in words];altered[1]['text']='changed'
   def mismatch(dialog):
    assert dialog.findChild(QLabel,'reviewCount').text()==window.ui('{0} von {1} geprüft').format(0,2)
    assert any(l.text()==window.ui('Kein passender Prüfstand. Dokument oder Erkennung können verändert sein.') for l in dialog.findChildren(QLabel))
    return QDialog.DialogCode.Rejected
   with patch.object(c,'recognize',return_value=(altered,pix)),patch.object(QDialog,'exec',mismatch):state.open_record(window,doc,r,True)
   def comparison(dialog):
    table=dialog.findChild(QTableWidget,'correctionComparison');assert table.rowCount()==1
    assert table.item(0,1).text()=='Original OCR' and table.item(0,2).text()=='Corrected Ω العربية'
    assert dialog.layoutDirection()==window.layoutDirection();capture(dialog,code,'compare')
    return QDialog.DialogCode.Rejected
   with patch.object(QDialog,'exec',comparison):assert not state.compare_corrections(window,doc,{0:['Corrected Ω العربية']})
   with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Accepted):assert state.compare_corrections(window,doc,{0:['Corrected Ω العربية']})
   # Click actual apply button: cancelling comparison retains edits and dialog.
   def editor(dialog):
    table=dialog.findChild(QTableWidget);table.item(0,0).setText('Changed')
    buttons=dialog.findChild(QDialogButtonBox)
    with patch.object(state,'compare_corrections',return_value=False) as compare:
     buttons.button(QDialogButtonBox.StandardButton.Ok).click();assert dialog.result()!=QDialog.DialogCode.Accepted
     assert table.item(0,0).text()=='Changed';assert compare.call_args.args[2]=={0:['Changed']}
    with patch.object(state,'compare_corrections',return_value=True):buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    return dialog.result()
   with patch.object(QDialog,'exec',editor):assert window.review_ocr(doc,0)=={0:['Changed']}
   assert doc.tobytes(no_new_id=True)==before
  window.deleteLater();app.processEvents()
 print('20 languages: jump, checkpoints, legacy, stale rejection, comparison, cancel/apply, unchanged PDF passed')

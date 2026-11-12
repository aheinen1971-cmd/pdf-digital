from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings,Qt
from PySide6.QtGui import QFontDatabase,QFont,QKeyEvent
from PySide6.QtWidgets import QApplication,QDialog,QMessageBox,QPushButton,QCheckBox,QTableWidget,QLabel
import pdf_werkbank as m
import ocr_confidence as c
import ocr_review_state as state
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 path=Path('C:/Windows/Fonts')/name
 if path.exists():QFontDatabase.addApplicationFont(str(path))
app.setFont(QFont('Segoe UI',10))
root=Path(m.__file__).parent;out=root/'tests/safety-results';out.mkdir(exist_ok=True)
keys=['Nur offene Wörter anzeigen','Dein Prüffortschritt wurde geändert. Vor dem Schließen speichern?','Änderungen verwerfen','Weiterprüfen','Keine offenen Wörter unter diesem Prüfwert.','Scan-Ausschnitt der ausgewählten Zeile','Scan-Ausschnitt nicht verfügbar.']
with TemporaryDirectory() as folder,pdf.open() as doc,pdf.open() as seed:
 p=seed.new_page(width=300,height=300);p.insert_text((20,40),'Original scan')
 p=doc.new_page(width=300,height=300);p.insert_image(p.rect,pixmap=seed[0].get_pixmap());p.insert_text((20,40),'Original OCR',render_mode=3)
 words=[dict(text='one',score=10,box=[10,10,50,25]),dict(text='two',score=20,box=[10,70,50,85])]
 pix=p.get_pixmap();raw=doc.tobytes(no_new_id=True)
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):window=m.Window()
  for key in keys:assert code=='de' or window.ui(key)!=key,(code,key)
  with patch.object(state,'settings',return_value=settings):
   def action(dialog):
    dialog.show();app.processEvents()
    box=dialog.findChild(QCheckBox,'reviewedWord');pending=dialog.findChild(QCheckBox,'onlyOpenWords');table=dialog.findChild(QTableWidget)
    box.click();pending.click();assert table.rowCount()==1 and table.item(0,0).text()=='two'
    assert dialog.findChild(QLabel,'reviewCount').text()==window.ui('{0} von {1} geprüft').format(1,2)
    pending.click();assert table.rowCount()==2;table.setCurrentCell(0,0);box.click();assert not box.isChecked()
    box.click();pending.click();box.click();assert table.rowCount()==0
    assert not dialog.findChild(QPushButton,'nextWord').isEnabled()
    pending.click();assert table.rowCount()==2
    def choose(prompt,key):
     assert prompt.defaultButton().text()==window.ui('Weiterprüfen')
     prompt.show();app.processEvents()
     if code in ('de','ar','ja'):prompt.grab().save(str(out/('prompt-'+code+'.png')))
     next(b for b in prompt.buttons() if b.text()==window.ui(key)).click()
     return 0
    with patch.object(QMessageBox,'exec',lambda prompt:choose(prompt,'Weiterprüfen')):
     dialog.reject();assert dialog.isVisible()
     dialog.close();assert dialog.isVisible()
     app.sendEvent(dialog,QKeyEvent(QKeyEvent.Type.KeyPress,Qt.Key.Key_Escape,Qt.KeyboardModifier.NoModifier));assert dialog.isVisible()
    with patch.object(QMessageBox,'exec',lambda prompt:choose(prompt,'Prüfstand speichern')),patch.object(settings,'setValue',side_effect=OSError('fixture')),patch.object(window,'error') as error:
     dialog.reject();assert dialog.isVisible();error.assert_called_once()
    with patch.object(QMessageBox,'exec',lambda prompt:choose(prompt,'Prüfstand speichern')):
     dialog.reject();assert not dialog.isVisible();assert state.read()[0]['reviewed']==[0,1]
    return QDialog.DialogCode.Rejected
   with patch.object(c,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',action):c.show_review(window,doc[0],'eng',root/'tessdata')
   def discard(dialog):
    dialog.show();dialog.findChild(QCheckBox,'reviewedWord').click()
    def choose(prompt):
     next(b for b in prompt.buttons() if b.text()==window.ui('Änderungen verwerfen')).click();return 0
    with patch.object(QMessageBox,'exec',choose):dialog.accept();assert not dialog.isVisible()
    assert state.read()[0]['reviewed']==[0,1]
    assert list(window._ocr_review_memory.values())[-1]['reviewed']==[]
    return QDialog.DialogCode.Rejected
   with patch.object(c,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',discard):c.show_review(window,doc[0],'eng',root/'tessdata')
   def unchanged(dialog):
    dialog.show()
    with patch.object(QMessageBox,'exec') as prompt:dialog.reject();prompt.assert_not_called()
    return QDialog.DialogCode.Rejected
   with patch.object(c,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',unchanged):c.show_review(window,doc[0],'eng',root/'tessdata')
   for rotation in (0,90,180,270):
    doc[0].set_rotation(rotation)
    before=doc.tobytes(no_new_id=True)
    def compare(dialog):
     preview=dialog.findChild(QLabel,'scanExcerpt');assert preview.pixmap() is not None and not preview.pixmap().isNull()
     assert preview.pixmap().width()<=1402 and preview.pixmap().height()<=222
     assert preview.pixmap().toImage().bits().tobytes().count(b'\xff')<preview.pixmap().toImage().sizeInBytes()
     dialog.show();app.processEvents()
     if code in ('de','ar','ja') and rotation==0:dialog.grab().save(str(out/('scan-'+code+'.png')))
     return QDialog.DialogCode.Rejected
    with patch.object(QDialog,'exec',compare):assert not state.compare_corrections(window,doc,{0:['Correction']})
    assert doc.tobytes(no_new_id=True)==before
   doc[0].set_rotation(0)
  window.deleteLater();app.processEvents()
 print('20 languages: save/discard/continue, close/Escape, write failure, pending filter, reversible marks, four rotated scan excerpts passed')


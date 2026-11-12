# SPDX-License-Identifier: AGPL-3.0-only
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QListWidget, QTableWidget, QProgressDialog
import pdf_werkbank as m
from page_tools import ocr_lines, replace_ocr_layer
from ocr_languages import valid_language, choices, require_data

app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','msgothic.ttc','malgun.ttf'):
 path=Path('C:/Windows/Fonts')/name
 if path.exists():QFontDatabase.addApplicationFont(str(path))
app.setFont(QFont('Segoe UI',10))
root=Path(m.__file__).parent
screens=root/'tests/ocr-review-results';screens.mkdir(exist_ok=True)
texts=['Korrigiertes Dokument','Corrected document','Document corrigé','Documento corregido',
 'Gecorrigeerd document','Documento corretto','Documento corrigido','Poprawiony dokument',
 'Opravený dokument','Rettet dokument','Korrigerat dokument','Korrigert dokument',
 'Korjattu asiakirja','Düzeltilmiş belge','Виправлений документ','Διορθωμένο έγγραφο',
 '修正された文書','수정된 문서','المستند المصحح','המסמך המתוקן',
 'ABC المستند 123', 'המסמך ABC 123']
with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
 folder=Path(folder)
 for i,text in enumerate(texts):
  with pdf.open() as seed,pdf.open() as doc:
   p=seed.new_page();p.insert_text((45,90),'Original scan')
   page=doc.new_page();page.insert_image(page.rect,pixmap=p.get_pixmap())
   page.insert_text((45,90),'Wrong OCR',render_mode=3)
   page.set_rotation((i%4)*90)
   pixels=page.get_pixmap().samples
   replace_ocr_layer(page,[text])
   assert page.get_pixmap().samples==pixels
   target=folder/f'{i}.pdf';doc.save(target)
  with pdf.open(target) as saved:
   actual=' '.join(saved[0].get_text().split())
   assert actual==text,(text,actual)
   assert saved[0].search_for(text),(text,'not searchable')
   assert 'Wrong OCR' not in actual
   assert ocr_lines(saved[0]),'not editable a second time'
   revised=[line[3] for line in ocr_lines(saved[0])]
   revised[-1]+=' 123'
   replace_ocr_layer(saved[0],revised)
   assert ' '.join(saved[0].get_text().split())==text+' 123',(text,saved[0].get_text())
   if i==0:
    replace_ocr_layer(saved[0],['X'])
    assert saved[0].get_text().strip()=='X' and saved[0].search_for('X')
   before=saved[0].get_text()
   for bad in ('bad\nline','x'*10001):
    try:replace_ocr_layer(saved[0],[bad]*len(ocr_lines(saved[0])))
    except ValueError:pass
    else:raise AssertionError('invalid correction accepted')
    assert saved[0].get_text()==before
 assert valid_language('fra+eng')=='fra+eng'
 for invalid in ('','fra+fra','fra+../../x','fra++eng',None):assert valid_language(invalid)=='deu+eng'
 require_data('fra+eng',root/'tessdata')
 assert 'fra+eng' in choices(lambda s:s,'fra+eng').values()
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(folder/(code+'.ini')),QSettings.Format.IniFormat)
  settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):window=m.Window()
  window.error=lambda exc: (_ for _ in ()).throw(AssertionError(exc))
  for key in ('Eine oder mehrere Dokumentsprachen auswählen. Nur benötigte Sprachen wählen.',
              'Text ergänzt','Bereits durchsuchbar','Fehlgeschlagen','Nicht bearbeitet (Abbruch)','Zur Seite',
              'Wähle eine Zeile zum Markieren. Ein Doppelklick ändert nur die unsichtbare Textebene.',
              'Diese Seite hat keine bearbeitbare Scan-Textebene. Führe bei Bedarf zuerst OCR aus.',
              'Bitte pro Zeile höchstens 10000 Zeichen ohne Steuerzeichen eingeben.'):
   assert code=='de' or window.ui(key)!=key,(code,key)
  def pick(dialog):
   listing=dialog.findChild(QListWidget);buttons=dialog.findChild(QDialogButtonBox)
   for j in range(listing.count()):listing.item(j).setCheckState(Qt.CheckState.Unchecked)
   assert not buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
   for j in range(listing.count()):
    item=listing.item(j)
    if item.data(Qt.ItemDataRole.UserRole) in ('fra','eng'):item.setCheckState(Qt.CheckState.Checked)
   assert buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
   dialog.show();app.processEvents();dialog.grab().save(str(screens/f'languages-{code}.png'))
   return QDialog.DialogCode.Accepted
  with patch.object(QDialog,'exec',pick):window.choose_ocr_language()
  assert set(window.ocr_language().split('+'))=={'fra','eng'}
  prior=window.ocr_language()
  with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Rejected):window.choose_ocr_language()
  assert window.ocr_language()==prior
  path=folder/'mixed.pdf'
  with pdf.open() as doc,pdf.open() as seed:
   p=seed.new_page();p.insert_text((45,90),'Bonjour world document',fontsize=24)
   page=doc.new_page();page.insert_image(page.rect,pixmap=p.get_pixmap(dpi=250))
   doc.new_page().insert_text((45,90),'Already searchable')
   doc.new_page()
   doc.save(path)
  session=window.open_path(str(path));record=[]
  with patch.object(window,'ask_ocr_language',return_value=('fra+eng',True)),patch.object(window,'show_ocr_report',side_effect=lambda s,r:record.extend(r)):
   window.ocr(True)
  assert [r[1] for r in record]==['added','existing','failed'],record
  assert session.doc[0].search_for('Bonjour') and session.doc[0].search_for('world')
  pixels=session.doc[0].get_pixmap().samples
  original=path.read_bytes()
  replacement=texts[list(m.UI_LANGUAGES).index(code)]
  with patch.object(window,'review_ocr',return_value={0:[replacement]*len(ocr_lines(session.doc[0]))}):window.correct_ocr()
  assert session.doc[0].search_for(replacement),(code,replacement,session.doc[0].get_text())
  assert session.doc[0].get_pixmap().samples==pixels and path.read_bytes()==original
  window.undo();assert session.doc[0].search_for('Bonjour')
  window.redo();assert session.doc[0].search_for(replacement),(code,replacement,session.doc[0].get_text())
  def jump(dialog):
   table=dialog.findChild(QTableWidget)
   assert table.rowCount()==3
   assert table.item(2,1).text()==window.ui('Fehlgeschlagen')
   dialog.show();app.processEvents();dialog.grab().save(str(screens/f'report-{code}.png'))
   table.setCurrentCell(2,0)
   table.cellDoubleClicked.emit(2,0)
   return QDialog.DialogCode.Accepted
  with patch.object(QDialog,'exec',jump):window.show_ocr_report(session,record)
  assert session.page==2
  with patch.object(window,'ask_ocr_language',return_value=('fra+eng',True)),patch.object(window,'show_ocr_report') as report,patch.object(QProgressDialog,'wasCanceled',return_value=True):
   window.ocr(True)
  assert [r[1] for r in report.call_args.args[1]]==['cancelled']*3
  session.doc.close();window.sessions.clear();window.close();window.deleteLater();app.processEvents()
print('20 Unicode corrections/search/save/re-edit; mixed-language OCR, validation, 20 UI selection/report/jump/cancel passed.')

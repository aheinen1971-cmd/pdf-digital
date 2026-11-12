import json, re, hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QProgressDialog, QListWidget
import pdf_werkbank as m
from ocr_languages import LANGUAGES, choices, require_data

app=QApplication.instance() or QApplication([])
# Qt's headless platform does not discover Windows fonts automatically.
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','msgothic.ttc','malgun.ttf'):
 path=Path('C:/Windows/Fonts')/name
 if path.exists():QFontDatabase.addApplicationFont(str(path))
app.setFont(QFont('Segoe UI',10))
root=Path(m.__file__).parent
samples={
 'deu':('Das Dokument ist heute fertig.','Dokument'),
 'eng':('The document is ready today.','document'),
 'fra':('Le document est prêt pour demain.','document'),
 'spa':('El documento está listo para mañana.','documento'),
 'nld':('Het document is klaar voor morgen.','document'),
 'ita':('Il documento è pronto per domani.','documento'),
 'por':('O documento está pronto para amanhã.','documento'),
 'pol':('Dokument jest gotowy do wysłania.','Dokument'),
 'ces':('Dokument je připraven k odeslání.','Dokument'),
 'dan':('Dokumentet er klar til afsendelse.','Dokumentet'),
 'swe':('Dokumentet är klart att skickas.','Dokumentet'),
 'nor':('Dokumentet er klart til sending.','Dokumentet'),
 'fin':('Asiakirja on valmis lähetettäväksi.','Asiakirja'),
 'tur':('Belge bugün gönderilmeye hazır.','Belge'),
 'ukr':('Документ готовий до відправлення.','Документ'),
 'ell':('Το έγγραφο είναι έτοιμο σήμερα.','έγγραφο'),
 'jpn':('日本語の文書を確認します。','日本語'),
 'kor':('한국어 문서를 확인합니다.','한국어'),
 'ara':('هذا المستند جاهز للإرسال اليوم','المستند'),
 'heb':('המסמך מוכן לשליחה היום','המסמך'),
}
results=[]
output=root/'tests/ocr-language-results';output.mkdir(exist_ok=True)
for code,(text,keyword) in samples.items():
 with pdf.open() as source, pdf.open() as doc:
  page=source.new_page(width=600,height=220)
  direction='dir="rtl"' if code in ('ara','heb') else ''
  page.insert_htmlbox(pdf.Rect(25,25,575,200),f'<div {direction} style="font-size:24pt">{text}<br>{text}</div>')
  scan=doc.new_page(width=600,height=220)
  scan.insert_image(scan.rect,pixmap=page.get_pixmap(dpi=300))
  before=scan.get_pixmap().samples
  m.recognize_page(scan,code,root/'tessdata')
  assert scan.get_pixmap().samples==before, ('visible image changed',code)
  target=output/(code+'.pdf');doc.save(target)
 with pdf.open(target) as reopened:
  text=reopened[0].get_text(); print(code,repr(text),flush=True)
  assert '?' not in text, (code,text)
  assert keyword in text.replace(' ',''), (code,keyword,text)
  assert reopened[0].search_for(keyword), ('search failed',code)
  reopened[0].get_pixmap().save(output/(code+'.png'))
  results.append({'language':code,'text':text,'search':True,'renderUnchanged':True})

with TemporaryDirectory() as folder:
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat)
  settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):window=m.Window()
  labels=choices(window.ui);assert len(labels)==21
  assert window.ui('OCR-Sprache')!='OCR-Sprache' or code=='de'
  assert window.ui('Sprache des Dokuments')!='Sprache des Dokuments' or code=='de'
  assert window.layoutDirection()==(Qt.LayoutDirection.RightToLeft if code in ('ar','he') else Qt.LayoutDirection.LeftToRight)
  for label,language in labels.items():
   with patch.object(window,'ask_ocr_language',return_value=(label,True)):window.choose_ocr_language()
   assert window.ocr_language()==language
  with patch.object(window,'ask_ocr_language',return_value=('',False)):window.choose_ocr_language()
  assert window.ocr_language()=='heb'
  settings.setValue('ocr/language','../../bad');assert window.ocr_language()=='deu+eng'
  dialog_checks=[]
  def inspect_dialog():
   dialog=app.activeModalWidget()
   if isinstance(dialog,QDialog):
    combo=dialog.findChild(QListWidget)
    dialog_checks.append(combo is not None and combo.count()==20)
    dialog.grab().save(str(output/('dialog-'+code+'.png')))
    dialog.reject()
   else:
    dialog_checks.append(False)
  QTimer.singleShot(150,inspect_dialog)
  window.choose_ocr_language()
  assert dialog_checks==[True],code
  window.close();window.deleteLater();app.processEvents()
 try:require_data('fra',Path(folder))
 except ValueError:pass
 else:raise AssertionError('Missing model accepted')
for row in (root/'DEPENDENCIES.tsv').read_text(encoding='utf-8-sig').splitlines():
 fields=row.split('\t')
 if len(fields)==5 and fields[1]=='data':
  assert hashlib.sha256((root/'tessdata'/fields[2]).read_bytes()).hexdigest()==fields[3]

# Failed or cancelled recognition must not mutate the document or undo history.
with TemporaryDirectory() as folder:
 path=Path(folder)/'mixed.pdf'
 with pdf.open() as doc, pdf.open() as seed:
  p=seed.new_page();p.insert_text((45,90),'Bonjour document français',fontsize=24)
  p=doc.new_page();p.insert_image(p.rect,pixmap=seed[0].get_pixmap(dpi=250))
  doc.new_page().insert_text((45,90),'Already searchable')
  doc.save(path)
 settings=QSettings(str(Path(folder)/'ui.ini'),QSettings.Format.IniFormat)
 with patch.object(m,'QSettings',return_value=settings):window=m.Window()
 window.open_path(str(path));session=window.current
 original=path.read_bytes();before=session.doc[0].get_pixmap().samples
 window.favorite_settings.setValue('ocr/language','fra')
 label=next(k for k,v in choices(window.ui).items() if v=='fra')
 with patch.object(window,'ask_ocr_language',return_value=(label,True)),patch.object(window,'show_ocr_report'):
  window.ocr(True)
 assert session.doc[0].search_for('Bonjour')
 assert session.doc[1].get_text().strip()=='Already searchable'
 assert session.doc[0].get_pixmap().samples==before
 window.undo();assert not session.doc[0].get_text().strip()
 window.redo();assert session.doc[0].search_for('Bonjour')
 window.undo();history=len(session.history)
 with patch.object(window,'ask_ocr_language',return_value=(label,True)),patch.object(window,'show_ocr_report'),patch.object(QProgressDialog,'wasCanceled',return_value=True):
  window.ocr(True)
 assert not session.doc[0].get_text().strip() and len(session.history)==history
 with patch.object(window,'ask_ocr_language',return_value=(label,True)),patch.object(window,'show_ocr_report'),patch.object(m,'require_data',side_effect=ValueError('Missing model')):
  window.ocr(True)
 assert not session.doc[0].get_text().strip() and len(session.history)==history
 assert path.read_bytes()==original
 window.sessions.clear();window.close();window.deleteLater();app.processEvents()
(output/'report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print('20 real OCR languages, saved text/search, unchanged rendering, 20 UI languages and model digests passed.')



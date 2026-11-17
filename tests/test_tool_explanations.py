import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PySide6.QtCore import QSettings,Qt
from PySide6.QtWidgets import QApplication,QDialog,QLineEdit,QListWidget,QLabel,QDialogButtonBox
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
expected={'Seiten extrahieren':'Ausgewählte Seiten als eigene PDF speichern.','PDF anhängen':'Eine weitere PDF an das aktuelle Dokument anhängen.','Scan-PDF verkleinern':'Die Dateigröße einer gescannten PDF verringern. Die Bildqualität kann sich ändern.','OCR für das Dokument':'Text in gescannten Seiten erkennen und durchsuchbar machen. Ergebnisse anschließend prüfen.','Drucken':'Seitenvorschau und Druckeinstellungen öffnen, bevor du den Druckauftrag sendest.','Kopie speichern':'Das bearbeitete Dokument als PDF-Datei speichern. Den Zielnamen im Speicherdialog prüfen.','Update':'Nach einer neuen Programmversion suchen und die Aktualisierung vorbereiten.'}
with TemporaryDirectory() as temp:
 for lang in m.UI_LANGUAGES:
  settings=QSettings(str(Path(temp)/(lang+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',lang)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  calls=[];original=dict(w.favorite_commands);before={k:settings.value(k) for k in settings.allKeys()}
  w.favorite_commands={k:(label,lambda k=k:calls.append(k)) for k,(label,_) in original.items()}
  w.open_update_center=lambda:calls.append('update-center')
  def inspect(d):
   q=d.findChild(QLineEdit,'toolSearchQuery');rows=d.findChild(QListWidget,'toolSearchResults');hint=d.findChild(QLabel,'toolSearchExplanation')
   assert hint.wordWrap() and hint.textFormat()==Qt.TextFormat.PlainText
   assert rows.currentItem() is None
   for label,source in expected.items():
    q.clear()
    matches=[i for i in range(rows.count()) if rows.item(i).text()==w.ui(label)]
    assert matches,(lang,label)
    rows.setCurrentRow(matches[0]);app.processEvents()
    assert hint.text()==w.ui(source),(lang,label,hint.text())
    if lang!='de':assert hint.text()!=source,(lang,label)
    assert not calls
   q.setText('not-a-tool-88343');assert rows.count()==0 and hint.text()==w.ui('Wähle ein Werkzeug, um die Erklärung zu sehen.')
   q.clear();assert rows.currentItem() is None
   for i in range(rows.count()):
    if rows.item(i).text()==w.ui('Seiten extrahieren'):rows.setCurrentRow(i);break
   d.show();app.processEvents()
   assert hint.width()>150 and hint.height()>=hint.fontMetrics().height()
   if lang in ('de','ar','ja'):
    out=Path(os.environ['APPDATA'])/'explanation-views';out.mkdir(exist_ok=True);assert d.grab().save(str(out/(lang+'.png')))
   return QDialog.DialogCode.Accepted
  with patch.object(QDialog,'exec',inspect):w.search_tools()
  assert len(calls)==1 and original[calls[0]][0]=='Seiten extrahieren'
  with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Rejected):w.search_tools()
  assert len(calls)==1 and before=={k:settings.value(k) for k in settings.allKeys()}
  w.close();w.deleteLater();app.processEvents()
print('PASS 20 languages: explanations, plain text, selection/filter reset, explicit invocation/cancel, settings preservation and layout.')

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
pairs={
 'Seiten extrahieren':'Seiten rausnehmen herausnehmen herausholen abtrennen einzeln speichern',
 'PDF anhängen':'PDF zusammenfügen verbinden kombinieren zusammenführen',
 'Scan-PDF verkleinern':'Dateigröße reduzieren komprimieren kleinere Datei',
 'OCR für das Dokument':'Text erkennen Texterkennung Scan durchsuchbar machen',
 'Drucken':'Papier ausdrucken Druckvorschau',
 'Kopie speichern':'Speichern unter Datei kopieren neue Kopie',
}
with TemporaryDirectory() as temp:
 for lang in m.UI_LANGUAGES:
  settings=QSettings(str(Path(temp)/(lang+'.ini')),QSettings.Format.IniFormat)
  settings.setValue('view/language',lang)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  before={k:settings.value(k) for k in settings.allKeys()};calls=[]
  original=dict(w.favorite_commands)
  w.favorite_commands={k:(label,lambda k=k:calls.append(k)) for k,(label,_) in original.items()}
  def inspect(d):
   q=d.findChild(QLineEdit,'toolSearchQuery');rows=d.findChild(QListWidget,'toolSearchResults')
   for label,source in pairs.items():
    translated=w.ui(source)
    if lang!='de':assert translated!=source,(lang,label)
    q.setText(translated.upper())
    found=[rows.item(i).data(Qt.ItemDataRole.UserRole) for i in range(rows.count())]
    assert any(original[k][0]==label for k in found),(lang,label,translated,found)
    assert not calls and rows.currentItem() is None
   q.setText('!unknown-tool-xyz!');assert rows.count()==0
   q.clear();assert rows.count()>=len(original)
   q.setText(w.ui(pairs['Seiten extrahieren']))
   if lang in ('de','ar','ja'):
    d.show();app.processEvents()
    output=Path(os.environ['APPDATA'])/'alias-views';output.mkdir(exist_ok=True)
    assert d.grab().save(str(output/(lang+'.png')))
   for i in range(rows.count()):
    if original[rows.item(i).data(Qt.ItemDataRole.UserRole)][0]=='Seiten extrahieren':rows.setCurrentRow(i);break
   return QDialog.DialogCode.Accepted
  with patch.object(QDialog,'exec',inspect):w.search_tools()
  assert len(calls)==1 and original[calls[0]][0]=='Seiten extrahieren'
  assert before=={k:settings.value(k) for k in settings.allKeys()}
  w.close();w.deleteLater();app.processEvents()
print('PASS six everyday search concepts in all 20 languages; explicit correct action only; favorites/settings unchanged.')

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PySide6.QtCore import QSettings,Qt
from PySide6.QtWidgets import QApplication,QDialog,QLineEdit,QListWidget,QDialogButtonBox
import pdf_werkbank as m
app=QApplication.instance() or QApplication([])
out=Path(os.environ['APPDATA'])/'search';out.mkdir(exist_ok=True)
with TemporaryDirectory() as temp:
 for lang in m.UI_LANGUAGES:
  settings=QSettings(str(Path(temp)/(lang+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',lang)
  with patch.object(m,'QSettings',return_value=settings):w=m.Window()
  before=settings.allKeys(),str(settings.value('toolbar/favorites'));calls=[]
  originals=dict(w.favorite_commands)
  w.favorite_commands={k:(label,lambda k=k:calls.append(k)) for k,(label,_) in originals.items()}
  def inspect(d):
   d.show();app.processEvents();q=d.findChild(QLineEdit,'toolSearchQuery');rows=d.findChild(QListWidget,'toolSearchResults')
   assert rows.count()>=len(originals) and not calls
   target=next(k for k,(label,_) in originals.items() if label=='Drucken')
   q.setText(w.ui('Drucken').upper());assert rows.count()>0
   assert not calls and rows.currentItem() is None
   q.setText('!noSuchTool42!');assert rows.count()==0 and rows.currentItem() is None
   q.clear();assert rows.count()>=len(originals)
   q.setText(w.ui('Drucken'))
   for i in range(rows.count()):
    if rows.item(i).data(Qt.ItemDataRole.UserRole)==target:rows.setCurrentRow(i);break
   assert rows.currentItem().data(Qt.ItemDataRole.UserRole)==target
   if lang in ('de','ar','ja'):d.grab().save(str(out/(lang+'.png')))
   return QDialog.DialogCode.Accepted
  with patch.object(QDialog,'exec',inspect):w.search_tools()
  assert len(calls)==1 and originals[calls[0]][0]=='Drucken'
  with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Rejected):w.search_tools()
  assert len(calls)==1
  assert before==(settings.allKeys(),str(settings.value('toolbar/favorites')))
  w.close();w.deleteLater();app.processEvents()
print('PASS all 20 languages: complete existing tool catalogue, localized case-insensitive search, no matches/clear, explicit selection only, exact command/cancel, preferences preserved.')

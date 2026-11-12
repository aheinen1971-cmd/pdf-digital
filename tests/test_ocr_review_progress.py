"""Real Qt interactions with synthetic pages and isolated settings in all UI languages."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings,QPoint
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtWidgets import QApplication,QDialog,QTableWidget,QSpinBox,QCheckBox,QPushButton,QLabel,QScrollArea
import pdf_werkbank as m
import ocr_confidence as c

app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','msgothic.ttc','malgun.ttf'):
    font=Path('C:/Windows/Fonts')/name
    if font.exists():QFontDatabase.addApplicationFont(str(font))
app.setFont(QFont('Segoe UI',10))
root=Path(m.__file__).parent
output=root/'tests/progress-results';output.mkdir(parents=True,exist_ok=True)
catalog=json.loads((root/'ui_languages.json').read_text(encoding='utf-8'))
keys=['Nächstes Wort','Als geprüft markieren','{0} von {1} geprüft',
      'Der Zähler gilt für die gefilterten Wörter in diesem Fenster. Als geprüft markieren bestätigt nur deine Durchsicht, keine automatische Korrektur.']
for lang,values in catalog.items():
    assert all(values.get(key) for key in keys),lang
    assert values[keys[2]].format(2,3)

with TemporaryDirectory() as folder,pdf.open() as doc:
    page=doc.new_page(width=500,height=800)
    page.insert_text((30,40),'same same third fourth')
    before=doc.tobytes(no_new_id=True)
    pix=page.get_pixmap()
    words=[{'text':text,'score':score,'box':[20,30+100*i,80,50+100*i]}
           for i,(text,score) in enumerate([('same',20),('same',40),('third',70),('fourth',90)])]
    for code in m.UI_LANGUAGES:
        settings=QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat)
        settings.setValue('view/language',code)
        with patch.object(m,'QSettings',return_value=settings): window=m.Window()
        def inspect(dialog):
            table=dialog.findChild(QTableWidget);limit=dialog.findChild(QSpinBox)
            mark=dialog.findChild(QCheckBox,'reviewedWord');nxt=dialog.findChild(QPushButton,'nextWord')
            count=dialog.findChild(QLabel,'reviewCount')
            def check(x,y):assert count.text()==window.ui(keys[2]).format(x,y),(code,count.text(),x,y)
            assert dialog.layoutDirection()==window.layoutDirection()
            assert nxt.text()==window.ui(keys[0]) and mark.text()==window.ui(keys[1])
            check(0,3);assert table.currentRow()==0
            nxt.click();assert table.currentRow()==1;check(0,3)
            mark.click();check(1,3)
            mark.click();check(0,3)
            mark.click();nxt.click();assert table.currentRow()==2
            nxt.click();assert table.currentRow()==0 # skips reviewed duplicate at row1 on subsequent wrap
            mark.click();check(2,3)
            nxt.click();assert table.currentRow()==2
            mark.click();check(3,3);assert not nxt.isEnabled()
            mark.click();check(2,3);assert nxt.isEnabled()
            limit.setValue(30);check(1,1);assert mark.isChecked() and not nxt.isEnabled()
            limit.setValue(0);check(0,0);assert not mark.isEnabled() and not nxt.isEnabled()
            limit.setValue(100);check(2,4);assert mark.isChecked()
            nxt.click();assert table.currentRow()==2
            mark.click();nxt.click();assert table.currentRow()==3
            mark.click();check(4,4);assert not nxt.isEnabled()
            table.setCurrentCell(1,0);mark.click();check(3,4)
            nxt.click();assert table.currentRow()==1 # only unreviewed word
            dialog.resize(720,650);dialog.show();app.processEvents()
            table.setCurrentCell(0,0);table.setCurrentCell(1,0);app.processEvents()
            area=dialog.findChild(QScrollArea)
            point=area.widget().mapTo(area.viewport(),QPoint(47,131))
            assert 0 <= point.x() < area.viewport().width(),(code,point,area.viewport().size())
            if code in ('de','ar','ja'):assert dialog.grab().save(str(output/(code+'.png')))
            return QDialog.DialogCode.Rejected
        with patch.object(c,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',inspect):
            assert c.show_review(window,page,'eng',root/'tessdata') is None
        def reopened(dialog):
            assert dialog.findChild(QLabel,'reviewCount').text()==window.ui(keys[2]).format(0,3)
            return QDialog.DialogCode.Rejected
        with patch.object(c,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',reopened):
            c.show_review(window,page,'eng',root/'tessdata')
        def empty(dialog):
            assert dialog.findChild(QLabel,'reviewCount').text()==window.ui(keys[2]).format(0,0)
            assert not dialog.findChild(QPushButton,'nextWord').isEnabled()
            assert not dialog.findChild(QCheckBox,'reviewedWord').isEnabled()
            return QDialog.DialogCode.Rejected
        with patch.object(c,'recognize',return_value=([],pix)),patch.object(QDialog,'exec',empty):
            c.show_review(window,page,'eng',root/'tessdata')
        assert doc.tobytes(no_new_id=True)==before
        window.close();window.deleteLater();app.processEvents()
print('PASS: 20 UI languages/RTL, explicit review only, reversible marking, duplicate identities, next/wrap/skip, threshold retention, empty/all complete, reset on reopen, PDF unchanged')

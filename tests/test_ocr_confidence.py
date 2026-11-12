# SPDX-License-Identifier: AGPL-3.0-only
import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtWidgets import QApplication, QDialog, QSpinBox, QTableWidget, QLabel, QPushButton, QScrollArea
import pdf_werkbank as m
import ocr_confidence as c

app = QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','msgothic.ttc','malgun.ttf'):
    path = Path('C:/Windows/Fonts') / name
    if path.exists(): QFontDatabase.addApplicationFont(str(path))
app.setFont(QFont('Segoe UI', 10))
root = Path(m.__file__).parent
output = root / 'tests/confidence-results'
output.mkdir(exist_ok=True)
tree = ast.parse((root / 'tests/test_ocr_languages.py').read_text(encoding='utf-8'))
samples = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(n, ast.Name) and n.id == 'samples' for n in node.targets))
results = []
for language, (text, keyword) in samples.items():
    with pdf.open() as doc:
        page = doc.new_page(width=600, height=220)
        direction = 'dir="rtl"' if language in ('ara','heb') else ''
        page.insert_htmlbox(pdf.Rect(25,25,575,200), f'<div {direction} style="font-size:24pt">{text}<br>{text}</div>')
        before = doc.tobytes(no_new_id=True)
        words, pix = c.recognize(page, language, root / 'tessdata')
        assert words and keyword in ''.join(w['text'] for w in words), (language, words)
        assert doc.tobytes(no_new_id=True) == before
        # Independent direct API call must return identical per-word values.
        import tesserocr
        with TemporaryDirectory() as reference, tesserocr.PyTessBaseAPI(path=str(root/'tessdata'), lang=language) as api:
            reference_image = str(Path(reference)/'scan.png')
            pix.save(reference_image)
            api.SetImageFile(reference_image)
            api.Recognize()
            if not api.AllWordConfidences():
                api.SetPageSegMode(tesserocr.PSM.SPARSE_TEXT)
                api.Recognize()
            scores = {}
            iterator = api.GetIterator()
            if iterator:
                while True:
                    if not iterator.Empty(tesserocr.RIL.WORD):
                        scores[tuple(iterator.BoundingBox(tesserocr.RIL.WORD))] = iterator.Confidence(tesserocr.RIL.WORD)
                    if not iterator.Next(tesserocr.RIL.WORD): break
        assert all(abs(w['score']-scores[tuple(w['box'])]) < .00001 for w in words), language
        results.append(dict(language=language, words=len(words), minimum=min(w['score'] for w in words)))
        print(language, 'passed', flush=True)

# Real low-score words exist in the multilingual fixtures, not simulated scores.
assert any(r['minimum'] < 80 for r in results)
with pdf.open() as doc:
    huge = doc.new_page(width=3000,height=3000)
    try:c.recognize(huge,'eng',root/'tessdata')
    except ValueError:pass
    else:raise AssertionError('pixel limit ignored')
with pdf.open() as doc:
    page = doc.new_page(width=300,height=150)
    assert c.recognize(page,'eng',root/'tessdata')[0] == []
    page.insert_text((20,60),'Document review 123', fontsize=20)
    assert c.recognize(page,'fra+eng',root/'tessdata')[0]
    page.set_rotation(90)
    words, pix = c.recognize(page,'eng',root/'tessdata')
    assert words
    folders=[]
    real_temp=c.tempfile.TemporaryDirectory
    def tracked_temp(*args,**kwargs):
        temp=real_temp(*args,**kwargs);folders.append(Path(temp.name));return temp
    with patch.object(c.tempfile,'TemporaryDirectory',tracked_temp):
        assert c.recognize(page,'eng',root/'tessdata',lambda:True) is None
    assert folders and all(not path.exists() for path in folders)
    try: c.recognize(page,'eng',root/'tessdata',timeout=-1)
    except TimeoutError: pass
    else: raise AssertionError('timeout ignored')
    with TemporaryDirectory() as empty:
        try: c.recognize(page,'eng',Path(empty))
        except ValueError: pass
        else: raise AssertionError('missing model accepted')
    with patch.object(c.subprocess,'Popen',side_effect=OSError('missing runtime')):
        try: c.recognize(page,'eng',root/'tessdata')
        except OSError: pass
        else: raise AssertionError('process failure ignored')
    # Every interface language, including both RTL layouts, with real OCR data.
    with TemporaryDirectory() as folder:
        for code in m.UI_LANGUAGES:
            settings = QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat)
            settings.setValue('view/language',code)
            with patch.object(m,'QSettings',return_value=settings): window=m.Window()
            def inspect(dialog):
                assert dialog.layoutDirection() == window.layoutDirection()
                table=dialog.findChild(QTableWidget); limit=dialog.findChild(QSpinBox)
                assert table is not None and limit is not None
                limit.setValue(100)
                assert table.rowCount() == sum(w['score'] < 100 for w in words)
                if table.rowCount(): table.setCurrentCell(0,0)
                limit.setValue(0); assert table.rowCount()==0
                assert any(label.text()==window.ui('Keine Wörter unter diesem Prüfwert.') for label in dialog.findChildren(QLabel))
                limit.setValue(100)
                dialog.show();app.processEvents()
                if table.rowCount():
                    table.setCurrentCell(table.rowCount()-1,0);app.processEvents()
                    assert dialog.findChild(QScrollArea).verticalScrollBar().value()>0
                dialog.grab().save(str(output/(code+'.png')))
                if code!='de': assert dialog.windowTitle()!='Unsichere Wörter prüfen'
                return QDialog.DialogCode.Rejected
            # The entry button invokes the selected language and current page.
            def entry(dialog):
                button=next(b for b in dialog.findChildren(QPushButton) if b.text()==window.ui('Unsichere Wörter prüfen'))
                button.click()
                return QDialog.DialogCode.Rejected
            with patch.object(window,'ask_ocr_language',return_value=('fra+eng',True)), patch.object(c,'show_review') as target, patch.object(QDialog,'exec',entry):
                assert window.review_ocr(doc,0)=={}
                assert target.call_count==1 and target.call_args.args[2]=='fra+eng'
            before=doc.tobytes(no_new_id=True)
            with patch.object(c,'recognize',return_value=(words,pix)), patch.object(QDialog,'exec',inspect):
                c.show_review(window,page,'eng',root/'tessdata')
            assert doc.tobytes(no_new_id=True)==before
            with patch.object(c,'recognize',side_effect=RuntimeError('private details')), patch.object(window,'error') as error:
                c.show_review(window,page,'eng',root/'tessdata')
                assert error.call_count==1 and 'private details' not in error.call_args.args[0]
            window.close();window.deleteLater();app.processEvents()

for bad in [float('nan'),float('inf'),-1,101]:
    try:c.validate([dict(text='test',score=bad,box=[0,0,10,10])],20,20)
    except ValueError:pass
    else:raise AssertionError('invalid score accepted')
for box in [[-1,0,10,10],[0,0,21,10],[10,0,0,10],[0,0,1.5,10]]:
    try:c.validate([dict(text='test',score=80,box=box)],20,20)
    except ValueError:pass
    else:raise AssertionError('invalid box accepted')
(output/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print('PASS: real OCR scores in 20 languages, combinations, rotation, empty page, cancellation, timeout, failures, 20 UIs, unchanged PDF')


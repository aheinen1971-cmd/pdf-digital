"""Opt-in local OCR review metadata. Never stores document paths or recognized text."""
import hashlib
import json

KEY = 'ocr/review-checkpoints-v1'

def document_key(doc):
    return hashlib.sha256(doc.tobytes(no_new_id=True)).hexdigest()

def word_key(words, language):
    return hashlib.sha256(json.dumps([language, words],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def settings():
    from PySide6.QtCore import QSettings
    return QSettings('PDF Werkbank','PDF Werkbank')

def read():
    raw=settings().value(KEY,'[]',str)
    if len(raw)>2000000:raise ValueError('Invalid review data')
    rows=json.loads(raw)
    if not isinstance(rows,list) or len(rows)>100:raise ValueError('Invalid review data')
    for r in rows:
        if not isinstance(r,dict) or set(r)!={'doc','page','words','language','scores','reviewed','threshold','position'}:raise ValueError('Invalid review data')
        if any(not isinstance(r[k],str) or len(r[k])!=64 or any(c not in '0123456789abcdef' for c in r[k]) for k in ('doc','words')):raise ValueError('Invalid review data')
        if type(r['page']) is not int or not 0<=r['page']<100000:raise ValueError('Invalid review data')
        if not isinstance(r['language'],str) or len(r['language'])>200:raise ValueError('Invalid review data')
        scores=r['scores'];ids=r['reviewed']
        if not isinstance(scores,list) or len(scores)>50000 or any(type(x) not in (int,float) or not 0<=x<=100 for x in scores):raise ValueError('Invalid review data')
        if not isinstance(ids,list) or len(ids)>len(scores) or any(type(x) is not int or not 0<=x<len(scores) for x in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid review data')
        if type(r['threshold']) is not int or not 0<=r['threshold']<=100 or type(r['position']) is not int or not -1<=r['position']<max(1,len(scores)):raise ValueError('Invalid review data')
    return rows

def write(record,remove=False):
    s=settings();before=s.value(KEY,'[]',str)
    rows=[r for r in read() if (r['doc'],r['page'],r['language'])!=(record['doc'],record['page'],record['language'])]
    if not remove:rows.append(record)
    raw=json.dumps(rows[-100:],separators=(',',':'))
    if len(raw)>2000000:raise ValueError('Review storage full')
    s.setValue(KEY,raw);s.sync()
    if s.status()!=s.Status.NoError:
        s.setValue(KEY,before);s.sync();raise OSError('Review storage failed')

def summary(parent,doc):
    """Ask only when OCR review metadata exists; current document hash is mandatory."""
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QDialogButtonBox
    tr=parent.ui
    problem=False
    try:rows=read()
    except (ValueError,OSError,TypeError):rows=[];problem=True
    rows+=list(getattr(parent,'_ocr_review_memory',{}).values())
    if not rows and not problem:return True
    key=document_key(doc);matched={r['page']:r for r in rows if r['doc']==key}
    dialog=QDialog(parent);dialog.setWindowTitle(tr('OCR-Prüfübersicht'));dialog.setLayoutDirection(parent.layoutDirection())
    dialog.resize(650,480);layout=QVBoxLayout(dialog)
    note=QLabel(tr('Nur bestätigte Wortprüfungen zählen. Geänderte Dokumente müssen erneut geprüft werden. Speichern bleibt möglich.'))
    note.setWordWrap(True);layout.addWidget(note)
    if problem:
        error=QLabel(tr('Prüfstand konnte nicht gelesen oder gespeichert werden.'));error.setWordWrap(True);layout.addWidget(error)
    table=QTableWidget(len(doc),5);table.setHorizontalHeaderLabels([tr('Seite'),tr('Geprüft'),tr('Offen'),tr('Prüfwert unter'),tr('OCR-Sprache')]);layout.addWidget(table)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    for page in range(len(doc)):
        r=matched.get(page);done=total=0
        if r:
            visible={i for i,s in enumerate(r['scores']) if s<r['threshold']}
            total=len(visible);done=len(visible.intersection(r['reviewed']))
        from ocr_languages import LANGUAGES
        language=' + '.join(LANGUAGES.get(code,code) for code in r['language'].split('+')) if r else '—'
        for col,value in enumerate([str(page+1),tr('{0} von {1} geprüft').format(done,total) if r else tr('Nicht geprüft'),str(total-done) if r else '—',str(r['threshold']) if r else '—',language]):
            table.setItem(page,col,QTableWidgetItem(value))
    table.resizeColumnsToContents()
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr('Speichern'))
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('Abbrechen'))
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    return dialog.exec()==QDialog.DialogCode.Accepted

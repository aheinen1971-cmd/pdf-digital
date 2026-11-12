"""Opt-in local OCR review metadata. Never stores document paths or recognized text."""
import hashlib
import json
from datetime import datetime, timezone

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
        if not isinstance(r,dict) or set(r) not in ({'doc','page','words','language','scores','reviewed','threshold','position'}, {'doc','page','words','language','scores','reviewed','threshold','position','saved_at'}):raise ValueError('Invalid review data')
        if any(not isinstance(r[k],str) or len(r[k])!=64 or any(c not in '0123456789abcdef' for c in r[k]) for k in ('doc','words')):raise ValueError('Invalid review data')
        if type(r['page']) is not int or not 0<=r['page']<100000:raise ValueError('Invalid review data')
        if not isinstance(r['language'],str) or len(r['language'])>200:raise ValueError('Invalid review data')
        if 'saved_at' in r:
            if not isinstance(r['saved_at'],str) or len(r['saved_at'])>40:raise ValueError('Invalid review date')
            date=datetime.fromisoformat(r['saved_at'])
            if date.tzinfo is None:raise ValueError('Invalid review date')
        scores=r['scores'];ids=r['reviewed']
        if not isinstance(scores,list) or len(scores)>50000 or any(type(x) not in (int,float) or not 0<=x<=100 for x in scores):raise ValueError('Invalid review data')
        if not isinstance(ids,list) or len(ids)>len(scores) or any(type(x) is not int or not 0<=x<len(scores) for x in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid review data')
        if type(r['threshold']) is not int or not 0<=r['threshold']<=100 or type(r['position']) is not int or not -1<=r['position']<max(1,len(scores)):raise ValueError('Invalid review data')
    return rows

def write(record,remove=False):
    s=settings();before=s.value(KEY,'[]',str)
    rows=[r for r in read() if (r['doc'],r['page'],r['language'])!=(record['doc'],record['page'],record['language'])]
    if not remove:rows.append(dict(record,saved_at=datetime.now(timezone.utc).isoformat(timespec='seconds')))
    raw=json.dumps(rows[-100:],separators=(',',':'))
    if len(raw)>2000000:raise ValueError('Review storage full')
    s.setValue(KEY,raw);s.sync()
    if s.status()!=s.Status.NoError:
        s.setValue(KEY,before);s.sync();raise OSError('Review storage failed')

def summary(parent,doc):
    """Ask only when OCR review metadata exists; current document hash is mandatory."""
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QDialogButtonBox,QPushButton
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
    jump=QPushButton(tr('Zur offenen Stelle'));jump.setObjectName('openReviewItem');layout.addWidget(jump)
    def selected():
        r=matched.get(table.currentRow())
        return r if r and any(score<r['threshold'] and i not in r['reviewed'] for i,score in enumerate(r['scores'])) else None
    jump.setEnabled(False)
    table.currentCellChanged.connect(lambda *_: jump.setEnabled(selected() is not None))
    def navigate():
        r=selected()
        if r is None:return
        dialog.reject()
        open_record(parent,doc,r,first_open=True)
    jump.clicked.connect(navigate)
    table.cellDoubleClicked.connect(lambda *_: navigate())
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr('Speichern'))
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('Abbrechen'))
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    return dialog.exec()==QDialog.DialogCode.Accepted


def open_record(parent,doc,record,first_open=False):
    """Re-recognize and require both document and recognition identity before resuming."""
    from pathlib import Path
    from ocr_confidence import show_review
    if record['doc']!=document_key(doc) or not 0<=record['page']<len(doc):
        parent.error(parent.ui('Kein passender Prüfstand. Dokument oder Erkennung können verändert sein.'))
        return
    show_review(parent,doc[record['page']],record['language'],Path(__file__).with_name('tessdata'),
                resume_record=record,first_open=first_open)


def checkpoints(parent,doc):
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QPushButton,QDialogButtonBox
    tr=parent.ui;key=document_key(doc)
    dialog=QDialog(parent);dialog.setWindowTitle(tr('Gespeicherte Prüfstände'));dialog.setLayoutDirection(parent.layoutDirection());dialog.resize(800,500)
    layout=QVBoxLayout(dialog)
    note=QLabel(tr('Öffnen ist nur für das unveränderte aktuelle Dokument möglich. Alte Prüfstände lassen sich löschen. Es werden keine Dateinamen oder Texte gespeichert.'));note.setWordWrap(True);layout.addWidget(note)
    table=QTableWidget(0,5);table.setObjectName('checkpoints');table.setHorizontalHeaderLabels([tr('Seite'),tr('Geprüft'),tr('OCR-Sprache'),tr('Gespeichert am'),tr('Aktuelles Dokument')]);table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);layout.addWidget(table)
    rows=[]
    def refresh():
        rows[:]=list(reversed(read()));table.setRowCount(len(rows))
        for n,r in enumerate(rows):
            visible={i for i,s in enumerate(r['scores']) if s<r['threshold']}
            date=datetime.fromisoformat(r['saved_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S') if r.get('saved_at') else '—'
            values=[str(r['page']+1),tr('{0} von {1} geprüft').format(len(visible.intersection(r['reviewed'])),len(visible)),r['language'],date,'✓' if r['doc']==key else '—']
            for col,value in enumerate(values):table.setItem(n,col,QTableWidgetItem(value))
        table.resizeColumnsToContents();update()
    opening=QPushButton(tr('Prüfung fortsetzen'));opening.setObjectName('openCheckpoint');layout.addWidget(opening)
    deleting=QPushButton(tr('Prüfstand löschen'));deleting.setObjectName('deleteCheckpoint');layout.addWidget(deleting)
    def selected():return rows[table.currentRow()] if 0<=table.currentRow()<len(rows) else None
    def update(*_):
        r=selected();opening.setEnabled(bool(r and r['doc']==key and r['page']<len(doc)));deleting.setEnabled(r is not None)
    def action(remove=False):
        r=selected()
        if not r:return
        try:
            if remove:write(r,remove=True);refresh()
            elif r['doc']==key:
                open_record(parent,doc,r)
        except (ValueError,OSError,TypeError):parent.error(tr('Prüfstand konnte nicht gelesen oder gespeichert werden.'))
    opening.clicked.connect(lambda:action());deleting.clicked.connect(lambda:action(True));table.cellDoubleClicked.connect(lambda *_:action());table.currentCellChanged.connect(update)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr('Schließen'));buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    refresh();dialog.exec()


def compare_corrections(parent,doc,edits):
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QTableWidget,QTableWidgetItem,QDialogButtonBox,QHeaderView,QLabel,QScrollArea
    from PySide6.QtGui import QPixmap,QImage
    from PySide6.QtCore import Qt
    import pymupdf as pdf
    from page_tools import ocr_lines
    changes=[]
    for page,values in sorted(edits.items()):
        original=ocr_lines(doc[page])
        for index,value in enumerate(values):
            if value!=original[index][3]:changes.append((page+1,original[index][3],value,original[index][0]))
    if not changes:return True
    tr=parent.ui;dialog=QDialog(parent);dialog.setWindowTitle(tr('OCR-Korrekturen vergleichen'));dialog.setLayoutDirection(parent.layoutDirection());dialog.resize(1000,600)
    layout=QVBoxLayout(dialog);table=QTableWidget(len(changes),3);table.setObjectName('correctionComparison');table.setHorizontalHeaderLabels([tr('Seite'),tr('Originaltext'),tr('Korrigierter Text')]);table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    for row,values in enumerate(changes):
        for col,value in enumerate(values[:3]):table.setItem(row,col,QTableWidgetItem(str(value)))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);table.resizeRowsToContents();layout.addWidget(table)
    label=QLabel(tr('Scan-Ausschnitt der ausgewählten Zeile'));layout.addWidget(label)
    area=QScrollArea();area.setMinimumHeight(160);area.setMaximumHeight(260)
    preview=QLabel();preview.setObjectName('scanExcerpt');preview.setAlignment(Qt.AlignmentFlag.AlignCenter);area.setWidget(preview);layout.addWidget(area)
    def show_excerpt(row,*_):
        if not 0<=row<len(changes):return
        page_no,_,_,box=changes[row];page=doc[page_no-1]
        clip=(pdf.Rect(box)*page.rotation_matrix)+(-8,-8,8,8)
        clip=clip & page.rect
        try:
            scale=min(2.0,1400/max(1,clip.width),220/max(1,clip.height))
            pix=page.get_pixmap(matrix=pdf.Matrix(scale,scale),clip=clip,colorspace=pdf.csRGB,alpha=False)
            shown=QPixmap.fromImage(QImage(pix.samples,pix.width,pix.height,pix.stride,QImage.Format.Format_RGB888).copy())
            preview.setPixmap(shown);preview.resize(shown.size())
        except Exception:
            preview.clear();preview.setText(tr('Scan-Ausschnitt nicht verfügbar.'));preview.adjustSize()
    table.currentCellChanged.connect(show_excerpt);table.setCurrentCell(0,0)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr('Korrekturen übernehmen'));buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('Zurück zur Bearbeitung'));buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    return dialog.exec()==QDialog.DialogCode.Accepted

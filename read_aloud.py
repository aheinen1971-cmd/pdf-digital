# SPDX-License-Identifier: AGPL-3.0-only
"""Local Windows speech, no automatic playback or voice downloads."""
import json
from pathlib import Path

NOTICE = 'Markiere Text in dieser Vorschau oder lies die ganze Seite vor. Wähle eine passende installierte Windows-Stimme. Keine Übertragung ins Internet.'
UNAVAILABLE = 'Vorlesen nicht verfügbar. Prüfe die installierten Windows-Stimmen und die Audioausgabe.'


def show_reader(parent, text):
    from PySide6.QtCore import QProcess, QTimer
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPlainTextEdit,
                                 QComboBox, QSpinBox, QPushButton, QHBoxLayout)
    tr = parent.ui
    if not text.strip():
        parent.error(tr('Kein Text zum Vorlesen. Bei Scans zuerst OCR ausführen.'))
        return
    if len(text) > 100000:
        parent.error(tr('Zu viel Text auf dieser Seite zum Vorlesen.'))
        return
    dialog = QDialog(parent);dialog.setWindowTitle(tr('Vorlesen'))
    dialog.setLayoutDirection(parent.layoutDirection());dialog.resize(720,560)
    layout = QVBoxLayout(dialog)
    notice = QLabel(tr(NOTICE));notice.setWordWrap(True);layout.addWidget(notice)
    preview = QPlainTextEdit();preview.setPlainText(text);preview.setReadOnly(True);layout.addWidget(preview)
    voices = QComboBox();voices.setAccessibleName(tr('Stimme'));layout.addWidget(voices)
    speed = QSpinBox();speed.setRange(-5,5);speed.setPrefix(tr('Tempo') + ' ');layout.addWidget(speed)
    status = QLabel(tr('Stimmen werden geladen …'));layout.addWidget(status)
    row = QHBoxLayout();layout.addLayout(row)
    buttons = {}
    for key in ['Vorlesen','Pause','Fortsetzen','Stoppen','Schließen']:
        button = QPushButton(tr(key));row.addWidget(button);buttons[key]=button
        button.setEnabled(key=='Schließen')
    process = QProcess(dialog)
    process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    timer = QTimer(dialog);timer.setInterval(300)
    closing = False
    failed = False
    def fail():
        nonlocal failed
        if failed:return
        failed=True
        process.kill()
        status.setText(tr(UNAVAILABLE));timer.stop()
        for key,button in buttons.items():button.setEnabled(key=='Schließen')
    def send(action, **values):
        if process.state()!=QProcess.ProcessState.Running:
            fail();return
        process.write((json.dumps(dict(action=action,**values),ensure_ascii=False)+'\n').encode('utf-8'))
    def receive():
        if failed:return
        while process.canReadLine():
            try:
                data=json.loads(bytes(process.readLine()).decode('utf-8'))
                if data.get('error'):fail();continue
                if 'voices' in data:
                    for voice in data['voices']:
                        voices.addItem(voice['name']+' · '+voice['culture'],voice['name'])
                    if not voices.count():fail();return
                    voices.setCurrentIndex(0)
                    timer.start()
                state=data.get('state','Ready')
                status.setText(tr({'Ready':'Bereit zum Vorlesen','Speaking':'Wird vorgelesen','Paused':'Vorlesen pausiert'}.get(state,'Bereit zum Vorlesen')))
                voices.setEnabled(state=='Ready')
                speed.setEnabled(state=='Ready')
                buttons['Vorlesen'].setEnabled(voices.count()>0)
                buttons['Pause'].setEnabled(state=='Speaking')
                buttons['Fortsetzen'].setEnabled(state=='Paused')
                buttons['Stoppen'].setEnabled(state in ('Speaking','Paused'))
            except (ValueError,KeyError,TypeError):fail()
    def start():
        selected=preview.textCursor().selectedText().replace('\u2029','\n')
        send('start',text=selected or text,voice=voices.currentData(),rate=speed.value())
    buttons['Vorlesen'].clicked.connect(start)
    buttons['Pause'].clicked.connect(lambda:send('pause'))
    buttons['Fortsetzen'].clicked.connect(lambda:send('resume'))
    buttons['Stoppen'].clicked.connect(lambda:send('stop'))
    buttons['Schließen'].clicked.connect(dialog.reject)
    process.readyReadStandardOutput.connect(receive)
    process.errorOccurred.connect(lambda *_:fail() if not closing else None)
    process.finished.connect(lambda *_:fail() if not closing else None)
    timer.timeout.connect(lambda:send('status'))
    startup=QTimer(dialog);startup.setSingleShot(True)
    startup.timeout.connect(lambda:fail() if not voices.count() else None);startup.start(10000)
    process.start(str(Path(__file__).with_suffix('.exe')),[])
    try:dialog.exec()
    finally:
        closing=True;timer.stop();startup.stop()
        if process.state()!=QProcess.ProcessState.NotRunning:
            process.write(b'{"action":"quit"}\n');process.closeWriteChannel()
            if not process.waitForFinished(1000):process.kill();process.waitForFinished(1000)

# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6 import QtCore
from PySide6.QtCore import QSettings, Qt, QObject, Signal, QByteArray
from PySide6.QtGui import QFontDatabase, QFont, QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTableWidget, QSpinBox, QPlainTextEdit, QComboBox
import pdf_werkbank as m
import ocr_confidence as confidence
import scan_review as quality
import read_aloud as speech

app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','msgothic.ttc','malgun.ttf'):
    path=Path('C:/Windows/Fonts')/name
    if path.exists():QFontDatabase.addApplicationFont(str(path))
app.setFont(QFont('Segoe UI',10))
root=Path(m.__file__).parent
catalog=json.loads((root/'ui_languages.json').read_text(encoding='utf-8'))
required=[quality.BLUR,quality.CONTRAST,quality.DARK,quality.NOTICE,speech.NOTICE,speech.UNAVAILABLE,
          'Möglicherweise leer','Möglicherweise schief: Seitenausrichtung prüfen.',
          'Keine zusätzlichen Auffälligkeiten erkannt. Bitte den Scan trotzdem ansehen.',
          'Zur Korrekturzeile','Nur eindeutig zugeordnete Textzeilen lassen sich direkt öffnen.',
          'Vorlesen','Scanqualität prüfen','Stimme','Tempo','Stimmen werden geladen …',
          'Pause','Fortsetzen','Stoppen','Bereit zum Vorlesen','Wird vorgelesen','Vorlesen pausiert',
          'Kein Text zum Vorlesen. Bei Scans zuerst OCR ausführen.','Zu viel Text auf dieser Seite zum Vorlesen.']
for code,values in catalog.items():assert all(key in values and values[key] for key in required),code
output=root/'tests/review-quality-speech-results';output.mkdir(exist_ok=True)

# Deterministic raster fixtures distinguish sharp, softened, faint and dark pages.
w=h=128
sharp=bytes(20 if 20<x<105 and y%24<12 else 240 for y in range(h) for x in range(w))
blurred=sharp
for _ in range(4):
    blurred=bytes(sum(blurred[yy*w+xx] for yy in range(max(0,y-2),min(h,y+3)) for xx in range(max(0,x-2),min(w,x+3)))//
                  ((min(h,y+3)-max(0,y-2))*(min(w,x+3)-max(0,x-2))) for y in range(h) for x in range(w))
assert quality.BLUR not in quality.raster_hints(sharp,w,h)
assert quality.BLUR in quality.raster_hints(blurred,w,h)
assert quality.CONTRAST in quality.raster_hints(bytes(190 if v<100 else 225 for v in sharp),w,h)
assert quality.DARK in quality.raster_hints(bytes([45])*(w*h),w,h)
assert quality.raster_hints(bytes([255])*(w*h),w,h)==[]
assert quality.raster_hints(b'',0,0)==[]

with pdf.open() as doc:
    page=doc.new_page(width=300,height=200)
    box=pdf.Rect(30,40,180,65);line=(box,0,0,'Test')
    for rotation in (0,90,180,270):
        page.set_rotation(rotation)
        pixel_box=box*page.rotation_matrix*pdf.Matrix(200/72,200/72)
        assert confidence.correction_line(page,pixel_box,[line])==0
        assert confidence.correction_line(page,pixel_box,[line,line]) is None
        assert confidence.correction_line(page,pdf.Rect(0,0,5,5),[line]) is None
    page.set_rotation(0)
    page.insert_text((30,60),'Test document',fontsize=18)
    original=doc.tobytes(no_new_id=True)
    quality.quality_hints(page)
    assert doc.tobytes(no_new_id=True)==original
    pix=page.get_pixmap(dpi=200,alpha=False)
    words=[dict(text='Test',score=35,box=list(box*pdf.Matrix(200/72,200/72)))]

    RealProcess=QtCore.QProcess
    class FakeProcess(QObject):
        readyReadStandardOutput=Signal();errorOccurred=Signal(int);finished=Signal(int,int)
        ProcessState=RealProcess.ProcessState;ProcessChannelMode=RealProcess.ProcessChannelMode
        instances=[]
        def __init__(self,*args):
            super().__init__(*args);self.pending=[];self.commands=[];self.running=False;self.instances.append(self)
        def setProcessChannelMode(self,*args):pass
        def state(self):return self.ProcessState.Running if self.running else self.ProcessState.NotRunning
        def reply(self,data):self.pending.append(json.dumps(data).encode());self.readyReadStandardOutput.emit()
        def start(self,*args):
            self.running=True;self.reply(dict(voices=[dict(name='Test voice',culture='de-DE')],state='Ready'))
        def write(self,raw):
            data=json.loads(bytes(raw));self.commands.append(data)
            state={'start':'Speaking','pause':'Paused','resume':'Speaking','stop':'Ready'}.get(data['action'],'Ready')
            if data['action']=='quit':self.running=False
            self.reply(dict(state=state));return len(raw)
        def canReadLine(self):return bool(self.pending)
        def readLine(self):return QByteArray(self.pending.pop(0))
        def closeWriteChannel(self):pass
        def waitForFinished(self,*args):return not self.running
        def kill(self):self.running=False

    with TemporaryDirectory() as folder:
        for code in m.UI_LANGUAGES:
            settings=QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat)
            settings.setValue('view/language',code)
            with patch.object(m,'QSettings',return_value=settings):window=m.Window()
            def review(dialog):
                table=dialog.findChild(QTableWidget);table.setCurrentCell(0,0)
                button=next(b for b in dialog.findChildren(QPushButton) if b.text()==window.ui('Zur Korrekturzeile'))
                assert button.isEnabled();button.click()
                return QDialog.DialogCode.Accepted
            with patch.object(confidence,'recognize',return_value=(words,pix)),patch.object(QDialog,'exec',review):
                assert confidence.show_review(window,page,'eng',root/'tessdata',[line])==0
            def inspect_quality(dialog):
                assert dialog.layoutDirection()==window.layoutDirection()
                assert dialog.windowTitle()==window.ui('Scanqualität prüfen')
                dialog.show();app.processEvents();dialog.grab().save(str(output/('quality-'+code+'.png')))
                return QDialog.DialogCode.Rejected
            with patch.object(quality,'quality_hints',return_value=[quality.BLUR,quality.CONTRAST,quality.DARK]),patch.object(QDialog,'exec',inspect_quality):
                quality.show_quality(window,page)
            def inspect_reader(dialog):
                assert dialog.layoutDirection()==window.layoutDirection()
                buttons={b.text():b for b in dialog.findChildren(QPushButton)}
                view=dialog.findChild(QPlainTextEdit)
                cursor=view.textCursor();cursor.setPosition(0);cursor.setPosition(5,QTextCursor.MoveMode.KeepAnchor);view.setTextCursor(cursor)
                dialog.findChild(QSpinBox).setValue(3)
                buttons[window.ui('Vorlesen')].click()
                proc=FakeProcess.instances[-1]
                assert proc.commands[-1]['text']=='Hallo' and proc.commands[-1]['rate']==3
                for label,action in [('Pause','pause'),('Fortsetzen','resume'),('Stoppen','stop')]:
                    assert buttons[window.ui(label)].isEnabled();buttons[window.ui(label)].click();assert proc.commands[-1]['action']==action
                cursor.clearSelection();view.setTextCursor(cursor);buttons[window.ui('Vorlesen')].click()
                assert proc.commands[-1]['text']=='Hallo العربية עברית 日本語'
                dialog.show();app.processEvents();dialog.grab().save(str(output/('speech-'+code+'.png')))
                return QDialog.DialogCode.Rejected
            with patch.object(QtCore,'QProcess',FakeProcess),patch.object(QDialog,'exec',inspect_reader):
                speech.show_reader(window,'Hallo العربية עברית 日本語')
            assert not FakeProcess.instances[-1].running and FakeProcess.instances[-1].commands[-1]['action']=='quit'
            # Missing voices / failed helper stay disabled without falling back online.
            def unavailable(dialog):
                buttons={b.text():b for b in dialog.findChildren(QPushButton)}
                assert not buttons[window.ui('Vorlesen')].isEnabled()
                return QDialog.DialogCode.Rejected
            def empty_start(proc,*args):
                proc.running=True;proc.reply(dict(voices=[],state='Ready'))
            with patch.object(QtCore,'QProcess',FakeProcess),patch.object(FakeProcess,'start',empty_start),patch.object(QDialog,'exec',unavailable):
                speech.show_reader(window,'Test')
            assert not FakeProcess.instances[-1].running
            if code=='de':
                with pdf.open() as scan_doc:
                    scan_page=scan_doc.new_page(width=300,height=200)
                    scan_page.insert_image(scan_page.rect,pixmap=page.get_pixmap())
                    scan_page.insert_text((30,60),'Existing OCR',fontsize=18,render_mode=3)
                    before=scan_doc.tobytes(no_new_id=True)
                    def entry(dialog):
                        button=next(b for b in dialog.findChildren(QPushButton) if b.text()==window.ui('Unsichere Wörter prüfen'))
                        button.click()
                        assert dialog.findChild(QTableWidget).currentRow()==0
                        return QDialog.DialogCode.Rejected
                    with patch.object(window,'ask_ocr_language',return_value=('eng',True)),patch.object(confidence,'show_review',return_value=0),patch.object(QTableWidget,'editItem') as edit,patch.object(QDialog,'exec',entry):
                        assert window.review_ocr(scan_doc,0)=={}
                        assert edit.call_count==1 and edit.call_args.args[0].text()=='Existing OCR'
                    assert scan_doc.tobytes(no_new_id=True)==before
                def native_dialog(dialog):
                    deadline=time.monotonic()+10
                    voices=dialog.findChild(QComboBox)
                    while not voices.count() and time.monotonic()<deadline:
                        app.processEvents();time.sleep(.02)
                    assert voices.count()>0,'Real QProcess voice handshake failed'
                    return QDialog.DialogCode.Rejected
                with patch.object(QDialog,'exec',native_dialog):speech.show_reader(window,'Local test without playback')
            with patch.object(window,'error') as error:
                speech.show_reader(window,'');speech.show_reader(window,'x'*100001)
                assert error.call_count==2
            assert doc.tobytes(no_new_id=True)==original
            window.close();window.deleteLater();app.processEvents()

# Real Windows engine, silent memory output: no private text, no speaker playback.
program=root/'read_aloud.exe'
proc=subprocess.Popen([str(program),'--test-output'],
                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',
                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
messages=queue.Queue()
threading.Thread(target=lambda:[messages.put(line) for line in proc.stdout],daemon=True).start()
def read():
    value=json.loads(messages.get(timeout=20));assert not value.get('error'),value;return value
def command(action,**values):
    proc.stdin.write(json.dumps(dict(action=action,**values),ensure_ascii=False)+'\n');proc.stdin.flush();return read()
try:
    hello=read();assert hello['voices'],'No installed Windows speech voice available for native test'
    voice=hello['voices'][0]['name']
    command('start',voice=voice,rate=0,text='PDF Digital. This is a local speech test. '*1000)
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        state=command('status')
        if state['state']=='Speaking':break
        time.sleep(.05)
    assert state['state']=='Speaking',state
    assert command('pause')['state']=='Paused'
    assert command('resume')['state']=='Speaking'
    command('stop')
    command('start',voice=voice,rate=2,text='PDF Digital offline test.')
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        state=command('status')
        if state['state']=='Ready' and state['bytes']>44:break
        time.sleep(.05)
    assert state['bytes']>44,state
    audio_bytes=state['bytes']
    command('start',voice=voice,rate=0,text='Closing a paused reading test. '*1000)
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if command('status')['state']=='Speaking':break
        time.sleep(.05)
    assert command('pause')['state']=='Paused'
    command('quit');proc.wait(timeout=5);assert proc.returncode==0
    (output/'native-speech.json').write_text(json.dumps(dict(passed=True,voices=len(hello['voices']),audioBytes=audio_bytes,pauseResume=True,closeWhilePaused=True)),encoding='utf-8')
finally:
    if proc.poll() is None:proc.kill();proc.wait(timeout=5)
print('PASS: correction mapping/rotation/ambiguity, quality fixtures, unchanged PDF, all 20 UIs, speech selection/control/cleanup, native Windows synthesis and pause/resume')

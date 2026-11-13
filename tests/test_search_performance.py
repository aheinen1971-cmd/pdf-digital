import json,time,unicodedata,sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pymupdf as pdf
from PySide6.QtCore import QSettings,QTimer,Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtWidgets import QApplication,QDialog,QPushButton,QTableWidget,QLabel,QLineEdit,QCheckBox
import pdf_werkbank as m
import ocr_review_state as state
app=QApplication.instance() or QApplication([])
for name in ('segoeui.ttf','arial.ttf','YuGothR.ttc','malgun.ttf'):
 p=Path('C:/Windows/Fonts')/name
 if p.exists():QFontDatabase.addApplicationFont(str(p))
app.setFont(QFont('Segoe UI',10));root=Path(m.__file__).parent;out=root/'tests/search-performance-results';out.mkdir(exist_ok=True)
def wait_finished(dialog,window):
 until=time.monotonic()+30
 while dialog.findChild(QLabel,'ocrSearchStatus').text()==window.ui('Suche läuft …'):
  app.processEvents();QTest.qWait(1)
  assert time.monotonic()<until,'Search timeout'
def fixture(count):
 doc=pdf.open()
 with pdf.open() as seed,pdf.open() as raster:
  raster.new_page().insert_text((30,50),'Scanned fixture')
  p=seed.new_page();p.insert_image(p.rect,pixmap=raster[0].get_pixmap())
  p.insert_text((30,50),'\n'.join('Alpha needle line '+str(n) for n in range(25)),fontsize=10,render_mode=3)
  for _ in range(count):doc.insert_pdf(seed)
 return doc
with TemporaryDirectory() as folder,fixture(12) as doc:
 for code in m.UI_LANGUAGES:
  settings=QSettings(str(Path(folder)/(code+'.ini')),QSettings.Format.IniFormat);settings.setValue('view/language',code)
  with patch.object(m,'QSettings',return_value=settings):window=m.Window()
  for key in ('Fundstelle {0} von {1} · Seite {2}','Suche läuft …','Nur aktuelles Dokument','Nur offene Prüfungen'):assert code=='de' or window.ui(key)!=key
  def editor(dialog):
   search=dialog.findChild(QLineEdit,'ocrLineSearch');table=dialog.findChild(QTableWidget);status=dialog.findChild(QLabel,'ocrSearchStatus');next_=dialog.findChild(QPushButton,'ocrSearchNext')
   original=m.ocr_lines
   with patch.object(m,'ocr_lines',wraps=original) as extract:
    for text in ('n','ne','nee','needle'):search.setText(text)
    assert extract.call_count==0,'Typing triggered immediate extraction'
    wait_finished(dialog,window);assert extract.call_count==12
    assert status.text()==window.ui('Fundstelle {0} von {1} · Seite {2}').format(1,300,1)
    search.setText('alpha');wait_finished(dialog,window);assert extract.call_count==12,'Cache was not reused'
    next_.click();wait_finished(dialog,window);assert status.text()==window.ui('Fundstelle {0} von {1} · Seite {2}').format(2,300,1)
    table.item(1,0).setText('Draft Ω العربية');wait_finished(dialog,window)
    search.setText('العربية');wait_finished(dialog,window);assert status.text()==window.ui('Fundstelle {0} von {1} · Seite {2}').format(1,1,1)
    table.item(1,0).setText('Alpha needle line 1');search.setText('needle');wait_finished(dialog,window)
    # Cancel an active chunked scan by replacing the query; stale results must not return.
    search.returnPressed.emit();search.setText('absent');wait_finished(dialog,window);assert status.text()==window.ui('{0} passende Zeilen').format(0)
    search.clear();assert not next_.isEnabled()
    search.setText('alpha');search.returnPressed.emit();dialog.reject();calls=extract.call_count;QTest.qWait(250);assert extract.call_count==calls,'Search continued after close'
   return QDialog.DialogCode.Rejected
  with patch.object(QDialog,'exec',editor):assert window.review_ocr(doc,0)=={}
  with patch.object(state,'settings',return_value=settings):
   r=dict(doc=state.document_key(doc),page=0,language='eng',words='a'*64,scores=[20],reviewed=[],threshold=80,position=0)
   state.write(r);state.write(dict(r,page=1,reviewed=[0]));state.write(dict(r,doc='b'*64))
   def manager(dialog):
    table=dialog.findChild(QTableWidget,'checkpoints');current=dialog.findChild(QCheckBox,'currentDocumentOnly');pending=dialog.findChild(QCheckBox,'pendingReviewsOnly')
    assert table.rowCount()==3;table.setCurrentCell(0,0);current.click();assert table.rowCount()==2
    assert not dialog.findChild(QPushButton,'deleteCheckpoint').isEnabled(),'Hidden selection survived filtering'
    pending.click();assert table.rowCount()==1;table.setCurrentCell(0,0)
    with patch.object(state,'open_record') as opened:dialog.findChild(QPushButton,'openCheckpoint').click();assert opened.call_args.args[2]['page']==0
    dialog.show();app.processEvents()
    if code in ('de','ar','ja'):dialog.grab().save(str(out/('filters-'+code+'.png')))
    dialog.findChild(QPushButton,'deleteCheckpoint').click();assert table.rowCount()==0
    pending.click();assert table.rowCount()==1;current.click();assert table.rowCount()==2
    assert len(state.read())==2
    return QDialog.DialogCode.Rejected
   with patch.object(QDialog,'exec',manager):state.checkpoints(window,doc)
  window.deleteLater();app.processEvents()
 metrics=[]
 for count in (() if '--ui-only' in sys.argv else (500,1500)):
  with fixture(count) as large:
   settings=QSettings(str(Path(folder)/('large'+str(count)+'.ini')),QSettings.Format.IniFormat)
   with patch.object(m,'QSettings',return_value=settings):window=m.Window()
   start=time.perf_counter()
   for query in ('needle','alpha'):
    total=sum(query in unicodedata.normalize('NFC',line[3]).casefold() for page in large for line in m.ocr_lines(page))
    assert total==count*25
   baseline=time.perf_counter()-start
   def benchmark(dialog):
    search=dialog.findChild(QLineEdit,'ocrLineSearch');original=m.ocr_lines;ticks=[]
    beat=QTimer(dialog);beat.setInterval(1);beat.timeout.connect(lambda:ticks.append(time.perf_counter()));beat.start()
    with patch.object(m,'ocr_lines',wraps=original) as extract:
     start=time.perf_counter();search.setText('needle');wait_finished(dialog,window);cold=time.perf_counter()-start
     cold_calls=extract.call_count;assert cold_calls==count
     start=time.perf_counter();search.setText('alpha');wait_finished(dialog,window);warm=time.perf_counter()-start
     assert extract.call_count==cold_calls,'Warm query extracted PDF again'
     assert len(ticks)>2,'UI event loop stalled throughout search'
     assert dialog.findChild(QLabel,'ocrSearchStatus').text()==window.ui('Fundstelle {0} von {1} · Seite {2}').format(1,count*25,1)
     metrics.append(dict(pages=count,lines=count*25,previousTwoQueriesSeconds=round(baseline,4),coldSeconds=round(cold,4),warmSeconds=round(warm,4),pdfExtractionsCold=cold_calls,pdfExtractionsWarm=0,eventLoopTicks=len(ticks),maxTickGapSeconds=round(max((b-a for a,b in zip(ticks,ticks[1:])),default=0),4)))
    beat.stop();return QDialog.DialogCode.Rejected
   with patch.object(QDialog,'exec',benchmark):window.review_ocr(large,0)
   window.deleteLater();app.processEvents()
 if metrics:(out/'benchmark.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
 print(json.dumps(metrics),flush=True)
 print('20 UI languages: debounce/cache/drafts/stale cancellation/close/position/filter combinations/delete passed; large PDF benchmarks passed')


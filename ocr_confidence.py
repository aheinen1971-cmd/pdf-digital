# SPDX-License-Identifier: AGPL-3.0-only
"""Offline word review. Scores describe fresh raster OCR, never edited PDF text."""
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from ocr_languages import LANGUAGES, require_data


def validate(words, width, height):
    if not isinstance(words, list) or len(words) > 50000:
        raise ValueError('Invalid OCR result')
    for word in words:
        text, score, box = word['text'], word['score'], word['box']
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError('Invalid word')
        if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError('Invalid score')
        if len(box) != 4 or not all(isinstance(n, int) for n in box):
            raise ValueError('Invalid box')
        if not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
            raise ValueError('Invalid bounds')
    return words


def worker(image, target, data, language):
    import tesserocr
    require_data(language, Path(data))
    words = []
    with tesserocr.PyTessBaseAPI(path=data, lang=language) as api:
        api.SetImageFile(image)
        api.Recognize()
        # Sparse text fallback for scans whose layout yields no words (e.g. Korean).
        if not api.AllWordConfidences():
            api.SetPageSegMode(tesserocr.PSM.SPARSE_TEXT)
            api.Recognize()
        # Native TSV preserves logical RTL word order; the wrapper's LTR
        # iterator reverses Arabic/Hebrew words on this Windows build.
        for line in api.GetTSVText(0).splitlines():
            fields = line.split('\t', 11)
            if len(fields) != 12 or fields[0] != '5' or not fields[11].strip():
                continue
            left, top, width, height = map(int, fields[6:10])
            words.append(dict(text=fields[11].strip(), score=float(fields[10]),
                              box=[left, top, left + width, top + height]))
    Path(target).write_text(json.dumps(words, ensure_ascii=False), encoding='utf-8')


def recognize(page, language, data, cancelled=lambda: False, timeout=180):
    """Return pixel boxes and scores; None on cancellation. Never modify page."""
    require_data(language, Path(data))
    if page.rect.width * page.rect.height * (200 / 72) ** 2 > 30000000:
        raise ValueError('Image too large')
    pix = page.get_pixmap(dpi=200, alpha=False, annots=False)
    with tempfile.TemporaryDirectory(prefix='pdf-ocr-review-') as folder:
        image, output = Path(folder) / 'page.png', Path(folder) / 'words.json'
        pix.save(image)
        command = [sys.executable, str(Path(__file__).resolve()), str(image), str(output), str(data), language]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        start = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled():
                    return None
                if time.monotonic() - start > timeout:
                    raise TimeoutError('OCR timeout')
                time.sleep(.03)
            if cancelled():
                return None
            if process.returncode or not output.exists() or output.stat().st_size > 16000000:
                raise RuntimeError('OCR component failed')
            return validate(json.loads(output.read_text(encoding='utf-8')), pix.width, pix.height), pix
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


def correction_line(page, box, lines):
    import pymupdf as pdf
    rect = pdf.Rect(box) * pdf.Matrix(72 / 200, 72 / 200) * page.derotation_matrix
    if rect.is_empty:
        return None
    matches = [(i, (rect & line[0]).get_area() / rect.get_area()) for i, line in enumerate(lines)]
    matches = [i for i, overlap in matches if overlap >= .5]
    return matches[0] if len(matches) == 1 else None


def show_review(parent, page, language, data, correction_lines=None):
    from PySide6.QtCore import Qt, QRect
    from PySide6.QtGui import QImage, QPixmap, QPainter, QColor
    from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QLabel, QSpinBox,
                                 QTableWidget, QTableWidgetItem, QProgressDialog,
                                 QDialogButtonBox, QScrollArea, QSplitter, QHeaderView)
    tr = parent.ui
    progress = QProgressDialog(tr('Unsichere Wörter prüfen'), tr('Abbrechen'), 0, 0, parent)
    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.show()
    def cancelled():
        QApplication.processEvents()
        return progress.wasCanceled()
    try:
        result = recognize(page, language, data, cancelled)
    except Exception:
        progress.close()
        parent.error(tr('Die Offline-Prüfung ist fehlgeschlagen. Bitte Installation und OCR-Sprachen prüfen.'))
        return
    finally:
        progress.close()
    if result is None:
        return
    words, pix = result
    dialog = QDialog(parent)
    dialog.setLayoutDirection(parent.layoutDirection())
    dialog.setWindowTitle(tr('Unsichere Wörter prüfen'))
    dialog.resize(1000, 700)
    layout = QVBoxLayout(dialog)
    notice = QLabel(tr('Neue Scan-Erkennung, nicht der korrigierte Text. Niedrige Werte sind Prüfhinweise, keine sicheren Fehler.') + '\n' + tr('OCR-Sprache') + ': ' + ' + '.join(LANGUAGES[code] for code in language.split('+')))
    notice.setWordWrap(True)
    layout.addWidget(notice)
    threshold = QSpinBox()
    threshold.setRange(0, 100)
    threshold.setValue(80)
    threshold.setPrefix(tr('Prüfwert unter') + ' ')
    layout.addWidget(threshold)
    state = QLabel()
    layout.addWidget(state)
    split = QSplitter()
    layout.addWidget(split, 1)
    area = QScrollArea()
    image = QLabel()
    area.setWidget(image)
    split.addWidget(area)
    table = QTableWidget(0, 2)
    table.setHorizontalHeaderLabels([tr('Wort'), tr('Prüfwert (0–100)')])
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    split.addWidget(table)
    split.setSizes([490, 490])
    base = QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy())
    base = base.scaledToWidth(470, Qt.TransformationMode.SmoothTransformation)
    factor = base.width() / pix.width
    visible = []
    selected_line = None
    correct = None
    def highlight(index, *_):
        if correct is not None:
            match = correction_line(page, visible[index]['box'], correction_lines) if 0 <= index < len(visible) else None
            correct.setEnabled(match is not None)
        shown = QPixmap(base)
        painter = QPainter(shown)
        for n, word in enumerate(visible):
            x0, y0, x1, y1 = word['box']
            painter.fillRect(QRect(round(x0*factor), round(y0*factor), max(1, round((x1-x0)*factor)), max(1, round((y1-y0)*factor))), QColor(255, 150, 20, 160 if n == index else 65))
        painter.end()
        image.setPixmap(shown)
        image.resize(shown.size())
        if 0 <= index < len(visible):
            x0, y0, x1, y1 = visible[index]['box']
            area.ensureVisible(round((x0+x1)*factor/2), round((y0+y1)*factor/2), 30, 30)
    def refresh():
        visible[:] = [word for word in words if word['score'] < threshold.value()]
        table.blockSignals(True)
        table.setRowCount(len(visible))
        for i, word in enumerate(visible):
            table.setItem(i, 0, QTableWidgetItem(word['text']))
            table.setItem(i, 1, QTableWidgetItem(f"{word['score']:.1f}"))
        table.blockSignals(False)
        state.setText(tr('Markierte Wörter') + ': ' + str(len(visible)) if visible else tr('Keine Wörter unter diesem Prüfwert.' if words else 'Kein Text erkannt.'))
        highlight(-1)
    threshold.valueChanged.connect(refresh)
    table.currentCellChanged.connect(highlight)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr('Schließen'))
    if correction_lines is not None:
        correct = buttons.addButton(tr('Zur Korrekturzeile'), QDialogButtonBox.ButtonRole.ActionRole)
        correct.setToolTip(tr('Nur eindeutig zugeordnete Textzeilen lassen sich direkt öffnen.'))
        correct.setEnabled(False)
        def choose():
            nonlocal selected_line
            index = table.currentRow()
            if 0 <= index < len(visible):
                selected_line = correction_line(page, visible[index]['box'], correction_lines)
                if selected_line is not None:
                    dialog.accept()
        correct.clicked.connect(choose)
        table.cellDoubleClicked.connect(lambda *_: choose())
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    refresh()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return selected_line


if __name__ == '__main__':
    worker(*sys.argv[1:])

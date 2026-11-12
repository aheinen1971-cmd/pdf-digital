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
                                 QDialogButtonBox, QScrollArea, QSplitter, QHeaderView,
                                 QCheckBox, QPushButton, QHBoxLayout)
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
    review_count = QLabel()
    review_count.setObjectName('reviewCount')
    layout.addWidget(review_count)
    review_notice = QLabel(tr('Der Zähler gilt für die gefilterten Wörter in diesem Fenster. Als geprüft markieren bestätigt nur deine Durchsicht, keine automatische Korrektur.'))
    review_notice.setWordWrap(True)
    layout.addWidget(review_notice)
    navigation = QHBoxLayout()
    reviewed_box = QCheckBox(tr('Als geprüft markieren'))
    reviewed_box.setObjectName('reviewedWord')
    next_word = QPushButton(tr('Nächstes Wort'))
    next_word.setObjectName('nextWord')
    navigation.addWidget(reviewed_box)
    navigation.addWidget(next_word)
    layout.addLayout(navigation)
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
    visible_ids = []
    reviewed_ids = set()
    selected_line = None
    correct = None
    def update_review():
        index = table.currentRow()
        valid = 0 <= index < len(visible_ids)
        reviewed_box.blockSignals(True)
        reviewed_box.setEnabled(valid)
        reviewed_box.setChecked(valid and visible_ids[index] in reviewed_ids)
        reviewed_box.blockSignals(False)
        count = sum(word_id in reviewed_ids for word_id in visible_ids)
        review_count.setText(tr('{0} von {1} geprüft').format(count, len(visible_ids)))
        next_word.setEnabled(any(word_id not in reviewed_ids for word_id in visible_ids))
    def mark_reviewed(checked):
        index = table.currentRow()
        if 0 <= index < len(visible_ids):
            if checked:
                reviewed_ids.add(visible_ids[index])
            else:
                reviewed_ids.discard(visible_ids[index])
        update_review()
    def go_next():
        size = len(visible_ids)
        current = table.currentRow()
        for offset in range(1, size + 1):
            index = (current + offset) % size
            if visible_ids[index] not in reviewed_ids:
                table.setCurrentCell(index, 0)
                table.scrollToItem(table.item(index, 0))
                return
    reviewed_box.toggled.connect(mark_reviewed)
    next_word.clicked.connect(go_next)
    def highlight(index, *_):
        update_review()
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
            center_x = round((x0+x1)*factor/2)
            area.ensureVisible(center_x, round((y0+y1)*factor/2), 30, 30)
            # Scrollbar values run in the opposite direction in RTL interfaces.
            bar = area.horizontalScrollBar()
            offset = max(0, min(bar.maximum(), center_x - area.viewport().width() // 2))
            bar.setValue(bar.maximum() - offset if area.layoutDirection() == Qt.LayoutDirection.RightToLeft else offset)
    def refresh():
        old_index = table.currentRow()
        old_id = visible_ids[old_index] if 0 <= old_index < len(visible_ids) else None
        visible_ids[:] = [i for i, word in enumerate(words) if word['score'] < threshold.value()]
        visible[:] = [words[i] for i in visible_ids]
        table.blockSignals(True)
        table.setRowCount(len(visible))
        for i, word in enumerate(visible):
            table.setItem(i, 0, QTableWidgetItem(word['text']))
            table.setItem(i, 1, QTableWidgetItem(f"{word['score']:.1f}"))
        index = visible_ids.index(old_id) if old_id in visible_ids else (0 if visible else -1)
        table.setCurrentCell(index, 0)
        table.blockSignals(False)
        state.setText(tr('Markierte Wörter') + ': ' + str(len(visible)) if visible else tr('Keine Wörter unter diesem Prüfwert.' if words else 'Kein Text erkannt.'))
        highlight(index)
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

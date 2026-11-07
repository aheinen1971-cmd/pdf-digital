# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from itertools import zip_longest
from pathlib import Path
from xml.etree import ElementTree

import pymupdf as pdf
from PySide6.QtCore import QPoint, QRect, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPageSize, QPainter, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPlainTextEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QRubberBand, QScrollArea, QSplitter, QTabBar,
    QVBoxLayout, QWidget,
)

from local_ai import LocalTextEngine, ModelDownload, model_path, model_ready

from online_updates import ReleaseCheck
from update_worker import prepared_update

VERSION = (1, 4, 2)

STYLE = """
QMainWindow, QWidget#shell { background: #f4f7fb; color: #182b40; }
QTabBar::tab { padding: 8px 15px; color: #36516d; background: #e9eff6; }
QTabBar::tab:selected { background: white; color: #135da0; }
QWidget#ribbon { background: white; border-bottom: 1px solid #cfdbe9; }
QPushButton { padding: 7px 12px; border: 1px solid #cddaea; border-radius: 5px;
              background: white; color: #193651; }
QPushButton:hover { background: #e6f2ff; }
QPushButton:pressed { background: #d2e9ff; }
QListWidget { border: 0; background: #f2f6fb; color: #193651; padding: 4px; }
QListWidget::item { padding: 5px; }
QListWidget::item:selected { background: #d3e8ff; color: #12304e; }
QScrollArea { background: #dce5ef; border: 0; }
QSplitter::handle { background: #cfdbe9; width: 3px; }
QStatusBar { background: #f4f7fb; color: #36516d; }
"""


def pixmap(page, scale, annotations=True):
    data = page.get_pixmap(matrix=pdf.Matrix(scale, scale), alpha=False, annots=annotations)
    image = QImage(data.samples, data.width, data.height, data.stride, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image)


def save_copy(doc, target):
    target = Path(target)
    fd, temp = tempfile.mkstemp(suffix=".pdf", dir=target.parent)
    os.close(fd)
    try:
        doc.save(temp, garbage=4, deflate=True)
        with pdf.open(temp) as check:
            if len(check) != len(doc):
                raise ValueError("Die gespeicherte Kopie ist unvollständig.")
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)


def page_selection(value, count):
    pages = {}
    for token in value.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", token)
        if not match:
            raise ValueError("Bitte Seiten wie 1, 3-5, 8 eingeben.")
        first = int(match[1])
        last = int(match[2] or first)
        if not 1 <= first <= last <= count:
            raise ValueError(f"Die Seiten müssen zwischen 1 und {count} liegen.")
        for page in range(first - 1, last):
            pages[page] = None
    return list(pages)


def extract_pages(doc, indices):
    result = pdf.open(stream=doc.tobytes(), filetype="pdf")
    try:
        result.select(indices)
    except Exception:
        result.close()
        raise
    return result


COMPRESSION = {"Gute Lesbarkeit": (180, 82), "Kleine Datei": (125, 67), "Sehr kleine Datei": (90, 52)}


def compressed_copy(doc, target, profile):
    dpi, quality = COMPRESSION[profile]
    with tempfile.TemporaryDirectory() as folder:
        baseline = Path(folder) / "original.pdf"
        candidate = Path(folder) / "komprimiert.pdf"
        save_copy(doc, baseline)
        with pdf.open(baseline) as copy:
            copy.rewrite_images(dpi_threshold=int(dpi * 1.2), dpi_target=dpi,
                                quality=quality, bitonal=False)
            save_copy(copy, candidate)
        smaller = candidate.stat().st_size < baseline.stat().st_size
        with pdf.open(candidate if smaller else baseline) as copy:
            save_copy(copy, target)
        return baseline.stat().st_size, Path(target).stat().st_size


def align_pages(left, right):
    def describe(doc):
        fingerprints, texts = [], []
        for page in doc:
            scale = 96 / max(page.rect.width, page.rect.height)
            image = page.get_pixmap(matrix=pdf.Matrix(scale, scale), alpha=False)
            text = " ".join(page.get_text().split())
            fingerprints.append((page.rect.width, page.rect.height, image.digest,
                                 hashlib.sha256(text.encode("utf-8")).digest()))
            texts.append(text[:2400])
        return fingerprints, texts

    left_keys, left_text = describe(left)
    right_keys, right_text = describe(right)
    result = []
    matcher = SequenceMatcher(None, left_keys, right_keys, autojunk=False)
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag != "replace" or b - a == d - c or (b - a) * (d - c) > 300:
            result.extend((i, j, tag) for i, j in zip_longest(range(a, b), range(c, d)))
            continue
        # Exact pages are anchors. Between anchors, pair textually similar revisions
        # before assigning unmatched pages as additions or removals.
        shorter, longer = (list(range(a, b)), list(range(c, d))) if b - a < d - c else (
            list(range(c, d)), list(range(a, b)))
        swapped = b - a > d - c
        short_text, long_text = (right_text, left_text) if swapped else (left_text, right_text)
        scores = [[0.0] * (len(longer) + 1) for _ in range(len(shorter) + 1)]
        matched = set()
        for i in range(len(shorter) - 1, -1, -1):
            for j in range(len(longer) - 1, i - 1, -1):
                if len(shorter) - i > len(longer) - j:
                    continue
                pair_score = SequenceMatcher(None, short_text[shorter[i]], long_text[longer[j]]).ratio()
                pair_score += scores[i + 1][j + 1]
                if len(shorter) - i == len(longer) - j or pair_score >= scores[i][j + 1]:
                    scores[i][j] = pair_score
                    matched.add((i, j))
                else:
                    scores[i][j] = scores[i][j + 1]
        i = 0
        for j, index in enumerate(longer):
            if (i, j) in matched:
                pair = (index, shorter[i]) if swapped else (shorter[i], index)
                result.append((*pair, "replace"))
                i += 1
            else:
                result.append((index, None, "delete") if swapped else (None, index, "insert"))
    return result



def compare_images(left, right):
    pages = [page for page in (left, right) if page is not None]
    scale = 660 / max(max(page.rect.width, page.rect.height) for page in pages)
    raster = [page.get_pixmap(matrix=pdf.Matrix(scale, scale), alpha=False) if page is not None else None
              for page in (left, right)]
    if any(image is None for image in raster):
        return [pixmap(page, scale) if page is not None else None for page in (left, right)], True

    width = max(image.width for image in raster)
    height = max(image.height for image in raster)
    samples = []
    previews = []
    for image in raster:
        preview = QImage(width, height, QImage.Format.Format_RGB32)
        preview.fill(Qt.GlobalColor.white)
        painter = QPainter(preview)
        painter.drawImage(0, 0, QImage(image.samples, image.width, image.height, image.stride,
                                      QImage.Format.Format_RGB888))
        painter.end()
        samples.append(bytes(preview.constBits()))
        previews.append(preview)
    stride = previews[0].bytesPerLine()
    changes = []
    step = 12
    for y in range(0, height, step):
        for x in range(0, width, step):
            right_edge = min(x + step, width)
            bottom = min(y + step, height)
            if any(samples[0][row * stride + x * 4:row * stride + right_edge * 4] !=
                   samples[1][row * stride + x * 4:row * stride + right_edge * 4]
                   for row in range(y, bottom)):
                changes.append(QRect(x, y, right_edge - x, bottom - y))

    for preview, color in zip(previews, (QColor(230, 75, 55, 78), QColor(34, 158, 109, 83))):
        painter = QPainter(preview)
        for rect in changes:
            painter.fillRect(rect, color)
        painter.end()
    return [QPixmap.fromImage(image) for image in previews], bool(changes)


class CompareDialog(QDialog):
    def __init__(self, parent, left, right, names, pairs):
        super().__init__(parent)
        self.setWindowTitle("PDF-Versionen vergleichen")
        self.resize(1090, 790)
        self.docs = (left, right)
        self.pairs = pairs
        layout = QVBoxLayout(self)
        legend = QLabel("Links: bisherige Fassung   ·   Rechts: neue Fassung   ·   Farbige Flächen: sichtbare Unterschiede")
        legend.setWordWrap(True)
        layout.addWidget(legend)
        self.pages = QComboBox()
        for a, b, tag in pairs:
            state = "Hinzugefügt" if a is None else "Entfernt" if b is None else "Abgeglichen" if tag == "equal" else "Prüfen"
            self.pages.addItem(f"{a + 1 if a is not None else '–'}  /  {b + 1 if b is not None else '–'}  ·  {state}")
        layout.addWidget(self.pages)
        self.result = QLabel()
        layout.addWidget(self.result)
        panels = QSplitter()
        self.images = []
        self.areas = []
        for name in names:
            panel = QWidget()
            column = QVBoxLayout(panel)
            heading = QLabel(name)
            heading.setWordWrap(True)
            column.addWidget(heading)
            area = QScrollArea()
            area.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image = QLabel()
            area.setWidget(image)
            column.addWidget(area)
            self.images.append(image)
            self.areas.append(area)
            panels.addWidget(panel)
        layout.addWidget(panels, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.pages.currentIndexChanged.connect(self.render_pair)
        first_change = next((index for index, pair in enumerate(pairs) if pair[2] != "equal"), 0)
        if first_change:
            self.pages.setCurrentIndex(first_change)
        else:
            self.render_pair(0)

    def render_pair(self, index):
        a, b, _ = self.pairs[index]
        left = self.docs[0][a] if a is not None else None
        right = self.docs[1][b] if b is not None else None
        images, changed = compare_images(left, right)
        if left is None or right is None:
            status = "Seite hinzugefügt" if left is None else "Seite entfernt"
        elif changed:
            status = "Sichtbare Unterschiede markiert"
        elif left.rect != right.rect:
            status = "Unterschiedliches Seitenformat"
        elif left.get_text() != right.get_text():
            status = "Unterschied in der Textebene"
        else:
            status = "Keine sichtbaren Unterschiede in der Vorschau"
        self.result.setText(status)
        for label, area, image in zip(self.images, self.areas, images):
            if image is None:
                label.setText("Keine entsprechende Seite")
                label.adjustSize()
            else:
                label.setPixmap(image)
                label.resize(image.size())
            area.verticalScrollBar().setValue(0)
            area.horizontalScrollBar().setValue(0)



SCAN_PROFILES = ("Kontrast in Farbe", "Heller Hintergrund in Graustufen")


def enhance_scan(page, profile, dpi=220):
    scale = min(dpi / 72, 2800 / max(page.rect.width, page.rect.height))
    raster = page.get_pixmap(matrix=pdf.Matrix(scale, scale), alpha=False, annots=False)
    samples = raster.samples
    luminance = bytes((77 * r + 150 * g + 29 * b) >> 8 for r, g, b in zip(samples[::3], samples[1::3], samples[2::3]))
    histogram = Counter(luminance)

    def percentile(fraction):
        limit = len(luminance) * fraction
        count = 0
        for value in range(256):
            count += histogram[value]
            if count >= limit:
                return value
        return 255

    black = min(145, percentile(0.006))
    white = max(black + 28, percentile(0.96))
    if profile == SCAN_PROFILES[1]:
        white = max(black + 28, white - 13)
    mapping = bytes(max(0, min(255, round((value - black) * 255 / (white - black)))) for value in range(256))
    colorspace = pdf.csRGB if profile == SCAN_PROFILES[0] else pdf.csGRAY
    result = (samples if colorspace.n == 3 else luminance).translate(mapping)
    return pdf.Pixmap(colorspace, raster.width, raster.height, result, False)


def normalize_text(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def redaction_findings(doc, terms):
    keys = [(term, normalize_text(term)) for term in terms if normalize_text(term)]
    findings = {term: [] for term, _ in keys}

    def inspect(location, values):
        content = normalize_text(" ".join(str(value) for value in values if value is not None))
        for term, key in keys:
            if key in content:
                findings[term].append(location)

    for page in doc:
        number = page.number + 1
        inspect(f"Seite {number} · Text / OCR", [page.get_text(sort=True)])
        for annotation in page.annots():
            inspect(f"Seite {number} · Kommentar", annotation.info.values())
        for field in page.widgets():
            inspect(f"Seite {number} · Formularfeld", [field.field_name, field.field_label, field.field_value])
    inspect("Dokumenteigenschaften", (doc.metadata or {}).values())
    if doc.get_xml_metadata():
        xml = doc.get_xml_metadata()
        try:
            values = list(ElementTree.fromstring(xml).itertext())
        except ElementTree.ParseError:
            values = [xml]
        inspect("XML-Metadaten", [xml, *values])
    for _, title, _ in doc.get_toc():
        inspect("Lesezeichen", [title])
    for name in doc.embfile_names():
        inspect("Dateiname einer Anlage", [name])
    return {term: list(dict.fromkeys(locations)) for term, locations in findings.items() if locations}


def print_sheets(indices, duplex):
    if not duplex:
        return [(index,) for index in indices]
    return [(indices[i], indices[i + 1] if i + 1 < len(indices) else None)
            for i in range(0, len(indices), 2)]


def print_preview_image(doc, index, printer, width):
    paper = printer.pageLayout()
    sheet = paper.fullRectPoints()
    printable = paper.paintRectPoints()
    scale = min(width / sheet.width(), 495 / sheet.height())
    preview = QPixmap(max(1, round(sheet.width() * scale)), max(1, round(sheet.height() * scale)))
    preview.fill(Qt.GlobalColor.white)
    if index is not None:
        page = doc[index]
        ratio = min(printable.width() / page.rect.width, printable.height() / page.rect.height) * scale
        content = pixmap(page, ratio)
        painter = QPainter(preview)
        painter.drawPixmap(round((printable.x() + printable.width() / 2) * scale - content.width() / 2),
                           round((printable.y() + printable.height() / 2) * scale - content.height() / 2),
                           content)
        painter.end()
    return preview



def print_pages(doc, printer, indices):
    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError("Der Druckauftrag konnte nicht gestartet werden.")
    try:
        for i, index in enumerate(indices):
            if i and not printer.newPage():
                raise RuntimeError("Der Drucker hat keine weitere Seite angenommen.")
            page = doc[index]
            area = printer.pageLayout().paintRectPixels(printer.resolution())
            ratio = min(area.width() / page.rect.width, area.height() / page.rect.height)
            width, height = int(page.rect.width * ratio), int(page.rect.height * ratio)
            destination = QRect((area.width() - width) // 2, (area.height() - height) // 2, width, height)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(destination, pixmap(page, min(ratio, 300 / 72)))
    except Exception:
        printer.abort()
        raise
    finally:
        painter.end()
    if printer.printerState() == QPrinter.PrinterState.Error:
        raise RuntimeError("Der Drucker meldet einen Fehler.")


def text_spans(page, textpage=None):
    flags = pdf.TEXTFLAGS_DICT & ~pdf.TEXT_PRESERVE_IMAGES
    for block in page.get_text("dict", flags=flags, textpage=textpage)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                yield line["dir"], span


def recognize_page(page, language, tessdata):
    if page.get_text().strip():
        raise ValueError("Diese Seite enthält bereits auswählbaren Text.")
    if page.rotation:
        raise ValueError("Bitte die Seite für OCR zuerst in ihre ursprüngliche Ausrichtung drehen.")
    recognized = page.get_textpage_ocr(language=language, dpi=200, full=True, tessdata=str(tessdata))
    spans = [span for _, span in text_spans(page, recognized) if span["text"].strip()]
    if not spans:
        raise ValueError("Auf dieser Seite wurde kein Text erkannt.")
    font = pdf.Font("helv")
    shape = page.new_shape()
    for span in spans:
        origin = pdf.Point(span["origin"])
        text = span["text"].replace(chr(160), " ").encode("cp1252", errors="replace").decode("cp1252")
        fontsize = span["size"]
        actual = font.text_length(text, fontsize=fontsize)
        width = pdf.Rect(span["bbox"]).width
        if actual > 0 and width > 0:
            shape.insert_text(origin, text, fontsize=fontsize, fontname="helv", render_mode=3,
                              morph=(origin, pdf.Matrix(width / actual, 1)))
    if not shape.text_cont:
        raise ValueError("Die erkannten Zeichen konnten nicht platziert werden.")
    shape.commit()


def check_pending_redactions(page):
    if any(annot.type[0] == pdf.PDF_ANNOT_REDACT for annot in page.annots()):
        raise ValueError("Diese Seite enthält bereits vorgemerkte Schwärzungen. Bitte diese zuerst separat bearbeiten.")


def replace_span(page, target, value):
    check_pending_redactions(page)
    if "\n" in value or any(ord(char) < 32 for char in value):
        raise ValueError("Bitte eine einzelne Zeile eingeben.")
    value.encode("cp1252")
    box = pdf.Rect(target["bbox"])
    # A narrow band through the glyphs avoids touching ascenders or descenders on adjacent lines.
    y = min(box.y1 - 0.2, max(box.y0 + 0.2, target["origin"][1] - target["size"] * 0.24))
    band = pdf.Rect(box.x0 + 0.1, y - 0.12, box.x1 - 0.1, y + 0.12)
    for _, other in text_spans(page):
        if other != target and band.intersects(pdf.Rect(other["bbox"])):
            raise ValueError("Die Textbereiche überlappen sich. Dieser Bereich kann nicht einzeln korrigiert werden.")
    size = target["size"]
    new_width = pdf.get_text_length(value, fontname="helv", fontsize=size)
    if new_width > box.width:
        size *= box.width / new_width
        if size < target["size"] * 0.8:
            raise ValueError("Der neue Text ist zu lang für den vorhandenen Platz.")
    color = pdf.sRGB_to_pdf(target["color"])
    page.add_redact_annot(band, fill=False, cross_out=False)
    page.apply_redactions(images=pdf.PDF_REDACT_IMAGE_NONE, graphics=pdf.PDF_REDACT_LINE_ART_NONE)
    if value:
        page.insert_text(target["origin"], value, fontsize=size, fontname="helv", color=color)


def redact_area(page, box):
    check_pending_redactions(page)
    for annot in list(page.annots()):
        if annot.rect.intersects(box):
            page.delete_annot(annot)
    for widget in list(page.widgets()):
        if widget.rect.intersects(box):
            page.delete_widget(widget)
    page.add_redact_annot(box, fill=(0, 0, 0), cross_out=False)
    page.apply_redactions(images=pdf.PDF_REDACT_IMAGE_PIXELS, graphics=pdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)



class Session:
    def __init__(self, path):
        self.path = Path(path)
        self.doc = pdf.open(path)
        if not self.doc.is_pdf or not len(self.doc) or self.doc.needs_pass:
            self.doc.close()
            raise ValueError("Diese Version benötigt eine lesbare PDF ohne Passwortschutz.")
        self.page = 0
        self.changed = False
        self.history = []

    def checkpoint(self):
        self.history.append((self.doc.tobytes(), self.page, self.changed))
        del self.history[:-8]

    def undo(self):
        if self.history:
            content, self.page, self.changed = self.history.pop()
            self.doc.close()
            self.doc = pdf.open(stream=content, filetype="pdf")


class Canvas(QLabel):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.origin = None
        self.band = QRubberBand(QRubberBand.Shape.Rectangle, self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window.current:
            self.origin = event.position().toPoint()
            self.band.setGeometry(QRect(self.origin, QSize()))
            if self.window.mode in ("Markieren", "Schwärzen"):
                self.band.show()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.band.setGeometry(QRect(self.origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        if self.origin is not None:
            rect = QRect(self.origin, event.position().toPoint()).normalized()
            self.origin = None
            self.band.hide()
            self.window.annotate(rect)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sessions = []
        self.zoom = 1.0
        self.scale = 1.0
        self.mode = "Lesen"
        self.search_text = ""
        self.thumb_index = 0
        self.setWindowTitle("PDF Werkbank")
        self.release_check = ReleaseCheck(VERSION, self)
        self.release_check.finished.connect(self.show_online_update)
        self.update_process = None
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.finish_online_update)
        self.resize(1200, 820)
        self.setMinimumSize(820, 500)
        self.setStyleSheet(STYLE)
        shell = QWidget(objectName="shell")
        self.setCentralWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tools = QTabBar()
        self.tools.setDrawBase(False)
        self.tools.setExpanding(False)
        self.groups = [
            ("Datei", [("Öffnen", self.open_files), ("Kopie speichern", self.save), ("Drucken", self.print_dialog),
                       ("Samsung-Profil", lambda: self.print_dialog(samsung=True)), ("Online-Update suchen", self.check_online), ("Update-Ordner", self.choose_update_folder), ("PDF-Standard", self.register_pdf_app)]),
            ("Start", [("Öffnen", self.open_files), ("Kopie speichern", self.save), ("Rückgängig", self.undo),
                       ("Suchen", self.search), ("Drucken", self.print_dialog)]),
            ("Ansicht", [("Verkleinern", lambda: self.change_zoom(-0.2)), ("Vergrößern", lambda: self.change_zoom(0.2)),
                         ("Seitenbreite", self.fit_width), ("Miniaturen", self.toggle_thumbs)]),
            ("Kommentar", [("Lesen", lambda: self.set_mode("Lesen")), ("Text markieren", lambda: self.set_mode("Markieren")),
                            ("Notiz setzen", lambda: self.set_mode("Notiz"))]),
            ("Seiten", [("Nach links drehen", lambda: self.edit_page("rotate", -90)),
                        ("Nach rechts drehen", lambda: self.edit_page("rotate", 90)),
                        ("Nach vorn", lambda: self.edit_page("move", -1)), ("Nach hinten", lambda: self.edit_page("move", 1)),
                        ("Löschen", lambda: self.edit_page("delete")), ("PDF anhängen", self.append)]),
            ("Bearbeiten", [("Text korrigieren", lambda: self.set_mode("Text")), ("Schwärzen", lambda: self.set_mode("Schwärzen")), ("OCR für diese Seite", self.ocr), ("OCR für das Dokument", lambda: self.ocr(True))]),
            ("Werkzeuge", [("Seiten extrahieren", self.extract), ("Scan-PDF verkleinern", self.compress),
                           ("PDF-Versionen vergleichen", self.compare), ("Versandkopie", self.prepare_send), ("Schwärzung prüfen", self.verify_redaction)]),
            ("Verbessern", [("Scan lesbarer machen", self.scan_cleanup), ("KI-Textvorschläge", self.ai_text)]),
            ("Formular", [("Feld ausfüllen", lambda: self.set_mode("Formular")), ("Lesen", lambda: self.set_mode("Lesen"))]),
        ]
        for name, _ in self.groups:
            self.tools.addTab(name)
        layout.addWidget(self.tools)
        ribbon_panel = QWidget(objectName="ribbon")
        self.ribbon = QHBoxLayout(ribbon_panel)
        self.ribbon.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(ribbon_panel)
        self.tools.currentChanged.connect(self.update_ribbon)
        self.tools.setCurrentIndex(1)

        self.tabs = QTabBar()
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.setTabsClosable(True)
        self.tabs.currentChanged.connect(self.switch_doc)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        layout.addWidget(self.tabs)

        self.splitter = QSplitter()
        self.thumbs = QListWidget()
        self.thumbs.setIconSize(QSize(85, 110))
        self.thumbs.currentRowChanged.connect(self.go_page)
        self.splitter.addWidget(self.thumbs)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas = Canvas(self)
        self.scroll.setWidget(self.canvas)
        self.splitter.addWidget(self.scroll)
        self.splitter.setSizes([170, 1030])
        self.splitter.setStretchFactor(1, 1)
        self.welcome = QWidget()
        welcome = QVBoxLayout(self.welcome)
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("PDF Werkbank")
        title.setStyleSheet("font-size: 32px; font-weight: 650; color: #193651;")
        welcome.addWidget(title, alignment=Qt.AlignmentFlag.AlignHCenter)
        welcome.addWidget(QLabel("PDFs öffnen, bearbeiten und drucken"), alignment=Qt.AlignmentFlag.AlignHCenter)
        start = QPushButton("PDF öffnen")
        start.clicked.connect(self.open_files)
        welcome.addWidget(start, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.welcome, 1)
        layout.addWidget(self.splitter, 1)
        self.splitter.hide()

        self.thumbnails = QTimer(self)
        self.thumbnails.timeout.connect(self.next_thumbnail)
        self.status = QLabel("Bereit")
        self.statusBar().addWidget(self.status, 1)
        for label, callback in [("‹", lambda: self.turn(-1)), ("›", lambda: self.turn(1)),
                                ("−", lambda: self.change_zoom(-0.2)), ("+", lambda: self.change_zoom(0.2))]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            self.statusBar().addPermanentWidget(button)
        for key, callback in [("Ctrl+O", self.open_files), ("Ctrl+S", self.save), ("Ctrl+P", self.print_dialog),
                              ("Ctrl+Z", self.undo), ("Ctrl+F", self.search), ("PgUp", lambda: self.turn(-1)),
                              ("PgDown", lambda: self.turn(1)), ("Escape", lambda: self.set_mode("Lesen"))]:
            action = QAction(self)
            action.setShortcut(key)
            action.triggered.connect(callback)
            self.addAction(action)

    @property
    def current(self):
        index = self.tabs.currentIndex()
        return self.sessions[index] if 0 <= index < len(self.sessions) else None

    def error(self, exc):
        QMessageBox.warning(self, "PDF Werkbank", str(exc))

    def update_ribbon(self, index):
        while self.ribbon.count():
            item = self.ribbon.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for name, callback in self.groups[index][1]:
            button = QPushButton(name)
            button.clicked.connect(lambda checked=False, run=callback: run())
            self.ribbon.addWidget(button)
        self.ribbon.addStretch()

    def open_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "PDF öffnen", "", "PDF-Dateien (*.pdf)")
        for path in paths:
            self.open_path(path)

    def open_path(self, path):
        try:
            session = Session(path)
        except Exception as exc:
            self.error(exc)
            return
        self.sessions.append(session)
        tab = self.tabs.addTab(session.path.name)
        self.tabs.setCurrentIndex(tab)

    def switch_doc(self, index):
        self.search_text = ""
        self.mode = "Lesen"
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        self.refresh_thumbs()
        self.render()

    def refresh_thumbs(self):
        self.thumbnails.stop()
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        session = self.current
        if session:
            self.thumbs.addItems([f"Seite {index + 1}" for index in range(len(session.doc))])
            self.thumbs.setCurrentRow(session.page)
            self.thumb_index = 0
            if not self.thumbs.isHidden():
                self.thumbnails.start(8)
        self.thumbs.blockSignals(False)

    def next_thumbnail(self):
        session = self.current
        if not session or self.thumb_index >= len(session.doc):
            self.thumbnails.stop()
            return
        page = session.doc[self.thumb_index]
        try:
            small = pixmap(page, min(85 / page.rect.width, 106 / page.rect.height))
            self.thumbs.item(self.thumb_index).setIcon(QIcon(small))
        except (ValueError, RuntimeError):
            pass
        self.thumb_index += 1

    def render(self):
        session = self.current
        self.welcome.setVisible(session is None)
        self.splitter.setVisible(session is not None)
        if not session:
            self.status.setText("Bereit")
            return
        page = session.doc[session.page]
        try:
            self.scale = min(self.zoom, 4096 / max(page.rect.width, page.rect.height))
            image = pixmap(page, self.scale)
            if self.search_text:
                painter = QPainter(image)
                for result in page.search_for(self.search_text):
                    rect = result * page.rotation_matrix * pdf.Matrix(self.scale, self.scale)
                    painter.fillRect(QRect(int(rect.x0), int(rect.y0), int(rect.width), int(rect.height)),
                                     QColor(255, 206, 32, 110))
                painter.end()
            self.canvas.setPixmap(image)
            self.canvas.resize(image.size())
        except Exception as exc:
            self.canvas.clear()
            self.error(exc)
        self.status.setText(f"Seite {session.page + 1} von {len(session.doc)}   •   {self.scale:.0%}   •   {self.mode}")

    def go_page(self, index):
        if self.current and 0 <= index < len(self.current.doc):
            self.current.page = index
            self.render()
            self.scroll.verticalScrollBar().setValue(0)

    def turn(self, delta):
        if self.current:
            self.thumbs.setCurrentRow(max(0, min(len(self.current.doc) - 1, self.current.page + delta)))

    def change_zoom(self, delta):
        self.zoom = max(0.2, min(4, self.zoom + delta))
        self.render()

    def fit_width(self):
        if self.current:
            page = self.current.doc[self.current.page]
            self.zoom = max(0.1, (self.scroll.viewport().width() - 26) / page.rect.width)
            self.render()

    def toggle_thumbs(self):
        hidden = not self.thumbs.isHidden()
        self.thumbs.setHidden(hidden)
        if hidden:
            self.thumbnails.stop()
        elif self.current:
            self.thumbnails.start(8)

    def set_mode(self, mode):
        self.mode = mode
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor if mode == "Lesen" else Qt.CursorShape.CrossCursor)
        self.render()
        hints = {"Markieren": "Mit gedrückter Maustaste einen Bereich um den Text ziehen.",
                 "Notiz": "Auf die gewünschte Position klicken.",
                 "Formular": "Auf ein vorhandenes Formularfeld klicken.",
                 "Text": "Auf einen horizontalen Textabschnitt klicken.",
                 "Schwärzen": "Einen Bereich aufziehen und die Entfernung bestätigen."}
        if mode in hints:
            self.statusBar().showMessage(hints[mode], 6500)

    def annotate(self, selection):
        session = self.current
        if not session:
            return
        page = session.doc[session.page]
        screen = pdf.Rect(selection.left(), selection.top(), selection.right(), selection.bottom())
        rect = screen * pdf.Matrix(1 / self.scale, 1 / self.scale) * page.derotation_matrix
        point = rect.tl
        try:
            if self.mode == "Lesen":
                for note in page.annots():
                    if note.type[0] == pdf.PDF_ANNOT_TEXT and note.rect.contains(point):
                        QMessageBox.information(self, "PDF-Notiz", note.info.get("content") or "Leere Notiz")
                        break
                return
            if self.mode == "Text":
                if not self.correct_text(page, point):
                    return
            elif self.mode == "Schwärzen":
                if rect.width < 2 or rect.height < 2:
                    return
                if QMessageBox.question(self, "Bereich schwärzen",
                                        "Text und Bildinhalt in diesem Bereich aus der Kopie entfernen?"
                                        ) != QMessageBox.StandardButton.Yes:
                    return
                session.checkpoint()
                try:
                    redact_area(page, rect)
                except Exception:
                    session.undo()
                    raise
            elif self.mode == "Markieren":
                words = [pdf.Rect(word[:4]) for word in page.get_text("words")
                         if rect.contains(pdf.Rect(word[:4]).tl + (pdf.Rect(word[:4]).br - pdf.Rect(word[:4]).tl) * 0.5)]
                if not words:
                    self.statusBar().showMessage("Kein auswählbarer Text in diesem Bereich.", 4500)
                    return
                session.checkpoint()
                page.add_highlight_annot(words)
            elif self.mode == "Notiz":
                text, ok = QInputDialog.getMultiLineText(self, "Notiz", "Kommentar")
                if not ok or not text.strip():
                    return
                session.checkpoint()
                page.add_text_annot(point, text)
            elif self.mode == "Formular":
                widget = next((field for field in page.widgets() if field.rect.contains(point)), None)
                if not widget:
                    self.statusBar().showMessage("An dieser Stelle befindet sich kein Formularfeld.", 4500)
                    return
                if widget.field_flags & pdf.PDF_FIELD_IS_READ_ONLY:
                    raise ValueError("Dieses Feld ist schreibgeschützt.")
                if widget.field_type == pdf.PDF_WIDGET_TYPE_TEXT:
                    value, ok = QInputDialog.getText(self, "Formularfeld", widget.field_label or widget.field_name,
                                                     text=str(widget.field_value or ""))
                elif widget.field_type == pdf.PDF_WIDGET_TYPE_CHECKBOX:
                    value, ok = QInputDialog.getItem(self, "Kontrollkästchen", widget.field_name,
                                                     ["Aktiviert", "Leer"], editable=False)
                    value = widget.on_state() if value == "Aktiviert" else "Off"
                else:
                    raise ValueError("Diese Version unterstützt Textfelder und Kontrollkästchen.")
                if not ok:
                    return
                session.checkpoint()
                widget.field_value = value
                widget.update()
            else:
                return
        except Exception as exc:
            self.error(exc)
            return
        self.changed()

    def scan_cleanup(self):
        session = self.current
        if not session:
            return
        page = session.doc[session.page]
        if not page.get_image_info() or any(span["type"] != 3 for span in page.get_texttrace()):
            self.error("Bitte eine Bild-Scan-Seite wählen. Für Seiten mit normalem PDF-Text ist dieses Werkzeug ungeeignet.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Scan-Seite lesbarer machen")
        layout = QVBoxLayout(dialog)
        explanation = QLabel("Die Vorschau zeigt die aktuelle Seite. Die neue PDF enthält nur diese Seite als Bild.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        profiles = QComboBox()
        profiles.addItems(SCAN_PROFILES)
        layout.addWidget(profiles)
        panels = QHBoxLayout()
        labels = []
        for title in ("Original", "Verbessert"):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
            column.addWidget(label, 1)
            labels.append(label)
            panels.addLayout(column, 1)
        layout.addLayout(panels, 1)

        def render_preview():
            scale = min(365 / page.rect.width, 470 / page.rect.height)
            labels[0].setPixmap(pixmap(page, scale, annotations=False))
            raster = enhance_scan(page, profiles.currentText(), dpi=scale * 72)
            mode = QImage.Format.Format_Grayscale8 if raster.n == 1 else QImage.Format.Format_RGB888
            labels[1].setPixmap(QPixmap.fromImage(QImage(raster.samples, raster.width, raster.height, raster.stride, mode)))

        profiles.currentIndexChanged.connect(render_preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Als neue PDF speichern")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            render_preview()
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            target = self.output_path(f" - Scan Seite {session.page + 1}", "Verbesserte Scan-Seite speichern")
            if target:
                raster = enhance_scan(page, profiles.currentText())
                with pdf.open() as result:
                    output_page = result.new_page(width=page.rect.width, height=page.rect.height)
                    output_page.insert_image(output_page.rect, pixmap=raster)
                    save_copy(result, target)
                self.statusBar().showMessage(f"Verbesserte Scan-Seite gespeichert: {target.name}", 6500)
        except Exception as exc:
            self.error(exc)

    def ai_text(self):
        session = self.current
        if not session:
            return
        text = session.doc[session.page].get_text(sort=True).strip()
        if not text:
            self.error("Auf dieser Seite gibt es noch keinen auswählbaren Text. Erkenne einen Scan zuerst mit OCR.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Textvorschläge mit lokaler KI")
        dialog.resize(950, 570)
        layout = QVBoxLayout(dialog)
        notice = QLabel("Links kannst du den Textausschnitt kürzen. Vorschläge entstehen auf deinem PC. "
                        "Die PDF wird dabei nicht verändert. Prüfe insbesondere Namen, Zahlen und Fachbegriffe.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        controls = QHBoxLayout()
        mode = QComboBox()
        mode.addItems(["Rechtschreibung und Grammatik", "Klarer formulieren"])
        controls.addWidget(mode)
        generate = QPushButton("Vorschlag erstellen")
        controls.addWidget(generate)
        controls.addStretch()
        layout.addLayout(controls)
        columns = QHBoxLayout()
        original = QPlainTextEdit(text)
        proposed = QPlainTextEdit()
        for label, editor in (("Textausschnitt für die KI", original), ("Vorschlag zum Prüfen und Kopieren", proposed)):
            column = QVBoxLayout()
            column.addWidget(QLabel(label))
            column.addWidget(editor)
            columns.addLayout(column, 1)
        layout.addLayout(columns, 1)
        path = model_path()
        status = QLabel("Lokales Modell bereit." if model_ready(path)
                        else "Beim ersten Vorschlag lädt PDF Werkbank einmalig das Sprachmodell herunter (ca. 1,1 GB).")
        status.setWordWrap(True)
        layout.addWidget(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        copy = buttons.addButton("Vorschlag kopieren", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(proposed.toPlainText()))
        copy.setEnabled(False)
        proposed.textChanged.connect(lambda: copy.setEnabled(bool(proposed.toPlainText().strip())))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        engine = LocalTextEngine(root / "ki" / "runtime" / "llama-server.exe", path, dialog)
        download = None

        def failed(message):
            status.setText(message)
            generate.setEnabled(True)

        def show_suggestion(text, truncated):
            proposed.setPlainText(text)
            status.setText("Die Antwort wurde gekürzt. Bitte einen kürzeren Ausschnitt wählen."
                           if truncated else "Vorschlag bereit.")
            generate.setEnabled(True)

        engine.status.connect(status.setText)
        engine.finished.connect(show_suggestion)
        engine.failed.connect(failed)

        def generate_text():
            nonlocal download
            source = original.toPlainText().strip()
            if not source or len(source) > 3800:
                status.setText("Bitte einen Textausschnitt zwischen 1 und 3.800 Zeichen wählen.")
                return
            task = ("Korrigiere nur Rechtschreibung, Zeichensetzung und Grammatik."
                    if mode.currentIndex() == 0 else "Formuliere den Text klarer, ohne seine Bedeutung zu ändern.")
            if model_ready(path):
                generate.setEnabled(False)
                proposed.clear()
                engine.generate(source, task)
                return
            answer = QMessageBox.question(dialog, "Sprachmodell herunterladen",
                                          "PDF Werkbank lädt einmalig ca. 1,1 GB von Hugging Face herunter. "
                                          "Dein PDF-Text bleibt auf diesem PC. Jetzt herunterladen?",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            generate.setEnabled(False)
            proposed.clear()
            status.setText("Sprachmodell wird heruntergeladen …")
            if download:
                download.deleteLater()
            download = ModelDownload(path, dialog)
            download.progress.connect(lambda percent: status.setText(f"Sprachmodell wird heruntergeladen · {percent} %"))
            download.failed.connect(failed)
            download.finished.connect(lambda: engine.generate(source, task))
            download.start()

        generate.clicked.connect(generate_text)
        try:
            dialog.exec()
        finally:
            if download:
                download.stop()
            engine.stop()
            dialog.deleteLater()


    def prepare_send(self):
        session = self.current
        if not session:
            return
        options = QDialog(self)
        options.setWindowTitle("Für den Versand fertig machen")
        form = QFormLayout(options)
        pages = QLineEdit(f"1-{len(session.doc)}")
        pages.setToolTip("Seiten und Reihenfolge, zum Beispiel 1, 3-5, 8")
        form.addRow("Seiten", pages)
        ocr = QComboBox()
        languages = {"Deutsch und Englisch": "deu+eng", "Deutsch": "deu", "Englisch": "eng"}
        ocr.addItems([*languages, "Ohne Texterkennung"])
        form.addRow("Texterkennung", ocr)
        compression = QComboBox()
        compression.addItems([*COMPRESSION, "Ohne Bildkomprimierung"])
        form.addRow("Bildqualität", compression)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Versandkopie vorbereiten")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(options.accept)
        buttons.rejected.connect(options.reject)
        form.addRow(buttons)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            indices = page_selection(pages.text(), len(session.doc))
            with tempfile.TemporaryDirectory() as folder, extract_pages(session.doc, indices) as copy:
                stage = Path(folder) / "Versandkopie.pdf"
                language = languages.get(ocr.currentText())
                added, failures = self.send_ocr(copy, indices, language)
                if added is None:
                    return
                profile = compression.currentText()
                if profile in COMPRESSION:
                    compressed_copy(copy, stage, profile)
                else:
                    save_copy(copy, stage)
                summary = f"{len(copy)} Seiten · {stage.stat().st_size / 1024:.1f} KB"
                if language:
                    summary += " · 1 Seite mit neuer Textebene" if added == 1 else f" · {added} Seiten mit neuer Textebene"
                if failures:
                    summary += "\nOhne neue Textebene: " + ", ".join(map(str, failures)) + " (Originalseiten)"
                with pdf.open(stage) as preview:
                    if not self.preview_send(preview, summary):
                        return
                target = self.output_path(" - Versand", "Versandkopie speichern")
                if target:
                    fd, temp = tempfile.mkstemp(suffix=".pdf", dir=target.parent)
                    os.close(fd)
                    try:
                        shutil.copyfile(stage, temp)
                        os.replace(temp, target)
                    finally:
                        Path(temp).unlink(missing_ok=True)
                    self.statusBar().showMessage(f"Versandkopie gespeichert: {target.name}", 6500)
        except Exception as exc:
            self.error(exc)

    def send_ocr(self, doc, indices, language):
        if not language:
            return 0, []
        progress = QProgressDialog("Texterkennung für die Versandkopie …", "Abbrechen", 0, len(doc), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        added, failures = 0, []
        try:
            for index, page in enumerate(doc):
                progress.setValue(index)
                QApplication.processEvents()
                if progress.wasCanceled():
                    return None, []
                if page.get_text().strip():
                    continue
                try:
                    recognize_page(page, language, root / "tessdata")
                    added += 1
                except (ValueError, RuntimeError):
                    failures.append(indices[index] + 1)
            return (None, []) if progress.wasCanceled() else (added, failures)
        finally:
            progress.close()

    def preview_send(self, doc, summary):
        dialog = QDialog(self)
        dialog.setWindowTitle("Versandkopie prüfen")
        layout = QVBoxLayout(dialog)
        info = QLabel(summary)
        info.setWordWrap(True)
        layout.addWidget(info)
        pages = QComboBox()
        pages.addItems([f"Seite {index + 1}" for index in range(len(doc))])
        layout.addWidget(pages)
        area = QScrollArea()
        area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image = QLabel()
        area.setWidget(image)
        layout.addWidget(area, 1)

        def show_page(index):
            page = doc[index]
            rendered = pixmap(page, min(490 / page.rect.width, 550 / page.rect.height))
            image.setPixmap(rendered)
            image.resize(rendered.size())

        pages.currentIndexChanged.connect(show_page)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Kopie speichern")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(660, 720)
        show_page(0)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def verify_redaction(self):
        folder = str(self.current.path.parent) if self.current else ""
        path, _ = QFileDialog.getOpenFileName(self, "Gespeicherte geschwärzte Kopie auswählen", folder, "PDF-Dateien (*.pdf)")
        if not path:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Schwärzung prüfen",
                                                "Welche Begriffe sollen aus der Kopie entfernt sein? Ein Begriff je Zeile.")
        terms = list(dict.fromkeys(term.strip() for term in text.splitlines() if term.strip()))
        if not ok or not terms:
            return
        try:
            with pdf.open(path) as copy:
                if not copy.is_pdf or copy.needs_pass:
                    raise ValueError("Bitte eine lesbare PDF ohne Passwortschutz auswählen.")
                findings = redaction_findings(copy, terms)
        except Exception as exc:
            self.error(exc)
            return
        report = QMessageBox(self)
        report.setWindowTitle("Abschlussprüfung der Schwärzung")
        report.setText(f"{Path(path).name}\n" + (f"{len(findings)} von {len(terms)} Suchbegriffen gefunden."
                                                  if findings else "Keiner der Suchbegriffe wurde gefunden."))
        report.setInformativeText("Geprüft wurden auslesbarer Text, OCR-Text, Kommentare, Formularfelder, "
                                  "Lesezeichen, Metadaten und Anlagennamen. Bilder ohne Textebene und "
                                  "der Inhalt von Anlagen wurden nicht geprüft.")
        if findings:
            report.setDetailedText("\n\n".join(f"{term}\n" + "\n".join(locations)
                                                for term, locations in findings.items()))
        report.exec()


    def output_path(self, suffix, title):
        original = self.current.path
        suggested = original.with_stem(original.stem + suffix)
        target, _ = QFileDialog.getSaveFileName(self, title, str(suggested), "PDF-Dateien (*.pdf)")
        if not target:
            return None
        path = Path(target if target.lower().endswith(".pdf") else target + ".pdf")
        if any(path.resolve() == session.path.resolve() for session in self.sessions):
            self.error("Bitte einen anderen Namen als den einer geöffneten PDF wählen.")
            return None
        return path

    def extract(self):
        session = self.current
        if not session:
            return
        selection, ok = QInputDialog.getText(self, "Seiten extrahieren", "Seiten und Reihenfolge, z. B. 1, 3-5, 8",
                                             text=str(session.page + 1))
        if not ok:
            return
        try:
            indices = page_selection(selection, len(session.doc))
            target = self.output_path(" - Auszug", "Ausgewählte Seiten speichern")
            if target:
                with extract_pages(session.doc, indices) as result:
                    save_copy(result, target)
                self.statusBar().showMessage(f"{len(indices)} Seiten gespeichert: {target.name}", 6500)
        except Exception as exc:
            self.error(exc)

    def compress(self):
        session = self.current
        if not session:
            return
        profile, ok = QInputDialog.getItem(self, "Scan-PDF verkleinern",
                                          "Bildqualität der Kopie (Text und Formularfelder bleiben erhalten)",
                                          list(COMPRESSION), editable=False)
        if not ok:
            return
        target = self.output_path(" - kompakt", "Komprimierte Kopie speichern")
        if not target:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            before, after = compressed_copy(session.doc, target, profile)
        except Exception as exc:
            self.error(exc)
            return
        finally:
            QApplication.restoreOverrideCursor()
        saved = max(0, round(100 * (before - after) / before))
        message = (f"{target.name}\n{before / 1024:.0f} KB → {after / 1024:.0f} KB · {saved} % kleiner"
                   if saved else f"{target.name}\nMit diesem Profil wurde die Kopie nicht merklich kleiner.")
        QMessageBox.information(self, "Komprimierung abgeschlossen", message)

    def compare(self):
        session = self.current
        if not session:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Neuere PDF für den Vergleich auswählen", "", "PDF-Dateien (*.pdf)")
        if not path:
            return
        try:
            with pdf.open(path) as other:
                if not other.is_pdf or other.needs_pass or not len(other):
                    raise ValueError("Bitte eine lesbare PDF ohne Passwortschutz auswählen.")
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    pairs = align_pages(session.doc, other)
                    dialog = CompareDialog(self, session.doc, other, (session.path.name, Path(path).name), pairs)
                finally:
                    QApplication.restoreOverrideCursor()
                dialog.exec()
        except Exception as exc:
            self.error(exc)

    def ocr(self, whole_document=False):
        session = self.current
        if not session:
            return
        choices = {"Deutsch und Englisch": "deu+eng", "Deutsch": "deu", "Englisch": "eng"}
        title = "Sprache für das ganze Dokument" if whole_document else "Sprache der aktuellen Scan-Seite"
        choice, ok = QInputDialog.getItem(self, "Texterkennung", title, list(choices), editable=False)
        if not ok:
            return
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        indices = range(len(session.doc)) if whole_document else [session.page]
        try:
            session.checkpoint()
        except Exception as exc:
            self.error(exc)
            return
        progress = QProgressDialog("Scan-Seiten werden erkannt …", "Abbrechen", 0, len(indices), self)
        progress.setWindowTitle("Texterkennung")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0 if whole_document else 600)
        added, skipped, failed, cancelled = 0, 0, [], False
        try:
            for offset, index in enumerate(indices):
                progress.setLabelText(f"Seite {index + 1} von {len(session.doc)}")
                progress.setValue(offset)
                QApplication.processEvents()
                if progress.wasCanceled():
                    cancelled = True
                    break
                try:
                    page = session.doc[index]
                    if whole_document and page.get_text().strip():
                        skipped += 1
                        continue
                    recognize_page(page, choices[choice], root / "tessdata")
                    added += 1
                except Exception as exc:
                    failed.append((index + 1, str(exc)))
        finally:
            progress.close()
        if added:
            self.changed()
        else:
            session.history.pop()
        summary = [f"{added} Seiten mit neuer Textebene."]
        if skipped:
            summary.append(f"{skipped} Seiten hatten bereits Text.")
        if failed:
            summary.append(f"{len(failed)} Seiten ohne neue Textebene.")
        if cancelled:
            summary.append("Abgebrochen. Fertige Seiten bleiben bearbeitet.")
        report = QMessageBox(self)
        report.setWindowTitle("Texterkennung abgeschlossen")
        report.setText("\n".join(summary))
        if failed:
            report.setDetailedText("\n".join(f"Seite {index}: {reason}" for index, reason in failed))
        report.exec()


    def correct_text(self, page, point):
        target = next((span for direction, span in text_spans(page)
                       if direction == (1.0, 0.0) and span.get("alpha", 255)
                       and pdf.Rect(span["bbox"]).contains(point)), None)
        if not target:
            raise ValueError("Hier wurde kein horizontaler Text gefunden.")
        value, ok = QInputDialog.getText(self, "Text korrigieren",
                                        "Neuer Text (wird in einer Standardschrift gesetzt)",
                                        text=target["text"])
        if not ok or value == target["text"]:
            return False
        session = self.current
        session.checkpoint()
        try:
            replace_span(page, target, value)
        except UnicodeEncodeError as exc:
            session.undo()
            raise ValueError("Die Korrektur unterstützt deutsche und westeuropäische Zeichen.") from exc
        except Exception:
            session.undo()
            raise
        return True


    def changed(self, refresh=True):
        self.current.changed = True
        self.tabs.setTabText(self.tabs.currentIndex(), self.current.path.name + " *")
        if refresh:
            self.refresh_thumbs()
        self.render()

    def edit_page(self, operation, amount=0):
        session = self.current
        if not session:
            return
        index = session.page
        count = len(session.doc)
        if operation == "move" and not 0 <= index + amount < count:
            return
        if operation == "delete":
            if count == 1:
                self.error("Die letzte Seite kann nicht gelöscht werden.")
                return
            if QMessageBox.question(self, "Seite löschen", f"Seite {index + 1} löschen?") != QMessageBox.StandardButton.Yes:
                return
        try:
            session.checkpoint()
            if operation == "rotate":
                page = session.doc[index]
                page.set_rotation((page.rotation + amount) % 360)
            elif operation == "delete":
                session.doc.delete_page(index)
                session.page = min(index, count - 2)
            elif operation == "move":
                order = list(range(count))
                order[index], order[index + amount] = order[index + amount], order[index]
                session.doc.select(order)
                session.page += amount
            self.changed()
        except Exception as exc:
            self.error(exc)

    def append(self):
        if not self.current:
            return
        path, _ = QFileDialog.getOpenFileName(self, "PDF anhängen", "", "PDF-Dateien (*.pdf)")
        if not path:
            return
        try:
            with pdf.open(path) as other:
                if not other.is_pdf or other.needs_pass:
                    raise ValueError("Bitte eine PDF ohne Passwortschutz auswählen.")
                self.current.checkpoint()
                self.current.doc.insert_pdf(other)
            self.changed()
        except Exception as exc:
            self.error(exc)

    def search(self):
        session = self.current
        if not session:
            return
        query, ok = QInputDialog.getText(self, "Text suchen", "Suchbegriff", text=self.search_text)
        if not ok:
            return
        previous = self.search_text
        self.search_text = query.strip()
        if self.search_text:
            start = session.page + int(previous == self.search_text)
            for offset in range(len(session.doc)):
                index = (start + offset) % len(session.doc)
                if session.doc[index].search_for(self.search_text):
                    self.thumbs.setCurrentRow(index)
                    self.render()
                    return
            self.statusBar().showMessage("Kein Treffer.", 4500)
        self.render()

    def undo(self):
        if self.current:
            self.current.undo()
            marker = " *" if self.current.changed else ""
            self.tabs.setTabText(self.tabs.currentIndex(), self.current.path.name + marker)
            self.refresh_thumbs()
            self.render()

    def save(self):
        session = self.current
        if not session:
            return False
        suggested = session.path.with_stem(session.path.stem + " - bearbeitet")
        target, _ = QFileDialog.getSaveFileName(self, "PDF-Kopie speichern", str(suggested), "PDF-Dateien (*.pdf)")
        if not target:
            return False
        if not target.lower().endswith(".pdf"):
            target += ".pdf"
        if Path(target).resolve() == session.path.resolve():
            self.error("Bitte für die Kopie einen anderen Dateinamen wählen.")
            return False
        try:
            save_copy(session.doc, target)
        except Exception as exc:
            self.error(exc)
            return False
        self.statusBar().showMessage(f"Kopie gespeichert: {Path(target).name}", 6500)
        session.changed = False
        session.history = [(data, page, True) for data, page, _ in session.history]
        self.tabs.setTabText(self.tabs.currentIndex(), session.path.name)
        return True

    def confirm_close(self, index):
        session = self.sessions[index]
        if not session.changed:
            return True
        self.tabs.setCurrentIndex(index)
        result = QMessageBox.question(self, "Änderungen speichern?", f"Bearbeitete Kopie von {session.path.name} speichern?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                      QMessageBox.StandardButton.Cancel)
        if result == QMessageBox.StandardButton.Save:
            return self.save()
        return result == QMessageBox.StandardButton.Discard

    def close_tab(self, index):
        if self.confirm_close(index):
            self.sessions.pop(index).doc.close()
            self.tabs.removeTab(index)
            self.switch_doc(self.tabs.currentIndex())

    def closeEvent(self, event):
        for index in range(len(self.sessions)):
            if not self.confirm_close(index):
                event.ignore()
                return
        event.accept()

    def print_dialog(self, samsung=False):
        session = self.current
        if not session:
            return
        devices = QPrinterInfo.availablePrinters()
        if not devices:
            self.error("Windows meldet keinen installierten Drucker.")
            return
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        if samsung:
            known = settings.value("samsung_printer", "", str)
            candidates = [device for device in devices if "samsung" in device.printerName().casefold()]
            device = next((device for device in candidates if device.printerName() == known), None)
            if device is None:
                device = next((device for device in candidates if "clx-3300" in device.printerName().casefold()), None)
            if device is None and candidates:
                device = candidates[0]
            if device is None:
                self.error("Windows meldet keinen Samsung-Drucker. Verwende Datei → Drucken für einen anderen Drucker.")
                return
            if QPrinter.DuplexMode.DuplexLongSide not in device.supportedDuplexModes():
                self.error("Der ausgewählte Samsung-Treiber meldet keinen Duplexdruck an der langen Kante.")
                return
            printer = QPrinter(device, QPrinter.PrinterMode.HighResolution)
            printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide)
            settings.setValue("samsung_printer", device.printerName())
        else:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(session.path.name)
        dialog = QPrintDialog(printer, self)
        dialog.setMinMax(1, len(session.doc))
        dialog.setFromTo(1, len(session.doc))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        start, end = 1, len(session.doc)
        if printer.printRange() == QPrinter.PrintRange.PageRange:
            start = max(1, printer.fromPage())
            end = min(end, printer.toPage())
        indices = list(range(start - 1, end))
        options = QDialog(self)
        options.setWindowTitle("Duplex und Seitenfolge")
        form = QFormLayout(options)
        duplex = QComboBox()
        device = QPrinterInfo.printerInfo(printer.printerName())
        supported = device.supportedDuplexModes()
        duplex.addItem("Einseitig", QPrinter.DuplexMode.DuplexNone)
        for label, mode in [("Lange Kante (wie ein Buch)", QPrinter.DuplexMode.DuplexLongSide),
                            ("Kurze Kante (wie ein Kalender)", QPrinter.DuplexMode.DuplexShortSide)]:
            if mode in supported:
                duplex.addItem(label, mode)
        duplex.setCurrentIndex(max(0, duplex.findData(printer.duplex())))
        form.addRow("Duplex", duplex)
        subset = QComboBox()
        subset.addItems(["Alle ausgewählten Seiten", "Nur ungerade Seiten", "Nur gerade Seiten"])
        subset.setToolTip("Für manuelles Wenden kannst du zwei getrennte Druckaufträge starten.")
        form.addRow("Seiten", subset)
        order = QComboBox()
        order.addItems(["Vorwärts", "Rückwärts"])
        form.addRow("Reihenfolge", order)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Vorschau")
        buttons.accepted.connect(options.accept)
        buttons.rejected.connect(options.reject)
        form.addRow(buttons)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        if subset.currentIndex():
            indices = [index for index in indices if (index + 1) % 2 == (subset.currentIndex() % 2)]
        reverse = order.currentIndex() == 1
        if reverse:
            indices.reverse()
        if not indices:
            self.error("Die Auswahl enthält keine Seiten.")
            return
        printer.setDuplex(duplex.currentData())
        printer.setPageOrder(QPrinter.PageOrder.FirstPageFirst)
        try:
            if not self.confirm_print(session.doc, indices, printer):
                return
            print_pages(session.doc, printer, indices)
        except Exception as exc:
            self.error(exc)
            return
        self.statusBar().showMessage("Druckauftrag übergeben.", 6500)

    def confirm_print(self, doc, indices, printer):
        dialog = QDialog(self)
        dialog.setWindowTitle("Druckvorschau")
        layout = QVBoxLayout(dialog)
        duplex = printer.duplex() != QPrinter.DuplexMode.DuplexNone
        sheets = print_sheets(indices, duplex)
        edge = {QPrinter.DuplexMode.DuplexLongSide: "lange Kante",
                QPrinter.DuplexMode.DuplexShortSide: "kurze Kante"}.get(printer.duplex(), "automatisch")
        description = f"{printer.printerName()} · {len(sheets)} Blatt pro Exemplar"
        if duplex:
            description += f" · Duplex, {edge}"
        layout.addWidget(QLabel(description))
        panels = QHBoxLayout()
        layout.addLayout(panels, 1)
        labels = []
        for _ in range(2 if duplex else 1):
            panel = QVBoxLayout()
            heading = QLabel()
            panel.addWidget(heading, alignment=Qt.AlignmentFlag.AlignHCenter)
            area = QScrollArea()
            area.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image = QLabel()
            area.setWidget(image)
            panel.addWidget(area, 1)
            panels.addLayout(panel, 1)
            labels.append((heading, image))
        nav = QHBoxLayout()
        prev = QPushButton("‹")
        next_button = QPushButton("›")
        count = QLabel()
        nav.addWidget(prev)
        nav.addWidget(count)
        nav.addWidget(next_button)
        nav.addStretch()
        layout.addLayout(nav)
        position = 0

        def show_sheet(delta=0):
            nonlocal position
            position = min(len(sheets) - 1, max(0, position + delta))
            for side, ((heading, image), index) in enumerate(zip(labels, sheets[position])):
                label = "Vorderseite" if side == 0 else "Rückseite"
                heading.setText(f"{label} · PDF-Seite {index + 1}" if index is not None else f"{label} · bleibt leer")
                preview = print_preview_image(doc, index, printer, 340 if duplex else 390)
                image.setPixmap(preview)
                image.resize(preview.size())
            count.setText(f"Blatt {position + 1} von {len(sheets)}")
            prev.setEnabled(position > 0)
            next_button.setEnabled(position < len(sheets) - 1)

        prev.clicked.connect(lambda: show_sheet(-1))
        next_button.clicked.connect(lambda: show_sheet(1))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Jetzt drucken")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(850 if duplex else 550, 660)
        show_sheet()
        return dialog.exec() == QDialog.DialogCode.Accepted


    def check_online(self):
        self.statusBar().showMessage("Suche nach Updates …", 6000)
        self.release_check.check(manual=True)

    def show_online_update(self, update, success, manual):
        if prepared_update(VERSION):
            self.statusBar().showMessage("Ein Update ist bereit und startet beim nächsten Öffnen.", 8000)
            return
        if not update:
            if manual:
                message = ("Du hast die aktuelle Version." if success
                           else "Die Update-Suche war gerade nicht erreichbar. Bitte versuche es später erneut.")
                self.statusBar().showMessage(message, 8000)
            return
        if self.update_process is not None:
            self.statusBar().showMessage("Ein Update wird bereits vorbereitet.", 8000)
            return
        version, digest, size = update
        worker = Path(__file__).with_name("update_worker.py")
        try:
            self.update_process = subprocess.Popen(
                [sys.executable, str(worker), version, digest, str(size)],
                creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            self.statusBar().showMessage("Das Update konnte nicht vorbereitet werden.", 8000)
            return
        self.statusBar().showMessage(f"Update {version} wird im Hintergrund vorbereitet.", 8000)
        self.update_timer.start(1000)

    def finish_online_update(self):
        result = self.update_process.poll()
        if result is None:
            return
        self.update_process = None
        self.update_timer.stop()
        if result == 0:
            message = "Update bereit. Beim nächsten Öffnen startet die neue Version."
        elif result == 2:
            message = "Ein anderes Fenster bereitet das Update bereits vor."
        else:
            message = "Update konnte nicht vorbereitet werden. Beim nächsten Start wird erneut geprüft."
        self.statusBar().showMessage(message, 10000)


    def register_pdf_app(self):
        import winreg

        launcher = os.environ.get("PDF_WERKBANK_ENTRYPOINT") or os.environ.get("PDF_WERKBANK_LAUNCHER")
        icon = Path(__file__).with_name("pdf-werkbank.ico")
        if not launcher or not Path(launcher).is_file() or not icon.is_file():
            self.error("Bitte PDF Werkbank über die Windows-EXE starten.")
            return

        def write(key, name, value):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
                winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

        try:
            prog = r"Software\Classes\PDFWerkbank.PDF"
            capabilities = r"Software\PDF Werkbank\Capabilities"
            write(prog, "", "PDF-Dokument")
            write(prog + r"\DefaultIcon", "", f'"{icon}",0')
            write(prog + r"\shell\open\command", "", f'"{launcher}" "%1"')
            write(capabilities, "ApplicationName", "PDF Werkbank")
            write(capabilities, "ApplicationDescription", "PDF-Dateien öffnen und bearbeiten")
            write(capabilities + r"\FileAssociations", ".pdf", "PDFWerkbank.PDF")
            write(r"Software\RegisteredApplications", "PDF Werkbank", capabilities)
            os.startfile("ms-settings:defaultapps?registeredAppUser=PDF%20Werkbank")
        except OSError as exc:
            self.error(exc)


    def choose_update_folder(self):
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        folder = QFileDialog.getExistingDirectory(self, "Vertrauenswürdigen Update-Ordner auswählen",
                                                   settings.value("updates", "", str))
        if folder:
            result = QMessageBox.question(
                self, "Automatische Updates",
                "Beim Start führt PDF Werkbank neuere Programmdateien aus diesem Ordner aus. "
                "Wähle einen Ordner, in den nur du oder eine vertrauenswürdige Person schreiben kann. Aktivieren?")
            if result == QMessageBox.StandardButton.Yes:
                settings.setValue("updates", folder)
                self.statusBar().showMessage("Automatische Updates aus dem gewählten Ordner aktiviert.", 6500)
        elif settings.value("updates", "", str):
            if QMessageBox.question(self, "Updates", "Automatische Updates deaktivieren?") == QMessageBox.StandardButton.Yes:
                settings.remove("updates")


def find_update(folder, version=VERSION):
    matches = []
    for candidate in Path(folder).glob("PDF-Werkbank-*.exe"):
        match = re.fullmatch(r"PDF-Werkbank-(\d+)\.(\d+)\.(\d+)\.exe", candidate.name)
        if match and candidate.is_file() and not candidate.is_symlink():
            newer = tuple(map(int, match.groups()))
            if newer > version:
                matches.append((newer, candidate))
    return max(matches, default=(version, None))[1]


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Werkbank")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("pdf-werkbank.ico"))))
    settings = QSettings("PDF Werkbank", "PDF Werkbank")
    folder = settings.value("updates", "", str)
    forwarded = os.environ.pop("PDF_WERKBANK_UPDATE_FORWARDED", "")
    launcher = os.environ.get("PDF_WERKBANK_LAUNCHER")
    if launcher and not forwarded:
        update = prepared_update(VERSION)
        if update:
            try:
                child = subprocess.Popen([str(update), *sys.argv[1:]])
                try:
                    if child.wait(timeout=3) == 0:
                        return 0
                except subprocess.TimeoutExpired:
                    return 0
            except OSError:
                pass
    if folder and not forwarded and (launcher or getattr(sys, "frozen", False)):
        try:
            update = find_update(folder)
            if update and update.resolve() != Path(launcher or sys.executable).resolve():
                env = os.environ.copy()
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                env["PDF_WERKBANK_UPDATE_FORWARDED"] = "1"
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
                subprocess.Popen([str(update), *sys.argv[1:]], env=env, cwd=update.parent)
                return 0
        except OSError:
            pass
    window = Window()
    for path in sys.argv[1:]:
        if Path(path).is_file():
            window.open_path(path)
    window.show()
    QTimer.singleShot(1200, window.release_check.check)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

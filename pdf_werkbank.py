# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime
from difflib import SequenceMatcher
from itertools import zip_longest
from pathlib import Path
from threading import Event
from xml.etree import ElementTree

import pymupdf as pdf
from PySide6.QtCore import QDate, QEventLoop, QItemSelectionModel, QLockFile, QMimeData, QPoint, QPointF, QRect, QRectF, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QDrag, QIcon, QImage, QPageLayout, QPageSize, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPlainTextEdit, QListWidget, QListWidgetItem, QMainWindow,
    QHeaderView, QMenu, QMessageBox, QProgressDialog, QPushButton, QRubberBand, QScrollArea, QSplitter,
    QSlider, QSpinBox, QTabBar, QTableWidget, QTableWidgetItem, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from local_ai import LocalTextEngine, ModelDownload, model_path, model_ready

from online_updates import ReleaseCheck
from update_worker import SIGNER, digest_file, prepared_update, runtime_ready, verified_launcher, version_tuple

from office_export import (export_word, export_word_layout, export_excel, export_form_entry, export_invoices, export_linked_sheet,
                           fill_form, form_entry_columns, form_fields, form_row_plan, map_form_values, read_form_sheet,
                           read_form_values, workbook_sheets)
from intake import (EXPLORER_COMMANDS, audit_pdf, duplicate_groups, explorer_paths, inspect_duplicate,
                    install_explorer_commands, invoice_match_key, invoice_xml, merge_pdfs, read_invoice)
from invoice_validation import KOSIT_FOLDER, RULES_VERSION, ensure_validator, validate_invoice_xml, xml_root
from invoice_watch import (IntakeSettler, archive_setting_key, archive_watched_invoice,
                           invoice_archive_state, read_watched_invoice)
from pdf_standards import accessibility_blocks, export_accessible, export_archive, export_vector_archive, validate_standard
from pdf_signatures import sign_pdf, verify_pdf
from page_tools import (booklet_sheets, crop_page, edit_image, feeder_pages, fixed_form, image_at, impose_booklet,
                        missing_required_fields, ocr_lines, replace_ocr_layer, scan_command, scanned_document, twain_command)
from assistants import (add_suggested_field, append_scan, clean_hidden, form_candidates, redaction_candidates,
                        scan_quality, similar_scan, text_changes)
from recovery import Recovery, atomic_write
from document_review import attachments, comments, insert_labels, label_conflicts, page_labels
from filing import (archive_target, clear_index, filename_hint, file_signature, folder_matches, indexed_matches,
                    pdf_files, scan_breaks, scan_ranges, search_rectangles)
from preferences import (DEFAULT_FAVORITES, FORM_TEMPLATES, GROUPS, export_package, form_templates, import_groups,
                         read_package, validate_form_templates)
from archive_run import ArchiveRun
from form_run import FormRun
from work_backup import (BACKUP_GROUPS, MAX_PASSWORD, MIN_PASSWORD, BackupPasswordRequired,
                         app_backup_root, archive_remap, automatic_backup, backup_check_rows, backup_preview,
                         default_scan_inbox, location_plan, location_roots, read_backup, reminder_date,
                         restore_backup, restore_locations, write_backup)
from plan_measure import (EPSILON, MAX_VERTICES, MEASURE_MODES, METRES_PER_POINT, Measurement,
                          PlanMeasurements, calibrated_scale, decode_plans, distance, draw_plan_copy, encode_plans,
                          format_measure, plan_entries, plan_page_key, plan_token, polygon_area, store_path, stored_bytes,
                          alignment, decode_project, encode_project, measurement_group_totals, measurement_rows, snap_point, vector_geometry,
                          CountedSymbol, add_cutout, move_vertex, numbered_symbols, polygon_perimeter)

VERSION = (1, 38, 0)
POINTS_PER_MM = 72 / 25.4
THUMB_HEIGHT = 122
RENDER_BUDGET = 64 * 1024 * 1024

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


def plan_difference(first, second):
    first = first.convertToFormat(QImage.Format.Format_Grayscale8)
    second = second.convertToFormat(QImage.Format.Format_Grayscale8)
    width, height = first.width(), first.height()
    left, right = first.constBits(), second.constBits()
    result = bytearray(width * height * 3)

    def nearby(data, stride, x, y):
        offset = y * stride + x
        return min(data[offset], data[offset - (x > 0)], data[offset + (x + 1 < width)],
                   data[offset - (stride if y > 0 else 0)], data[offset + (stride if y + 1 < height else 0)])

    a, b = first.bytesPerLine(), second.bytesPerLine()
    for y in range(height):
        for x in range(width):
            old, new = left[y * a + x], right[y * b + x]
            shade = 155 + min(old, new) * 100 // 255
            color = (shade, shade, shade)
            if new - old > 55 and nearby(right, b, x, y) - old > 55:
                color = (194, 32, 47)
            elif old - new > 55 and nearby(left, a, x, y) - new > 55:
                color = (0, 112, 67)
            index = (y * width + x) * 3
            result[index], result[index + 1], result[index + 2] = color
    return QImage(bytes(result), width, height, width * 3, QImage.Format.Format_RGB888).copy()


def pixmap(page, scale, annotations=True):
    data = page.get_pixmap(matrix=pdf.Matrix(scale, scale), alpha=False, annots=annotations)
    image = QImage(data.samples, data.width, data.height, data.stride, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image)


def save_copy(doc, target, password=None, **options):
    target = Path(target)
    fd, temp = tempfile.mkstemp(suffix=".pdf", dir=target.parent)
    os.close(fd)
    try:
        doc.save(temp, garbage=4, deflate=True, **options)
        with pdf.open(temp) as check:
            if check.needs_pass and (password is None or not check.authenticate(password)):
                raise ValueError("Die gespeicherte Kopie konnte mit dem Passwort nicht geöffnet werden.")
            if password is not None and not check.needs_pass:
                raise ValueError("Die gespeicherte Kopie ist nicht passwortgeschützt.")
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
        next_change = buttons.addButton("Nächste Änderung", QDialogButtonBox.ButtonRole.ActionRole)
        report = buttons.addButton("Änderungsbericht speichern", QDialogButtonBox.ButtonRole.ActionRole)
        next_change.clicked.connect(self.next_change)
        report.clicked.connect(self.save_report)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.pages.currentIndexChanged.connect(self.render_pair)
        first_change = next((index for index, pair in enumerate(pairs) if pair[2] != "equal"), 0)
        if first_change:
            self.pages.setCurrentIndex(first_change)
        else:
            self.render_pair(0)

    def next_change(self):
        current = self.pages.currentIndex()
        for offset in range(1, len(self.pairs) + 1):
            index = (current + offset) % len(self.pairs)
            if self.pairs[index][2] != "equal":
                self.pages.setCurrentIndex(index)
                return

    def save_report(self):
        target, _ = QFileDialog.getSaveFileName(self, "Änderungsbericht speichern", "Änderungsbericht.txt",
                                                "Textdateien (*.txt)")
        if not target:
            return
        lines = ["PDF-Vergleich", "Links: bisherige Fassung. Rechts: neue Fassung.", ""]
        for index, (a, b, tag) in enumerate(self.pairs):
            if tag == "equal":
                continue
            lines.append(self.pages.itemText(index))
            changes = list(text_changes(self.docs[0][a] if a is not None else None,
                                        self.docs[1][b] if b is not None else None))
            if not changes:
                lines.append("  Seitenbild, Format oder Position prüfen.")
            for _, old, new in changes:
                for prefix, words in (("−", old), ("+", new)):
                    if words:
                        lines.append(f"  {prefix} " + " ".join(word[4] for word in words))
        try:
            path = Path(target)
            if path.suffix.lower() != ".txt":
                raise ValueError("Bitte einen Dateinamen mit der Endung .txt wählen.")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Bericht konnte nicht gespeichert werden", str(exc))

    def render_pair(self, index):
        a, b, _ = self.pairs[index]
        left = self.docs[0][a] if a is not None else None
        right = self.docs[1][b] if b is not None else None
        images, changed = compare_images(left, right)
        changes = list(text_changes(left, right))
        scale = 660 / max(max(page.rect.width, page.rect.height) for page in (left, right) if page is not None)
        for side, (page, image) in enumerate(zip((left, right), images)):
            if page is None or not changes:
                continue
            painter = QPainter(image)
            color = QColor(200, 35, 25, 135) if side == 0 else QColor(10, 135, 65, 135)
            for change in changes:
                for word in change[side + 1]:
                    box = pdf.Rect(word[:4]) * page.rotation_matrix
                    painter.fillRect(QRect(round(box.x0 * scale), round(box.y0 * scale),
                                           round(box.width * scale), round(box.height * scale)), color)
            painter.end()
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
        self.result.setText(f"{status} · {len(changes)} geänderte Textstellen")
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


def replace_span(page, target, value, style=None):
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
    name, size, color = style or ("helv", target["size"], pdf.sRGB_to_pdf(target["color"]))
    new_width = pdf.get_text_length(value, fontname=name, fontsize=size)
    if style and (new_width > box.width or size > target["size"] * 1.5):
        raise ValueError("Die gewählte Schrift passt nicht in diesen Textbereich.")
    if new_width > box.width:
        size *= box.width / new_width
        if size < target["size"] * 0.8:
            raise ValueError("Der neue Text ist zu lang für den vorhandenen Platz.")
    page.add_redact_annot(band, fill=False, cross_out=False)
    page.apply_redactions(images=pdf.PDF_REDACT_IMAGE_NONE, graphics=pdf.PDF_REDACT_LINE_ART_NONE)
    if value:
        page.insert_text(target["origin"], value, fontsize=size, fontname=name, color=color)


def paragraph_at(page, point):
    flags = pdf.TEXTFLAGS_DICT & ~pdf.TEXT_PRESERVE_IMAGES
    for block in page.get_text("dict", flags=flags)["blocks"]:
        if not pdf.Rect(block["bbox"]).contains(point):
            continue
        lines = block.get("lines", [])
        if not lines or any(line["dir"] != (1.0, 0.0) for line in lines):
            continue
        visible = [[span for span in line["spans"] if span.get("alpha", 255)] for line in lines]
        spans = [span for line in visible for span in line]
        if spans:
            return {"text": "\n".join("".join(span["text"] for span in line) for line in visible),
                    "size": spans[0]["size"], "paragraph": spans, "bbox": block["bbox"]}
    raise ValueError("Hier wurde kein horizontaler Textabsatz gefunden.")


def replace_paragraph(page, target, value, style, bounds=None):
    check_pending_redactions(page)
    font, size, color = style
    bounds = pdf.Rect(target["bbox"] if bounds is None else bounds)
    if bounds.is_empty or not (page.rect * page.derotation_matrix).contains(bounds):
        raise ValueError("Der Textrahmen muss vollständig auf der Seite liegen.")
    trial = page.new_shape()
    if trial.insert_textbox(bounds, value, fontname=font, fontsize=size, color=color) < 0:
        raise ValueError("Der neue Absatz passt nicht in den gewählten Textrahmen.")
    removed = target["paragraph"]
    if bounds != pdf.Rect(target["bbox"]):
        if any(bounds.intersects(pdf.Rect(span["bbox"])) for _, span in text_spans(page)
               if span not in removed and span.get("alpha", 255)):
            raise ValueError("Der neue Textrahmen überlagert anderen Text.")
    bands = []
    for span in removed:
        box = pdf.Rect(span["bbox"])
        y = min(box.y1 - 0.2, max(box.y0 + 0.2, span["origin"][1] - span["size"] * 0.24))
        bands.append(pdf.Rect(box.x0 + 0.1, y - 0.12, box.x1 - 0.1, y + 0.12))
    if any(band.intersects(pdf.Rect(span["bbox"])) for _, span in text_spans(page)
           if span not in removed for band in bands):
        raise ValueError("Der Absatz überlagert anderen Text und kann nicht sicher ersetzt werden.")
    for band in bands:
        page.add_redact_annot(band, fill=False, cross_out=False)
    page.apply_redactions(images=pdf.PDF_REDACT_IMAGE_NONE, graphics=pdf.PDF_REDACT_LINE_ART_NONE)
    if page.insert_textbox(bounds, value, fontname=font, fontsize=size, color=color) < 0:
        raise ValueError("Der Absatz konnte nicht gesetzt werden.")



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



class TransferList(QListWidget):
    MIME = "application/x-pdf-werkbank-page"

    def __init__(self, transfer):
        super().__init__()
        self.transfer = transfer
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def startDrag(self, _):
        if self.currentRow() < 0:
            return
        mime = QMimeData()
        mime.setData(self.MIME, str(self.currentRow()).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event):
        if isinstance(event.source(), TransferList) and event.source() is not self and event.mimeData().hasFormat(self.MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if not isinstance(event.source(), TransferList) or event.source() is self or not event.mimeData().hasFormat(self.MIME):
            return
        point = event.position().toPoint()
        item = self.itemAt(point)
        index = self.row(item) + int(point.y() >= self.visualItemRect(item).center().y()) if item else self.count()
        row = int(bytes(event.mimeData().data(self.MIME)))
        if self.transfer(event.source(), row, self, index):
            event.acceptProposedAction()


class Session:
    def __init__(self, path, password=None, snapshot=None):
        self.path = Path(path)
        self.doc = pdf.open(stream=snapshot, filetype="pdf") if snapshot is not None else pdf.open(path)
        self.password = password
        if not self.doc.is_pdf or not len(self.doc):
            self.doc.close()
            raise ValueError("Bitte eine lesbare PDF auswählen.")
        if self.doc.needs_pass:
            result = self.doc.authenticate(password or "")
            if not result:
                self.doc.close()
                raise PermissionError("Für diese PDF ist das richtige Passwort erforderlich.")
            if not (result & 4) and not (self.doc.permissions & pdf.PDF_PERM_MODIFY):
                self.doc.close()
                raise PermissionError("Diese PDF beschränkt das Bearbeiten. Bitte das Eigentümerpasswort verwenden.")
        else:
            self.password = None
        self.page = 0
        self.changed = False
        self.history = []
        self.redo_stack = []
        self.recovery_id = uuid.uuid4().hex
        self.recovery_path = self.path.resolve()
        self.recovery_dirty = True
        self.plan_measurements = {}
        self.measure_undo = {}
        self.measure_geometry = {}
        self.plan_saved_token = None
        self.plan_source_stamp = file_signature(self.path) if snapshot is None else None

    def snapshot(self, label):
        return [self.doc.tobytes(encryption=pdf.PDF_ENCRYPT_KEEP), self.page, self.changed, label]

    def checkpoint(self, label="PDF bearbeiten"):
        self.history.append(self.snapshot(label))
        del self.history[:-8]

    def restore_edit(self, forward=False, record=True):
        stack, other = (self.redo_stack, self.history) if forward else (self.history, self.redo_stack)
        if stack:
            content, page, changed, label = stack[-1]
            with_current = self.snapshot(label) if record else None
            replacement = pdf.open(stream=content, filetype="pdf")
            if replacement.needs_pass and not replacement.authenticate(self.password):
                replacement.close()
                raise ValueError("Die gesicherte Änderung konnte nicht geöffnet werden.")
            self.doc.close()
            self.doc = replacement
            self.page, self.changed = page, changed
            self.recovery_dirty = True
            stack.pop()
            if with_current is not None:
                other.append(with_current)
                del other[:-8]
            return label

    def undo(self):
        self.restore_edit(record=False)


class Canvas(QLabel):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.origin = None
        self.band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            self.window.paint_measurements(painter)
        finally:
            painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window.current:
            if self.window.mode in MEASURE_MODES:
                self.setFocus()
                self.window.measure_click(event.position())
                return
            self.origin = event.position().toPoint()
            self.band.setGeometry(QRect(self.origin, QSize()))
            if self.window.mode in ("Markieren", "Schwärzen", "Zuschneiden", "Link", "Feld"):
                self.band.show()

    def mouseMoveEvent(self, event):
        if self.window.mode in MEASURE_MODES and self.window.current:
            self.window.measure_pointer(event.position())
        elif self.origin is not None:
            self.band.setGeometry(QRect(self.origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        if self.origin is not None:
            origin, endpoint = self.origin, event.position().toPoint()
            rect = QRect(origin, endpoint).normalized()
            self.origin = None
            self.band.hide()
            self.window.annotate(rect, origin, endpoint)

    def leaveEvent(self, event):
        if self.window.measure_hover is not None:
            self.window.measure_hover = None
            self.window.measure_cursor = None
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if self.window.mode in MEASURE_MODES:
            if event.key() == Qt.Key.Key_Escape:
                self.window.clear_measure_draft()
                self.update()
                return
            if self.window.mode in ("Fläche messen", "Aussparung messen") and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.window.finish_area()
                return
        super().keyPressEvent(event)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sessions = []
        self.measure_points = []
        self.measure_edit = None
        self.measure_assist = "Automatisch"
        self.symbol_category = ""
        self.measure_cursor = None
        self.measure_snap_hit = False
        self.measure_hover = None
        self.recovery = None
        self.grid_mm = 0
        self.transfer_history = []
        self.transfer_redo = []
        self.zoom = 1.0
        self.scale = 1.0
        self.mode = "Lesen"
        self.search_text = ""
        self.form_preview = None
        self.form_jump = None
        self.open_work_dialog = None
        self.thumb_icons = OrderedDict()
        self.page_cache = OrderedDict()
        self.setWindowTitle("PDF Werkbank " + ".".join(map(str, VERSION)))
        self.release_check = ReleaseCheck(VERSION, self)
        self.release_check.finished.connect(self.show_online_update)
        self.update_process = None
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.finish_online_update)
        self.resize(1200, 820)
        self.setMinimumSize(820, 500)
        self.setStyleSheet(STYLE)
        self.setup_favorites()
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
                       ("Einstellungen übertragen", self.transfer_preferences), ("Arbeitsstand sichern", self.work_backup), ("Explorer-Befehle einrichten", self.configure_explorer),
                       ("Broschürendruck", self.booklet_dialog),
                       ("Arbeitsmappe speichern", self.save_workspace), ("Arbeitsmappe öffnen", self.open_workspace),
                       ("Samsung-Profil", lambda: self.print_dialog(samsung=True)), ("Online-Update suchen", self.check_online), ("Update-Info", self.update_info), ("Update-Ordner", self.choose_update_folder), ("PDF-Standard", self.register_pdf_app)]),
            ("Start", [("Offene Arbeiten", self.show_open_work), ("Öffnen", self.open_files), ("Kopie speichern", self.save), ("Rückgängig", self.undo),
                       ("Wiederholen", self.redo), ("Änderungsverlauf", self.edit_history),
                       ("Suchen", self.search), ("Ordner durchsuchen", self.search_folder), ("Drucken", self.print_dialog)]),
            ("Ansicht", [("Verkleinern", lambda: self.change_zoom(-0.2)), ("Vergrößern", lambda: self.change_zoom(0.2)),
                         ("Zu Seite springen", self.jump_to_page),
                         ("Seitenbreite", self.fit_width), ("Miniaturen", self.toggle_thumbs), ("Hilfsraster", self.configure_grid)]),
            ("Kommentar", [("Lesen", lambda: self.set_mode("Lesen")), ("Text markieren", lambda: self.set_mode("Markieren")),
                            ("Notiz setzen", lambda: self.set_mode("Notiz")), ("Kommentarübersicht", self.review_comments)]),
            ("Seiten", [("Nach links drehen", lambda: self.edit_page("rotate", -90)),
                        ("Nach rechts drehen", lambda: self.edit_page("rotate", 90)),
                        ("Nach vorn", lambda: self.edit_page("move", -1)), ("Nach hinten", lambda: self.edit_page("move", 1)),
                        ("Löschen", lambda: self.edit_page("delete")), ("PDF anhängen", self.append),
                        ("Seiten zwischen PDFs", self.page_transfer_dialog)]),
            ("Bearbeiten", [("Text korrigieren", lambda: self.set_mode("Text")), ("Text einfügen", lambda: self.set_mode("Text einfügen")), ("Text formatieren", lambda: self.set_mode("Textformat")), ("Absatz bearbeiten", lambda: self.set_mode("Absatz")), ("Schwärzen", lambda: self.set_mode("Schwärzen")), ("OCR für diese Seite", self.ocr), ("OCR für das Dokument", lambda: self.ocr(True)), ("OCR-Korrektur", self.correct_ocr)]),
            ("Werkzeuge", [("Seiten extrahieren", self.extract), ("Scan-PDF verkleinern", self.compress),
                           ("PDF-Versionen vergleichen", self.compare), ("Versandkopie", self.prepare_send),
                           ("Versandcheck", self.dispatch_check), ("Schwärzung prüfen", self.verify_redaction)]),
            ("Messen", [("Maßstab eingeben", self.set_plan_scale),
                        ("Kalibrieren", lambda: self.start_measure("Kalibrieren")),
                        ("Strecke messen", lambda: self.start_measure("Strecke messen")),
                        ("Fläche messen", lambda: self.start_measure("Fläche messen")),
                        ("Fläche abschließen", self.finish_area), ("Letzte Messung entfernen", self.remove_measurement),
                        ("Messliste", self.measurement_list), ("PDF mit Maßen", self.export_measurements),
                        ("Messoptionen", self.measurement_options), ("Pläne überlagern", self.plan_overlay),
                        ("Aufmaß", self.takeoff_tools), ("Messpunkt verschieben", lambda: self.start_measure("Messpunkt verschieben")),
                        ("Messpunkt zurücksetzen", self.undo_measure_point)]),
            ("Verbessern", [("Scan lesbarer machen", self.scan_cleanup), ("KI-Textvorschläge", self.ai_text)]),
            ("Bilder & Scan", [("Vom Scanner einlesen", self.scan_from_device), ("Mehrseitig scannen", self.scan_stack),
                              ("Samsung → Posteingang", self.quick_samsung_scan),
                              ("Scan-Posteingang", self.scan_inbox), ("Rechnungseingang", self.invoice_review), ("Rechnungsordner", self.invoice_folder), ("Seite zuschneiden", lambda: self.set_mode("Zuschneiden")),
                              ("Bild verschieben", lambda: self.set_mode("Bild verschieben")), ("Bild ersetzen", lambda: self.set_mode("Bild ersetzen")),
                              ("Bild positionieren", lambda: self.set_mode("Bild positionieren"))]),
            ("Dokument", [("Lesezeichen", self.bookmarks), ("Link einfügen", lambda: self.set_mode("Link")),
                           ("Seitenzahlen", self.add_page_numbers), ("Kopf- und Fußzeilen", self.headers_and_footers),
                           ("Anlagen", self.manage_attachments), ("Stempel", lambda: self.set_mode("Stempel"))]),
            ("Export & Schutz", [("Word", self.export_office), ("Excel", lambda: self.export_office(True)),
                                 ("Word mit Layout", lambda: self.export_office(layout=True)),
                                 ("Passwortschutz", self.protect_copy), ("Stapelverarbeitung", self.batch_process),
                                 ("Gespeicherte Abläufe", self.batch_profiles)]),
            ("Archiv & Zugang", [("PDF/A-Archivkopie", self.archive_copy),
                                ("PDF/A mit Originaltext", self.archive_vector_copy),
                                ("PDF/A oder PDF/UA prüfen", self.standard_check), ("Archiv-Check für Ordner", self.archive_check), ("Dubletten prüfen", self.duplicate_check),
                                ("Lesereihenfolge & Bildbeschreibungen", self.accessible_copy)]),
            ("Signaturen", [("Digital signieren", self.sign_document), ("Signaturen prüfen", self.check_signatures)]),
            ("Formular", [("Feld erstellen", lambda: self.set_mode("Feld")), ("Felder vorschlagen", self.suggest_fields),
                          ("Pflichtfelder prüfen", self.check_required_fields), ("Feste Formularkopie", self.flatten_form_copy),
                          ("Formulare nach Excel", self.export_form_batch),
                          ("Excel-Formularvorlagen", lambda: self.export_form_batch(use_template=True)),
                          ("Formulare aus Excel füllen", self.fill_form_batch),
                          ("Formularlauf fortsetzen", self.resume_form_fill),
                          ("Excel-Eingabevorlage erstellen", self.create_form_entry),
                          ("Feld ausfüllen", lambda: self.set_mode("Formular")), ("Lesen", lambda: self.set_mode("Lesen"))]),
            ("Info", [("Info zu PDF Werkbank", self.show_program_info), ("Update-Info", self.update_info)]),
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
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setGridSize(QSize(130, THUMB_HEIGHT))
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
        self.thumbs.verticalScrollBar().valueChanged.connect(self.schedule_thumbnails)
        self.thumbs.verticalScrollBar().rangeChanged.connect(self.schedule_thumbnails)
        self.splitter.splitterMoved.connect(self.schedule_thumbnails)
        self.status = QLabel("Bereit")
        self.statusBar().addWidget(self.status, 1)
        self.invoice_badge = QPushButton("Rechnungen")
        self.invoice_badge.setToolTip("Überwachten Rechnungseingang öffnen")
        self.invoice_badge.clicked.connect(lambda: self.invoice_folder())
        self.statusBar().addPermanentWidget(self.invoice_badge)
        self.invoice_badge.hide()
        work_badge = QPushButton("Offene Arbeiten")
        work_badge.clicked.connect(lambda: self.show_open_work())
        self.statusBar().addPermanentWidget(work_badge)
        for label, callback in [("‹", lambda: self.turn(-1)), ("›", lambda: self.turn(1)),
                                ("−", lambda: self.change_zoom(-0.2)), ("+", lambda: self.change_zoom(0.2))]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            self.statusBar().addPermanentWidget(button)
        for key, callback in [("Ctrl+O", self.open_files), ("Ctrl+S", self.save), ("Ctrl+P", self.print_dialog),
                              ("Ctrl+K", self.command_palette),
                              ("Ctrl+G", self.jump_to_page),
                              ("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+F", self.search), ("PgUp", lambda: self.turn(-1)),
                              ("PgDown", lambda: self.turn(1)), ("Escape", lambda: self.set_mode("Lesen"))]:
            action = QAction(self)
            action.setShortcut(key)
            action.triggered.connect(callback)
            self.addAction(action)

    @property
    def current(self):
        index = self.tabs.currentIndex()
        return self.sessions[index] if 0 <= index < len(self.sessions) else None

    def setup_favorites(self):
        self.favorite_commands = {
            "scan": ("Scannen", self.scan_stack),
            "quick-scan": ("Samsung → Posteingang", self.quick_samsung_scan),
            "inbox": ("Scan-Posteingang", self.scan_inbox),
            "folder": ("Ordnersuche", self.search_folder),
            "duplex": ("Samsung-Duplex", lambda: self.print_dialog(samsung=True)),
            "booklet": ("Broschürendruck", self.booklet_dialog),
            "open": ("PDF öffnen", self.open_files),
            "save": ("Kopie speichern", self.save),
            "archive": ("PDF/A-Archiv", self.archive_copy),
            "compare": ("PDFs vergleichen", self.compare),
        }
        self.favorite_settings = QSettings("PDF Werkbank", "PDF Werkbank")
        self.profile_commands()
        defaults = DEFAULT_FAVORITES
        saved = self.favorite_settings.value("toolbar/favorites", defaults)
        if not isinstance(saved, list):
            saved = defaults
        self.favorite_keys = list(dict.fromkeys(key for key in saved if isinstance(key, str) and key in self.favorite_commands))
        self.favorite_actions = []
        self.favorite_toolbar = QToolBar("Angeheftete Funktionen", self)
        self.favorite_toolbar.setObjectName("favorites")
        self.favorite_toolbar.setMovable(False)
        self.favorite_toolbar.setFloatable(False)
        self.favorite_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.favorite_toolbar)
        button = QToolButton()
        button.setText("Favoriten")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.favorite_menu = QMenu(button)
        button.setMenu(self.favorite_menu)
        self.favorite_toolbar.addWidget(button)
        self.populate_favorite_menu()
        self.refresh_favorites()

    def profile_commands(self):
        for key in [key for key in self.favorite_commands if key.startswith("profile:")]:
            self.favorite_commands.pop(key)
        for name in sorted(self.read_batch_profiles(self.favorite_settings)):
            self.favorite_commands[f"profile:{name}"] = (f"Ablauf: {name}", lambda name=name: self.start_profile(name))

    def populate_favorite_menu(self):
        self.favorite_menu.clear()
        profiles = self.favorite_menu.addMenu("Gespeicherte Abläufe")
        profiles.setEnabled(any(key.startswith("profile:") for key in self.favorite_commands))
        for key, (label, _) in self.favorite_commands.items():
            action = (profiles if key.startswith("profile:") else self.favorite_menu).addAction(label)
            action.setCheckable(True)
            action.setChecked(key in self.favorite_keys)
            action.toggled.connect(lambda checked, key=key: self.pin_favorite(key, checked))

    def pin_favorite(self, key, enabled):
        if enabled and key not in self.favorite_keys:
            self.favorite_keys.append(key)
        elif not enabled and key in self.favorite_keys:
            self.favorite_keys.remove(key)
        self.favorite_settings.setValue("toolbar/favorites", self.favorite_keys)
        self.refresh_favorites()

    def refresh_favorites(self):
        for action in self.favorite_actions:
            self.favorite_toolbar.removeAction(action)
            action.deleteLater()
        self.favorite_actions.clear()
        for key in self.favorite_keys:
            label, callback = self.favorite_commands[key]
            action = self.favorite_toolbar.addAction(label)
            action.triggered.connect(lambda _=False, run=callback: run())
            self.favorite_actions.append(action)

    def transfer_preferences(self):
        action, ok = QInputDialog.getItem(self, "Einstellungen übertragen", "Aktion",
                                          ["Einstellungen exportieren", "Einstellungen importieren"], editable=False)
        if not ok:
            return
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        try:
            if action == "Einstellungen exportieren":
                path, _ = QFileDialog.getSaveFileName(self, action, "PDF-Werkbank-Einstellungen.json",
                                                      "Einstellungspaket (*.json)")
                if path:
                    target = Path(path if path.lower().endswith(".json") else path + ".json")
                    atomic_write(target, export_package(settings))
                    self.statusBar().showMessage("Einstellungspaket gespeichert.", 6500)
                return
            path, _ = QFileDialog.getOpenFileName(self, action, "", "Einstellungspaket (*.json)")
            if not path:
                return
            groups = read_package(Path(path))
            dialog = QDialog(self)
            dialog.setWindowTitle("Einstellungsgruppen übernehmen")
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("Ausgewählte Gruppen ersetzen deine derzeitigen Einstellungen dieser Gruppe."))
            choices = {}
            for key, label in GROUPS.items():
                if key in groups:
                    choices[key] = QCheckBox(label)
                    choices[key].setChecked(True)
                    layout.addWidget(choices[key])
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            selected = {key: groups[key] for key, check in choices.items() if check.isChecked()}
            if not selected:
                return
            import_groups(settings, selected)
            if settings.status() != QSettings.Status.NoError:
                raise OSError("Die Einstellungen konnten nicht gespeichert werden.")
            self.favorite_settings.sync()
            self.profile_commands()
            keys = self.favorite_settings.value("toolbar/favorites", DEFAULT_FAVORITES)
            self.favorite_keys = [key for key in keys if key in self.favorite_commands]
            self.populate_favorite_menu()
            self.refresh_favorites()
            self.statusBar().showMessage("Ausgewählte Einstellungsgruppen übernommen.", 6500)
        except (OSError, ValueError, TypeError) as exc:
            self.error(exc)

    @staticmethod
    def read_batch_profiles(settings):
        try:
            profiles = json.loads(settings.value("batch/profiles", "{}", str))
            if isinstance(profiles, dict):
                return {name: steps for name, steps in profiles.items() if isinstance(steps, list)
                        and steps and all(step in ("ocr", "rotate", "compress") for step in steps)}
        except ValueError:
            pass
        return {}

    def start_profile(self, name):
        steps = self.read_batch_profiles(self.favorite_settings).get(name)
        if not steps:
            self.error("Dieser gespeicherte Ablauf ist nicht mehr vorhanden.")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, f"Dateien für „{name}“ auswählen", "", "PDF-Dateien (*.pdf)")
        if paths:
            self.run_batch(paths, steps)

    def command_palette(self):
        aliases = {"Samsung → Posteingang": "schnellscan wia scanner",
                   "Ordner durchsuchen": "ordnersuche index finden",
                   "Broschürendruck": "heft falten duplex",
                   "Arbeitsmappe öffnen": "projekt vorgang tabs",
                   "Arbeitsmappe speichern": "projekt vorgang tabs"}

        def key(text):
            return "".join(char for char in unicodedata.normalize("NFKD", text.casefold())
                           if not unicodedata.combining(char))

        commands = [(label, group, callback, key(f"{label} {group} {aliases.get(label, '')}"))
                    for group, actions in self.groups for label, callback in actions]
        dialog = QDialog(self)
        dialog.setWindowTitle("Befehl suchen")
        dialog.resize(630, 450)
        layout = QVBoxLayout(dialog)
        query = QLineEdit()
        query.setPlaceholderText("Befehl eingeben, z. B. Scan, Broschüre oder Schwärzen")
        layout.addWidget(query)
        results = QListWidget()
        layout.addWidget(results, 1)
        selected = []

        def filter_commands():
            phrase = key(query.text().strip())
            terms = phrase.split()
            selected[:] = sorted((entry for entry in commands if all(term in entry[3] for term in terms)),
                                 key=lambda entry: (not key(entry[0]).startswith(phrase), entry[0].casefold()))
            results.clear()
            results.addItems([f"{label} · {group}" for label, group, _, _ in selected])
            results.setCurrentRow(0)

        def accept():
            if results.currentRow() >= 0:
                dialog.accept()

        for shortcut, step in (("Down", 1), ("Up", -1)):
            action = QAction(dialog)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(lambda _=False, step=step: results.setCurrentRow(
                max(0, min(results.count() - 1, results.currentRow() + step))) if results.count() else None)
            dialog.addAction(action)
        query.textChanged.connect(lambda _: filter_commands())
        query.returnPressed.connect(accept)
        results.itemActivated.connect(lambda _: accept())
        filter_commands()
        query.setFocus()
        if dialog.exec() == QDialog.DialogCode.Accepted and results.currentRow() >= 0:
            selected[results.currentRow()][2]()

    @staticmethod
    def workspaces(settings):
        value = json.loads(settings.value("workspace/maps", "{}", str))
        if not isinstance(value, dict):
            raise ValueError("Die gespeicherten Arbeitsmappen konnten nicht gelesen werden.")
        return value

    def save_workspace(self):
        if not self.sessions:
            self.error("Öffne zuerst die PDFs für deine Arbeitsmappe.")
            return
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        try:
            maps = self.workspaces(settings)
            name, ok = QInputDialog.getText(self, "Arbeitsmappe speichern", "Name der Arbeitsmappe")
            name = name.strip()
            if not ok or not name:
                return
            if name in maps and QMessageBox.question(self, "Arbeitsmappe aktualisieren",
                                                       f"„{name}“ mit den jetzt geöffneten PDFs aktualisieren?"
                                                       ) != QMessageBox.StandardButton.Yes:
                return
            active = self.tabs.currentIndex()
            if any(session.changed for session in self.sessions):
                choice = QMessageBox.question(self, "Bearbeitete PDFs speichern",
                                              "Für die Arbeitsmappe werden geänderte PDFs zuerst als Kopien gespeichert. Fortfahren?")
                if choice != QMessageBox.StandardButton.Yes:
                    return
                try:
                    for index, session in enumerate(self.sessions):
                        if session.changed:
                            self.tabs.setCurrentIndex(index)
                            if not self.save():
                                return
                finally:
                    self.tabs.setCurrentIndex(active)
            maps[name] = [{"path": str(session.recovery_path), "page": session.page, "active": index == active}
                          for index, session in enumerate(self.sessions)]
            settings.setValue("workspace/maps", json.dumps(maps, ensure_ascii=False))
            settings.sync()
            if settings.status() != QSettings.Status.NoError:
                raise OSError("Die Arbeitsmappe konnte nicht lokal gespeichert werden.")
            self.statusBar().showMessage(f"Arbeitsmappe „{name}“ gespeichert.", 6500)
        except (OSError, ValueError) as exc:
            self.error(exc)

    def open_workspace(self):
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        try:
            maps = self.workspaces(settings)
            if not maps:
                QMessageBox.information(self, "Arbeitsmappen", "Es sind noch keine Arbeitsmappen gespeichert.")
                return
            name, ok = QInputDialog.getItem(self, "Arbeitsmappe öffnen", "Arbeitsmappe", sorted(maps), editable=False)
            if not ok:
                return
            records = maps[name]
            if not isinstance(records, list) or any(not isinstance(entry, dict)
                    or not isinstance(entry.get("path"), str) or not isinstance(entry.get("page"), int)
                    for entry in records):
                raise ValueError("Diese Arbeitsmappe enthält ungültige Einträge.")
            opened = {path.resolve(): session for session in self.sessions
                      for path in (session.path, session.recovery_path)}
            active, missing = None, []
            for entry in records:
                path = Path(entry["path"])
                if not path.is_absolute() or not path.is_file():
                    missing.append(path.name)
                    continue
                session = opened.get(path.resolve())
                if session:
                    session.page = max(0, min(entry["page"], len(session.doc) - 1))
                else:
                    session = self.open_path(str(path), page=entry["page"])
                    if session:
                        opened[path.resolve()] = session
                if session and entry.get("active"):
                    active = session
            if active:
                self.tabs.setCurrentIndex(self.sessions.index(active))
            self.switch_doc(self.tabs.currentIndex())
            self.write_recovery()
            if missing:
                QMessageBox.information(self, "Dateien fehlen", "Diese PDFs wurden verschoben oder gelöscht:\n"
                                        + "\n".join(missing))
        except (OSError, ValueError) as exc:
            self.error(exc)

    def error(self, exc):
        QMessageBox.warning(self, "PDF Werkbank", str(exc))

    def update_ribbon(self, index):
        while self.ribbon.count():
            item = self.ribbon.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        group, commands = self.groups[index]
        if group == "Info":
            self.ribbon.addWidget(QLabel("PDF Werkbank " + ".".join(map(str, VERSION))))
        for name, callback in commands:
            button = QPushButton(name)
            button.clicked.connect(lambda checked=False, run=callback: run())
            self.ribbon.addWidget(button)
        self.ribbon.addStretch()

    def open_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "PDF öffnen", "", "PDF-Dateien (*.pdf)")
        for path in paths:
            self.open_path(path)

    def open_path(self, path, snapshot=None, page=0):
        try:
            with (pdf.open(stream=snapshot, filetype="pdf") if snapshot is not None else pdf.open(path)) as probe:
                password = None
                if probe.needs_pass:
                    password, ok = QInputDialog.getText(self, "PDF öffnen", "Passwort", QLineEdit.EchoMode.Password)
                    if not ok:
                        return
            session = Session(path, password, snapshot)
        except Exception as exc:
            self.error(exc)
            return
        session.page = max(0, min(page, len(session.doc) - 1))
        session.changed = snapshot is not None
        self.sessions.append(session)
        tab = self.tabs.addTab(session.path.name + (" *" if session.changed else ""))
        self.tabs.setCurrentIndex(tab)
        if snapshot is None:
            try:
                self.restore_measurements(session)
                if session.plan_measurements:
                    self.render()
            except (ValueError, OSError) as exc:
                self.statusBar().showMessage(f"Messliste konnte nicht geladen werden: {exc}", 9000)

        self.write_recovery()
        return session

    def start_recovery(self, root):
        try:
            self.recovery = Recovery(root)
            timer = QTimer(self, interval=5000)
            timer.timeout.connect(self.write_recovery)
            timer.start()
            for folder, lock in self.recovery.pending():
                try:
                    records = self.recovery.records(folder)
                    if not records:
                        self.recovery.clear(folder, lock)
                        continue
                    choice = QMessageBox.question(self, "Sitzung wiederherstellen",
                                                  f"{len(records)} offene PDF-Tabs aus einer früheren Sitzung wiederherstellen?",
                                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                                  QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
                    if choice == QMessageBox.StandardButton.Cancel:
                        continue
                    if choice == QMessageBox.StandardButton.No:
                        self.recovery.clear(folder, lock)
                        continue
                    active, failures = None, []
                    for entry in records:
                        try:
                            filename = entry["snapshot"]
                            if filename and not re.fullmatch(r"[a-f0-9]{32}\.pdf", filename):
                                raise ValueError("Ungültiger Wiederherstellungspfad.")
                            snapshot = (folder / filename).read_bytes() if filename else None
                            restored = self.open_path(entry["path"], snapshot, int(entry["page"]))
                            if restored is None:
                                failures.append(entry)
                            elif entry.get("active"):
                                active = restored
                        except (OSError, ValueError, KeyError, TypeError) as exc:
                            self.error(exc)
                            failures.append(entry)
                    if active is not None:
                        self.tabs.setCurrentIndex(self.sessions.index(active))
                    if self.write_recovery():
                        if failures:
                            atomic_write(folder / "tabs.json", json.dumps(failures, ensure_ascii=False).encode("utf-8"))
                        else:
                            self.recovery.clear(folder, lock)
                except (OSError, ValueError) as exc:
                    self.error(exc)
                finally:
                    lock.unlock()
        except (OSError, ValueError) as exc:
            self.error(exc)

    def write_recovery(self):
        if not self.recovery:
            return False
        try:
            self.recovery.save(self.sessions, self.current)
            return True
        except Exception as exc:
            self.statusBar().showMessage(f"Wiederherstellung konnte nicht gesichert werden: {exc}", 8000)
            return False

    def switch_doc(self, index):
        self.clear_measure_draft()
        self.search_text = ""
        self.mode = "Lesen"
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        self.render()
        self.refresh_thumbs()

    def refresh_thumbs(self):
        self.thumbnails.stop()
        self.thumb_icons.clear()
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        session = self.current
        if session:
            self.thumbs.addItems([f"Seite {index + 1}" for index in range(len(session.doc))])
            self.thumbs.setCurrentRow(session.page)
        self.thumbs.blockSignals(False)
        self.schedule_thumbnails()

    def schedule_thumbnails(self, *_):
        if self.current and not self.thumbs.isHidden() and not self.thumbnails.isActive():
            self.thumbnails.start(12)

    def next_thumbnail(self):
        session = self.current
        if not session or not self.thumbs.count() or self.thumbs.isHidden():
            self.thumbnails.stop()
            return
        viewport = self.thumbs.viewport()
        top = self.thumbs.indexAt(QPoint(viewport.width() // 2, 6)).row()
        if top < 0:
            top = max(0, self.thumbs.indexAt(QPoint(8, 6)).row())
        bottom = min(len(session.doc), top + viewport.height() // THUMB_HEIGHT + 4)
        wanted = list(dict.fromkeys([session.page, *range(max(0, top - 1), bottom)]))
        for index in wanted:
            if index in self.thumb_icons:
                self.thumb_icons.move_to_end(index)
        index = next((value for value in wanted if value not in self.thumb_icons), None)
        if index is None:
            self.thumbnails.stop()
            return
        page = session.doc[index]
        try:
            small = pixmap(page, min(85 / page.rect.width, 106 / page.rect.height))
            self.thumbs.item(index).setIcon(QIcon(small))
        except (ValueError, RuntimeError):
            pass
        self.thumb_icons[index] = None
        if len(self.thumb_icons) > 96:
            old, _ = self.thumb_icons.popitem(last=False)
            self.thumbs.item(old).setIcon(QIcon())

    def clear_page_cache(self, session):
        session.measure_geometry.clear()
        for key in [key for key in self.page_cache if key[0] == session.recovery_id]:
            self.page_cache.pop(key)

    def displayed_page(self, session, page):
        key = (session.recovery_id, page.number, self.scale)
        image = self.page_cache.get(key)
        if image is None:
            image = pixmap(page, self.scale)
            self.page_cache[key] = image
            while len(self.page_cache) > 1 and sum(p.width() * p.height() * 4 for p in self.page_cache.values()) > RENDER_BUDGET:
                self.page_cache.popitem(last=False)
        else:
            self.page_cache.move_to_end(key)
        return image

    def render(self):
        self.measure_cursor = self.measure_hover = None
        session = self.current
        self.welcome.setVisible(session is None)
        self.splitter.setVisible(session is not None)
        if not session:
            self.status.setText("Bereit")
            return
        page = session.doc[session.page]
        try:
            self.scale = min(self.zoom, 4096 / max(page.rect.width, page.rect.height))
            image = self.displayed_page(session, page)
            fields = self.form_highlights(session, page)
            if self.search_text or self.grid_mm or fields:
                image = QPixmap(image)
                painter = QPainter(image)
                for result in search_rectangles(page, self.search_text) if self.search_text else ():
                    rect = result * page.rotation_matrix * pdf.Matrix(self.scale, self.scale)
                    painter.fillRect(QRect(int(rect.x0), int(rect.y0), int(rect.width), int(rect.height)),
                                     QColor(255, 206, 32, 110))
                for box in fields:
                    rect = box * page.rotation_matrix * pdf.Matrix(self.scale, self.scale)
                    area = QRectF(rect.x0, rect.y0, rect.width, rect.height)
                    painter.fillRect(area, QColor(255, 153, 32, 80))
                    painter.setPen(QColor("#a44800"))
                    painter.drawRect(area)
                if self.grid_mm:
                    spacing = self.grid_mm * POINTS_PER_MM * self.scale
                    if spacing >= 3:
                        painter.setPen(QColor(39, 118, 187, 100))
                        for step in range(1, int(image.width() / spacing) + 1):
                            x = round(step * spacing)
                            painter.drawLine(x, 0, x, image.height())
                        for step in range(1, int(image.height() / spacing) + 1):
                            y = round(step * spacing)
                            painter.drawLine(0, y, image.width(), y)
                painter.end()
            self.canvas.setPixmap(image)
            self.canvas.resize(image.size())
        except Exception as exc:
            self.canvas.clear()
            self.error(exc)
        self.update_page_status()

    def update_page_status(self):
        session = self.current
        plan = self.current_plan() if session.plan_measurements else None
        label = f"Seite {session.page + 1} von {len(session.doc)}   •   {self.scale:.0%}   •   {self.mode}"
        if plan and plan.scale:
            label += "   •   Maßstab 1 : " + format_measure(plan.scale / METRES_PER_POINT, "").strip()
        self.status.setText(label)

    def go_page(self, index):
        if self.current and 0 <= index < len(self.current.doc):
            if self.current.page != index:
                self.clear_measure_draft()
            self.current.page = index
            self.render()
            self.scroll.verticalScrollBar().setValue(0)
            self.schedule_thumbnails()

    def turn(self, delta):
        if self.current:
            self.thumbs.setCurrentRow(max(0, min(len(self.current.doc) - 1, self.current.page + delta)))

    def jump_to_page(self):
        if self.current:
            page, ok = QInputDialog.getInt(self, "Zu Seite springen", "Seite", self.current.page + 1,
                                            1, len(self.current.doc))
            if ok:
                self.thumbs.setCurrentRow(page - 1)

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
        else:
            self.schedule_thumbnails()

    def set_mode(self, mode):
        self.clear_measure_draft()
        self.mode = mode
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor if mode == "Lesen" else Qt.CursorShape.CrossCursor)
        self.render()
        hints = {"Markieren": "Mit gedrückter Maustaste einen Bereich um den Text ziehen.",
                 "Notiz": "Auf die gewünschte Position klicken.",
                 "Formular": "Auf ein vorhandenes Formularfeld klicken.",
                 "Text": "Auf einen horizontalen Textabschnitt klicken.",
                 "Schwärzen": "Einen Bereich aufziehen und die Entfernung bestätigen.",
                 "Zuschneiden": "Den Bereich aufziehen, der sichtbar bleiben soll.",
                 "Bild verschieben": "Ein Bild mit gedrückter Maustaste an die neue Position ziehen.",
                 "Bild ersetzen": "Ein Bild anklicken und die Ersatzdatei wählen.",
                 "Bild positionieren": "Ein Bild anklicken und den Abstand von oben und links in Millimetern einstellen.",
                 "Absatz": "Auf den gewünschten Absatz klicken.", "Textformat": "Auf einen bestehenden Textabschnitt klicken.", "Text einfügen": "Auf die Stelle für den neuen Text klicken.",
                 "Feld": "Einen Bereich für das neue Formularfeld aufziehen.", "Link": "Einen Bereich für den Link aufziehen.", "Stempel": "Auf die Stelle für den Stempel klicken."}
        if mode in hints:
            self.statusBar().showMessage(hints[mode], 6500)

    def work_backup(self):
        action, ok = QInputDialog.getItem(self, "Arbeitsstand sichern", "Aktion",
                                          ("Backup speichern", "Backup wiederherstellen", "Rücksicherung testen"),
                                          editable=False)
        if not ok:
            return
        settings = self.favorite_settings
        kind = "Werkbank-Backup (*.werkbankbackup)"
        try:
            if action == "Backup speichern":
                name, _ = QFileDialog.getSaveFileName(self, action, "PDF-Werkbank.werkbankbackup", kind)
                if name:
                    confirmed, password = self.backup_password_dialog()
                    if not confirmed:
                        return
                    target = Path(name if name.lower().endswith(".werkbankbackup") else name + ".werkbankbackup")
                    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
                        temporary = Path(folder) / target.name
                        write_backup(temporary, settings, self.measurement_store(), password)
                        read_backup(temporary, password)
                        os.replace(temporary, target)
                    self.statusBar().showMessage(f"Arbeitsstand gesichert: {target.name}", 8000)
                return
            name, _ = QFileDialog.getOpenFileName(self, action, str(app_backup_root()), kind)
            if not name:
                return
            try:
                data = read_backup(name)
            except BackupPasswordRequired:
                password, confirmed = QInputDialog.getText(
                    self, "Geschütztes Backup öffnen", "Passwort für dieses Backup", QLineEdit.EchoMode.Password)
                if not confirmed:
                    return
                data = read_backup(name, password)
            if action == "Rücksicherung testen":
                self.backup_check_dialog(data)
            else:
                self.restore_backup_dialog(data, settings)
        except Exception as exc:
            self.error(exc)

    def backup_check_dialog(self, data):
        dialog = QDialog(self)
        dialog.setWindowTitle("Rücksicherung testen")
        dialog.resize(840, 420)
        layout = QVBoxLayout(dialog)
        rows = backup_check_rows(data)
        notice = QLabel("Backup erfolgreich gelesen. Es wurden keine Daten übernommen. "
                        "Geprüft werden gespeicherte Archivpfade auf lokalen Laufwerken.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(["Gruppe", "Eintrag", "Prüfergebnis"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 145)
        table.setColumnWidth(2, 245)
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, column, item)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def backup_password_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Backup speichern")
        layout = QVBoxLayout(dialog)
        protect = QCheckBox("Backup mit Passwort schützen")
        layout.addWidget(protect)
        fields = QWidget(dialog)
        form = QFormLayout(fields)
        password, repeat = QLineEdit(), QLineEdit()
        for field in (password, repeat):
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setMaxLength(MAX_PASSWORD)
        form.addRow("Passwort", password)
        form.addRow("Passwort wiederholen", repeat)
        note = QLabel(f"Mindestens {MIN_PASSWORD} Zeichen. Bewahre das Passwort sicher auf. "
                      "Es kann später nicht zurückgesetzt werden.")
        note.setWordWrap(True)
        form.addRow(note)
        error = QLabel()
        error.setWordWrap(True)
        form.addRow(error)
        layout.addWidget(fields)
        fields.setVisible(False)
        protect.toggled.connect(fields.setVisible)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Speichern")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        layout.addWidget(buttons)

        def accept():
            if protect.isChecked():
                if len(password.text()) < MIN_PASSWORD:
                    error.setText(f"Das Passwort muss mindestens {MIN_PASSWORD} Zeichen lang sein.")
                    password.setFocus()
                    return
                if password.text() != repeat.text():
                    error.setText("Die beiden Passwörter stimmen nicht überein.")
                    repeat.setFocus()
                    return
            dialog.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        confirmed = dialog.exec() == QDialog.DialogCode.Accepted
        return confirmed, password.text() if confirmed and protect.isChecked() else None

    def restore_backup_dialog(self, data, settings):
        dialog = QDialog(self)
        dialog.setWindowTitle("Backup prüfen und wiederherstellen")
        layout = QVBoxLayout(dialog)
        info = QLabel("Wähle die Gruppen für die Wiederherstellung. Abweichende lokale Messlisten bleiben erhalten.")
        info.setWordWrap(True)
        layout.addWidget(info)
        checks = {key: QCheckBox(label) for key, label in BACKUP_GROUPS.items()}
        choices = QHBoxLayout()
        for box in checks.values():
            box.setChecked(True)
            choices.addWidget(box)
        layout.addLayout(choices)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Gruppe", "Eintrag", "Geplante Aktion"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 150)
        table.setColumnWidth(2, 290)
        layout.addWidget(table)
        base_rows = backup_preview(data, settings, self.measurement_store())
        locations = data.get("locations", {"folders": {}, "runs": {}})
        replacements = {}
        roots = location_roots(locations)
        choose_locations = QPushButton("Ordner und Formularläufe neu zuordnen")
        choose_locations.setEnabled(bool(roots))
        layout.addWidget(choose_locations)
        mapped = {}
        remap_rows = []
        new_archive = None
        remap = QPushButton("Archivordner neu zuordnen")
        remap.setEnabled(any("/archived" in key for key in data["archive"]))
        layout.addWidget(remap)

        def show_rows():
            rows = []
            _, _, path_rows = location_plan(locations, replacements)
            for entry in base_rows + remap_rows + path_rows:
                group = {"Einstellungen": "settings", "Messlisten": "plans",
                         "Ordner und Läufe": "locations"}.get(entry[0], "archive")
                if checks[group].isChecked():
                    rows.append(entry)
            table.setRowCount(len(rows))
            for row, values in enumerate(rows):
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    table.setItem(row, column, item)

        def choose_archive():
            nonlocal new_archive
            old, ok = QInputDialog.getText(dialog, "Archivordner neu zuordnen", "Bisheriger Archivordner",
                                            text=data.get("archive_root", ""))
            if not ok or not old.strip():
                return
            new = QFileDialog.getExistingDirectory(dialog, "Neuen Archivordner auswählen")
            if not new:
                return
            try:
                updated, report = archive_remap(data["archive"], old.strip(), new)
            except (OSError, ValueError) as exc:
                self.error(exc)
                return
            mapped.clear()
            mapped.update(updated)
            remap_rows[:] = report or [("Archivzuordnung", new, "Keine passenden Einträge für den bisherigen Pfad")]
            new_archive = new if updated else None
            show_rows()

        remap.clicked.connect(choose_archive)

        def choose_location():
            old, ok = QInputDialog.getItem(dialog, "Ordner neu zuordnen", "Bisheriger Ordner", roots, editable=False)
            if not ok:
                return
            new = QFileDialog.getExistingDirectory(dialog, "Ordner am neuen Ort auswählen", replacements.get(old, ""))
            if not new:
                return
            proposed = {**replacements, old: new}
            try:
                location_plan(locations, proposed)
                if old == locations["folders"].get("invoice-watch/archive"):
                    updated, report = archive_remap(data["archive"], old, new)
                    mapped.clear()
                    mapped.update(updated)
                    remap_rows[:] = report
            except (OSError, ValueError) as exc:
                self.error(exc)
                return
            replacements.update(proposed)
            show_rows()

        choose_locations.clicked.connect(choose_location)
        for box in checks.values():
            box.toggled.connect(show_rows)
        show_rows()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ausgewählte Gruppen übernehmen")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(1000, 560)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selection = {key for key, box in checks.items() if box.isChecked()}
        conflicts = restore_backup(data, selection, settings, self.measurement_store(), mapped)
        warnings = []
        if "locations" in selection:
            previous = settings.value("invoice-watch/folder", "", str)
            warnings = restore_locations(locations, replacements, settings)
            current = settings.value("invoice-watch/folder", "", str)
            if previous != current:
                watcher = getattr(self, "_invoice_folder_window", None)
                if watcher is not None:
                    watcher.done(QDialog.DialogCode.Rejected)
                if current:
                    self.invoice_folder(show=False)
        if "archive" in selection and new_archive:
            settings.setValue("invoice-watch/archive", new_archive)
            settings.sync()
        self.refresh_favorites()
        if self.open_work_dialog is not None:
            self.open_work_dialog.refresh_entries()
        if warnings:
            QMessageBox.warning(self, "Formularläufe teilweise übernommen", "\n".join(warnings))
        self.statusBar().showMessage(f"Backup übernommen. Beibehaltene lokale Messlisten: {conflicts}.", 10000)

    def undo_measure_point(self):
        session = self.current
        plan = self.current_plan()
        if not session or not plan:
            return
        history = session.measure_undo.get(plan_page_key(session.doc[session.page]), [])
        while history:
            index, before, after = history.pop()
            if index >= len(plan.marks):
                continue
            current = plan.marks[index]
            if replace(current, name=after.name, room=after.room, trade=after.trade) != after:
                continue
            plan.marks[index] = replace(before, name=current.name, room=current.room, trade=current.trade)
            self.clear_measure_draft()
            self.canvas.update()
            self.statusBar().showMessage("Messpunkt zurückgesetzt · " + plan.marks[index].caption(plan.scale), 8000)
            return
        self.statusBar().showMessage("Keine passende Messpunktkorrektur zum Rückgängigmachen.", 6500)


    def edit_measure_point(self, point):
        plan = self.current_plan()
        if not plan or not plan.marks:
            self.statusBar().showMessage("Auf dieser Seite gibt es keine Messpunkte.", 6500)
            return
        if self.measure_edit is None:
            candidates = [(distance(point, vertex), index, ring_index, vertex_index, vertex)
                          for index, mark in enumerate(plan.marks)
                          for ring_index, ring in enumerate((mark.points, *mark.cutouts))
                          for vertex_index, vertex in enumerate(ring)]
            gap, index, ring_index, vertex_index, vertex = min(candidates, key=lambda entry: entry[0])
            if gap * self.scale > 12:
                self.statusBar().showMessage("Bitte nahe an einen markierten End- oder Eckpunkt klicken.", 6500)
                return
            self.measure_edit = index, ring_index, vertex_index
            self.measure_points[:] = [vertex]
            self.canvas.update()
            self.statusBar().showMessage("Neue Position anklicken. Esc bricht ab.", 8000)
            return
        index, ring_index, vertex_index = self.measure_edit
        try:
            changed = move_vertex(plan.marks[index], ring_index, vertex_index, point)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 10000)
            return
        session = self.current
        key = plan_page_key(session.doc[session.page])
        history = session.measure_undo.setdefault(key, [])
        history.append((index, plan.marks[index], changed))
        del history[:-30]
        plan.marks[index] = changed
        self.clear_measure_draft()
        self.canvas.update()
        self.statusBar().showMessage(changed.caption(plan.scale), 8000)


    def takeoff_tools(self):
        if not self.current:
            return
        choice, ok = QInputDialog.getItem(self, "Aufmaß", "Werkzeug", ("Aussparung messen", "Symbole zählen"), editable=False)
        if not ok:
            return
        if choice == "Symbole zählen":
            name, ok = QInputDialog.getText(self, "Symbole zählen", "Kategorie, z. B. Steckdose", text=self.symbol_category)
            category = " ".join(name.split())[:40]
            if not ok or not category:
                return
            known = (symbol.category for _, plan in plan_entries(self.current.doc, self.current.plan_measurements)
                     for symbol in plan.symbols if symbol.category.casefold() == category.casefold())
            self.symbol_category = next(known, category)
        self.start_measure(choice)

    @staticmethod
    def last_area(plan):
        return next((index for index in range(len(plan.marks) - 1, -1, -1) if plan.marks[index].area), None) if plan else None

    def area_edges(self, mark, scale):
        outer = format_measure(polygon_perimeter(mark.points) * scale, "m")
        inner = (format_measure(sum(polygon_perimeter(ring) for ring in mark.cutouts) * scale, "m")
                 if mark.cutouts else "")
        return outer, inner


    def measurement_options(self):
        options = ("Automatisch", "Fangpunkte", "Lupe", "Aus")
        selected, ok = QInputDialog.getItem(self, "Messoptionen", "Beim Messen verwenden",
                                            options, options.index(self.measure_assist), editable=False)
        if ok:
            self.measure_assist = selected
            self.clear_measure_draft()
            self.canvas.update()

    def measurement_geometry(self):
        session = self.current
        if not session or self.measure_assist in ("Aus", "Lupe"):
            return (), ()
        page = session.doc[session.page]
        key = plan_page_key(page)
        if key not in session.measure_geometry:
            try:
                session.measure_geometry[key] = vector_geometry(page)
            except (RuntimeError, ValueError):
                session.measure_geometry[key] = (), ()
        return session.measure_geometry[key]

    def measure_pointer(self, screen):
        if self.mode not in MEASURE_MODES:
            return
        point = self.measure_position(screen, hover=True)
        self.measure_hover = point
        self.measure_cursor = screen if point is not None and self.measure_assist != "Aus" else None
        self.canvas.update()

    def paint_measure_assist(self, painter, transform):
        if self.measure_cursor is None or self.measure_hover is None or self.mode not in MEASURE_MODES:
            return
        point = self.measure_hover * transform
        marker = QPointF(point.x, point.y)
        vertices, _ = self.measurement_geometry()
        if self.measure_assist == "Lupe" or (self.measure_assist == "Automatisch" and not vertices):
            image = self.canvas.pixmap()
            if image.isNull():
                return
            radius = 18
            source = QRectF(point.x - radius, point.y - radius, radius * 2, radius * 2)
            size = 108
            left = max(0, min(self.measure_cursor.x() + 16, self.canvas.width() - size))
            top = max(0, min(self.measure_cursor.y() + 16, self.canvas.height() - size))
            if left <= self.measure_cursor.x() <= left + size and top <= self.measure_cursor.y() <= top + size:
                top = max(0, self.measure_cursor.y() - size - 16)
            target = QRectF(left, top, size, size)
            painter.fillRect(target, QColor("white"))
            painter.drawPixmap(target, image, source)
            painter.setPen(QPen(QColor("#005b91"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(target)
            center = target.center()
            painter.drawLine(QPointF(center.x() - 7, center.y()), QPointF(center.x() + 7, center.y()))
            painter.drawLine(QPointF(center.x(), center.y() - 7), QPointF(center.x(), center.y() + 7))
        elif self.measure_snap_hit:
            painter.setPen(QPen(QColor("#007047"), 2))
            painter.setBrush(QColor(245, 255, 250, 205))
            painter.drawRect(QRectF(marker.x() - 5, marker.y() - 5, 10, 10))

    def export_measurement_excel(self):
        session = self.current
        rows = measurement_rows(session.doc, session.plan_measurements, grouped=True)
        if not rows:
            QMessageBox.information(self, "Messliste", "Bitte zuerst eine Strecke, Fläche oder Stückzahl erfassen.")
            return
        name, _ = QFileDialog.getSaveFileName(self, "Messliste als Excel speichern",
                                               str(session.path.with_suffix(".xlsx")), "Excel (*.xlsx)")
        if name:
            totals = measurement_group_totals(session.doc, session.plan_measurements)
            self.save_workbook(name, lambda target: export_linked_sheet(
                rows, target, "Messliste", ("Seite", "Name", "Messwert", "Einheit", "Raum", "Gewerk"),
                (10, 45, 18, 12, 24, 24), extra_sheets=(
                    ("Gruppensummen", ("Raum", "Gewerk", "Flächen (m²)", "Strecken (m)", "Stückzahlen",
                                       "Außenumfang (m)", "Innenkanten (m)"),
                     (28, 28, 19, 19, 17, 22, 22), totals, None),
                )))

    def transfer_measurement_project(self, importing):
        session = self.current
        try:
            if session.changed:
                raise ValueError("Bitte zuerst eine PDF-Kopie der bearbeiteten Fassung speichern.")
            stamp = session.plan_source_stamp
            if stamp is None or file_signature(session.recovery_path) != stamp:
                raise ValueError("Die PDF auf dem Datenträger wurde geändert. Bitte erneut öffnen.")
            digest = digest_file(session.recovery_path)
            if file_signature(session.recovery_path) != stamp:
                raise ValueError("Die PDF auf dem Datenträger wurde geändert. Bitte erneut öffnen.")
            kind = "Messprojekt (*.messprojekt)"
            if importing:
                name, _ = QFileDialog.getOpenFileName(self, "Messprojekt öffnen", "", kind)
                if not name:
                    return False
                data = stored_bytes(Path(name))
                if data is None:
                    raise ValueError("Das Messprojekt wurde nicht gefunden.")
                plans = decode_project(session.doc, data, digest)
                if session.plan_measurements and QMessageBox.question(
                        self, "Messprojekt öffnen", "Die Messliste im geöffneten Tab durch das Projekt ersetzen?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return False
                session.plan_measurements = plans
                session.measure_undo.clear()
                self.clear_measure_draft()
                self.render()
                self.statusBar().showMessage("Messprojekt geladen. Mit „Messliste speichern“ lokal übernehmen.", 7500)
                return True
            name, _ = QFileDialog.getSaveFileName(self, "Messprojekt speichern",
                                                   str(session.path.with_suffix(".messprojekt")), kind)
            if name:
                path = Path(name)
                if path.suffix.lower() != ".messprojekt":
                    path = Path(name + ".messprojekt")
                atomic_write(path, encode_project(session.doc, session.plan_measurements, digest))
                self.statusBar().showMessage(f"Messprojekt gespeichert: {path.name}", 7500)
        except Exception as exc:
            self.error(exc)
        return False

    def plan_overlay(self):
        session = self.current
        if not session:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Zweite Planfassung wählen", "", "PDF-Dateien (*.pdf)")
        if not path:
            return
        try:
            with pdf.open(path) as other:
                if not other.is_pdf or other.needs_pass or not len(other):
                    raise ValueError("Bitte eine lesbare zweite PDF ohne Passwort auswählen.")
                self.overlay_dialog(session.doc, session.page, other)
        except Exception as exc:
            self.error(exc)

    def overlay_dialog(self, fixed, index, moving):
        dialog = QDialog(self)
        dialog.setWindowTitle("Planfassungen überlagern")
        layout = QVBoxLayout(dialog)
        info = QLabel("Wähle je Plan dieselben zwei Referenzpunkte in derselben Reihenfolge. "
                      "Danach kannst du zwischen beiden Fassungen umblenden.")
        info.setWordWrap(True)
        layout.addWidget(info)
        controls = QHBoxLayout()
        first, second = QSpinBox(), QSpinBox()
        for field, name, count, value in ((first, "Referenzseite ", len(fixed), index + 1),
                                          (second, "Vergleichsseite ", len(moving), min(index + 1, len(moving)))):
            field.setRange(1, count)
            field.setPrefix(name)
            field.setValue(value)
            controls.addWidget(field)
        mode = QComboBox()
        mode.addItems(["Referenzpunkte im ersten Plan", "Referenzpunkte im zweiten Plan", "Überlagerung"])
        controls.addWidget(mode)
        layout.addLayout(controls)
        blend = QSlider(Qt.Orientation.Horizontal)
        blend.setRange(0, 100)
        blend.setValue(50)
        blend.setToolTip("Links erster Plan, rechts zweiter Plan")
        layout.addWidget(blend)
        changes = QCheckBox("Änderungen markieren (Rot: entfernt · Grün: neu)")
        layout.addWidget(changes)
        area = QScrollArea()
        area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(area, 1)
        anchors = [[], []]
        images = []
        differences = {}

        class OverlayCanvas(QLabel):
            def mousePressEvent(self, event):
                side = mode.currentIndex()
                if side < 2 and event.button() == Qt.MouseButton.LeftButton:
                    point = (event.position().x(), event.position().y())
                    if len(anchors[side]) == 2:
                        anchors[side].clear()
                    anchors[side].append(point)
                    differences.clear()
                    refresh()

        image = OverlayCanvas()
        area.setWidget(image)

        def refresh():
            side = mode.currentIndex()
            result = QPixmap(images[0 if side == 2 else side])
            painter = QPainter(result)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                if side == 2:
                    if all(len(points) == 2 for points in anchors):
                        try:
                            a, b, x, y = alignment(*anchors)
                            transform = QTransform(a, b, -b, a, x, y)
                        except ValueError as exc:
                            info.setText(str(exc))
                            return
                        aligned = QPixmap(result.size())
                        aligned.fill(QColor("white"))
                        overlay = QPainter(aligned)
                        try:
                            overlay.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                            overlay.setTransform(transform)
                            overlay.drawPixmap(0, 0, images[1])
                        finally:
                            overlay.end()
                        if changes.isChecked():
                            if "result" not in differences:
                                differences["result"] = QPixmap.fromImage(plan_difference(images[0].toImage(), aligned.toImage()))
                            painter.drawPixmap(0, 0, differences["result"])
                            info.setText("Farbige Unterschiede der ausgerichteten Seiten. Kleine Abweichungen der Ausrichtung können mit erscheinen.")
                        else:
                            painter.setOpacity(blend.value() / 100)
                            painter.drawPixmap(0, 0, aligned)
                            info.setText("Ausgerichtete Überlagerung. Der Regler blendet zwischen den Fassungen um.")
                    else:
                        info.setText("Bitte zuerst je zwei Referenzpunkte setzen.")
                else:
                    for number, (x, y) in enumerate(anchors[side], 1):
                        painter.setPen(QPen(QColor("#005b91"), 2))
                        painter.setBrush(QColor("white"))
                        painter.drawEllipse(QPointF(x, y), 6, 6)
                        painter.drawText(QPointF(x + 9, y - 4), str(number))
                    info.setText(f"Plan {side + 1} · {len(anchors[side])} von 2 Referenzpunkten gesetzt. "
                                 "Ein weiterer Klick beginnt die Auswahl neu.")
            finally:
                painter.end()
            image.setPixmap(result)
            image.resize(result.size())
            aligned_ready = side == 2 and all(len(points) == 2 for points in anchors)
            changes.setEnabled(aligned_ready)
            blend.setEnabled(aligned_ready and not changes.isChecked())

        def pages_changed():
            differences.clear()
            anchors[0].clear()
            anchors[1].clear()
            pages = (fixed[first.value() - 1], moving[second.value() - 1])
            images[:] = [pixmap(page, 740 / max(page.rect.width, page.rect.height)) for page in pages]
            mode.setCurrentIndex(0)
            refresh()

        first.valueChanged.connect(pages_changed)
        second.valueChanged.connect(pages_changed)
        mode.currentIndexChanged.connect(refresh)
        blend.valueChanged.connect(refresh)
        changes.toggled.connect(refresh)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(950, 850)
        pages_changed()
        dialog.exec()


    def measurement_store(self):
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF Werkbank" / "Messungen"

    def restore_measurements(self, session):
        path = store_path(self.measurement_store(), session.recovery_path, session.plan_source_stamp)
        if path is None:
            return
        data = stored_bytes(path)
        session.plan_saved_token = plan_token(data)
        if data is None:
            data = stored_bytes(self.measurement_store() / "Importiert" / path.name)
        if data is not None:
            session.plan_measurements = decode_plans(session.doc, data)

    def save_measurements(self, session):
        try:
            if session.changed:
                raise ValueError("Bitte zuerst eine PDF-Kopie der bearbeiteten Seiten speichern.")
            path = store_path(self.measurement_store(), session.recovery_path, session.plan_source_stamp, create=True)
            data = encode_plans(session.doc, session.plan_measurements)
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = QLockFile(str(path.with_suffix(".lock")))
            if not lock.tryLock(0):
                raise ValueError("Diese Messliste wird gerade gespeichert. Bitte erneut versuchen.")
            try:
                if plan_token(stored_bytes(path)) != session.plan_saved_token:
                    raise ValueError("Die Messliste wurde in einem anderen Tab geändert. Bitte die PDF erneut öffnen.")
                atomic_write(path, data)
                session.plan_saved_token = plan_token(data)
            finally:
                lock.unlock()
            self.statusBar().showMessage("Maßstäbe und Messliste lokal gespeichert.", 8000)
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def measurement_list(self):
        session = self.current
        if not session:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Messliste")
        layout = QVBoxLayout(dialog)
        info = QLabel("Namen, Räume und Gewerke per Doppelklick bearbeiten. „Messliste speichern“ speichert die Zuordnungen und Maße "
                      "lokal für diese PDF-Fassung.")
        info.setWordWrap(True)
        layout.addWidget(info)
        rows = QTableWidget(0, 7)
        rows.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        rows.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        rows.setHorizontalHeaderLabels(["Seite", "Name / Symbol", "Messwert", "Umfang", "Innenkanten", "Raum", "Gewerk"])
        rows.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        entries = []
        for index, plan in plan_entries(session.doc, session.plan_measurements):
            entries.extend((index, plan, number, False) for number in range(len(plan.marks)))
            entries.extend((index, plan, number, True) for number in range(len(plan.symbols)))
        rows.setRowCount(len(entries))
        for row, (index, plan, number, is_symbol) in enumerate(entries):
            mark = (plan.symbols if is_symbol else plan.marks)[number]
            if is_symbol:
                values = (str(index + 1), mark.category, "1 Stk.", "", "", mark.room, mark.trade)
            else:
                outer, inner = self.area_edges(mark, plan.scale) if mark.area else ("", "")
                values = (str(index + 1), mark.name, mark.label(plan.scale), outer, inner, mark.room, mark.trade)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column not in (5, 6) and (column != 1 or is_symbol):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                rows.setItem(row, column, item)
        layout.addWidget(rows)
        counts = Counter(symbol.category for _, plan in plan_entries(session.doc, session.plan_measurements)
                         for symbol in plan.symbols)
        if counts:
            summary = QLabel("Stückzahlen · " + " · ".join(f"{name}: {count}" for name, count in counts.items()))
            summary.setTextFormat(Qt.TextFormat.PlainText)
            summary.setWordWrap(True)
            layout.addWidget(summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        save = buttons.addButton("Messliste speichern", QDialogButtonBox.ButtonRole.ActionRole)
        extra = buttons.addButton("Export und Projekte", QDialogButtonBox.ButtonRole.ActionRole)
        menu = QMenu(extra)
        menu.addAction("Messliste als Excel", self.export_measurement_excel)
        menu.addAction("Messprojekt speichern", lambda: self.transfer_measurement_project(False))
        menu.addAction("Messprojekt öffnen", lambda: dialog.accept() if self.transfer_measurement_project(True) else None)
        extra.setMenu(menu)
        jump = buttons.addButton("Zur Seite", QDialogButtonBox.ButtonRole.ActionRole)
        jump.setEnabled(bool(entries))
        assign = buttons.addButton("Raum / Gewerk zuweisen", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def edit_item(item):
            field = {1: "name", 5: "room", 6: "trade"}.get(item.column())
            if not field:
                return
            _, plan, number, is_symbol = entries[item.row()]
            marks = plan.symbols if is_symbol else plan.marks
            if field == "name" and is_symbol:
                return
            value = "".join(char for char in item.text().strip() if ord(char) >= 32)[:80]
            if field != "name":
                value = " ".join(value.split())
            marks[number] = replace(marks[number], **{field: value})
            if item.text() != value:
                item.setText(value)
            self.canvas.update()

        def assign_group():
            selected = rows.selectionModel().selectedRows()
            if not selected:
                return
            group = QDialog(dialog)
            group.setWindowTitle("Raum und Gewerk zuweisen")
            form = QFormLayout(group)
            form.addRow(QLabel("Leere Felder lassen die bisherige Zuordnung unverändert."))
            fields = []
            for column, label in ((5, "Raum"), (6, "Gewerk")):
                entry = QComboBox()
                entry.setEditable(True)
                known = {rows.item(row, column).text() for row in range(rows.rowCount())}
                entry.addItems(["", *sorted(known - {""}, key=str.casefold)])
                entry.lineEdit().setMaxLength(80)
                form.addRow(label, entry)
                fields.append((column, entry))
            actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            actions.accepted.connect(group.accept)
            actions.rejected.connect(group.reject)
            form.addRow(actions)
            if group.exec() == QDialog.DialogCode.Accepted:
                for column, entry in fields:
                    value = entry.currentText().strip()
                    if value:
                        for index in selected:
                            rows.item(index.row(), column).setText(value)
        def go():
            if rows.currentRow() >= 0:
                self.go_page(entries[rows.currentRow()][0])
                dialog.accept()

        rows.itemChanged.connect(edit_item)
        rows.itemSelectionChanged.connect(lambda: assign.setEnabled(bool(rows.selectionModel().selectedRows())))
        assign.clicked.connect(assign_group)
        assign.setEnabled(False)
        save.clicked.connect(lambda: self.save_measurements(session))
        jump.clicked.connect(go)
        if entries:
            rows.setCurrentCell(0, 1)
        dialog.resize(1080, 490)
        dialog.exec()

    def export_measurements(self):
        session = self.current
        if not session:
            return
        if not any(plan.marks or plan.symbols for _, plan in plan_entries(session.doc, session.plan_measurements)):
            QMessageBox.information(self, "PDF mit Maßen", "Bitte zuerst eine Strecke, Fläche oder Stückzahl erfassen.")
            return
        try:
            if session.password:
                raise ValueError("Bitte eine PDF ohne Passwortschutz auswählen.")
            font_path = Path(os.environ["WINDIR"]) / "Fonts/arial.ttf"
            with draw_plan_copy(session.doc, session.plan_measurements, font_path) as output:
                if not self.preview_send(output, "Die Maßlinien, Flächen und Beschriftungen erscheinen in einer separaten PDF-Kopie.",
                                         "PDF-Kopie mit Maßen prüfen"):
                    return
                target = self.output_path(" - mit Maßen", "PDF-Kopie mit Maßen speichern")
                if not target:
                    return
                if target.resolve() == session.recovery_path:
                    raise ValueError("Bitte einen anderen Namen als den der bearbeiteten PDF wählen.")
                save_copy(output, target)
            self.statusBar().showMessage(f"PDF-Kopie mit Maßen gespeichert: {target.name}", 7500)
        except Exception as exc:
            self.error(exc)


    def current_plan(self, create=False):
        session = self.current
        if not session:
            return None
        page = session.doc[session.page]
        key = plan_page_key(page)
        if create and key not in session.plan_measurements:
            session.plan_measurements[key] = PlanMeasurements()
        return session.plan_measurements.get(key)

    def clear_measure_draft(self):
        self.measure_edit = None
        self.measure_points.clear()
        self.measure_hover = None
        self.measure_cursor = None

    def set_plan_scale(self):
        if not self.current:
            return
        plan = self.current_plan()
        ratio, ok = QInputDialog.getDouble(self, "Maßstab eingeben", "Maßstab 1 : n dieser Seite",
                                            plan.scale / METRES_PER_POINT if plan and plan.scale else 100,
                                            0.001, 1000000, 3)
        if ok:
            self.current_plan(create=True).scale = ratio * METRES_PER_POINT
            self.clear_measure_draft()
            self.canvas.update()
            self.show_plan_scale()

    def show_plan_scale(self):
        self.update_page_status()
        plan = self.current_plan()
        ratio = format_measure(plan.scale / METRES_PER_POINT, "").strip()
        self.statusBar().showMessage(f"Maßstab dieser Seite 1 : {ratio} · Messwerte in m und m².", 9500)

    def start_measure(self, mode):
        if not self.current:
            return
        plan = self.current_plan()
        if mode == "Aussparung messen" and self.last_area(plan) is None:
            QMessageBox.information(self, "Aussparung", "Bitte zuerst die Außenfläche messen.")
            return
        if mode not in ("Kalibrieren", "Symbole zählen") and (not plan or not plan.scale):
            QMessageBox.information(self, "Maßstab fehlt",
                                    "Bitte zuerst den Maßstab dieser Seite eingeben oder eine bekannte Strecke kalibrieren.")
            return
        self.set_mode(mode)
        self.canvas.setFocus()
        hints = {"Kalibrieren": "Zwei Endpunkte einer bekannten Strecke anklicken.",
                 "Strecke messen": "Anfang und Ende der Strecke anklicken.",
                 "Fläche messen": "Eckpunkte der Reihe nach anklicken und mit Eingabe oder „Fläche abschließen“ bestätigen.",
                 "Aussparung messen": "Umriss innerhalb der zuletzt gemessenen Fläche anklicken und mit Eingabe abschließen.",
                 "Symbole zählen": f"Kategorie {self.symbol_category}. Jedes Symbol einmal anklicken.",
                 "Messpunkt verschieben": "End- oder Eckpunkt und danach seine neue Position anklicken."}
        self.statusBar().showMessage(hints[mode] + " Esc verwirft die angefangene Messung.", 15000)

    def measure_position(self, screen, hover=False):
        if hover:
            self.measure_snap_hit = False
        session = self.current
        page = session.doc[session.page]
        display = pdf.Point(screen.x() / self.scale, screen.y() / self.scale)
        if not page.rect.contains(display):
            return None
        point = display * page.derotation_matrix
        snapped = snap_point(point, self.measurement_geometry(), 9 / self.scale)
        if snapped is not None and page.rect.contains(snapped * page.rotation_matrix):
            if hover:
                self.measure_snap_hit = True
            return snapped
        return point

    def measure_click(self, screen):
        point = self.measure_position(screen)
        if point is None:
            return
        if self.mode == "Messpunkt verschieben":
            self.edit_measure_point(point)
            return
        if self.mode == "Symbole zählen":
            plan = self.current_plan(create=True)
            if any(symbol.category == self.symbol_category and distance(symbol.point, point) * self.scale < 4
                   for symbol in plan.symbols):
                self.statusBar().showMessage("Dieses Symbol wurde bereits markiert.", 4500)
                return
            plan.symbols.append(CountedSymbol(point, self.symbol_category))
            count = sum(symbol.category == self.symbol_category for symbol in plan.symbols)
            self.statusBar().showMessage(f"{self.symbol_category}: {count} Stk. auf dieser Seite.", 7000)
            self.canvas.update()
            return
        if self.mode != "Kalibrieren" and not getattr(self.current_plan(), "scale", 0):
            self.start_measure(self.mode)
            return
        if self.measure_points and distance(self.measure_points[-1], point) <= EPSILON:
            self.statusBar().showMessage("Bitte einen anderen Punkt anklicken.", 6000)
            return
        if len(self.measure_points) >= MAX_VERTICES:
            self.statusBar().showMessage("Bitte die Fläche abschließen. Die maximale Punktzahl ist erreicht.", 6500)
            return
        self.measure_points.append(point)
        self.measure_hover = None
        self.canvas.update()
        if self.mode in ("Fläche messen", "Aussparung messen") or len(self.measure_points) < 2:
            return
        points = tuple(self.measure_points)
        self.clear_measure_draft()
        if self.mode == "Kalibrieren":
            plan = self.current_plan()
            estimate = distance(*points) * plan.scale if plan and plan.scale else 1.0
            metres, ok = QInputDialog.getDouble(self, "Bekannte Strecke", "Tatsächliche Länge in Metern",
                                                estimate, 0.000001, 10000000, 6)
            if ok:
                self.current_plan(create=True).scale = calibrated_scale(*points, metres)
                self.show_plan_scale()
        else:
            self.add_measurement(Measurement(points, distance(*points)))
        self.canvas.update()

    def add_measurement(self, mark):
        plan = self.current_plan()
        plan.marks.append(mark)
        value = mark.label(plan.scale)
        if mark.area:
            value += " · Umfang: " + self.area_edges(mark, plan.scale)[0]
        self.statusBar().showMessage(value, 12000)

    def finish_area(self):
        if not self.current or self.mode not in ("Fläche messen", "Aussparung messen"):
            return
        try:
            points = tuple(self.measure_points)
            if self.mode == "Aussparung messen":
                plan = self.current_plan()
                index = self.last_area(plan)
                if index is None:
                    raise ValueError("Bitte zuerst eine Außenfläche auf dieser Seite messen.")
                mark = add_cutout(plan.marks[index], points)
                plan.marks[index] = mark
                outer, inner = self.area_edges(mark, plan.scale)
                self.statusBar().showMessage(f"Netto: {mark.label(plan.scale)} · Umfang: {outer} · Innenkanten: {inner}", 12000)
            else:
                self.add_measurement(Measurement(points, polygon_area(points), area=True))
        except ValueError as exc:
            self.statusBar().showMessage(str(exc) + " Esc verwirft den Umriss.", 12000)
            return
        self.clear_measure_draft()
        self.canvas.update()

    def remove_measurement(self):
        if self.measure_points:
            self.clear_measure_draft()
        elif (plan := self.current_plan()):
            if self.mode == "Symbole zählen" and plan.symbols:
                plan.symbols.pop()
            elif self.mode == "Aussparung messen" and (index := self.last_area(plan)) is not None:
                mark = plan.marks[index]
                plan.marks[index] = replace(mark, cutouts=mark.cutouts[:-1])
            elif plan.marks:
                plan.marks.pop()
        self.canvas.update()

    def paint_measurements(self, painter):
        session = self.current
        if not session:
            return
        plan = self.current_plan() if session.plan_measurements else None
        marks = plan.marks if plan else ()
        symbols = plan.symbols if plan else ()
        if not marks and not symbols and not self.measure_points and self.measure_cursor is None:
            return
        page = session.doc[session.page]
        transform = page.rotation_matrix * pdf.Matrix(self.scale, self.scale)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        def draw(points, area=False, label="", pending=False, cutouts=()):
            rotated = [point * transform for point in points]
            screen = QPolygonF([QPointF(point.x, point.y) for point in rotated])
            if not screen:
                return
            accent = QColor("#ad5300") if pending else QColor("#005b91")
            painter.setPen(QPen(accent, 2, Qt.PenStyle.DashLine if pending else Qt.PenStyle.SolidLine))
            painter.setBrush(QColor(15, 126, 183, 45) if area else Qt.BrushStyle.NoBrush)
            if area:
                area_path = QPainterPath()
                area_path.setFillRule(Qt.FillRule.OddEvenFill)
                area_path.addPolygon(screen)
                area_path.closeSubpath()
                for ring in cutouts:
                    vertices = [point * transform for point in ring]
                    area_path.addPolygon(QPolygonF([QPointF(point.x, point.y) for point in vertices]))
                    area_path.closeSubpath()
                painter.drawPath(area_path)
            elif len(screen) > 1:
                painter.drawPolyline(screen)
            painter.setBrush(QColor("white"))
            for point in screen:
                painter.drawEllipse(point, 3.5, 3.5)
            if label:
                center = screen.boundingRect().center()
                metrics = painter.fontMetrics()
                label = metrics.elidedText(label, Qt.TextElideMode.ElideRight, max(1, self.canvas.width() - 16))
                width = metrics.horizontalAdvance(label) + 14
                x = min(max(1, center.x() - width / 2), max(1, self.canvas.width() - width - 1))
                y = min(max(1, center.y() - 24), max(1, self.canvas.height() - 24))
                box = QRectF(x, y, width, 23)
                painter.setBrush(QColor(255, 255, 255, 242))
                painter.drawRoundedRect(box, 4, 4)
                painter.setPen(accent)
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)

        for mark in marks:
            draw(mark.points, mark.area, mark.caption(plan.scale), cutouts=mark.cutouts)
        for symbol, number in numbered_symbols(symbols):
            draw((symbol.point,), label=f"{symbol.category} {number}")
        if self.measure_points:
            points = [*self.measure_points]
            if self.measure_hover is not None:
                points.append(self.measure_hover)
            draw(points, pending=True)
        self.paint_measure_assist(painter, transform)


    def configure_grid(self):
        value, ok = QInputDialog.getInt(self, "Hilfsraster", "Abstand in Millimetern (0 schaltet das Raster aus)",
                                        self.grid_mm or 5, 0, 100)
        if ok:
            self.grid_mm = value
            self.render()

    def snapped(self, page, point):
        if not self.grid_mm:
            return point
        step = self.grid_mm * POINTS_PER_MM
        display = point * page.rotation_matrix
        return pdf.Point(round(display.x / step) * step, round(display.y / step) * step) * page.derotation_matrix

    def image_position(self, page, selected):
        old = pdf.Rect(selected["bbox"]) * page.rotation_matrix
        dialog = QDialog(self)
        dialog.setWindowTitle("Bild positionieren")
        form = QFormLayout(dialog)
        values = []
        for label, initial in (("Von links", old.x0), ("Von oben", old.y0)):
            field = QDoubleSpinBox()
            field.setDecimals(2)
            field.setRange(0, 2000)
            field.setSuffix(" mm")
            field.setValue(initial / POINTS_PER_MM)
            form.addRow(label, field)
            values.append(field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        point = pdf.Point(*(field.value() * POINTS_PER_MM for field in values))
        return point * page.derotation_matrix - old.tl * page.derotation_matrix

    def annotate(self, selection, origin=None, endpoint=None):
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
                        return
                for link in page.get_links():
                    if not link["from"].contains(point):
                        continue
                    if link["kind"] == pdf.LINK_GOTO and 0 <= link.get("page", -1) < len(session.doc):
                        self.thumbs.setCurrentRow(link["page"])
                    elif link["kind"] == pdf.LINK_URI and QUrl(link.get("uri", "")).scheme() == "https":
                        QDesktopServices.openUrl(QUrl(link["uri"]))
                    break
                return
            if self.mode in ("Zuschneiden", "Bild verschieben", "Bild ersetzen", "Bild positionieren"):
                if self.mode == "Bild verschieben":
                    if origin is None or endpoint is None or (endpoint - origin).manhattanLength() < 3:
                        return
                    transform = pdf.Matrix(1 / self.scale, 1 / self.scale) * page.derotation_matrix
                    start = pdf.Point(origin.x(), origin.y()) * transform
                    end = pdf.Point(endpoint.x(), endpoint.y()) * transform
                    selected = image_at(page, start)
                    anchor = pdf.Rect(selected["bbox"]).tl
                    if self.grid_mm:
                        display = pdf.Rect(selected["bbox"]) * page.rotation_matrix
                        anchor = display.tl * page.derotation_matrix
                    offset = self.snapped(page, anchor + end - start) - anchor
                elif self.mode == "Bild positionieren":
                    selected = image_at(page, point)
                    offset = self.image_position(page, selected)
                    if offset is None:
                        return
                elif self.mode == "Bild ersetzen":
                    selected = image_at(page, point)
                    path, _ = QFileDialog.getOpenFileName(self, "Ersatzbild auswählen", "",
                                                         "Bilder (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)")
                    if not path:
                        return
                session.checkpoint(self.mode)
                try:
                    if self.mode == "Zuschneiden":
                        crop_page(page, rect)
                    elif self.mode in ("Bild verschieben", "Bild positionieren"):
                        edit_image(page, selected, offset=offset)
                    else:
                        edit_image(page, selected, replacement=path)
                except Exception:
                    session.undo()
                    raise
            elif self.mode == "Link":
                if not self.put_link(page, rect):
                    return
            elif self.mode == "Stempel":
                if not self.put_stamp(page, point):
                    return
            elif self.mode == "Feld":
                if not self.create_field(page, rect):
                    return
            elif self.mode in ("Text einfügen", "Textformat", "Absatz"):
                target = self.text_at(page, point) if self.mode == "Textformat" else None
                if self.mode == "Absatz":
                    target = paragraph_at(page, point)
                if not self.styled_text(page, self.snapped(page, point) if target is None else point, target):
                    return
            elif self.mode == "Text":

                if not self.correct_text(page, point):
                    return
            elif self.mode == "Schwärzen":
                if rect.width < 2 or rect.height < 2:
                    return
                if QMessageBox.question(self, "Bereich schwärzen",
                                        "Text und Bildinhalt in diesem Bereich aus der Kopie entfernen?"
                                        ) != QMessageBox.StandardButton.Yes:
                    return
                session.checkpoint("Bereich schwärzen")
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
                session.checkpoint("Text markieren")
                page.add_highlight_annot(words)
            elif self.mode == "Notiz":
                text, ok = QInputDialog.getMultiLineText(self, "Notiz", "Kommentar")
                if not ok or not text.strip():
                    return
                session.checkpoint("Notiz hinzufügen")
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
                elif widget.field_type in (pdf.PDF_WIDGET_TYPE_COMBOBOX, pdf.PDF_WIDGET_TYPE_LISTBOX):
                    choices = widget.choice_values or []
                    value, ok = QInputDialog.getItem(self, "Formularfeld", widget.field_name, choices, editable=False)
                else:
                    raise ValueError("Dieser Formularfeldtyp wird noch nicht unterstützt.")
                if not ok:
                    return
                session.checkpoint("Formular ausfüllen")
                widget.field_value = value
                widget.update()
            else:
                return
        except Exception as exc:
            self.error(exc)
            return
        self.changed()

    def acquire_scan(self, scan, device_id="", feeder=0, samsung=False):
        command, env = scan_command(scan, device_id, feeder, samsung)
        return self.acquire_command(command, env, "Scanner liest Seiten ein …" if feeder else "Scanner liest die Seite ein …")

    def acquire_command(self, command, env, label):
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            result = self.wait_for_scanner(command, env, label, output, errors)
            if result is None:
                return None
            output.seek(0)
            data = output.read(12000)
            if result not in (0, 4):
                errors.seek(0)
                data += b"\n" + errors.read(12000)
            return result, data

    def wait_for_scanner(self, command, env, label, output, errors):
        try:
            process = subprocess.Popen(command, env=env, creationflags=subprocess.CREATE_NO_WINDOW,
                                       stdout=output, stderr=errors)
        except OSError as exc:
            self.error(exc)
            return None
        progress = QProgressDialog(label, "Abbrechen", 0, 0, QApplication.activeModalWidget() or self)
        progress.setWindowTitle("Vom Scanner einlesen")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        timer = QTimer(progress, interval=250)
        timer.timeout.connect(lambda: progress.accept() if process.poll() is not None else None)
        timer.start()
        outcome = progress.exec()
        timer.stop()
        if outcome != QDialog.DialogCode.Accepted:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            process.wait()
            return None
        return process.wait()

    def scan_device(self, scan, feeder=0, samsung=False):
        acquired = self.acquire_scan(scan, feeder=feeder, samsung=samsung)
        if acquired is None:
            return None
        status, stdout = acquired
        if status == 4:
            try:
                devices = json.loads(stdout.decode("utf-8"))
                names = [f"{index + 1}. {device['name']}" for index, device in enumerate(devices)]
            except (ValueError, KeyError, TypeError):
                self.error("Die verfügbaren Scanner konnten nicht ermittelt werden.")
                return None
            choice, ok = QInputDialog.getItem(self, "Scanner auswählen", "Gerät", names, editable=False)
            if not ok:
                return None
            acquired = self.acquire_scan(scan, devices[names.index(choice)]["id"], feeder=feeder, samsung=samsung)
            if acquired is None:
                return None
            status, stdout = acquired
        if status == 3:
            self.error("Windows meldet keinen Samsung-Scanner über WIA." if samsung
                       else "Windows meldet keinen WIA-Scanner. Prüfe den Anschluss und den Scannertreiber.")
            return None
        return status, stdout

    def scan_page(self, scan, samsung=False):
        acquired = self.scan_device(scan, samsung=samsung)
        if acquired is None or acquired[0] == 2:
            return None
        status, stdout = acquired
        if status != 0 or not scan.is_file():
            self.error("Windows konnte kein Bild vom Scanner einlesen. Prüfe das Gerät und versuche es erneut.")
            return None
        match = re.search(rb"(\d+),(\d+)", stdout)
        dpi = tuple(map(int, match.groups())) if match else None
        return scanned_document(scan, dpi)

    def scan_feeder(self, folder):
        count, ok = QInputDialog.getInt(self, "Automatischer Blatteinzug",
                                        "Maximale Seitenzahl (der Einzug stoppt früher, wenn er leer ist)", 30, 1, 200)
        if not ok:
            return
        result = self.scan_device(folder, feeder=count)
        records = list(feeder_pages(folder))
        if result and result[0] != 0:
            status = result[0]
            message = {2: "Der Scan wurde abgebrochen.", 5: "Der WIA-Treiber meldet keinen unterstützten Blatteinzug.",
                       6: "Der Blatteinzug ist leer."}.get(status, "Der Scanner hat den Einzug mit einem Fehler beendet.")
            if records:
                message += f" {len(records)} bereits gescannte Seiten bleiben in der Vorschau."
            QMessageBox.information(self, "Blatteinzug", message)
        if not records:
            return
        document = pdf.open()
        try:
            for path, dpi in records:
                with scanned_document(path, dpi) as page:
                    document.insert_pdf(page)
            return document
        except Exception:
            document.close()
            raise

    def scan_twain(self, folder):
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        default = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NAPS2" / "NAPS2.Console.exe"
        executable = Path(settings.value("scan/twain-console", str(default), str))
        if not executable.is_file():
            path, _ = QFileDialog.getOpenFileName(self, "Installierte NAPS2.Console.exe auswählen",
                                                   str(executable.parent), "NAPS2-Konsole (NAPS2.Console.exe)")
            if not path:
                return
            executable = Path(path)
        if executable.name.casefold() != "naps2.console.exe":
            raise ValueError("Bitte NAPS2.Console.exe aus der separat installierten NAPS2-Anwendung auswählen.")
        result = self.acquire_command(twain_command(executable), None, "TWAIN-Geräte werden gesucht …")
        if result is None:
            return
        status, output = result
        names = [line.strip() for line in output.decode("utf-8-sig", errors="replace").splitlines() if line.strip()]
        if status != 0 or not names:
            raise ValueError("NAPS2 meldet keinen erreichbaren TWAIN-Scanner. Prüfe den TWAIN-Treiber des Herstellers.")
        name, ok = QInputDialog.getItem(self, "TWAIN-Scanner", "Gerät", names, editable=False)
        if not ok:
            return
        source, ok = QInputDialog.getItem(self, "Papierquelle", "Einzug oder Glas", ["Blatteinzug", "Scannerglas"], editable=False)
        if not ok:
            return
        settings.setValue("scan/twain-console", str(executable))
        target = folder / "TWAIN.pdf"
        result = self.acquire_command(twain_command(executable, target, name, source == "Blatteinzug"), None,
                                      "TWAIN-Scanner liest Seiten ein …")
        if result is None:
            return
        if not target.is_file():
            details = result[1].decode("utf-8-sig", errors="replace").strip()
            message = "TWAIN hat keine Seite geliefert. Möchtest du dieselbe Papierquelle über Windows WIA versuchen?"
            if details:
                message += "\n\n" + details[-600:]
            if QMessageBox.question(self, "Scanner-Anbindung wechseln", message,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            return self.scan_feeder(folder) if source == "Blatteinzug" else self.scan_page(folder / "WIA.png")
        document = pdf.open(target)
        if not document.is_pdf or document.needs_pass or not len(document):
            document.close()
            raise ValueError("Der TWAIN-Scan hat keine lesbare PDF geliefert.")
        if result[0]:
            QMessageBox.information(self, "TWAIN", "Der Scan wurde vorzeitig beendet. Prüfe die übernommenen Seiten.")
        return document

    def save_scanned_document(self, document):
        target, _ = QFileDialog.getSaveFileName(self, "Scan als PDF speichern",
                                              str(Path.home() / "Documents" / "Scan.pdf"), "PDF-Dateien (*.pdf)")
        if not target:
            return
        path = Path(target if target.lower().endswith(".pdf") else target + ".pdf")
        if any(path.resolve() == session.path.resolve() for session in self.sessions):
            self.error("Bitte einen anderen Namen als den einer geöffneten PDF wählen.")
            return
        save_copy(document, path)
        self.open_path(str(path))

    def scan_from_device(self):
        try:
            with tempfile.TemporaryDirectory() as folder:
                document = self.scan_page(Path(folder) / "Scan.png")
                if document is not None:
                    with document:
                        self.save_scanned_document(document)
        except Exception as exc:
            self.error(exc)

    def quick_samsung_scan(self):
        self.scan_stack(quick=True)

    def scan_stack(self, quick=False):
        with tempfile.TemporaryDirectory() as folder, pdf.open() as document:
            dialog = QDialog(self)
            dialog.setWindowTitle("Samsung → Posteingang" if quick else "Scanassistent")
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("Die erste Seite wird über Windows WIA gescannt. Lege danach bei Bedarf die nächste Seite auf."
                                    if quick else "Lege eine Seite auf und klicke auf „Seite scannen“. Ziehe die Miniaturen zum Sortieren."))
            settings = QSettings("PDF Werkbank", "PDF Werkbank")
            a4 = QCheckBox("Samsung-Profil · Seiten auf A4 einpassen")
            a4.setChecked(settings.value("scan/a4", False, type=bool))
            layout.addWidget(a4)
            hint = QLabel("Bei „Schief“ kannst du das Kästchen für die Korrektur aktivieren. Prüfe leere und ähnliche Seiten in der Vorschau.")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            pages = QListWidget()
            pages.setIconSize(QSize(95, 125))
            pages.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            layout.addWidget(pages, 1)
            row = QHBoxLayout()
            add = QPushButton("Seite scannen")
            feeder = QPushButton("Blatteinzug")
            twain = QPushButton("TWAIN")
            remove = QPushButton("Aus Auswahl entfernen")
            remove.setEnabled(False)
            row.addWidget(add)
            row.addWidget(feeder)
            row.addWidget(twain)
            feeder.setVisible(not quick)
            twain.setVisible(not quick)
            row.addWidget(remove)
            layout.addLayout(row)
            ocr = QCheckBox("Scan-PDF durchsuchbar machen (Deutsch und Englisch)")
            ocr.setChecked(settings.value("scan/ocr", True, type=bool))
            layout.addWidget(ocr)
            archive = QCheckBox("Als geprüftes PDF/A-Archiv speichern")
            layout.addWidget(archive)
            inbox = QCheckBox("Im Scan-Posteingang ablegen")
            layout.addWidget(inbox)
            if quick:
                inbox.setChecked(True)
                inbox.hide()
                archive.hide()
            inbox.toggled.connect(lambda checked: archive.setChecked(False) if checked else None)
            archive.toggled.connect(lambda checked: inbox.setChecked(False) if checked else None)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
            save_button.setText("In den Posteingang" if quick else "Als eine PDF speichern")
            save_button.setEnabled(False)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            fingerprints = []

            def add_previews(start):
                for index in range(start, len(document)):
                    page = document[index]
                    blank, angle, fingerprint = scan_quality(page)
                    notes = [note for note in ("Möglicherweise leer" if blank else "",
                                               "Mögliches Duplikat" if any(similar_scan(fingerprint, old)
                                                                            for old in fingerprints) else "",
                                               f"Schief ({angle:+d}°)" if angle else "") if note]
                    fingerprints.append(fingerprint)
                    item = QListWidgetItem(QIcon(pixmap(page, min(95 / page.rect.width, 125 / page.rect.height))),
                                           " · ".join([f"Scan {index + 1}", *notes]))
                    item.setData(Qt.ItemDataRole.UserRole, index)
                    item.setData(Qt.ItemDataRole.UserRole + 1, angle)
                    if angle:
                        item.setCheckState(Qt.CheckState.Unchecked)
                    pages.addItem(item)
                    pages.setCurrentItem(item)
                    save_button.setEnabled(True)

            def capture(method):
                try:
                    with tempfile.TemporaryDirectory(dir=folder) as capture_folder:
                        location = Path(capture_folder)
                        source = method(location)
                        if source is None:
                            return
                        first = len(document)
                        with source:
                            document.insert_pdf(source)
                        add_previews(first)
                except Exception as exc:
                    self.error(exc)

            def discard():
                if pages.currentRow() >= 0:
                    pages.takeItem(pages.currentRow())
                save_button.setEnabled(pages.count() > 0)

            def capture_glass():
                capture(lambda location: self.scan_page(location / "scan.png", samsung=True)
                        if quick else self.scan_page(location / "scan.png"))

            add.clicked.connect(capture_glass)
            feeder.clicked.connect(lambda: capture(self.scan_feeder))
            twain.clicked.connect(lambda: capture(self.scan_twain))
            remove.clicked.connect(discard)
            pages.currentRowChanged.connect(lambda index: remove.setEnabled(index >= 0))
            dialog.resize(620, 575)
            if quick:
                QTimer.singleShot(0, dialog, capture_glass)
            if dialog.exec() != QDialog.DialogCode.Accepted or not pages.count():
                return
            settings.setValue("scan/a4", a4.isChecked())
            settings.setValue("scan/ocr", ocr.isChecked())
            try:
                with pdf.open() as result:
                    for index in range(pages.count()):
                        item = pages.item(index)
                        angle = item.data(Qt.ItemDataRole.UserRole + 1) if item.checkState() == Qt.CheckState.Checked else 0
                        append_scan(result, document[item.data(Qt.ItemDataRole.UserRole)], a4.isChecked(), angle)
                    if ocr.isChecked():
                        count, failures = self.send_ocr(result, range(len(result)), "deu+eng")
                        if count is None:
                            if not quick:
                                return
                            QMessageBox.information(self, "Texterkennung abgebrochen",
                                                    "Die gescannten Seiten werden im Posteingang behalten.")
                        if failures:
                            QMessageBox.information(self, "Texterkennung", "Ohne neue Textebene: Seite " +
                                                    ", ".join(map(str, failures)))
                    if inbox.isChecked():
                        self.queue_scan(result)
                        self.scan_inbox()
                    elif archive.isChecked():
                        self.archive_document(result, Path.home() / "Documents" / "Scan - Archiv.pdf",
                                              run_ocr=False)
                    else:
                        self.save_scanned_document(result)
            except Exception as exc:
                self.error(exc)

    def inbox_folder(self):
        return Path(self.favorite_settings.value("scan/inbox-folder", str(default_scan_inbox()), str))

    def queue_scan(self, doc):
        folder = self.inbox_folder()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{date.today().isoformat()}-{uuid.uuid4().hex}.pdf"
        save_copy(doc, target)
        return target

    def scan_drafts(self):
        drafts = []
        for path in pdf_files(self.inbox_folder(), False):
            try:
                drafts.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
        return [path for _, path in sorted(drafts, reverse=True)]

    def scan_inbox(self, selected_path=None):
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        folder = self.inbox_folder()
        dialog = QDialog(self)
        dialog.setWindowTitle("Scan-Posteingang")
        dialog.resize(650, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Die Vorschläge entstehen aus dem lokal erkannten Text. Prüfe Namen und Ablage vor dem Speichern."))
        rows = QListWidget()
        layout.addWidget(rows, 1)
        form = QFormLayout()
        base = QLineEdit(settings.value("scan/archive-folder", "", str))
        name = QLineEdit()
        category = QLineEdit()
        form.addRow("Archivordner", base)
        form.addRow("Unterordner", category)
        form.addRow("Dateiname", name)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        choose = buttons.addButton("Archivordner wählen", QDialogButtonBox.ButtonRole.ActionRole)
        archive = buttons.addButton("Prüfen und archivieren", QDialogButtonBox.ButtonRole.ActionRole)
        batch = buttons.addButton("Mehrere archivieren", QDialogButtonBox.ButtonRole.ActionRole)
        correct = buttons.addButton("OCR korrigieren", QDialogButtonBox.ButtonRole.ActionRole)
        split = buttons.addButton("Stapel aufteilen", QDialogButtonBox.ButtonRole.ActionRole)
        discard = buttons.addButton("Entwurf löschen", QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        run_buttons = QHBoxLayout()
        resume = QPushButton("Archivlauf fortsetzen")
        forget = QPushButton("Gespeicherten Lauf verwerfen")
        run_buttons.addWidget(resume)
        run_buttons.addWidget(forget)
        run_buttons.addStretch()
        layout.addLayout(run_buttons)
        drafts = []

        def refresh():
            drafts[:] = self.scan_drafts()
            rows.clear()
            for index, path in enumerate(drafts):
                rows.addItem(f"{path.name[:10]} · Scan {len(drafts) - index}")
            rows.setCurrentRow(0)
            archive.setEnabled(bool(drafts))
            pending = ArchiveRun(folder).path.is_file()
            batch.setEnabled(bool(drafts) and not pending)
            resume.setEnabled(pending)
            forget.setEnabled(pending)
            correct.setEnabled(bool(drafts))
            split.setEnabled(bool(drafts))
            discard.setEnabled(bool(drafts))

        def suggest(index):
            if index < 0:
                name.clear()
                category.clear()
                return
            try:
                path = drafts[index]
                with pdf.open(path) as doc:
                    filename, group = filename_hint(doc, date.fromisoformat(path.name[:10]))
                name.setText(filename)
                category.setText(group)
            except Exception as exc:
                self.error(exc)

        def select_base():
            value = QFileDialog.getExistingDirectory(dialog, "Archivordner auswählen", base.text())
            if value:
                base.setText(value)

        def archive_draft():
            index = rows.currentRow()
            if index < 0:
                return
            try:
                root = Path(base.text().strip())
                target = archive_target(root, folder, name.text(), category.text())
                target.parent.mkdir(exist_ok=True)
                settings.setValue("scan/archive-folder", str(root))
                path = drafts[index]
                with pdf.open(path) as doc:
                    saved = self.archive_document(doc, target, confirmed_target=True)
                if saved:
                    path.unlink(missing_ok=True)
                    refresh()
            except Exception as exc:
                self.error(exc)

        def discard_draft():
            index = rows.currentRow()
            if index >= 0 and QMessageBox.question(dialog, "Entwurf löschen", "Diesen lokalen Scan-Entwurf löschen?"
                                                   ) == QMessageBox.StandardButton.Yes:
                try:
                    drafts[index].unlink(missing_ok=True)
                    refresh()
                except OSError as exc:
                    self.error(exc)

        def split_selected():
            index = rows.currentRow()
            if index >= 0:
                try:
                    if self.split_scan_draft(drafts[index]):
                        refresh()
                except Exception as exc:
                    self.error(exc)

        def correct_selected():
            index = rows.currentRow()
            if index < 0:
                return
            try:
                path = drafts[index]
                if any(path.resolve() == session.path.resolve() for session in self.sessions):
                    raise ValueError("Diese Datei ist als Tab geöffnet. Verwende dort die OCR-Korrektur.")
                with pdf.open(stream=path.read_bytes(), filetype="pdf") as doc:
                    edits = self.review_ocr(doc, 0)
                    if edits:
                        for page, values in edits.items():
                            replace_ocr_layer(doc[page], values)
                        save_copy(doc, path)
                        suggest(index)
            except Exception as exc:
                self.error(exc)

        rows.currentRowChanged.connect(suggest)
        choose.clicked.connect(select_base)
        archive.clicked.connect(archive_draft)
        batch.clicked.connect(lambda: (self.review_archive_batch(drafts, base.text()), refresh()))
        resume.clicked.connect(lambda: (self.manage_archive_run(folder), refresh()))
        forget.clicked.connect(lambda: (self.manage_archive_run(folder, discard=True), refresh()))
        correct.clicked.connect(correct_selected)
        split.clicked.connect(split_selected)
        discard.clicked.connect(discard_draft)
        refresh()
        if selected_path is not None:
            if selected_path not in drafts:
                self.statusBar().showMessage("Dieser Scan liegt nicht mehr im Posteingang.", 8000)
                return
            rows.setCurrentRow(drafts.index(selected_path))
        dialog.exec()

    def review_archive_batch(self, drafts, initial_folder):
        if not drafts:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Scan-Entwürfe gemeinsam freigeben")
        dialog.resize(920, 560)
        layout = QVBoxLayout(dialog)
        explanation = QLabel("Prüfe Dateiname und Unterordner in jeder Zeile. Nur angekreuzte Entwürfe "
                             "werden archiviert. Bei Fehlern bleibt der jeweilige Entwurf im Posteingang.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        controls.addWidget(QLabel("Archivordner"))
        base = QLineEdit(initial_folder)
        controls.addWidget(base, 1)
        browse = QPushButton("Ordner wählen")
        controls.addWidget(browse)
        table = QTableWidget(len(drafts), 5)
        table.setHorizontalHeaderLabels(["Auswahl", "Miniatur", "Seiten", "Dateiname", "Unterordner"])
        table.setIconSize(QSize(56, 72))
        table.verticalHeader().setVisible(False)
        for index in range(5):
            mode = QHeaderView.ResizeMode.Stretch if index in (3, 4) else QHeaderView.ResizeMode.ResizeToContents
            table.horizontalHeader().setSectionResizeMode(index, mode)
        layout.addWidget(table, 1)
        proposed = set()
        for row, path in enumerate(drafts):
            table.setRowHeight(row, 76)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            table.setItem(row, 0, check)
            try:
                with pdf.open(path) as doc:
                    filename, group = filename_hint(doc, date.fromisoformat(path.name[:10]))
                    page = doc[0]
                    icon = QIcon(pixmap(page, min(56 / page.rect.width, 70 / page.rect.height)))
                    thumb = QTableWidgetItem(icon, "")
                    count = QTableWidgetItem(str(len(doc)))
                stem = Path(filename).stem
                number = 2
                while (group.casefold(), filename.casefold()) in proposed:
                    filename = f"{stem} ({number}).pdf"
                    number += 1
                proposed.add((group.casefold(), filename.casefold()))
                check.setCheckState(Qt.CheckState.Checked)
            except Exception as exc:
                filename, group = "Entwurf konnte nicht gelesen werden", ""
                thumb, count = QTableWidgetItem(), QTableWidgetItem("–")
                check.setCheckState(Qt.CheckState.Unchecked)
                check.setFlags(Qt.ItemFlag.NoItemFlags)
                check.setToolTip(str(exc))
            for column, item in ((1, thumb), (2, count), (3, QTableWidgetItem(filename)), (4, QTableWidgetItem(group))):
                if column < 3:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, column, item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Auswahl archivieren", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def select_folder():
            directory = QFileDialog.getExistingDirectory(dialog, "Archivordner auswählen", base.text())
            if directory:
                base.setText(directory)

        browse.clicked.connect(select_folder)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            root = Path(base.text().strip())
            jobs, names = [], set()
            try:
                for row, path in enumerate(drafts):
                    if table.item(row, 0).checkState() != Qt.CheckState.Checked:
                        continue
                    try:
                        target = archive_target(root, path.parent, table.item(row, 3).text(), table.item(row, 4).text())
                        key = os.path.normcase(str(target.resolve()))
                        if key in names:
                            raise ValueError("Der Zielname kommt in der Auswahl mehrfach vor.")
                        names.add(key)
                        jobs.append((path, target))
                    except ValueError as exc:
                        table.setCurrentCell(row, 3)
                        raise ValueError(f"Zeile {row + 1}. {exc}") from exc
                if not jobs:
                    raise ValueError("Bitte mindestens einen Entwurf auswählen.")
            except ValueError as exc:
                self.error(exc)
                continue
            QSettings("PDF Werkbank", "PDF Werkbank").setValue("scan/archive-folder", str(root))
            self.start_archive_run(jobs)
            return

    def start_archive_run(self, jobs):
        if jobs:
            self.manage_archive_run(jobs[0][0].parent, jobs=jobs)

    def manage_archive_run(self, folder, jobs=None, discard=False):
        run = ArchiveRun(folder)
        lock = QLockFile(str(run.inbox / ".archivlauf.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            self.error("Der Archivlauf ist in einem anderen Fenster geöffnet.")
            return
        try:
            if discard:
                if QMessageBox.question(self, "Archivlauf verwerfen",
                                        "Den gespeicherten Ablauf verwerfen? Die verbliebenen Entwürfe bleiben erhalten."
                                        ) == QMessageBox.StandardButton.Yes:
                    run.path.unlink(missing_ok=True)
                return
            if jobs is not None:
                run.begin(jobs)
            else:
                run.load()
            self.archive_batch([run.paths(job) for job in run.jobs], run)
        except Exception as exc:
            self.error(exc)
        finally:
            lock.unlock()

    def archive_batch(self, jobs, journal=None):
        tools = self.standard_tools()
        if tools is None:
            return
        progress = QProgressDialog("Archivkopien werden erstellt …", "Pausieren" if journal else "Abbrechen", 0, len(jobs),
                                   QApplication.activeModalWidget() or self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        saved, skipped, failures, notes = 0, 0, [], []
        cancelled = False
        try:
            for index, (draft, target) in enumerate(jobs):
                progress.setValue(index)
                progress.setLabelText(f"Prüfe {target.name} …")
                QApplication.processEvents()
                if progress.wasCanceled():
                    cancelled = True
                    break
                try:
                    record = journal.jobs[index] if journal else None
                    if record and record["done"]:
                        skipped += 1
                        continue
                    if record and target.resolve() != target:
                        raise ValueError("Der freigegebene Zielpfad hat sich geändert.")
                    if record and record["sha256"] and target.is_file() and digest_file(target) == record["sha256"]:
                        if draft.exists():
                            if not journal.matches_source(record):
                                raise ValueError("Der Entwurf wurde nach der Freigabe geändert.")
                            draft.unlink()
                        record["done"] = True
                        journal.write()
                        skipped += 1
                        continue
                    if record and not journal.matches_source(record):
                        raise ValueError("Der Entwurf wurde nach der Freigabe geändert.")
                    if target.exists() or any(target.resolve() == session.path.resolve() for session in self.sessions):
                        raise ValueError("Die Zieldatei ist bereits vorhanden oder geöffnet.")
                    target.parent.mkdir(exist_ok=True)
                    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
                        candidate = Path(folder) / "Archiv.pdf"
                        with pdf.open(draft) as source:
                            if not source.is_pdf or source.needs_pass or not len(source):
                                raise ValueError("Der Entwurf ist keine lesbare ungeschützte PDF.")
                            summary = self.build_archive(source, candidate, True, tools)
                        if summary is None or progress.wasCanceled():
                            cancelled = True
                            break
                        if record:
                            if not journal.matches_source(record):
                                raise ValueError("Der Entwurf wurde während der Prüfung geändert.")
                            record["sha256"] = digest_file(candidate)
                            journal.write()
                        candidate.rename(target)
                    saved += 1
                    if "Ohne Suchtext" in summary:
                        notes.append(f"{target.name}. {summary}")
                    if record and draft.exists() and not journal.matches_source(record):
                        raise ValueError("Die Kopie wurde gespeichert, der inzwischen geänderte Entwurf bleibt erhalten.")
                    draft.unlink(missing_ok=True)
                    if record:
                        record["done"] = True
                        journal.write()
                except Exception as exc:
                    failures.append(f"{target.name}. {exc}")
        finally:
            progress.close()
        if journal and all(job["done"] for job in journal.jobs):
            journal.path.unlink(missing_ok=True)
        report = QMessageBox(self)
        report.setWindowTitle("Archivierung abgeschlossen")
        report.setText(f"{saved} geprüfte Archivkopien gespeichert. {len(failures)} Fehler."
                       + (f" {skipped} bereits fertig." if skipped else "")
                       + (" Der Lauf wurde unterbrochen." if cancelled and journal else
                          " Der Lauf wurde abgebrochen." if cancelled else ""))
        if failures or notes:
            report.setInformativeText("Die Details zeigen Fehler und Seiten ohne Suchtext.")
            report.setDetailedText("\n\n".join(failures + notes))
        report.exec()

    def split_scan_draft(self, original):
        with tempfile.TemporaryDirectory(dir=original.parent) as folder:
            with pdf.open(original) as doc:
                if len(doc) < 2:
                    QMessageBox.information(self, "Stapel aufteilen", "Der Entwurf enthält nur eine Seite.")
                    return []
                ranges = self.review_scan_breaks(doc)
                if len(ranges) < 2:
                    return []
                parts = []
                for first, end in ranges:
                    name = f"{original.name[:10]}-{uuid.uuid4().hex}.pdf"
                    path = Path(folder) / name
                    with pdf.open() as part:
                        part.insert_pdf(doc, from_page=first, to_page=end - 1)
                        save_copy(part, path)
                    parts.append(path)
            promoted = []
            try:
                for part in parts:
                    target = original.parent / part.name
                    part.rename(target)
                    promoted.append(target)
                original.unlink()
            except Exception:
                for target in promoted:
                    target.unlink(missing_ok=True)
                raise
        self.statusBar().showMessage(f"{len(promoted)} einzelne Entwürfe liegen zum Archivieren bereit.", 8000)
        return promoted

    def review_scan_breaks(self, doc):
        dialog = QDialog(self)
        dialog.setWindowTitle("Scan-Stapel aufteilen")
        dialog.resize(940, 640)
        layout = QVBoxLayout(dialog)
        notice = QLabel("Markiere jede Seite, auf der ein neues Dokument beginnt. Alle Seiten bleiben erhalten. "
                        "Die einzelnen Entwürfe kannst du anschließend im Posteingang prüfen und archivieren.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        pane = QSplitter()
        layout.addWidget(pane, 1)
        rows = QListWidget()
        pane.addWidget(rows)
        previews = QWidget()
        sides = QHBoxLayout(previews)
        images = []
        for _ in range(2):
            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sides.addWidget(image, 1)
            images.append(image)
        pane.addWidget(previews)
        pane.setStretchFactor(1, 3)
        suggested = set(scan_breaks(doc))
        for index in range(len(doc)):
            item = QListWidgetItem()
            if index:
                item.setCheckState(Qt.CheckState.Checked if index in suggested else Qt.CheckState.Unchecked)
            rows.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        apply = buttons.button(QDialogButtonBox.StandardButton.Ok)
        apply.setText("Einzelne Entwürfe erstellen")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def ranges():
            return scan_ranges(len(doc), [index for index in range(1, len(doc))
                                          if rows.item(index).checkState() == Qt.CheckState.Checked])

        def label_groups():
            rows.blockSignals(True)
            try:
                groups = ranges()
                for number, (start, end) in enumerate(groups, 1):
                    for index in range(start, end):
                        label = f"Seite {index + 1} · Dokument {number}"
                        if index == start:
                            label += " · Anfang"
                        if index in suggested:
                            label += " · Vorschlag"
                        rows.item(index).setText(label)
                apply.setEnabled(len(groups) > 1)
            finally:
                rows.blockSignals(False)

        def preview(index):
            for image, page_index in zip(images, (index - 1, index)):
                if page_index < 0:
                    image.setText("Keine vorherige Seite")
                else:
                    page = doc[page_index]
                    image.setPixmap(pixmap(page, min(300 / page.rect.width, 490 / page.rect.height)))

        rows.itemChanged.connect(lambda _: label_groups())
        rows.currentRowChanged.connect(preview)
        rows.setCurrentRow(min(suggested) if suggested else 0)
        label_groups()
        return ranges() if dialog.exec() == QDialog.DialogCode.Accepted else []

    def bookmarks(self):
        session = self.current
        if not session:
            return
        entries = session.doc.get_toc(simple=False)
        dialog = QDialog(self)
        dialog.setWindowTitle("Lesezeichen")
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        layout.addWidget(listing, 1)
        row = QHBoxLayout()
        for title in ("Zu Seite", "Hinzufügen", "Umbenennen", "Entfernen"):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, action=title: perform(action))
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def redraw(index=0):
            listing.clear()
            for depth, title, page, _ in entries:
                listing.addItem("    " * (depth - 1) + f"{title}  ·  {page if page > 0 else '–'}")
            listing.setCurrentRow(min(index, len(entries) - 1))

        def perform(action):
            nonlocal entries
            index = listing.currentRow()
            updated = [entry.copy() for entry in entries]
            if action == "Hinzufügen":
                name, ok = QInputDialog.getText(dialog, "Lesezeichen hinzufügen", "Titel")
                if not ok or not name.strip():
                    return
                updated.append([1, name.strip(), session.page + 1, {"kind": pdf.LINK_GOTO, "page": session.page}])
                index = len(updated) - 1
            elif index < 0:
                return
            elif action == "Zu Seite":
                if entries[index][2] > 0:
                    self.thumbs.setCurrentRow(entries[index][2] - 1)
                    dialog.accept()
                return
            elif action == "Umbenennen":
                name, ok = QInputDialog.getText(dialog, "Lesezeichen umbenennen", "Titel", text=entries[index][1])
                if not ok or not name.strip():
                    return
                updated[index][1] = name.strip()
            else:
                depth = updated[index][0]
                stop = index + 1
                while stop < len(updated) and updated[stop][0] > depth:
                    updated[stop][0] -= 1
                    stop += 1
                del updated[index]
            session.checkpoint("Lesezeichen bearbeiten")
            try:
                session.doc.set_toc(updated)
            except Exception as exc:
                session.undo()
                self.error(exc)
                return
            entries = updated
            self.changed()
            redraw(index)

        listing.itemDoubleClicked.connect(lambda _: perform("Zu Seite"))
        redraw()
        dialog.resize(560, 450)
        dialog.exec()

    def add_page_numbers(self):
        session = self.current
        if not session:
            return
        value, ok = QInputDialog.getText(self, "Seitenzahlen einfügen", "Seiten (z. B. 1, 3-5)",
                                         text=f"1-{len(session.doc)}")
        if not ok:
            return
        try:
            indices = page_selection(value, len(session.doc))
        except ValueError as exc:
            self.error(exc)
            return
        session.checkpoint("Seitenzahlen einfügen")
        try:
            for index in indices:
                page = session.doc[index]
                number = str(index + 1)
                width = pdf.get_text_length(number, fontsize=10)
                point = pdf.Point((page.rect.width - width) / 2, page.rect.height - 17) * page.derotation_matrix
                page.insert_text(point, number, fontsize=10, rotate=page.rotation)
        except Exception as exc:
            session.undo()
            self.error(exc)
            return
        self.changed()

    def headers_and_footers(self):
        session = self.current
        if not session:
            return
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        try:
            templates = json.loads(settings.value("document/page-labels", "{}", str))
            if not isinstance(templates, dict):
                raise ValueError()
        except ValueError:
            templates = {}
        dialog = QDialog(self)
        dialog.setWindowTitle("Kopf- und Fußzeilen")
        dialog.resize(680, 730)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        layout.addLayout(form)
        template = QComboBox()
        template.addItem("Neue Vorlage")
        template.addItems(sorted(templates))
        form.addRow("Vorlage", template)
        header = QLineEdit()
        footer = QLineEdit("Seite {Seite} von {Seiten}")
        indices = QLineEdit(f"1-{len(session.doc)}")
        alignment = QComboBox()
        alignment.addItems(["links", "mittig", "rechts"])
        alignment.setCurrentText("mittig")
        size = QSpinBox()
        size.setRange(6, 24)
        size.setValue(10)
        sample = QSpinBox()
        sample.setRange(1, len(session.doc))
        sample.setValue(session.page + 1)
        for label, field in (("Kopfzeile", header), ("Fußzeile", footer), ("Seiten", indices),
                             ("Ausrichtung", alignment), ("Schriftgröße", size), ("Vorschauseite", sample)):
            form.addRow(label, field)
        layout.addWidget(QLabel("Platzhalter  {Seite}   {Seiten}   {Datum}   {Datei}"))
        status = QLabel()
        status.setWordWrap(True)
        layout.addWidget(status)
        area = QScrollArea()
        area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image = QLabel()
        area.setWidget(image)
        layout.addWidget(area, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.addButton("Vorlage speichern", QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)
        today = date.today()

        def values():
            return header.text(), footer.text(), alignment.currentText(), size.value()

        def placements(page):
            return list(page_labels(page, session.path.name, *values(), today))

        def render_preview():
            try:
                selected = page_selection(indices.text(), len(session.doc))
                page = session.doc[sample.value() - 1]
                labels = placements(page) if page.number in selected else []
                notice = ("Mögliche Überdeckung auf der Vorschauseite. Bitte prüfen." if label_conflicts(page, labels)
                          else "Vorschauseite" if labels else "Diese Seite ist von der Auswahl ausgenommen.")
                with pdf.open() as copy:
                    copy.insert_pdf(session.doc, from_page=page.number, to_page=page.number)
                    insert_labels(copy[0], labels, size.value())
                    rendered = pixmap(copy[0], min(440 / page.rect.width, 480 / page.rect.height))
                image.setPixmap(rendered)
                image.resize(rendered.size())
                status.setText(notice)
            except Exception as exc:
                image.clear()
                status.setText(str(exc))

        def save_template():
            name, ok = QInputDialog.getText(dialog, "Vorlage speichern", "Name",
                                            text=template.currentText() if template.currentIndex() else "")
            name = name.strip()
            if not ok or not name or name == template.itemText(0):
                return
            if name not in templates:
                template.addItem(name)
            templates[name] = values()
            settings.setValue("document/page-labels", json.dumps(templates, ensure_ascii=False))
            template.setCurrentText(name)

        def load_template(index):
            stored = templates.get(template.itemText(index)) if index > 0 else None
            if isinstance(stored, (list, tuple)) and len(stored) == 4:
                header.setText(str(stored[0]))
                footer.setText(str(stored[1]))
                alignment.setCurrentText(str(stored[2]))
                if isinstance(stored[3], int):
                    size.setValue(stored[3])

        timer = QTimer(dialog, interval=120, singleShot=True)
        timer.timeout.connect(render_preview)
        for field in (header, footer, indices):
            field.textChanged.connect(lambda _: timer.start())
        alignment.currentIndexChanged.connect(lambda _: timer.start())
        size.valueChanged.connect(lambda _: timer.start())
        sample.valueChanged.connect(lambda _: timer.start())
        template.currentIndexChanged.connect(load_template)
        save.clicked.connect(save_template)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        render_preview()
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                selected = page_selection(indices.text(), len(session.doc))
                staged = [(index, placements(session.doc[index])) for index in selected]
                if not any(labels for _, labels in staged):
                    raise ValueError("Bitte eine Kopf- oder Fußzeile eingeben.")
                overlaps = [str(index + 1) for index, labels in staged if label_conflicts(session.doc[index], labels)]
                if overlaps and QMessageBox.question(dialog, "Mögliche Überdeckung",
                        "Vorhandener Inhalt liegt im Bereich der Kopf- oder Fußzeile auf Seite "
                        + ", ".join(overlaps) + ". Trotzdem einfügen?") != QMessageBox.StandardButton.Yes:
                    continue
            except (ValueError, RuntimeError) as exc:
                self.error(exc)
                continue
            session.checkpoint("Kopf- und Fußzeilen einfügen")
            try:
                for index, labels in staged:
                    insert_labels(session.doc[index], labels, size.value())
            except Exception as exc:
                session.undo()
                self.error(exc)
                return
            self.changed()
            return

    def review_comments(self):
        session = self.current
        if not session:
            return
        entries = list(comments(session.doc))
        dialog = QDialog(self)
        dialog.setWindowTitle("Kommentarübersicht")
        dialog.resize(650, 540)
        layout = QVBoxLayout(dialog)
        search = QLineEdit()
        search.setPlaceholderText("Nach Text, Typ, Autor oder Seite filtern")
        layout.addWidget(search)
        rows = QListWidget()
        layout.addWidget(rows, 1)
        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setMaximumHeight(120)
        layout.addWidget(body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        jump = buttons.addButton("Zur Stelle", QDialogButtonBox.ButtonRole.ActionRole)
        export = buttons.addButton("Gefilterte Übersicht speichern", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def label(entry):
            index, _, kind, author, text = entry
            return f"Seite {index + 1} · {kind}" + (f" · {author}" if author else "") + (f" · {text}" if text else "")

        def refresh():
            rows.clear()
            for index, entry in enumerate(entries):
                value = label(entry)
                if search.text().casefold() in value.casefold():
                    item = QListWidgetItem(" ".join(value.split())[:160])
                    item.setData(Qt.ItemDataRole.UserRole, index)
                    rows.addItem(item)
            rows.setCurrentRow(0)
            export.setEnabled(rows.count() > 0)

        def display(item):
            jump.setEnabled(item is not None)
            body.setPlainText(label(entries[item.data(Qt.ItemDataRole.UserRole)]) if item is not None else "Keine Kommentare gefunden.")

        def go():
            item = rows.currentItem()
            if item is None:
                return
            page, box, *_ = entries[item.data(Qt.ItemDataRole.UserRole)]
            self.thumbs.setCurrentRow(page)
            self.go_page(page)
            self.scroll.ensureVisible(round(box.x0 * self.scale), round(box.y0 * self.scale), 20, 60)
            dialog.accept()

        def save_summary():
            target, _ = QFileDialog.getSaveFileName(dialog, "Kommentarübersicht speichern",
                                                    str(session.path.with_suffix(".txt")), "Textdateien (*.txt)")
            if target:
                path = Path(target if target.lower().endswith(".txt") else target + ".txt")
                lines = [label(entries[rows.item(i).data(Qt.ItemDataRole.UserRole)]) for i in range(rows.count())]
                try:
                    atomic_write(path, ("\n\n".join(lines) + "\n").encode("utf-8"))
                except OSError as exc:
                    self.error(exc)

        search.textChanged.connect(lambda _: refresh())
        rows.currentItemChanged.connect(lambda item, _: display(item))
        rows.itemDoubleClicked.connect(lambda _: go())
        jump.clicked.connect(go)
        export.clicked.connect(save_summary)
        refresh()
        display(rows.currentItem())
        dialog.exec()

    def manage_attachments(self):
        session = self.standards_source()
        if not session:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Anlagen")
        dialog.resize(600, 440)
        layout = QVBoxLayout(dialog)
        rows = QListWidget()
        layout.addWidget(rows)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        add = buttons.addButton("Datei hinzufügen", QDialogButtonBox.ButtonRole.ActionRole)
        save = buttons.addButton("Anlage speichern unter", QDialogButtonBox.ButtonRole.ActionRole)
        remove = buttons.addButton("Anlage entfernen", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        entries = []

        def refresh():
            entries[:] = attachments(session.doc)
            rows.clear()
            for _, name, index, _ in entries:
                rows.addItem(f"{name} · " + (f"Seite {index + 1}" if index is not None else "Dokument"))
            rows.setCurrentRow(0)
            save.setEnabled(bool(entries))
            remove.setEnabled(bool(entries))

        def mutate(callback):
            session.checkpoint("Anlage bearbeiten")
            try:
                callback()
            except Exception:
                session.undo()
                raise
            self.changed()
            refresh()

        def add_file():
            path, _ = QFileDialog.getOpenFileName(dialog, "Datei als Anlage hinzufügen")
            if path:
                source = Path(path)
                if source.name in session.doc.embfile_names():
                    raise ValueError("Eine Dokumentanlage mit diesem Namen ist bereits vorhanden.")
                if source.stat().st_size > 100 * 1024 * 1024:
                    raise ValueError("Die Anlage darf höchstens 100 MB groß sein.")
                payload = source.read_bytes()
                mutate(lambda: session.doc.embfile_add(source.name, payload, filename=source.name, ufilename=source.name))

        def selected(remove_selected=False):
            row = rows.currentRow()
            if row < 0:
                return
            key, name, index, xref = entries[row]
            page = session.doc[index] if index is not None else None
            annot = page.load_annot(xref) if page is not None else None
            if remove_selected:
                if QMessageBox.question(dialog, "Anlage entfernen", f"„{name}“ aus der PDF entfernen?"
                                        ) == QMessageBox.StandardButton.Yes:
                    mutate(lambda: page.delete_annot(annot) if page is not None else session.doc.embfile_del(key))
                return
            filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "Anlage"
            target, _ = QFileDialog.getSaveFileName(dialog, "Anlage speichern unter", str(session.path.parent / filename))
            if target:
                path = Path(target)
                if any(path.resolve() == opened.path.resolve() for opened in self.sessions):
                    raise ValueError("Eine geöffnete PDF kann nicht durch die Anlage ersetzt werden.")
                atomic_write(path, annot.get_file() if annot is not None else session.doc.embfile_get(key))

        def safely(callback):
            try:
                callback()
            except Exception as exc:
                self.error(exc)

        add.clicked.connect(lambda: safely(add_file))
        save.clicked.connect(lambda: safely(selected))
        remove.clicked.connect(lambda: safely(lambda: selected(True)))
        refresh()
        dialog.exec()

    def put_link(self, page, rect):
        if rect.width < 3 or rect.height < 3:
            return False
        session = self.current
        value, ok = QInputDialog.getText(self, "Link einfügen", "Ziel: Seitennummer oder https://-Adresse")
        if not ok:
            return False
        value = value.strip()
        if value.isdecimal() and 1 <= int(value) <= len(session.doc):
            link = {"kind": pdf.LINK_GOTO, "page": int(value) - 1, "to": pdf.Point(0, 0)}
        else:
            url = QUrl(value)
            if not url.isValid() or url.scheme() != "https" or not url.host():
                raise ValueError("Bitte eine gültige Seitennummer oder eine HTTPS-Adresse eingeben.")
            link = {"kind": pdf.LINK_URI, "uri": url.toString()}
        session.checkpoint("Link einfügen")
        try:
            page.insert_link(dict(link, **{"from": rect}))
        except Exception:
            session.undo()
            raise
        return True

    def put_stamp(self, page, point):
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        entries = settings.value("stamp_presets", ["GEPRÜFT", "EINGEGANGEN"], list)
        value, ok = QInputDialog.getItem(self, "Stempel", "Stempel wählen oder eigenen Text eingeben",
                                         [str(entry) for entry in entries], editable=True)
        value = value.strip()
        if not ok or not value:
            return False
        value.encode("cp1252")
        width = pdf.get_text_length(value, fontname="hebo", fontsize=12) + 16
        rectangle = pdf.Rect(point.x, point.y, point.x + width, point.y + 25)
        if not (page.rect * page.derotation_matrix).contains(rectangle):
            raise ValueError("Der Stempel passt an dieser Stelle nicht vollständig auf die Seite.")
        session = self.current
        session.checkpoint("Stempel einfügen")
        try:
            shape = page.new_shape()
            shape.draw_rect(rectangle)
            shape.finish(color=(0.68, 0.08, 0.13), width=1.5)
            shape.insert_text(point + (8, 17), value, fontsize=12, fontname="hebo", color=(0.68, 0.08, 0.13))
            shape.commit()
        except Exception:
            session.undo()
            raise
        if value not in entries:
            settings.setValue("stamp_presets", [value, *entries][:20])
        return True


    def review_candidates(self, title, doc, candidates, description, options=()):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(860, 700)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(description)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        split = QSplitter()
        rows = QListWidget()
        split.addWidget(rows)
        area = QScrollArea()
        area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image = QLabel()
        area.setWidget(image)
        split.addWidget(area)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)
        for index, rect, label in candidates:
            item = QListWidgetItem(f"Seite {index + 1} · {label}")
            item.setCheckState(Qt.CheckState.Unchecked)
            rows.addItem(item)
        checkboxes = []
        for label in options:
            checkbox = QCheckBox(label)
            layout.addWidget(checkbox)
            checkboxes.append(checkbox)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Auswahl übernehmen")
        select_all = buttons.addButton("Alle markieren", QDialogButtonBox.ButtonRole.ActionRole)
        def mark_all():
            for row in range(rows.count()):
                rows.item(row).setCheckState(Qt.CheckState.Checked)
        select_all.clicked.connect(mark_all)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def preview(row):
            if row < 0:
                return
            index, rect, _ = candidates[row]
            page = doc[index]
            scale = 460 / max(page.rect.width, page.rect.height)
            drawing = pixmap(page, scale)
            box = rect * page.rotation_matrix
            painter = QPainter(drawing)
            painter.fillRect(QRect(round(box.x0 * scale), round(box.y0 * scale), max(2, round(box.width * scale)),
                                   max(2, round(box.height * scale))), QColor(255, 135, 20, 150))
            painter.end()
            image.setPixmap(drawing)
            image.resize(drawing.size())

        rows.currentRowChanged.connect(preview)
        rows.setCurrentRow(0)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return ([candidate for i, candidate in enumerate(candidates)
                 if rows.item(i).checkState() == Qt.CheckState.Checked],
                [option.isChecked() for option in checkboxes])

    def archive_check(self):
        folder = QFileDialog.getExistingDirectory(self, "PDF-Archiv auswählen")
        if not folder:
            return
        choice = QMessageBox.question(self, "Archiv-Check", "Unterordner mitprüfen?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                      QMessageBox.StandardButton.Cancel)
        if choice == QMessageBox.StandardButton.Cancel:
            return
        paths = list(pdf_files(Path(folder), choice == QMessageBox.StandardButton.Yes))
        if not paths:
            QMessageBox.information(self, "Archiv-Check", "Keine PDFs gefunden.")
            return
        tools = self.standard_tools()
        results = self.inspect_pdfs(paths, "Archiv wird geprüft …", lambda path: audit_pdf(path, tools))
        if not results:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Archiv-Check")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{len(results)} von {len(paths)} PDFs geprüft. "
                               "Doppelklick öffnet die Datei, bei fehlendem Suchtext an der ersten betroffenen Seite."))
        table = QTableWidget(len(results), 5)
        table.setHorizontalHeaderLabels(["Datei", "Öffnen", "PDF/A-Profil", "Normprüfung", "Seiten ohne Suchtext"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setStretchLastSection(True)
        for row, (path, result) in enumerate(results):
            values = (str(path.relative_to(folder)), result.opening, result.profile.upper(),
                      result.archive, ", ".join(map(str, result.missing_text)))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(result.details if column in (1, 3) and result.details else value)
                table.setItem(row, column, item)

        def open_result(row, _column):
            path, result = results[row]
            if result.readable:
                dialog.accept()
                index = result.missing_text[0] - 1 if result.missing_text else 0
                for tab, session in enumerate(self.sessions):
                    if session.path.resolve() == path.resolve():
                        self.tabs.setCurrentIndex(tab)
                        self.go_page(index)
                        return
                self.open_path(path, page=index)

        filters = QComboBox()
        filters.addItems(["Alle Dateien", "Nur Auffälligkeiten", "Seiten ohne Suchtext"])
        layout.addWidget(filters)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        def filter_results(index):
            for row, (_, result) in enumerate(results):
                issue = result.opening != "Lesbar" or result.archive != "Bestanden" or result.missing_text
                table.setRowHidden(row, bool((index == 1 and not issue) or (index == 2 and not result.missing_text)))
            table.clearSelection()

        def report():
            name, _ = QFileDialog.getSaveFileName(dialog, "Archivbericht speichern", "Archivbericht.xlsx", "Excel (*.xlsx)")
            if not name:
                return
            rows = [(str(path.resolve()), result.opening, result.profile.upper(), result.archive,
                     ", ".join(map(str, result.missing_text)), result.details)
                    for row, (path, result) in enumerate(results) if not table.isRowHidden(row)]
            self.save_workbook(name, lambda target: export_linked_sheet(
                rows, target, "Archiv-Check", ("Datei", "Öffnen", "PDF/A-Profil", "Normprüfung", "Seiten ohne Suchtext",
                                               "Details"), (65, 24, 18, 25, 28, 85), 1))

        def repair():
            selected = {results[index.row()][0]: results[index.row()][1] for index in table.selectionModel().selectedRows()
                        if not table.isRowHidden(index.row()) and results[index.row()][1].missing_text}
            if not selected:
                QMessageBox.information(dialog, "Suchtext ergänzen", "Bitte Dateien mit Seiten ohne Suchtext auswählen.")
                return
            if not tools:
                QMessageBox.information(dialog, "Suchtext ergänzen", "Für geprüfte Kopien wird veraPDF benötigt.")
                return
            destination = QFileDialog.getExistingDirectory(dialog, "Zielordner für geprüfte Archivkopien")
            if not destination:
                return
            plan = self.batch_preview(list(selected), Path(destination), (), selected)
            if not plan:
                return
            targets = dict(plan)
            done = self.inspect_pdfs(targets, "Archivkopien werden erstellt …",
                                     lambda source: self.repair_archive_copy(source, targets[source], tools))
            result = QMessageBox(dialog)
            result.setWindowTitle("Archivkopien")
            result.setText(f"{sum(saved for _, (saved, _) in done)} von {len(plan)} geprüften Kopien gespeichert.")
            result.setDetailedText("\n".join(f"{path.name}: {message}" for path, (_, message) in done))
            result.exec()

        filters.currentIndexChanged.connect(filter_results)

        table.cellDoubleClicked.connect(open_result)
        layout.addWidget(table)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        actions.addButton("Sichtbare Ergebnisse als Excel", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(report)
        actions.addButton("Auswahl: Suchtext ergänzen", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(repair)
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)
        dialog.resize(1000, 520)
        dialog.exec()

    def save_workbook(self, name, export):
        try:
            target = Path(name)
            if target.suffix.lower() != ".xlsx":
                raise ValueError("Bitte eine Datei mit der Endung .xlsx wählen.")
            with tempfile.TemporaryDirectory(dir=target.parent) as temp:
                candidate = Path(temp) / target.name
                export(candidate)
                os.replace(candidate, target)
            self.statusBar().showMessage(f"Excel-Liste gespeichert: {target.name}", 7500)
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def repair_archive_copy(self, path, target, tools):
        try:
            with pdf.open(path) as source, tempfile.TemporaryDirectory(dir=target.parent) as temp:
                if source.needs_pass or source.get_sigflags() > 0:
                    raise ValueError("Passwortgeschützte oder digital signierte PDF.")
                candidate = Path(temp) / "Archiv.pdf"
                summary = self.build_archive(source, candidate, True, tools)
                if not summary:
                    return False, "Abgebrochen."
                candidate.rename(target)
            return True, summary
        except Exception as exc:
            return False, str(exc)


    def inspect_pdfs(self, paths, title, inspect, parent=None):
        progress = QProgressDialog(title, "Abbrechen", 0, len(paths), parent or self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        results = []
        try:
            for index, path in enumerate(paths):
                progress.setLabelText(f"{title}\n{path.name}")
                progress.setValue(index)
                QApplication.processEvents()
                if progress.wasCanceled():
                    break
                results.append((path, inspect(path)))
        finally:
            progress.close()
        return results

    def duplicate_check(self, paths=None):
        if paths is None:
            folder = QFileDialog.getExistingDirectory(self, "Ordner für Dublettenprüfung auswählen")
            if not folder:
                return
            choice = QMessageBox.question(self, "Dubletten prüfen", "Unterordner einbeziehen?",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                          QMessageBox.StandardButton.Cancel)
            if choice == QMessageBox.StandardButton.Cancel:
                return
            paths = list(pdf_files(Path(folder), choice == QMessageBox.StandardButton.Yes, (".pdf", ".xml")))
        if not paths:
            QMessageBox.information(self, "Dubletten prüfen", "Keine PDF- oder XML-Dateien gefunden.")
            return
        cache = {}
        checked = self.inspect_pdfs(paths, "Dateien werden verglichen …", lambda path: inspect_duplicate(
            path, Path(__file__).with_name("tessdata"), cache))
        if not checked:
            return
        sources = [source for _, source in checked]
        exact, possible = duplicate_groups(sources)
        status = (f"{len(sources)} von {len(paths)} Dateien geprüft · Dateiidentische Gruppen: {len(exact)} · "
                  f"Mögliche Doppelbelege: {len(possible)}")
        unavailable = sum(invoice_match_key(source.fields) is None for source in sources)
        dialog = QDialog(self)
        dialog.setWindowTitle("Dubletten prüfen")
        layout = QVBoxLayout(dialog)
        status_label = QLabel(status)
        status_label.setWordWrap(True)
        layout.addWidget(status_label)
        info = QLabel(f"Dateien ohne vergleichbare Rechnungsdaten: {unavailable}. "
                      "Mögliche Doppelbelege stimmen bei Nummer, Datum, Betrag und Währung überein.")
        info.setWordWrap(True)
        layout.addWidget(info)
        groups = [*exact, *possible]
        choice = QComboBox()
        choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for index, group in enumerate(groups):
            if index < len(exact):
                label = f"Dateiidentisch · {group[0].path.name}"
            else:
                fields = group[0].fields
                label = "Möglicher Doppelbeleg · " + " · ".join(field.value for field in fields)
            choice.addItem(f"{label} ({len(group)} Dateien)")
        if groups:
            layout.addWidget(choice)
            split = QSplitter()
            sides = [self.duplicate_pane(split) for _ in range(2)]
            split.setSizes([500, 500])
            layout.addWidget(split, 1)

            def select_group(index):
                for side, populate in enumerate(sides):
                    populate(groups[index], side)

            choice.currentIndexChanged.connect(select_group)
            select_group(0)
        else:
            layout.addWidget(QLabel("In den geprüften Dateien wurden keine Dubletten gefunden."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(1040, 660 if groups else 190)
        dialog.exec()

    def duplicate_pane(self, split):
        pane = QWidget()
        split.addWidget(pane)
        layout = QVBoxLayout(pane)
        files = QComboBox()
        files.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(files)
        summary = QLabel()
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        pages = QSpinBox()
        pages.setPrefix("Seite ")
        layout.addWidget(pages)
        area = QScrollArea()
        area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image = QLabel()
        image.setTextFormat(Qt.TextFormat.PlainText)
        area.setWidget(image)
        layout.addWidget(area, 1)
        sources = []

        def render(value):
            index = files.currentIndex()
            if index < 0:
                return
            source = sources[index]
            if source.path.suffix.lower() == ".xml":
                labels = ("Rechnungsnummer", "Rechnungsdatum", "Betrag")
                values = [f"{label}  {field.value}" for label, field in zip(labels, source.fields) if field]
                image.setText("\n".join([source.origin, *values]) if values else source.problem or "XML-Datei")
                image.adjustSize()
                return
            try:
                with pdf.open(source.path) as doc:
                    page = doc[value - 1]
                    drawing = pixmap(page, 480 / max(page.rect.width, page.rect.height))
                image.setPixmap(drawing)
                image.resize(drawing.size())
            except Exception:
                image.setText("Keine Seitenvorschau verfügbar.")
                image.adjustSize()

        def select(index):
            if index < 0:
                return
            source = sources[index]
            fields = [field.value for field in source.fields if field]
            summary.setText(" · ".join(fields) or "Rechnungsdaten nicht verfügbar.")
            summary.setToolTip(str(source.path) + ("\n" + source.problem if source.problem else ""))
            count = 0
            if source.path.suffix.lower() == ".pdf":
                try:
                    with pdf.open(source.path) as doc:
                        if doc.is_pdf and not doc.needs_pass:
                            count = len(doc)
                except Exception:
                    pass
            first = next((field.page + 1 for field in source.fields if field and field.page is not None), 1)
            pages.blockSignals(True)
            pages.setRange(1, max(1, count))
            pages.setValue(first)
            pages.blockSignals(False)
            pages.setVisible(count > 0)
            render(pages.value())

        def populate(group, index):
            sources[:] = group
            files.blockSignals(True)
            files.clear()
            for source in group:
                files.addItem(f"{source.path.name} · {source.path.parent.name}")
            files.setCurrentIndex(index)
            files.blockSignals(False)
            select(index)

        files.currentIndexChanged.connect(select)
        pages.valueChanged.connect(render)
        return populate


    def invoice_java(self, parent):
        settings = self.favorite_settings
        tools = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF Werkbank" / "Prüfwerkzeuge"
        java = Path(settings.value("einvoice/java", "", str) or settings.value("standards/java", "", str)
                    or tools / "Java/bin/java.exe")
        if not java.is_file():
            selected, _ = QFileDialog.getOpenFileName(parent, "Java 11 oder neuer auswählen", "", "Java (java.exe)")
            if not selected:
                return None
            java = Path(selected)
            settings.setValue("einvoice/java", selected)
        return java, tools / KOSIT_FOLDER

    def check_invoice_batch(self, records, parent):
        electronic = any(record.source.path.suffix.lower() == ".xml" or record.embedded or record.source.origin
                         for record in records)
        setup = self.invoice_java(parent) if electronic else None
        if electronic and setup is None:
            return []
        stop = Event()
        results = []
        progress = QProgressDialog("Ausgewählte Belege werden lokal geprüft …", "Nach aktueller Datei abbrechen",
                                   0, len(records), parent)
        progress.setWindowTitle("Sammelprüfung")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoReset(False)
        progress.setAutoClose(False)
        progress.canceled.connect(stop.set)
        loop = QEventLoop(progress)
        timer = QTimer(progress)

        def check():
            installed = None
            if setup:
                java, directory = setup
                jar, rules = ensure_validator(directory)
                installed = java, jar, rules
            tessdata = Path(__file__).with_name("tessdata")
            for record in records:
                if stop.is_set():
                    break
                path = record.source.path
                try:
                    checked = read_watched_invoice(path, record.signature, tessdata, installed)
                    if not checked or checked.source.digest != record.source.digest or file_signature(path) != record.signature:
                        raise ValueError("Beleg hat sich geändert und muss erneut eingelesen werden.")
                    results.append((record, checked, ""))
                except Exception as exc:
                    results.append((record, None, str(exc)))

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(check)

                def refresh():
                    if not stop.is_set():
                        progress.setValue(len(results))
                    if future.done():
                        loop.quit()

                timer.timeout.connect(refresh)
                progress.show()
                timer.start(75)
                loop.exec()
                future.result()
        except Exception as exc:
            self.error(exc)
        finally:
            timer.stop()
            progress.close()
        return results

    def invoice_batch_dialog(self, records, duplicates, parent, destination=None, blocked=None, errors=None):
        dialog = QDialog(parent)
        dialog.setWindowTitle("Archivfreigabe für Auswahl" if destination else "Ergebnis der Sammelprüfung")
        layout = QVBoxLayout(dialog)
        heading = QLabel()
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        heading.setText(f"Zielordner: {destination}\nBelege mit Auffälligkeiten sind zunächst abgewählt. "
                        "Setze bei Bedarf ein Häkchen für die bewusste Freigabe."
                        if destination else "Doppelklick auf einen Beleg öffnet den KoSIT-Prüfbericht, sofern vorhanden.")
        layout.addWidget(heading)
        columns = ["Datei", "Prüfergebnis", "Hinweise"]
        if destination:
            columns.insert(0, "Freigeben")
        table = QTableWidget(len(records), len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(1 if destination else 0, 240)
        table.setColumnWidth(2 if destination else 1, 195)
        for row, record in enumerate(records):
            path = record.source.path
            duplicate = path in duplicates
            status, notices = invoice_archive_state(record, duplicate)
            issue = (errors or {}).get(path)
            if issue:
                status = "Prüfung fehlgeschlagen"
                notices.insert(0, issue)
            block = (blocked or {}).get(path)
            if block:
                notices.insert(0, block)
            offset = int(bool(destination))
            for column, value in enumerate((path.name, status, " · ".join(notices)), offset):
                item = QTableWidgetItem(value)
                item.setToolTip(str(path) if column == offset else value)
                table.setItem(row, column, item)
            if destination:
                box = QTableWidgetItem()
                box.setCheckState(Qt.CheckState.Unchecked if notices else Qt.CheckState.Checked)
                if block:
                    box.setFlags(box.flags() & ~(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled))
                table.setItem(row, 0, box)
        table.cellDoubleClicked.connect(lambda row, _: self.show_invoice_validation(
            records[row].source.path, records[row].embedded, records[row].validation, dialog)
            if records[row].validation else None)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel if destination else QDialogButtonBox.StandardButton.Close)
        if destination:
            approve = buttons.addButton("Markierte Belege freigeben", QDialogButtonBox.ButtonRole.AcceptRole)

            def update_button():
                approve.setEnabled(any(table.item(row, 0).checkState() == Qt.CheckState.Checked
                                       for row in range(table.rowCount())))

            table.itemChanged.connect(update_button)
            update_button()
            buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(920, 485)
        if dialog.exec() != QDialog.DialogCode.Accepted or not destination:
            return []
        return [record for row, record in enumerate(records)
                if not (blocked or {}).get(record.source.path) and table.item(row, 0).checkState() == Qt.CheckState.Checked]


    def check_einvoice(self, path, parent):
        try:
            data, embedded = invoice_xml(path)
            if data is None:
                QMessageBox.information(parent, "E-Rechnung prüfen",
                                        "Diese PDF enthält keine erkannte Rechnungs-XML. Für Bildscans stehen die OCR-Vorschläge bereit.")
                return
        except Exception as exc:
            self.error(exc)
            return
        setup = self.invoice_java(parent)
        if setup is None:
            return
        java, tools = setup

        def check():
            try:
                xml_root(data)
            except ElementTree.ParseError:
                return validate_invoice_xml(data, java, None, None)
            jar, rules = ensure_validator(tools)
            return validate_invoice_xml(data, java, jar, rules)

        progress = QProgressDialog("Rechnungs-XML wird lokal geprüft. Beim ersten Start werden die KoSIT-Prüfregeln geladen.",
                                   "", 0, 0, parent)
        progress.setWindowTitle("E-Rechnung prüfen")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        progress.setMinimumDuration(0)
        loop = QEventLoop(progress)
        timer = QTimer(progress)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(check)
                timer.timeout.connect(lambda: loop.quit() if future.done() else None)
                progress.show()
                timer.start(75)
                loop.exec()
                validation = future.result()
        except Exception as exc:
            self.error(exc)
            return
        finally:
            timer.stop()
            progress.close()
        self.show_invoice_validation(path, embedded, validation, parent)
        return validation, embedded

    def show_invoice_validation(self, path, embedded, validation, parent):
        dialog = QDialog(parent)
        dialog.setWindowTitle("E-Rechnung prüfen")
        layout = QVBoxLayout(dialog)
        statuses = {"passed": "Prüfung bestanden", "rejected": "Fehler gefunden",
                    "unsupported": "Rechnungsprofil wird vom Regelpaket nicht unterstützt"}
        summary = QLabel()
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        summary.setText(f"{path.name}\n{statuses[validation.status]}\n"
                        f"{validation.scenario or 'Ohne bestätigtes Prüfszenario'} · {RULES_VERSION}")
        layout.addWidget(summary)
        if embedded:
            note = QLabel("Geprüft wurde die eingebettete XML. Der PDF-Aufbau und die Übereinstimmung mit der Sichtfassung "
                          "sind gesondert zu prüfen.")
            note.setWordWrap(True)
            layout.addWidget(note)
        table = QTableWidget(len(validation.issues), 5)
        table.setHorizontalHeaderLabels(["Stufe", "Feld", "Regel", "Fundstelle", "Meldung"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(1, 250)
        table.setColumnWidth(3, 225)
        levels = {"error": "Fehler", "fatal": "Fehler", "warning": "Warnung", "information": "Hinweis"}
        for row, issue in enumerate(validation.issues):
            for column, value in enumerate((levels.get(issue.level, issue.level), issue.field, issue.rule,
                                            issue.location, issue.message)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, column, item)
        if validation.issues:
            layout.addWidget(table, 2)
            details = QPlainTextEdit()
            details.setReadOnly(True)
            layout.addWidget(details, 1)

            def selected(row, *_):
                if row >= 0:
                    issue = validation.issues[row]
                    details.setPlainText("\n".join(value for value in (issue.field, issue.rule, issue.location,
                                                                       issue.message) if value))

            table.currentCellChanged.connect(selected)
            table.setCurrentCell(0, 0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(1100, 610 if validation.issues else 220)
        dialog.exec()


    def invoice_folder(self, show=True, selected_path=None):
        previous = getattr(self, "_invoice_folder_window", None)
        if previous is not None:
            if show:
                previous.show()
                previous.raise_()
                previous.activateWindow()
                if selected_path is not None:
                    previous.focus_invoice(selected_path)
            return
        settings = self.favorite_settings
        class InvoiceFolderDialog(QDialog):
            def reject(self):
                self.hide()

            def closeEvent(self, event):
                self.hide()
                event.ignore()

        dialog = InvoiceFolderDialog(self)
        self._invoice_folder_window = dialog
        dialog.setWindowTitle("Überwachter Rechnungsordner")
        layout = QVBoxLayout(dialog)
        location = QLabel()
        location.setTextFormat(Qt.TextFormat.PlainText)
        location.setWordWrap(True)
        layout.addWidget(location)
        status = QLabel("Die Werkbank überwacht den gewählten Ordner auch bei geschlossenem Rechnungsfenster.")
        layout.addWidget(status)
        layout.addWidget(QLabel("Mit Strg oder Umschalt mehrere Belege auswählen."))
        filter = QComboBox()
        filter.addItems(["Alle", "Neu", "Fehler", "Dubletten", "Archiviert", "Wiedervorlage fällig", "Alle Wiedervorlagen"])
        layout.addWidget(filter)
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["Datei", "Rechnungsnummer", "E-Rechnungsprüfung", "Dubletten",
                                         "Archiv", "Notiz", "Wiedervorlage"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(1, 145)
        table.setColumnWidth(2, 195)
        table.setColumnWidth(3, 165)
        layout.addWidget(table)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        controls.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        choose = controls.addButton("Ordner wählen", QDialogButtonBox.ButtonRole.ActionRole)
        review = controls.addButton("Beleg ansehen", QDialogButtonBox.ButtonRole.ActionRole)
        validate = controls.addButton("Prüfdetails", QDialogButtonBox.ButtonRole.ActionRole)
        batch = controls.addButton("Auswahl prüfen", QDialogButtonBox.ButtonRole.ActionRole)
        note = controls.addButton("Notiz", QDialogButtonBox.ButtonRole.ActionRole)
        reminder = controls.addButton("Wiedervorlage", QDialogButtonBox.ButtonRole.ActionRole)
        reviewed = controls.addButton("Prüfstatus ändern", QDialogButtonBox.ButtonRole.ActionRole)
        archive = controls.addButton("Für Archiv freigeben", QDialogButtonBox.ButtonRole.ActionRole)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)

        saved_folder = settings.value("invoice-watch/folder", "", str)
        settler = IntakeSettler(saved_folder) if saved_folder else None
        records, retry, seen = {}, {}, {}
        future = None
        last_day = date.today()
        timer = QTimer(dialog)
        executor = ThreadPoolExecutor(max_workers=1)
        tools = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF Werkbank" / "Prüfwerkzeuge"
        statuses = {"passed": "Bestanden", "rejected": "Fehler gefunden", "unsupported": "Profil nicht unterstützt"}

        def selected():
            item = table.item(table.currentRow(), 0)
            return records.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

        def selected_records():
            paths = (table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                     for index in sorted(table.selectionModel().selectedRows(), key=lambda index: index.row())
                     if table.item(index.row(), 0) is not None)
            return [records[path] for path in paths if path in records]

        def duplicate_paths():
            exact, possible = duplicate_groups([record.source for record in records.values()])
            return ({source.path for group in exact for source in group},
                    {source.path for group in possible for source in group})

        def archived_path(record):
            source = record.source
            for key in (archive_setting_key(source.path), "invoice-watch/archived-digest/" + source.digest):
                digest, _, target = settings.value(key, "", str).partition("|")
                if digest == source.digest and target and Path(target).is_file():
                    return target
            return ""

        def update_actions():
            count = len(selected_records())
            for button in (review, validate, note, reviewed, reminder):
                button.setEnabled(count == 1)
            batch.setEnabled(count > 0)
            archive.setEnabled(count > 0)

        def update_badge():
            unread = 0
            for record in records.values():
                source = record.source
                key = archive_setting_key(source.path).replace("/archived/", "/seen/")
                if key not in seen:
                    seen[key] = settings.value(key, "", str)
                if dialog.isVisible():
                    if seen[key] != source.digest:
                        settings.setValue(key, source.digest)
                        seen[key] = source.digest
                elif seen[key] != source.digest:
                    unread += 1
            today = date.today()
            due = sum(day is not None and day <= today for day, _ in
                      (get_reminder(digest) for digest in {record.source.digest for record in records.values()}))
            counts = [label for count, label in ((unread, f"{unread} neu"), (due, f"{due} fällig")) if count]
            badge.setText("Rechnungen: " + " · ".join(counts) if counts else "Rechnungen")
            badge.setVisible(settler is not None)

        badge = self.invoice_badge

        def get_reminder(digest):
            stored = settings.value("invoice-watch/reminder/" + digest, "", str)
            try:
                if stored:
                    return reminder_date(stored)
            except ValueError:
                pass
            return None, ""

        def due_entries():
            today = date.today()
            due = []
            for path, record in records.items():
                day, memo = get_reminder(record.source.digest)
                if day is not None and day <= today:
                    due.append((day, path, memo))
            return sorted(due)

        def focus_invoice(path):
            filter.setCurrentText("Wiedervorlage fällig")
            for row in range(table.rowCount()):
                if table.item(row, 0).data(Qt.ItemDataRole.UserRole) == path:
                    table.setCurrentCell(row, 0)
                    table.selectRow(row)
                    return
            status.setText("Der Beleg ist nicht mehr in den fälligen Wiedervorlagen.")

        dialog.due_entries = due_entries
        dialog.focus_invoice = focus_invoice

        def refresh():
            update_badge()
            current = selected()
            selection = {record.source.path for record in selected_records()}
            exact, possible = duplicate_paths()
            ordered = []
            visible = []
            today = date.today()
            for path in sorted(records, key=lambda path: path.name.casefold()):
                record = records[path]
                source = record.source
                destination = archived_path(record)
                archived = bool(destination)
                checked = settings.value("invoice-watch/reviewed/" + source.digest, False, bool)
                problem = (record.validation and record.validation.status != "passed") or source.problem or (
                    record.check_problem and record.check_problem != "E-Rechnung noch nicht geprüft")
                day, reminder_note = get_reminder(source.digest)
                flags = (True, not archived and not checked, problem, path in exact or path in possible, archived,
                         day is not None and day <= today, day is not None)
                if flags[filter.currentIndex()]:
                    ordered.append(path)
                    visible.append((archived, checked, destination, day, reminder_note))
            table.blockSignals(True)
            table.setRowCount(len(ordered))
            for row, path in enumerate(ordered):
                record = records[path]
                source = record.source
                check = (statuses[record.validation.status] if record.validation else
                         record.check_problem or source.problem or "–")
                marks = [label for group, label in ((exact, "Dateiidentisch"), (possible, "Möglicher Doppelbeleg"))
                         if path in group]
                archived, checked, destination, day, reminder_note = visible[row]
                note_text = settings.value("invoice-watch/note/" + source.digest, "", str)
                schedule = day.strftime("%d.%m.%Y") if day else ""
                if day and day <= today:
                    schedule += " · Überfällig" if day < today else " · Heute fällig"
                if reminder_note:
                    schedule += " · " + reminder_note
                values = (path.name, source.fields[0].value if source.fields[0] else "", check,
                          " · ".join(marks), "Freigegeben und kopiert" if archived else "Gesehen" if checked else "Offen",
                          note_text, schedule)
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(str(path) if column == 0 else destination if column == 4 and archived else value)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, path)
                    table.setItem(row, column, item)
            table.clearSelection()
            if ordered:
                old = current.source.path if current else None
                chosen = [row for row, path in enumerate(ordered) if path in selection] or [0]
                active = ordered.index(old) if old in ordered and ordered.index(old) in chosen else chosen[0]
                flags = QItemSelectionModel.SelectionFlag
                table.selectionModel().setCurrentIndex(table.model().index(active, 0), flags.NoUpdate)
                for row in chosen:
                    table.selectionModel().select(table.model().index(row, 0), flags.Select | flags.Rows)
            table.blockSignals(False)
            update_actions()
        def change_folder():
            nonlocal settler
            folder = QFileDialog.getExistingDirectory(dialog, "Rechnungsordner auswählen",
                                                       str(settler.folder) if settler else "")
            if folder:
                folder = Path(folder).resolve()
                settings.setValue("invoice-watch/folder", str(folder))
                settler = IntakeSettler(folder)
                records.clear()
                retry.clear()
                refresh()
                tick()

        def tick():
            nonlocal future, last_day
            if settler is None:
                location.setText("Bitte einen Rechnungsordner wählen.")
                return
            location.setText(str(settler.folder))
            if not settler.folder.is_dir():
                if records:
                    records.clear()
                    refresh()
                settler.observed.clear()
                status.setText("Der Rechnungsordner ist derzeit nicht erreichbar.")
                return
            now = time.monotonic()
            stable = settler.poll(now)
            today = date.today()
            changed = today != last_day
            last_day = today
            for path in list(records):
                if stable.get(path) != records[path].signature:
                    del records[path]
                    changed = True
            for path in list(retry):
                if path not in stable:
                    del retry[path]
            if future and future[2].done():
                path, signature, job = future
                future = None
                if stable.get(path) == signature:
                    try:
                        record = job.result()
                    except OSError:
                        retry[path] = now + 2
                    else:
                        if record is not None:
                            records[path] = record
                            changed = True
            if future is None:
                path = next((path for path in stable if path not in records and retry.get(path, 0) <= now), None)
                if path is not None:
                    java = Path(settings.value("einvoice/java", "", str) or settings.value("standards/java", "", str)
                                or tools / "Java/bin/java.exe")
                    directory = tools / KOSIT_FOLDER
                    jar, rules = directory / "validator.jar", directory / "rules"
                    installed = (java, jar, rules) if java.is_file() and jar.is_file() and (rules / "scenarios.xml").is_file() else None
                    job = executor.submit(read_watched_invoice, path, stable[path], Path(__file__).with_name("tessdata"), installed)
                    future = path, stable[path], job
            if changed:
                refresh()
            update_badge()
            status.setText(f"{len(records)} Beleg(e) im Eingang · "
                           f"{max(0, len(settler.observed) - len(records))} warten auf Kopierende oder Prüfung")

        def show_check():
            record = selected()
            if not record:
                return
            path = record.source.path
            if record.validation:
                self.show_invoice_validation(path, record.embedded, record.validation, dialog)
                return
            result = self.check_einvoice(path, dialog)
            if result is not None and records.get(path) is record:
                validation, embedded = result
                try:
                    if file_signature(path) != record.signature:
                        return
                except OSError:
                    return
                records[path] = replace(record, validation=validation, embedded=embedded, check_problem="")
                refresh()

        def edit_note():
            record = selected()
            if not record:
                return
            key = "invoice-watch/note/" + record.source.digest
            text, ok = QInputDialog.getMultiLineText(dialog, "Belegnotiz", "Notiz zu dieser Datei",
                                                    settings.value(key, "", str))
            if ok:
                if text.strip():
                    settings.setValue(key, text.strip()[:1000])
                else:
                    settings.remove(key)
                refresh()

        def toggle_review():
            record = selected()
            if not record:
                return
            key = "invoice-watch/reviewed/" + record.source.digest
            settings.setValue(key, not settings.value(key, False, bool))
            refresh()

        def edit_reminder():
            record = selected()
            if not record:
                return
            key = "invoice-watch/reminder/" + record.source.digest
            day, memo = get_reminder(record.source.digest)
            editor = QDialog(dialog)
            editor.setWindowTitle("Wiedervorlage für " + record.source.path.name)
            form = QFormLayout(editor)
            enabled = QCheckBox("Wiedervorlage aktiv")
            enabled.setChecked(True)
            form.addRow(enabled)
            calendar = QDateEdit(QDate(day.year, day.month, day.day) if day else QDate.currentDate())
            calendar.setDisplayFormat("dd.MM.yyyy")
            calendar.setMinimumDate(QDate(1, 1, 1))
            calendar.setCalendarPopup(True)
            message = QLineEdit(memo)
            message.setMaxLength(1000)
            form.addRow("Termin", calendar)
            form.addRow("Notiz", message)
            enabled.toggled.connect(calendar.setEnabled)
            enabled.toggled.connect(message.setEnabled)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("Speichern")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
            buttons.accepted.connect(editor.accept)
            buttons.rejected.connect(editor.reject)
            form.addRow(buttons)
            if editor.exec() != QDialog.DialogCode.Accepted:
                return
            if enabled.isChecked():
                settings.setValue(key, calendar.date().toPython().isoformat() + "|" + message.text().strip())
            else:
                settings.remove(key)
            settings.sync()
            if settings.status() != QSettings.Status.NoError:
                self.error("Die Wiedervorlage konnte nicht dauerhaft gespeichert werden.")
            refresh()

        def check_selection():
            selected_items = selected_records()
            if not selected_items:
                return
            errors = {}
            completed = []
            for previous, checked, issue in self.check_invoice_batch(selected_items, dialog):
                path = previous.source.path
                current = records.get(path)
                if not checked or not current or current.signature != checked.signature or current.source.digest != checked.source.digest:
                    errors[path] = issue or "Beleg hat sich während der Prüfung geändert."
                    completed.append(previous)
                    continue
                records[path] = checked
                completed.append(checked)
            refresh()
            if completed:
                exact, possible = duplicate_paths()
                self.invoice_batch_dialog(completed, exact | possible, dialog, errors=errors)

        def approve_many(items, name):
            incoming, destination = settler.folder, Path(name).resolve()
            if not destination.is_dir() or destination.is_relative_to(incoming.resolve()):
                self.error("Bitte einen Archivordner außerhalb des Rechnungsordners auswählen.")
                return
            blocked = {}
            for record in items:
                path = record.source.path
                if archived_path(record):
                    blocked[path] = "Bereits archiviert."
                elif (destination / path.name).exists():
                    blocked[path] = "Zieldatei existiert bereits."
            exact, possible = duplicate_paths()
            approved = self.invoice_batch_dialog(items, exact | possible, dialog, destination, blocked)
            if not approved:
                return
            by_path = {record.source.path: record for record in approved}

            def save(path):
                record = by_path[path]
                try:
                    saved = archive_watched_invoice(record, incoming, destination)
                    settings.setValue("invoice-watch/archive", str(destination))
                    settings.setValue(archive_setting_key(path), f"{record.source.digest}|{saved}")
                    settings.sync()
                except Exception as exc:
                    return False, str(exc)
                if settings.status() != QSettings.Status.NoError:
                    return True, "Datei kopiert. Archivstatus konnte nicht gespeichert werden."
                return True, ""

            outcomes = self.inspect_pdfs(list(by_path), "Belege werden archiviert …", save, dialog)
            refresh()
            message = QMessageBox(dialog)
            message.setWindowTitle("Sammelarchivierung")
            message.setText(f"{sum(success for _, (success, _) in outcomes)} von {len(approved)} freigegebenen Belegen kopiert.")
            details = [f"{path.name}: {issue}" for path, (_, issue) in outcomes if issue]
            if details:
                message.setDetailedText("\n".join(details))
            message.exec()

        def approve():
            items = selected_records()
            if not items:
                return
            record = items[0]
            name = QFileDialog.getExistingDirectory(dialog, "Archivordner für die Originalkopien auswählen",
                                                     settings.value("invoice-watch/archive", "", str))
            if not name:
                return
            if len(items) > 1:
                approve_many(items, name)
                return
            target = Path(name) / record.source.path.name
            if QMessageBox.question(dialog, "Archivfreigabe", f"Diesen Beleg als unveränderte Kopie ablegen?\n{target}",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            try:
                saved = archive_watched_invoice(record, settler.folder, Path(name))
                settings.setValue("invoice-watch/archive", name)
                settings.setValue(archive_setting_key(record.source.path), f"{record.source.digest}|{saved}")
                settings.sync()
            except Exception as exc:
                self.error(exc)
            refresh()

        def finish(_):
            timer.stop()
            executor.shutdown(wait=False, cancel_futures=True)
            self._invoice_folder_window = None
            dialog.deleteLater()

        choose.clicked.connect(change_folder)
        review.clicked.connect(lambda: self.invoice_review([str(record.source.path)]) if (record := selected()) else None)
        validate.clicked.connect(show_check)
        batch.clicked.connect(check_selection)
        table.itemSelectionChanged.connect(update_actions)
        archive.clicked.connect(approve)
        note.clicked.connect(edit_note)
        reminder.clicked.connect(edit_reminder)
        reviewed.clicked.connect(toggle_review)
        filter.currentIndexChanged.connect(refresh)
        dialog.finished.connect(finish)
        timer.timeout.connect(tick)
        timer.start(750)
        refresh()
        tick()
        dialog.resize(1110, 550)
        if show:
            dialog.show()
            update_badge()
            if selected_path is not None:
                focus_invoice(selected_path)


    def invoice_review(self, names=None):
        if names is None:
            names, _ = QFileDialog.getOpenFileNames(self, "Rechnungen auswählen", "", "PDF und XML (*.pdf *.xml)")
        if not names:
            return

        def read(path):
            try:
                fields, origin = read_invoice(path, Path(__file__).with_name("tessdata"))
                return fields, origin, ""
            except Exception as exc:
                return (None, None, None), "", str(exc)

        results = self.inspect_pdfs([Path(name) for name in names], "Rechnungen werden gelesen …", read)
        if not results:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Rechnungseingang")
        layout = QVBoxLayout(dialog)
        info = QLabel("Vorschläge prüfen und in der Tabelle korrigieren. Die Vorschau markiert die Fundstelle. "
                      "Bei OCR werden die ersten drei Seiten und die letzte Seite ausgewertet. XML-Werte stammen aus der Quelldatei.")
        info.setWordWrap(True)
        layout.addWidget(info)
        split = QSplitter(Qt.Orientation.Vertical)
        table = QTableWidget(len(results), 5)
        table.setHorizontalHeaderLabels(["Rechnungsnummer", "Rechnungsdatum", "Betrag", "Quelldatei", "Hinweis"])
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for row, (path, (fields, origin, failure)) in enumerate(results):
            for column, value in enumerate([*(field.value if field else "" for field in fields),
                                            path.name, failure or origin or ("Vorschläge prüfen" if any(fields) else "Keine eindeutige Fundstelle")]):
                item = QTableWidgetItem(value)
                if column >= 3:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip(str(path) if column == 3 else value)
                table.setItem(row, column, item)
        split.addWidget(table)
        area = QScrollArea()
        area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image = QLabel()
        image.setTextFormat(Qt.TextFormat.PlainText)
        area.setWidget(image)
        split.addWidget(area)
        split.setStretchFactor(1, 2)
        layout.addWidget(split)

        def preview(row, column, *_):
            if row < 0:
                return
            path, (fields, origin, failure) = results[row]
            field = fields[column] if 0 <= column < 3 else None
            if path.suffix.lower() == ".xml":
                image.setText(failure or "\n".join([origin, path.name, *(field.value for field in fields if field)]))
                image.adjustSize()
                return
            try:
                with pdf.open(path) as doc:
                    page = doc[field.page if field and field.page is not None else 0]
                    scale = 520 / max(page.rect.width, page.rect.height)
                    drawing = pixmap(page, scale)
                    if field and field.rect is not None:
                        box = field.rect * page.rotation_matrix
                        painter = QPainter(drawing)
                        painter.fillRect(QRect(round(box.x0 * scale), round(box.y0 * scale),
                                               max(2, round(box.width * scale)), max(2, round(box.height * scale))),
                                         QColor(255, 135, 20, 150))
                        painter.end()
                image.setPixmap(drawing)
                image.resize(drawing.size())
            except Exception as exc:
                image.setText(failure or str(exc))
                image.adjustSize()

        def save():
            target, _ = QFileDialog.getSaveFileName(dialog, "Rechnungsliste speichern",
                                                    "Rechnungen.xlsx", "Excel (*.xlsx)")
            if not target:
                return
            rows = []
            for row, (path, (fields, origin, _)) in enumerate(results):
                values = [table.item(row, column).text().strip() for column in range(3)]
                pages = [str(field.page + 1) if field and field.page is not None and value else "–" for field, value in zip(fields, values)]
                rows.append((*values, str(path), origin or " / ".join(pages)))
            if self.save_workbook(target, lambda path: export_invoices(rows, path)):
                dialog.accept()

        table.currentCellChanged.connect(preview)
        table.setCurrentCell(0, 0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton("E-Rechnung der Auswahl prüfen", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(
            lambda: self.check_einvoice(results[table.currentRow()][0], dialog) if table.currentRow() >= 0 else None)
        buttons.addButton("Diese Dateien auf Dubletten prüfen", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(
            lambda: self.duplicate_check(list(dict.fromkeys(path for path, _ in results))))
        buttons.addButton("Geprüfte Liste als Excel speichern", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(save)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(1050, 760)
        dialog.exec()

    def check_required_fields(self, missing=None):
        session = self.current
        if not session:
            return
        if missing is None:
            missing = missing_required_fields(session.doc)
        if not missing:
            QMessageBox.information(self, "Pflichtfelder prüfen", "Keine leeren Pflichtfelder gefunden.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Leere Pflichtfelder")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{len(missing)} Pflichtfeld(er) ausfüllen."))
        rows = QListWidget()
        rows.addItems([f"Seite {index + 1} · {name}" for index, _, name in missing])
        rows.setCurrentRow(0)
        rows.itemDoubleClicked.connect(dialog.accept)
        layout.addWidget(rows)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton("Zum Feld", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and rows.currentRow() >= 0:
            index, rect, _ = missing[rows.currentRow()]
            self.set_mode("Formular")
            self.go_page(index)
            box = rect * session.doc[index].rotation_matrix
            self.scroll.ensureVisible(round(box.x0 * self.scale), round(box.y0 * self.scale), 20, 60)
            self.statusBar().showMessage(f"Pflichtfeld: {missing[rows.currentRow()][2]}", 7500)

    def configure_explorer(self):
        launcher = os.environ.get("PDF_WERKBANK_ENTRYPOINT") or os.environ.get("PDF_WERKBANK_LAUNCHER")
        if not launcher:
            QMessageBox.information(self, "Explorer-Befehle", "Bitte die installierte PDF Werkbank starten.")
            return
        try:
            import ctypes
            # Resolve the current user's actual SendTo folder, including redirected profiles.
            buffer = ctypes.create_unicode_buffer(32768)
            result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0009, None, 0, buffer)
            if result != 0:
                raise OSError("Windows konnte den Ordner Senden an nicht finden.")
            install_explorer_commands(launcher, buffer.value)
            QMessageBox.information(self, "Explorer-Befehle", "Die Befehle stehen unter Rechtsklick → Senden an. "
                                    "Unter Windows 11 zuerst Weitere Optionen anzeigen wählen.")
        except Exception as exc:
            self.error(exc)

    def batch_preview(self, paths, target, steps=None, audit_results=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Stapelvorschau")
        layout = QVBoxLayout(dialog)
        labels = {"ocr": "OCR", "rotate": "90° nach rechts drehen", "compress": "Komprimieren"}
        operation = (" → ".join(labels[step] for step in ("ocr", "rotate", "compress") if step in steps)
                     if steps is not None else "PDFs zusammenführen")
        if audit_results is not None:
            operation = "Fehlenden Suchtext ergänzen → PDF/A-Kopie erzeugen → mit veraPDF prüfen"
        layout.addWidget(QLabel(operation))
        destination = QLabel()
        destination.setTextFormat(Qt.TextFormat.PlainText)
        destination.setText(str(target))
        destination.setWordWrap(True)
        layout.addWidget(destination)
        rows = QListWidget()
        rows.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        rows.setDefaultDropAction(Qt.DropAction.MoveAction)
        layout.addWidget(rows)
        targets = ([target] * len(paths) if steps is None else
                   [target / f"{path.stem} - Stapel.pdf" for path in paths])
        if audit_results is not None:
            targets = [target / f"{path.stem} - OCR Archiv.pdf" for path in paths]
        keys = Counter(os.path.normcase(str(path.resolve())) for path in targets)
        sources = {path.resolve() for path in paths}
        for path, output in zip(paths, targets):
            issue = ""
            try:
                with pdf.open(path) as doc:
                    if not doc.is_pdf or doc.needs_pass or not len(doc):
                        raise ValueError("Keine lesbare PDF ohne Passwort.")
                    if (steps is None or audit_results is not None) and doc.get_sigflags() > 0:
                        raise ValueError("Digital signierte PDF.")
                    count = len(doc)
            except Exception as exc:
                count, issue = 0, str(exc)
            if output.exists() or output.resolve() in sources:
                issue = "Zieldatei existiert bereits."
            if steps is not None and keys[os.path.normcase(str(output.resolve()))] > 1:
                issue = "Mehrere Quellen ergeben denselben Zielnamen."
            detail = f" · Ohne Suchtext: {', '.join(map(str, audit_results[path].missing_text))}" if audit_results else ""
            item = QListWidgetItem(f"{path.name} · {count} Seite(n){detail}\n→ {output.name}" +
                                   (f" · Übersprungen: {issue}" if issue and steps is not None else f" · {issue}" if issue else ""))
            item.setToolTip(f"{path}\n{output}" + (f"\n{issue}" if issue else ""))
            item.setData(Qt.ItemDataRole.UserRole, (str(path), str(output), issue))
            if issue:
                item.setForeground(QColor("#a53c12"))
            rows.addItem(item)
        movement = QHBoxLayout()

        def move(delta):
            current = rows.currentRow()
            if current >= 0 and 0 <= current + delta < rows.count():
                rows.insertItem(current + delta, rows.takeItem(current))
                rows.setCurrentRow(current + delta)

        for label, delta in (("Nach oben", -1), ("Nach unten", 1)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, offset=delta: move(offset))
            movement.addWidget(button)
        movement.addStretch()
        layout.addLayout(movement)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        start = buttons.addButton("Verarbeitung starten", QDialogButtonBox.ButtonRole.AcceptRole)
        valid = sum(not rows.item(index).data(Qt.ItemDataRole.UserRole)[2] for index in range(rows.count()))
        start.setEnabled(valid >= 2 and valid == len(paths) if steps is None else valid > 0)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        rows.setCurrentRow(0)
        dialog.resize(770, 480)
        if dialog.exec() != QDialog.DialogCode.Accepted or not start.isEnabled():
            return []
        return [(Path(path), Path(output)) for index in range(rows.count())
                for path, output, issue in (rows.item(index).data(Qt.ItemDataRole.UserRole),) if not issue]


    def explorer_action(self, command, arguments):
        try:
            paths = explorer_paths(arguments)
            if not paths:
                raise ValueError("Bitte im Explorer mindestens eine PDF auswählen.")
            if command == "--explorer-workflow":
                profiles = self.read_batch_profiles(self.favorite_settings)
                if not profiles:
                    QMessageBox.information(self, "Gespeicherter Ablauf",
                                            "Zuerst unter Export & Schutz einen Ablauf speichern.")
                    return
                name, ok = QInputDialog.getItem(self, "Gespeicherter Ablauf", f"Ablauf für {len(paths)} PDF(s)",
                                                sorted(profiles), editable=False)
                if ok:
                    self.run_batch(paths, profiles[name])
                return
            if len(paths) < 2:
                raise ValueError("Bitte mindestens zwei PDFs auswählen.")
            name, _ = QFileDialog.getSaveFileName(self, "Zusammengeführte PDF speichern",
                                                 str(paths[0].parent / "Zusammengeführt.pdf"), "PDF (*.pdf)")
            if not name:
                return
            target = Path(name).resolve()
            if target.suffix.lower() != ".pdf" or target in paths or any(
                    target == session.path.resolve() for session in self.sessions):
                raise ValueError("Bitte eine andere PDF-Datei als Ziel wählen.")
            plan = self.batch_preview(paths, target)
            if not plan:
                return
            with pdf.open() as output, tempfile.TemporaryDirectory(dir=target.parent) as temp:
                merge_pdfs([source for source, _ in plan], output)
                candidate = Path(temp) / "Kopie.pdf"
                save_copy(output, candidate)
                candidate.rename(target)
            self.open_path(str(target))
        except Exception as exc:
            self.error(exc)


    def form_template_dialog(self, available):
        settings = self.favorite_settings
        templates = form_templates(settings)
        dialog = QDialog(self)
        dialog.setWindowTitle("Excel-Formularvorlagen")
        dialog.resize(850, 485)
        layout = QVBoxLayout(dialog)
        info = QLabel("Lege die Excel-Spalten von oben nach unten fest. Trage pro Zeile im mittleren Feld "
                      "einen PDF-Feldnamen ein. Doppelklick auf einen gefundenen Feldnamen fügt ihn der gewählten Spalte hinzu.")
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        choice = QComboBox()
        name = QLineEdit()
        name.setMaxLength(100)
        form.addRow("Vorlage", choice)
        form.addRow("Name beim Speichern", name)
        layout.addLayout(form)
        content = QHBoxLayout()
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Excel-Spalte", "PDF-Feldnamen", "Pflicht"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 190)
        table.setColumnWidth(2, 65)
        content.addWidget(table, 3)
        fields = QListWidget()
        fields.addItems(available)
        found = QVBoxLayout()
        found.addWidget(QLabel("Gefundene PDF-Feldnamen"))
        found.addWidget(fields)
        content.addLayout(found, 1)
        layout.addLayout(content)
        actions = QHBoxLayout()
        add = QPushButton("Spalte hinzufügen")
        remove = QPushButton("Spalte entfernen")
        delete = QPushButton("Vorlage löschen")
        for button in (add, remove, delete):
            actions.addWidget(button)
        layout.addLayout(actions)
        notice = QLabel()
        notice.setWordWrap(True)
        layout.addWidget(notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        use = buttons.addButton("Gespeicherte Vorlage verwenden", QDialogButtonBox.ButtonRole.ActionRole)
        save = buttons.addButton("Vorlage speichern und Vorschau", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        layout.addWidget(buttons)
        result = None

        def append_column(heading="", aliases=(), required=False):
            row = table.rowCount()
            if row >= 100:
                return
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(heading))
            editor = QPlainTextEdit("\n".join(aliases))
            table.setCellWidget(row, 1, editor)
            table.setRowHeight(row, 75)
            flag = QTableWidgetItem()
            flag.setFlags(flag.flags() & ~Qt.ItemFlag.ItemIsEditable)
            flag.setCheckState(Qt.CheckState.Checked if required else Qt.CheckState.Unchecked)
            table.setItem(row, 2, flag)
            table.setCurrentCell(row, 0)

        def load():
            selected = choice.currentData()
            name.setText(selected or "")
            table.setRowCount(0)
            for heading, aliases, required in templates.get(selected, []):
                append_column(heading, aliases, required)
            if not selected:
                append_column()
            else:
                table.setCurrentCell(0, 0)
            delete.setEnabled(selected is not None)
            use.setEnabled(selected is not None)
            notice.clear()

        def refresh_choices():
            choice.blockSignals(True)
            choice.clear()
            choice.addItem("Neue Vorlage")
            for label in sorted(templates, key=str.casefold):
                choice.addItem(label, label)
            if templates:
                choice.setCurrentIndex(1)
            choice.blockSignals(False)
            load()

        def add_field(item):
            if table.currentRow() < 0:
                append_column()
            row = table.currentRow()
            editor = table.cellWidget(row, 1)
            if item.text() not in editor.toPlainText().splitlines():
                editor.appendPlainText(item.text())
            if not table.item(row, 0).text().strip():
                table.item(row, 0).setText(item.text())

        def store(changed):
            validate_form_templates(changed)
            settings.setValue(FORM_TEMPLATES, json.dumps(changed, ensure_ascii=False))
            settings.sync()
            if settings.status() != QSettings.Status.NoError:
                raise OSError("Die Excel-Vorlagen konnten nicht dauerhaft gespeichert werden.")

        def accept():
            nonlocal result
            label = name.text().strip()
            columns = []
            for row in range(table.rowCount()):
                heading = table.item(row, 0).text().strip()
                aliases = list(dict.fromkeys(line for line in table.cellWidget(row, 1).toPlainText().splitlines()
                                              if line.strip()))
                required = table.item(row, 2).checkState() == Qt.CheckState.Checked
                columns.append([heading, aliases, required])
            try:
                current = form_templates(settings)
                if label in current and (label != choice.currentData() or current[label] != templates.get(label)):
                    raise ValueError("Die Vorlage ist bereits vorhanden oder wurde geändert. "
                                     "Wähle einen neuen Namen oder öffne den Dialog erneut.")
                store({**current, label: columns})
            except (OSError, ValueError) as exc:
                notice.setText(str(exc))
                return
            result = label, columns
            dialog.accept()

        def use_saved():
            nonlocal result
            selected = choice.currentData()
            if selected in templates:
                result = selected, templates[selected]
                dialog.accept()

        def delete_template():
            selected = choice.currentData()
            if selected is None or QMessageBox.question(
                    dialog, "Vorlage löschen", f"„{selected}“ löschen?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            try:
                changed = form_templates(settings)
                if changed.get(selected) != templates[selected]:
                    raise ValueError("Die Vorlage wurde geändert. Öffne den Dialog erneut.")
                del changed[selected]
                store(changed)
            except (OSError, ValueError) as exc:
                notice.setText(str(exc))
                return
            templates.clear()
            templates.update(changed)
            refresh_choices()

        choice.currentIndexChanged.connect(load)
        fields.itemDoubleClicked.connect(add_field)
        add.clicked.connect(lambda: append_column())
        remove.clicked.connect(lambda: table.removeRow(table.currentRow()))
        delete.clicked.connect(delete_template)
        use.clicked.connect(use_saved)
        save.clicked.connect(accept)
        buttons.rejected.connect(dialog.reject)
        refresh_choices()
        dialog.exec()
        return result

    def form_highlights(self, session, page):
        if self.form_jump is None or self.form_jump[0] is not session:
            return []
        _, aliases, filled = self.form_jump
        return [box for name, value, box in form_fields(page) if name in aliases and (not filled or value.strip())]

    def jump_to_form_field(self, path, aliases, filled, preview):
        session = next((item for item in self.sessions if item.path.resolve() == path), None)
        if session is None:
            session = self.open_path(str(path))
        if session is None:
            return
        self.form_jump = session, frozenset(aliases), filled
        if not any(self.form_highlights(session, page) for page in session.doc):
            self.form_jump = None
            self.render()
            QMessageBox.information(preview, "Kein passendes Formularfeld",
                                    "Gesuchte PDF-Feldnamen:\n" + "\n".join(aliases))
            return
        preview.hide()
        self.tabs.setCurrentIndex(self.sessions.index(session))
        self.raise_()
        self.activateWindow()
        self.set_mode("Formular")
        self.next_form_field(start=0)

    def next_form_field(self, start=None):
        if self.form_jump is None or self.form_jump[0] is not self.current:
            return
        session = self.current
        if start is None:
            start = session.page + 1
        for offset in range(len(session.doc)):
            index = (start + offset) % len(session.doc)
            page = session.doc[index]
            boxes = self.form_highlights(session, page)
            if boxes:
                self.thumbs.setCurrentRow(index)
                self.go_page(index)
                box = boxes[0] * page.rotation_matrix
                self.scroll.ensureVisible(round(box.x0 * self.scale), round(box.y0 * self.scale), 25, 65)
                return

    def export_form_batch(self, use_template=False, paths=None, template=None):
        if use_template and self.form_preview is not None:
            self.form_preview.show()
            self.form_preview.raise_()
            self.form_preview.activateWindow()
            return
        if paths is None:
            names, _ = QFileDialog.getOpenFileNames(self, "Ausgefüllte PDF-Formulare auswählen", "", "PDF (*.pdf)")
            if not names:
                return
            paths = list(dict.fromkeys(Path(name).resolve() for name in names))
        if len(paths) > 1000:
            self.error("Bitte höchstens 1.000 PDFs auf einmal auswählen.")
            return
        signatures = {}

        def inspect(path):
            stamp = None
            try:
                if use_template:
                    stamp = file_signature(path)
                fields = read_form_values(path, separate=use_template)
                if use_template and file_signature(path) != stamp:
                    return {}, "Die Datei wurde beim Einlesen geändert."
                return fields, "" if fields else "Keine exportierbaren Formularfelder"
            except (OSError, RuntimeError, ValueError) as exc:
                return {}, str(exc)
            finally:
                if use_template:
                    signatures[path] = stamp

        outcomes = self.inspect_pdfs(paths, "Formularfelder werden gelesen …", inspect)
        if len(outcomes) != len(paths):
            return
        columns = sorted({name for _, (fields, _) in outcomes for name in fields}, key=lambda name: (name.casefold(), name))
        cell_issues = {}
        if use_template:
            try:
                if template is None:
                    template = self.form_template_dialog(columns)
            except (OSError, ValueError) as exc:
                self.error(exc)
                return
            if template is None:
                return
            title, mappings = template
            columns = [heading for heading, _, _ in mappings]
        if len(columns) > 1000 or len(outcomes) * (len(columns) + 2) > 100000:
            self.error("Zu viele Formularfelder für die Vorschau. Bitte weniger PDFs auswählen.")
            return
        header = ["PDF-Datei", "Hinweis", *columns]
        rows = []
        for row, (path, (fields, issue)) in enumerate(outcomes):
            if template:
                values, issues = map_form_values(fields, mappings)
                cell_issues.update({(row, column + 2): item for column, item in issues.items()})
                status = " · ".join(filter(None, [issue, *(label for label, _ in issues.values())]))
            else:
                status = issue or (f"{len(columns) - len(fields)} Feld(er) fehlen" if len(fields) != len(columns) else "")
                values = [fields.get(name) for name in columns]
            rows.append((str(path), status, *values))
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Formular-Export prüfen – {title}" if template else "Formular-Export prüfen")
        dialog.resize(920, 500)
        layout = QVBoxLayout(dialog)
        guidance = ("Pflichtwerte und Widersprüche sind markiert. Fahre mit der Maus über eine markierte Zelle, "
                    "um die Feldnamen und gegebenenfalls die unterschiedlichen Werte zu sehen. "
                    "Ein Doppelklick springt zum Formularfeld. Nach dem Speichern der PDF kannst du die Vorschau neu einlesen. "
                    "Betroffene Zellen bleiben beim Export leer. Die Spalte „Hinweis“ hält den Grund fest."
                    if template else "„Fehlt“ bedeutet, dass das Feld in dieser Datei anders heißt oder nicht existiert.")
        notice = QLabel(f"{len(rows)} von {len(paths)} PDF-Dateien eingelesen. {guidance} "
                        "Die Werkbank liest den gespeicherten Stand. Passwort- und Signaturfelder werden ausgelassen.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        table = QTableWidget(len(rows), len(header))
        table.setHorizontalHeaderLabels(header)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setColumnWidth(0, 290)
        table.setColumnWidth(1, 200)
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                problem = cell_issues.get((row, column))
                if problem:
                    display, details = problem
                    tooltip = f"{display}\n{details}"
                elif value is None:
                    display = "" if template else "Fehlt"
                    tooltip = "Kein Wert zugeordnet." if template else "Dieses Feld ist in der PDF nicht vorhanden."
                else:
                    display = Path(value).name if column == 0 else value
                    tooltip = value
                item = QTableWidgetItem(display)
                item.setToolTip(tooltip)
                if problem or (value is None and not template):
                    item.setForeground(QColor("#854720"))
                table.setItem(row, column, item)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save = buttons.addButton("Mit Hinweisen speichern" if template and any(row[1] for row in rows)
                                 else "Als Excel speichern", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setEnabled(bool(columns))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def save_export():
            name, _ = QFileDialog.getSaveFileName(self, "Formulardaten speichern", "Formulardaten.xlsx", "Excel (*.xlsx)")
            if name:
                self.save_workbook(name, lambda path: export_linked_sheet(
                    rows, path, "Formulardaten", header, [45, 35, *([24] * len(columns))]))

        if not template:
            buttons.accepted.connect(dialog.accept)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                save_export()
            return

        def open_cell(row, column):
            if column < 2 or column >= len(header):
                return
            _, aliases, _ = mappings[column - 2]
            problem = cell_issues.get((row, column))
            filled = problem is not None and problem[0].startswith("Widerspruch:")
            self.jump_to_form_field(paths[row], aliases, filled, dialog)

        def finish(result):
            self.form_preview = self.form_jump = None
            for button in (back, next_page):
                self.statusBar().removeWidget(button)
                button.deleteLater()
            self.render()
            dialog.deleteLater()
            if result == QDialog.DialogCode.Accepted:
                save_export()

        def reload_preview():
            dialog.reject()
            self.export_form_batch(use_template=True, paths=paths, template=template)

        def accept_current():
            for path, previous in signatures.items():
                try:
                    current = file_signature(path)
                except OSError:
                    current = None
                if current != previous:
                    QMessageBox.information(dialog, "Vorschau aktualisieren",
                                            f"„{path.name}“ hat sich geändert. Bitte zuerst die PDFs neu einlesen.")
                    return
            dialog.accept()

        table.cellDoubleClicked.connect(open_cell)
        buttons.accepted.connect(accept_current)
        reload_button = buttons.addButton("PDFs neu einlesen", QDialogButtonBox.ButtonRole.ActionRole)
        reload_button.clicked.connect(reload_preview)
        back = QPushButton("Zur Formularvorschau")
        back.clicked.connect(lambda: self.export_form_batch(use_template=True))
        next_page = QPushButton("Nächste Feldseite")
        next_page.clicked.connect(lambda: self.next_form_field())
        self.statusBar().addPermanentWidget(back)
        self.statusBar().addPermanentWidget(next_page)
        next_page.hide()
        table.cellDoubleClicked.connect(lambda *_: next_page.setVisible(self.form_jump is not None))
        dialog.finished.connect(finish)
        self.form_preview = dialog
        dialog.show()

    def create_form_entry(self):
        try:
            templates = form_templates(self.favorite_settings)
            if not templates:
                raise ValueError("Lege zuerst unter „Excel-Formularvorlagen“ eine Feldzuordnung an.")
            mapping, ok = QInputDialog.getItem(self, "Excel-Eingabevorlage", "Gespeicherte Feldzuordnung",
                                               sorted(templates, key=str.casefold), editable=False)
            if not ok:
                return
            source, _ = QFileDialog.getOpenFileName(self, "PDF-Formular für die Auswahllisten wählen", "", "PDF (*.pdf)")
            if not source:
                return
            with pdf.open(source) as document:
                if document.needs_pass or not document.is_pdf:
                    raise ValueError("Bitte eine lesbare PDF-Formularvorlage ohne Passwort wählen.")
                columns = form_entry_columns(document, templates[mapping])
            name, _ = QFileDialog.getSaveFileName(self, "Excel-Eingabevorlage speichern",
                                                 "Formulareingabe.xlsx", "Excel (*.xlsx)")
            if name and self.save_workbook(name, lambda path: export_form_entry(path, columns)):
                self.statusBar().showMessage("Excel-Vorlage gespeichert. Orange Spaltenüberschriften markieren Pflichtangaben.", 8500)
        except Exception as exc:
            self.error(exc)

    def fill_form_batch(self):
        try:
            templates = form_templates(self.favorite_settings)
            if not templates:
                raise ValueError("Lege zuerst unter „Excel-Formularvorlagen“ eine Feldzuordnung an.")
            source, _ = QFileDialog.getOpenFileName(self, "PDF-Formularvorlage auswählen", "", "PDF (*.pdf)")
            if not source:
                return
            source = Path(source).resolve()
            workbook, _ = QFileDialog.getOpenFileName(self, "Excel-Daten auswählen", "", "Excel (*.xlsx)")
            if not workbook:
                return
            workbook = Path(workbook).resolve()
            sheets = workbook_sheets(workbook)
            sheet = sheets[0]
            if len(sheets) > 1:
                sheet, ok = QInputDialog.getItem(self, "Excel-Daten", "Tabellenblatt", sheets, editable=False)
                if not ok:
                    return
            mapping, ok = QInputDialog.getItem(self, "Formulare aus Excel füllen", "Gespeicherte Feldzuordnung",
                                               sorted(templates, key=str.casefold), editable=False)
            if not ok:
                return
            stamp = file_signature(workbook)
            rows = read_form_sheet(workbook, sheet)
            if file_signature(workbook) != stamp:
                raise ValueError("Die Excel-Datei wurde beim Einlesen geändert. Bitte erneut versuchen.")
            folder = QFileDialog.getExistingDirectory(self, "Zielordner für die PDF-Kopien", str(source.parent))
            if not folder:
                return
            original = self.form_source_bytes(source)
            with pdf.open(stream=original, filetype="pdf") as document:
                if document.needs_pass or document.is_encrypted or not document.permissions & pdf.PDF_PERM_FORM:
                    raise ValueError("Die PDF-Vorlage ist geschützt oder lässt sich nicht ausfüllen.")
                if document.get_sigflags() > 0:
                    raise ValueError("Bitte eine PDF-Vorlage ohne digitale Signatur verwenden.")
                target = Path(folder).resolve()
                plan = form_row_plan(document, templates[mapping], rows, target, source)
            run = FormRun.begin(target, source, hashlib.sha256(original).hexdigest(), workbook, sheet, templates[mapping])
            self.favorite_settings.setValue("form-export/run-folder", str(target))
            self.run_form_fill(run.path, original, plan, stamp)
        except Exception as exc:
            self.error(exc)

    def form_source_bytes(self, source):
        with source.open("rb") as stream:
            original = stream.read(32 * 1024 * 1024 + 1)
        if len(original) > 32 * 1024 * 1024:
            raise ValueError("Die PDF-Vorlage darf bis zu 32 MiB groß sein.")
        return original

    def open_form_runs(self):
        settings = self.favorite_settings
        paths = [Path(name) for name in settings.value("form-export/runs", [], list)]
        latest = settings.value("form-export/run-folder", "", str)
        if latest:
            paths.extend(Path(latest).glob("*.werkbanklauf"))
        entries = []
        for journal in list(dict.fromkeys(paths))[:200]:
            if not journal.is_absolute() or journal.suffix.lower() != ".werkbanklauf" or not journal.is_file():
                continue
            title = journal.name
            try:
                run = FormRun.load(journal)
                workbook = Path(run.data["workbook"])
                title = workbook.name
                stamp = file_signature(workbook)
                overview = run.data.get("overview")
                total = overview[0] if overview else len(read_form_sheet(workbook, run.data["sheet"]))
                remaining = max(0, total - sum(record["done"] for record in run.records.values()))
                if overview and tuple(overview[1:]) != stamp:
                    status = "Excel geändert · erneut prüfen"
                elif remaining:
                    status = f"{remaining} Zeile(n) offen oder zu prüfen"
                else:
                    continue
            except Exception as exc:
                status = f"Prüfung erforderlich: {exc}"
            entries.append(("Formularlauf", title, status, journal))
        return entries

    def show_open_work(self):
        if self.open_work_dialog is not None:
            self.open_work_dialog.refresh_entries()
            self.open_work_dialog.show()
            self.open_work_dialog.raise_()
            self.open_work_dialog.activateWindow()
            return
        if self.favorite_settings.value("invoice-watch/folder", "", str):
            self.invoice_folder(show=False)
        dialog = QDialog(self)
        self.open_work_dialog = dialog
        dialog.setWindowTitle("Offene Arbeiten")
        dialog.resize(870, 460)
        layout = QVBoxLayout(dialog)
        start = QCheckBox("Beim Start der Werkbank anzeigen")
        start.setChecked(self.favorite_settings.value("work-overview/start", True, bool))
        start.toggled.connect(lambda enabled: self.favorite_settings.setValue("work-overview/start", enabled))
        layout.addWidget(start)
        summary = QLabel()
        layout.addWidget(summary)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Bereich", "Eintrag", "Stand"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 135)
        table.setColumnWidth(2, 285)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        refresh = buttons.addButton("Aktualisieren", QDialogButtonBox.ButtonRole.ActionRole)
        jump = buttons.addButton("Zum Eintrag", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        entries, base = [], []
        timer = QTimer(dialog, interval=1500)

        def update(full=False):
            if full:
                scans = self.scan_drafts()
                base[:] = self.open_form_runs()
                base.extend(("Scan", f"{path.name[:10]} · Scan {len(scans) - index}", "Wartet auf Prüfung oder Ablage", path)
                            for index, path in enumerate(scans))
            watcher = getattr(self, "_invoice_folder_window", None)
            today = date.today()
            due = [("Rechnung", path.name, f"{day:%d.%m.%Y} · "
                    + ("Überfällig" if day < today else "Heute fällig") + (f" · {memo}" if memo else ""), path)
                   for day, path, memo in watcher.due_entries()] if watcher is not None else []
            current = table.currentRow()
            selected = entries[current][3] if 0 <= current < len(entries) else None
            updated = [*base, *due]
            if updated != entries:
                entries[:] = updated
                table.setRowCount(len(entries))
                for row, (kind, name, status, path) in enumerate(entries):
                    for col, value in enumerate((kind, name, status)):
                        item = QTableWidgetItem(value)
                        item.setToolTip(str(path) if col == 1 else value)
                        table.setItem(row, col, item)
                if entries:
                    table.selectRow(next((i for i, entry in enumerate(entries) if entry[3] == selected), 0))
            jump.setEnabled(bool(entries))
            summary.setText(f"{len(entries)} offene Einträge" if entries else "Zurzeit keine offenen Einträge gefunden.")

        def open_item(row, *_):
            if not 0 <= row < len(entries):
                return
            kind, _, _, path = entries[row]
            dialog.hide()
            if kind == "Rechnung":
                self.invoice_folder(selected_path=path)
                return
            try:
                if kind == "Scan":
                    self.scan_inbox(selected_path=path)
                else:
                    self.run_form_fill(path)
            except Exception as exc:
                self.error(exc)
            if self.open_work_dialog is dialog:
                update(True)
                dialog.show()

        def finish(_):
            timer.stop()
            self.open_work_dialog = None
            dialog.deleteLater()

        dialog.refresh_entries = lambda: update(True)
        dialog.finished.connect(finish)
        timer.timeout.connect(lambda: update() if dialog.isVisible() else None)
        refresh.clicked.connect(dialog.refresh_entries)
        jump.clicked.connect(lambda: open_item(table.currentRow()))
        table.cellDoubleClicked.connect(open_item)
        update(True)
        timer.start()
        dialog.show()

    def resume_form_fill(self):
        start = self.favorite_settings.value("form-export/run-folder", "", str)
        name, _ = QFileDialog.getOpenFileName(self, "Gespeicherten Formularlauf öffnen", start,
                                             "Werkbank-Formularlauf (*.werkbanklauf)")
        if name:
            try:
                self.run_form_fill(Path(name).resolve())
            except Exception as exc:
                self.error(exc)

    def run_form_fill(self, journal, original=None, plan=None, stamp=None):
        lock = QLockFile(str(journal) + ".lock")
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise ValueError("Dieser Formularlauf ist bereits in einem anderen Fenster geöffnet.")
        try:
            run = FormRun.load(journal)
            recent = self.favorite_settings.value("form-export/runs", [], list)
            self.favorite_settings.setValue("form-export/runs", list(dict.fromkeys([str(journal), *recent]))[:200])
            self.favorite_settings.sync()
            if original is None:
                source = Path(run.data["source"])
                original = self.form_source_bytes(source)
                if hashlib.sha256(original).hexdigest() != run.data["source_hash"]:
                    raise ValueError("Die PDF-Vorlage wurde geändert. Für eine neue Vorlage starte einen neuen Formularlauf.")
                workbook = Path(run.data["workbook"])
                stamp = file_signature(workbook)
                rows = read_form_sheet(workbook, run.data["sheet"])
                if file_signature(workbook) != stamp:
                    raise ValueError("Die Excel-Datei wurde beim Einlesen geändert. Bitte erneut versuchen.")
                with pdf.open(stream=original, filetype="pdf") as document:
                    plan = form_row_plan(document, run.data["columns"], rows, journal.parent, source)
            completed = run.reconcile(plan)
            run.remember_overview(len(plan), stamp)
            self.review_form_fill(original, plan, run, completed)
        finally:
            lock.unlock()

    def review_form_fill(self, original, plan, run, completed):
        def filled(path):
            document = pdf.open(stream=original, filetype="pdf")
            try:
                fill_form(document, eligible[path])
                missing = missing_required_fields(document)
                if missing:
                    names = ", ".join(name for _, _, name in missing)
                    raise ValueError(f"Pflichtfelder in der PDF bleiben leer: {names}")
                return document
            except Exception:
                document.close()
                raise

        def check(path):
            try:
                with filled(path):
                    return ""
            except (RuntimeError, ValueError) as exc:
                return str(exc)

        eligible = {path: values for line, _, path, values, issues in plan if not issues and line not in completed}
        results = dict(self.inspect_pdfs(list(eligible), "Formularzeilen werden geprüft …", check)) if eligible else {}
        if len(results) != len(eligible):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("PDF-Kopien aus Excel prüfen")
        dialog.resize(850, 480)
        layout = QVBoxLayout(dialog)
        notice = QLabel("Wähle die geprüften Zeilen aus. Mit Doppelklick siehst du die ausgefüllte PDF. "
                        "Die Spalte „Dateiname“ ist optional. Zahlen und Daten werden als gespeicherte Zellwerte "
                        "gelesen. Für eine bestimmte Schreibweise speichere die Zelle als Text. "
                        f"Laufdatei: {run.path.name}")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        table = QTableWidget(len(plan), 4)
        table.setHorizontalHeaderLabels(["Übernehmen", "Excel-Zeile", "PDF-Dateiname", "Prüfergebnis"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setColumnWidth(2, 230)
        table.horizontalHeader().setStretchLastSection(True)
        states = {}
        for index, (line, name, path, _, issues) in enumerate(plan):
            issue = completed.get(line) or " · ".join(issues) or results.get(path, "")
            states[line] = issue or "Offen"
            mark = QTableWidgetItem()
            mark.setCheckState(Qt.CheckState.Unchecked if issue else Qt.CheckState.Checked)
            if issue:
                mark.setFlags(mark.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                eligible.pop(path, None)
            table.setItem(index, 0, mark)
            for column, value in enumerate((str(line), name, issue or "Bereit"), 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(index, column, item)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        preview = buttons.addButton("Ausgefüllte PDF ansehen", QDialogButtonBox.ButtonRole.ActionRole)
        excel = buttons.addButton("Excel-Bericht speichern", QDialogButtonBox.ButtonRole.ActionRole)
        save = buttons.addButton("Auswahl als PDFs speichern", QDialogButtonBox.ButtonRole.AcceptRole)
        layout.addWidget(buttons)

        def selected_paths():
            return [path for index, (_, _, path, _, _) in enumerate(plan)
                    if path in eligible and table.item(index, 0).checkState() == Qt.CheckState.Checked]

        def export_report():
            name, _ = QFileDialog.getSaveFileName(self, "Excel-Bericht speichern", str(run.path.with_suffix(".xlsx")),
                                                 "Excel (*.xlsx)")
            if name:
                rows = [(line, filename, states[line]) for line, filename, _, _, _ in plan]
                self.save_workbook(name, lambda path: export_linked_sheet(
                    rows, path, "Formularlauf", ("Excel-Zeile", "PDF-Dateiname", "Ergebnis"), (16, 38, 65)))

        def refresh(*_):
            row = table.currentRow()
            preview.setEnabled(row >= 0 and plan[row][2] in eligible)
            save.setEnabled(bool(selected_paths()))

        def show_preview(row, *_):
            if row >= 0:
                _, _, path, _, _ = plan[row]
                if path in eligible:
                    try:
                        with filled(path) as document:
                            self.preview_send(document, "Ausgefüllte Kopie zur Kontrolle.", "PDF-Kopie ansehen",
                                              review_only=True)
                    except Exception as exc:
                        self.error(exc)

        table.cellDoubleClicked.connect(show_preview)
        table.currentCellChanged.connect(refresh)
        table.itemChanged.connect(refresh)
        preview.clicked.connect(lambda: show_preview(table.currentRow()))
        excel.clicked.connect(export_report)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        table.setCurrentCell(0, 1)
        refresh()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        lines = {path: line for line, _, path, _, _ in plan if path in eligible}

        def save_new(path):
            try:
                with filled(path) as document, tempfile.TemporaryDirectory(dir=path.parent) as folder:
                    candidate = Path(folder) / path.name
                    save_copy(document, candidate)
                    run.publish(lines[path], path, eligible[path], candidate)
                return ""
            except FileExistsError:
                return "Zieldatei existiert bereits."
            except Exception as exc:
                return str(exc)

        paths = selected_paths()
        saved = self.inspect_pdfs(paths, "Ausgefüllte PDFs werden gespeichert …", save_new)
        for path, error in saved:
            states[lines[path]] = error or "Gespeichert"
        report = QMessageBox(self)
        report.setWindowTitle("Formulare aus Excel")
        report.setText(f"{sum(not error for _, error in saved)} von {len(paths)} ausgewählten PDF-Kopien gespeichert. "
                       f"Der Lauf kann mit {run.path.name} fortgesetzt werden.")
        errors = [f"{path.name}: {error}" for path, error in saved if error]
        if errors:
            report.setDetailedText("\n".join(errors))
        report.addButton(QMessageBox.StandardButton.Close)
        export_button = report.addButton("Excel-Bericht speichern", QMessageBox.ButtonRole.ActionRole)
        report.exec()
        if report.clickedButton() is export_button:
            export_report()

    def flatten_form_copy(self):
        session = self.standards_source()
        if not session:
            return
        try:
            missing = missing_required_fields(session.doc)
            if missing:
                self.check_required_fields(missing)
                return
            copy, filled, empty = fixed_form(session.doc)
            with copy:
                summary = (f"{filled} ausgefüllte und {empty} leere Felder. "
                           "Prüfe die sichtbaren Werte vor dem Speichern. Die Kopie enthält feste Seiteninhalte.")
                if not self.preview_send(copy, summary, "Feste Formularkopie prüfen"):
                    return
                target = self.output_path(" - ausgefüllt", "Feste Formularkopie speichern")
                if target:
                    save_copy(copy, target)
                    self.statusBar().showMessage(f"Feste Formularkopie gespeichert: {target.name}", 7500)
        except Exception as exc:
            self.error(exc)

    def suggest_fields(self):
        session = self.current
        if not session:
            return
        try:
            candidates = [(page.number, rect, kind) for page in session.doc
                          for kind, rect in form_candidates(page)]
            if not candidates:
                QMessageBox.information(self, "Formular-Assistent", "Keine passenden Feldumrisse gefunden.")
                return
            review = self.review_candidates("Formular-Assistent", session.doc, candidates,
                                            "Prüfe Kästchen und Linien in der Vorschau und markiere die gewünschten Felder.")
            if not review or not review[0]:
                return
            session.checkpoint("Formularfelder übernehmen")
            try:
                for index, rect, kind in review[0]:
                    add_suggested_field(session.doc[index], kind, rect)
            except Exception:
                session.undo()
                raise
            self.changed()
        except Exception as exc:
            self.error(exc)

    def create_field(self, page, rect):
        if rect.width < 15 or rect.height < 9:
            raise ValueError("Bitte einen größeren Bereich für das Formularfeld aufziehen.")
        types = {"Textfeld": pdf.PDF_WIDGET_TYPE_TEXT, "Kontrollkästchen": pdf.PDF_WIDGET_TYPE_CHECKBOX,
                 "Dropdown": pdf.PDF_WIDGET_TYPE_COMBOBOX}
        kind, ok = QInputDialog.getItem(self, "Formularfeld erstellen", "Feldtyp", list(types), editable=False)
        if not ok:
            return False
        name, ok = QInputDialog.getText(self, "Formularfeld erstellen", "Eindeutiger Feldname")
        if not ok or not name.strip():
            return False
        session = self.current
        if any(field.field_name == name.strip() for item in session.doc for field in item.widgets()):
            raise ValueError("Dieser Feldname ist bereits vergeben.")
        widget = pdf.Widget()
        widget.rect, widget.field_name, widget.field_type = rect, name.strip(), types[kind]
        widget.field_border_color = (0.20, 0.42, 0.64)
        widget.field_border_width = 1
        if kind == "Dropdown":
            values, ok = QInputDialog.getMultiLineText(self, "Dropdown", "Eine Auswahlmöglichkeit pro Zeile")
            if not ok:
                return False
            options = list(dict.fromkeys(value.strip() for value in values.splitlines() if value.strip()))
            if not options:
                raise ValueError("Bitte mindestens eine Auswahlmöglichkeit eingeben.")
            widget.choice_values = options
            widget.field_value = options[0]
        session.checkpoint("Formularfeld erstellen")
        try:
            page.add_widget(widget)
        except Exception:
            session.undo()
            raise
        return True

    def styled_text(self, page, point, target=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Text formatieren" if target else "Text einfügen")
        form = QFormLayout(dialog)
        value = QPlainTextEdit(target["text"] if target else "")
        value.setMaximumHeight(140)
        form.addRow("Text", value)
        families = {"Helvetica": "helv", "Helvetica fett": "hebo", "Times": "tiro", "Courier": "cour"}
        family = QComboBox()
        family.addItems(families)
        form.addRow("Schrift", family)
        size = QSpinBox()
        size.setRange(5, 96)
        size.setValue(round(target["size"]) if target else 12)
        form.addRow("Größe", size)
        colors = {"Schwarz": (0, 0, 0), "Blau": (0.06, 0.34, 0.66), "Rot": (0.7, 0.08, 0.13)}
        color = QComboBox()
        color.addItems(colors)
        form.addRow("Farbe", color)
        dimensions = []
        custom = None
        if target is None or "paragraph" in target:
            custom = QCheckBox("Position und Textrahmen in Millimetern einstellen")
            form.addRow(custom)
            visible = page.rect * page.derotation_matrix
            box = pdf.Rect(target["bbox"]) if target else pdf.Rect(point, visible.br - pdf.Point(10, 10))
            box = box * page.rotation_matrix
            for label, initial in (("Von links", box.x0), ("Von oben", box.y0),
                                   ("Rahmenbreite", box.width), ("Rahmenhöhe", box.height)):
                field = QDoubleSpinBox()
                field.setRange(0, 2000)
                field.setDecimals(2)
                field.setSuffix(" mm")
                field.setValue(initial / POINTS_PER_MM)
                field.setEnabled(False)
                custom.toggled.connect(field.setEnabled)
                form.addRow(label, field)
                dimensions.append(field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        text = value.toPlainText().strip()
        if not text:
            return False
        try:
            text.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise ValueError("Die gewählte Standardschrift unterstützt einige Zeichen nicht.") from exc
        name, points, ink = families[family.currentText()], size.value(), colors[color.currentText()]
        bounds = None
        if custom is not None and custom.isChecked():
            x, y, width, height = [field.value() * POINTS_PER_MM for field in dimensions]
            box = pdf.Rect(x, y, x + width, y + height)
            if box.is_empty or not page.rect.contains(box):
                raise ValueError("Der Textrahmen muss vollständig auf der Seite liegen.")
            bounds = box * page.derotation_matrix
        session = self.current
        session.checkpoint("Text formatieren")
        try:
            if target and "paragraph" in target:
                replace_paragraph(page, target, text, (name, points, ink), bounds)
            elif target:
                if "\n" in text:
                    raise ValueError("Bei bestehendem Text kann jeweils ein Abschnitt in einer Zeile formatiert werden.")
                replace_span(page, target, text, style=(name, points, ink))
            else:
                area = page.rect * page.derotation_matrix
                box = bounds if bounds is not None else pdf.Rect(point.x, point.y, area.x1 - 10, area.y1 - 10)
                if box.is_empty or page.insert_textbox(box, text, fontsize=points, fontname=name, color=ink) < 0:
                    raise ValueError("Der Text passt an dieser Stelle nicht auf die Seite.")
        except Exception:
            session.undo()
            raise
        return True

    def export_office(self, excel=False, layout=False):
        session = self.current
        if not session:
            return
        if session.doc.needs_pass:
            self.error("Office-Export ist für passwortgeschützte PDFs deaktiviert.")
            return
        extension = ".xlsx" if excel else ".docx"
        label = "Excel" if excel else "Word"
        target, _ = QFileDialog.getSaveFileName(self, f"Nach {label} exportieren",
                                                str(session.path.with_suffix(extension)), f"{label} (*{extension})")
        if not target:
            return
        path = Path(target if target.lower().endswith(extension) else target + extension)
        fd, temp = tempfile.mkstemp(suffix=extension, dir=path.parent)
        os.close(fd)
        try:
            writer = export_excel if excel else export_word_layout if layout else export_word
            writer(session.doc, temp)
            os.replace(temp, path)
            self.statusBar().showMessage(f"Export gespeichert: {path.name}", 6500)
        except Exception as exc:
            self.error(exc)
        finally:
            Path(temp).unlink(missing_ok=True)

    def standards_source(self):
        session = self.current
        if session and session.doc.needs_pass:
            self.error("Für diese Kopie wird eine PDF ohne Passwortschutz benötigt.")
            return None
        return session

    def archive_copy(self):
        session = self.standards_source()
        if not session:
            return
        self.archive_document(session.doc, session.path.with_stem(session.path.stem + " - Archiv"))

    def archive_vector_copy(self):
        session = self.standards_source()
        if not session:
            return
        target = self.output_path(" - Archiv Originaltext", "PDF/A mit Originaltext speichern")
        if not target:
            return
        tools = self.standard_tools()
        if tools is None:
            return
        try:
            with tempfile.TemporaryDirectory(dir=target.parent) as folder:
                candidate = Path(folder) / "Originaltext.pdf"
                profile = Path(os.environ["WINDIR"]) / "System32/spool/drivers/color/sRGB Color Space Profile.icm"
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    export_vector_archive(session.doc, candidate, profile)
                    passed, details = validate_standard(*tools, candidate, "2b")
                finally:
                    QApplication.restoreOverrideCursor()
                if not passed:
                    raise ValueError("veraPDF hat diese PDF/A-2b-Kopie abgelehnt. "
                                     "Für eine Archivkopie als Seitenbild gibt es „PDF/A-Archivkopie“.\n"
                                     + "\n".join(details[:8]))
                with pdf.open(candidate) as check:
                    missing = [str(page.number + 1) for page in check if not page.get_text().strip()]
                    summary = "PDF/A-2b von veraPDF bestätigt. Vorhandener Text und Vektorgrafiken wurden übernommen."
                    if missing:
                        summary += " Ohne Suchtext: Seite " + ", ".join(missing) + "."
                    if not self.preview_send(check, summary, "PDF/A mit Originaltext prüfen"):
                        return
                os.replace(candidate, target)
            self.statusBar().showMessage(f"Geprüfte PDF/A-Kopie gespeichert: {target.name}", 9000)
        except Exception as exc:
            self.error(exc)

    def choose_archive_options(self, suggested, run_ocr):
        options = QDialog(self)
        options.setWindowTitle("Durchsuchbare PDF/A-Archivkopie")
        form = QFormLayout(options)
        description = QLabel("Das Seitenbild und vorhandener Suchtext werden übernommen. Ausfüllbare Felder, Anhänge "
                             "und Signaturen bleiben in der ursprünglichen Datei. veraPDF prüft die Kopie vor dem Speichern.")
        description.setWordWrap(True)
        form.addRow(description)
        recognize = QCheckBox("Seiten ohne Text per OCR erkennen (Deutsch und Englisch)")
        recognize.setChecked(run_ocr)
        form.addRow(recognize)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(options.accept)
        buttons.rejected.connect(options.reject)
        form.addRow(buttons)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        selected, _ = QFileDialog.getSaveFileName(self, "PDF/A-Archivkopie speichern", str(suggested), "PDF-Dateien (*.pdf)")
        if not selected:
            return
        return Path(selected if selected.lower().endswith(".pdf") else selected + ".pdf"), recognize.isChecked()

    def archive_document(self, source, suggested, run_ocr=True, confirmed_target=False):
        choices = (suggested, run_ocr) if confirmed_target else self.choose_archive_options(suggested, run_ocr)
        if not choices:
            return
        target, recognize = choices
        if any(target.resolve() == session.path.resolve() for session in self.sessions):
            self.error("Bitte einen anderen Namen als den einer geöffneten PDF wählen.")
            return
        tools = self.standard_tools()
        if tools is None:
            return
        try:
            with tempfile.TemporaryDirectory(dir=target.parent) as folder:
                candidate = Path(folder) / "Archiv.pdf"
                summary = self.build_archive(source, candidate, recognize, tools)
                if summary is None or not self.preview_send_from_path(candidate, summary):
                    return
                if confirmed_target:
                    candidate.rename(target)
                else:
                    os.replace(candidate, target)
            self.statusBar().showMessage(f"Geprüfte Archivkopie gespeichert: {target.name}", 9000)
            return target
        except Exception as exc:
            self.error(exc)

    def build_archive(self, source, candidate, recognize, tools):
        windows = Path(os.environ["WINDIR"])
        profile = windows / "System32/spool/drivers/color/sRGB Color Space Profile.icm"
        font = windows / "Fonts/arial.ttf"
        with pdf.open(stream=source.tobytes(), filetype="pdf") as copy:
            if recognize:
                for page in copy:
                    if page.rotation and not page.get_text().strip():
                        page.remove_rotation()
                count, _ = self.send_ocr(copy, range(len(copy)), "deu+eng")
                if count is None:
                    return
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                export_archive(copy, candidate, profile, font)
                passed, details = validate_standard(*tools, candidate, "2b")
            finally:
                QApplication.restoreOverrideCursor()
        if not passed:
            raise ValueError("veraPDF hat die Archivkopie abgelehnt.\n" + "\n".join(details[:10]))
        with pdf.open(candidate) as check:
            missing = [str(page.number + 1) for page in check if not page.get_text().strip()]
        return "PDF/A-2b von veraPDF bestätigt." + (" Ohne Suchtext: Seite " + ", ".join(missing) + "." if missing else "")

    def preview_send_from_path(self, path, summary):
        with pdf.open(path) as document:
            return self.preview_send(document, summary, "Geprüfte Archivkopie ansehen")

    def standard_tools(self):
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF Werkbank" / "Prüfwerkzeuge"
        jars = sorted((base / "veraPDF" / "bin").glob("cli-*.jar"))
        java = settings.value("standards/java", "", str) or str(base / "Java" / "bin" / "java.exe")
        jar = settings.value("standards/jar", "", str) or (str(jars[0]) if jars else "")
        if not Path(java).is_file():
            java, _ = QFileDialog.getOpenFileName(self, "Java für veraPDF auswählen", "", "Java (java.exe)")
        if not java:
            return
        if not jar or not Path(jar).is_file():
            jar, _ = QFileDialog.getOpenFileName(self, "veraPDF-CLI aus dem Ordner bin auswählen", "",
                                                 "veraPDF CLI (*.jar)")
        if not jar:
            return
        settings.setValue("standards/java", java)
        settings.setValue("standards/jar", jar)
        return java, jar

    def standard_check(self):
        label, ok = QInputDialog.getItem(self, "PDF-Norm prüfen", "Prüfprofil",
                                          ["PDF/A-2b (Archiv)", "PDF/UA-1 (Barrierefreiheit)"], editable=False)
        if not ok:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Gespeicherte PDF prüfen", "", "PDF-Dateien (*.pdf)")
        if not path:
            return
        tools = self.standard_tools()
        if tools is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            passed, details = validate_standard(*tools, path, "2b" if label.startswith("PDF/A") else "ua1")
        except Exception as exc:
            self.error(exc)
            return
        finally:
            QApplication.restoreOverrideCursor()
        result = QMessageBox(self)
        result.setWindowTitle(label)
        result.setText("veraPDF-Prüfung bestanden." if passed else "veraPDF hat Abweichungen gefunden.")
        if label.startswith("PDF/UA"):
            result.setInformativeText("Lesereihenfolge und Qualität von Bildbeschreibungen zusätzlich von Hand prüfen.")
        if details:
            result.setDetailedText("\n".join(details))
        result.exec()

    def accessible_copy(self):
        session = self.standards_source()
        if not session:
            return
        try:
            pages = [accessibility_blocks(page) for page in session.doc]
            dialog = QDialog(self)
            dialog.setWindowTitle("Lesereihenfolge und Bildbeschreibungen")
            dialog.resize(820, 600)
            layout = QVBoxLayout(dialog)
            info = QLabel("Die Kopie behält das Seitenbild und erhält eine separate Lesestruktur. "
                          "Formulare und Links werden als Seitenbild übernommen. Für Scans zuerst Texterkennung ausführen.")
            info.setWordWrap(True)
            layout.addWidget(info)
            form = QFormLayout()
            title = QLineEdit(session.doc.metadata.get("title") or session.path.stem)
            language = QLineEdit("de-DE")
            form.addRow("Dokumenttitel", title)
            form.addRow("Sprache", language)
            layout.addLayout(form)
            selector = QComboBox()
            selector.addItems([f"Seite {index + 1}" for index in range(len(pages))])
            layout.addWidget(selector)
            listing = QListWidget()
            layout.addWidget(listing, 1)
            controls = QHBoxLayout()
            layout.addLayout(controls)
            roles = {"Absatz": "P", **{f"Überschrift {level}": f"H{level}" for level in range(1, 7)}}

            def redraw(row=0):
                listing.clear()
                for role, _, text in pages[selector.currentIndex()]:
                    label = {"Figure": "Bild", "Artifact": "Dekoratives Bild", "P": "Absatz"}.get(role, role)
                    listing.addItem(label + " · " + (" ".join(text.split())[:140] or "Beschreibung fehlt"))
                listing.setCurrentRow(row)

            def move(offset):
                row = listing.currentRow()
                blocks = pages[selector.currentIndex()]
                if 0 <= row < len(blocks) and 0 <= row + offset < len(blocks):
                    blocks[row], blocks[row + offset] = blocks[row + offset], blocks[row]
                    redraw(row + offset)

            def edit():
                row = listing.currentRow()
                if row < 0:
                    return
                blocks = pages[selector.currentIndex()]
                role, rect, text = blocks[row]
                if role in ("Figure", "Artifact"):
                    choice, accepted = QInputDialog.getItem(dialog, "Bild", "Funktion", ["Bild beschreiben", "Dekorativ"],
                                                              int(role == "Artifact"), editable=False)
                    if not accepted:
                        return
                    if choice == "Dekorativ":
                        blocks[row] = ("Artifact", rect, "")
                    else:
                        value, accepted = QInputDialog.getMultiLineText(dialog, "Bildbeschreibung", "Beschreibung", text)
                        if accepted:
                            blocks[row] = ("Figure", rect, value.strip())
                else:
                    choice, accepted = QInputDialog.getItem(dialog, "Textstruktur", "Rolle", list(roles),
                                                              list(roles.values()).index(role), editable=False)
                    if accepted:
                        blocks[row] = (roles[choice], rect, text)
                redraw(row)

            for label, callback in (("Nach oben", lambda: move(-1)), ("Nach unten", lambda: move(1)), ("Rolle oder Beschreibung", edit)):
                button = QPushButton(label)
                button.clicked.connect(callback)
                controls.addWidget(button)
            listing.itemDoubleClicked.connect(lambda _: edit())
            selector.currentIndexChanged.connect(lambda _: redraw())
            redraw()
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            while dialog.exec() == QDialog.DialogCode.Accepted:
                if not title.text().strip() or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language.text().strip()):
                    self.error("Bitte Titel und gültige Sprachkennung wie de-DE angeben.")
                    continue
                if any(role == "Figure" and not text for blocks in pages for role, _, text in blocks):
                    self.error("Bitte jedem relevanten Bild eine Beschreibung geben oder das Bild als dekorativ markieren.")
                    continue
                target = self.output_path(" - Lesefassung", "PDF mit Lesestruktur speichern")
                if target:
                    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
                        candidate = Path(folder) / "Lesefassung.pdf"
                        included = [[block for block in blocks if block[0] != "Artifact"] for blocks in pages]
                        font = Path(os.environ["WINDIR"]) / "Fonts/arial.ttf"
                        export_accessible(session.doc, candidate, included, title.text().strip(), language.text().strip(), font)
                        os.replace(candidate, target)
                    self.statusBar().showMessage(f"Lesefassung gespeichert: {target.name}. PDF/UA separat prüfen.", 9000)
                return
        except Exception as exc:
            self.error(exc)

    def sign_document(self):
        folder = str(self.current.path.parent) if self.current else ""
        source, _ = QFileDialog.getOpenFileName(self, "Gespeicherte PDF zum Signieren auswählen", folder,
                                                "PDF-Dateien (*.pdf)")
        if not source:
            return
        certificate, _ = QFileDialog.getOpenFileName(self, "Zertifikat mit privatem Schlüssel auswählen", "",
                                                     "PKCS#12 (*.pfx *.p12)")
        if not certificate:
            return
        password, ok = QInputDialog.getText(self, "Zertifikat entsperren", "Passwort", QLineEdit.EchoMode.Password)
        if not ok:
            return
        target, _ = QFileDialog.getSaveFileName(self, "Signierte PDF speichern",
                                                str(Path(source).with_stem(Path(source).stem + " - signiert")),
                                                "PDF-Dateien (*.pdf)")
        if not target:
            return
        target = Path(target if target.lower().endswith(".pdf") else target + ".pdf")
        if target.resolve() == Path(source).resolve() or any(target.resolve() == session.path.resolve()
                                                            for session in self.sessions):
            self.error("Bitte die signierte Kopie unter einem anderen Namen speichern.")
            return
        try:
            sign_pdf(source, target, certificate, password)
            self.statusBar().showMessage(f"Signierte Kopie gespeichert: {target.name}", 8000)
            self.open_path(str(target))
        except Exception as exc:
            self.error(exc)

    def check_signatures(self):
        folder = str(self.current.path.parent) if self.current else ""
        source, _ = QFileDialog.getOpenFileName(self, "Gespeicherte PDF zur Signaturprüfung auswählen", folder,
                                                "PDF-Dateien (*.pdf)")
        if not source:
            return
        trust, _ = QFileDialog.getOpenFileName(self, "Optional: Vertrauenszertifikat wählen oder Abbrechen", "",
                                               "Zertifikate (*.crt *.cer *.pem *.der)")
        try:
            results = verify_pdf(source, trust or None)
        except Exception as exc:
            self.error(exc)
            return
        report = QMessageBox(self)
        report.setWindowTitle("Digitale Signaturen prüfen")
        if not results:
            report.setText("Keine Dokumentensignatur gefunden.")
        else:
            lines = []
            for name, intact, trusted, valid, _ in results:
                lines.append(f"{name}: " + ("Prüfung bestanden" if valid else
                                          "Signatur ist intakt, Vertrauenskette nicht bestätigt" if intact and not trusted else
                                          "Änderungen oder Prüffehler erkannt"))
            report.setText("\n".join(lines))
            report.setDetailedText("\n\n".join(details for *_, details in results))
        report.setInformativeText("Die Prüfung läuft lokal. Sperrlisten werden nicht aus dem Internet geladen.")
        report.exec()


    def protect_copy(self):
        if not self.current:
            return
        password, ok = QInputDialog.getText(self, "PDF mit Passwort speichern", "Passwort", QLineEdit.EchoMode.Password)
        if not ok or not password:
            return
        confirm, ok = QInputDialog.getText(self, "Passwort wiederholen", "Passwort", QLineEdit.EchoMode.Password)
        if not ok:
            return
        if confirm != password or len(password.encode("utf-8")) > 40:
            self.error("Die Passwörter müssen übereinstimmen und dürfen höchstens 40 UTF-8-Bytes lang sein.")
            return
        target = self.output_path(" - geschützt", "Passwortgeschützte Kopie speichern")
        if target:
            try:
                save_copy(self.current.doc, target, encryption=pdf.PDF_ENCRYPT_AES_256,
                          owner_pw=password, user_pw=password, password=password)
                self.statusBar().showMessage(f"Passwortgeschützte Kopie gespeichert: {target.name}", 6500)
            except Exception as exc:
                self.error(exc)

    def batch_process(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "PDFs für Stapelverarbeitung auswählen", "", "PDF-Dateien (*.pdf)")
        if not paths:
            return
        actions = ["Scans verkleinern", "Alle Seiten nach rechts drehen", "OCR für alle Scan-Seiten"]
        action, ok = QInputDialog.getItem(self, "Stapelverarbeitung", "Aufgabe", actions, editable=False)
        if not ok:
            return
        self.run_batch(paths, (("compress",), ("rotate",), ("ocr",))[actions.index(action)])

    def batch_profiles(self):
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        profiles = self.read_batch_profiles(settings)
        dialog = QDialog(self)
        dialog.setWindowTitle("Gespeicherte Abläufe")
        form = QFormLayout(dialog)
        existing = QComboBox()
        existing.addItem("Neuer Ablauf")
        existing.addItems(sorted(profiles))
        form.addRow("Vorlage", existing)
        name = QLineEdit()
        form.addRow("Name", name)
        steps = {key: QCheckBox(label) for key, label in
                 (("ocr", "Texterkennung (Deutsch und Englisch)"),
                  ("rotate", "Alle Seiten nach rechts drehen"), ("compress", "Scans verkleinern"))}
        for check in steps.values():
            form.addRow(check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save = buttons.addButton("Vorlage speichern", QDialogButtonBox.ButtonRole.ActionRole)
        run = buttons.addButton("Dateien auswählen und starten", QDialogButtonBox.ButtonRole.AcceptRole)
        remove = buttons.addButton("Vorlage entfernen", QDialogButtonBox.ButtonRole.ActionRole)
        form.addRow(buttons)

        def select(index):
            profile = existing.itemText(index) if index > 0 else ""
            name.setText(profile)
            for key, check in steps.items():
                check.setChecked(key in profiles.get(profile, []))
            remove.setEnabled(index > 0)

        def persist():
            settings.setValue("batch/profiles", json.dumps(profiles, ensure_ascii=False))
            settings.sync()
            self.favorite_settings.sync()
            self.profile_commands()
            self.favorite_keys[:] = [key for key in self.favorite_keys if key in self.favorite_commands]
            self.favorite_settings.setValue("toolbar/favorites", self.favorite_keys)
            self.populate_favorite_menu()
            self.refresh_favorites()

        def save_profile():
            title = name.text().strip()
            selected = [key for key, check in steps.items() if check.isChecked()]
            if not title or not selected or title == existing.itemText(0):
                self.error("Bitte einen eigenen Namen und mindestens einen Schritt wählen.")
                return
            if title in profiles and title != existing.currentText() and QMessageBox.question(
                    dialog, "Vorlage ersetzen?", f"Die Vorlage „{title}“ ersetzen?") != QMessageBox.StandardButton.Yes:
                return
            if title not in profiles:
                existing.addItem(title)
            profiles[title] = selected
            persist()
            existing.setCurrentText(title)

        def remove_profile():
            index = existing.currentIndex()
            if index > 0:
                del profiles[existing.currentText()]
                existing.removeItem(index)
                persist()

        existing.currentIndexChanged.connect(select)
        save.clicked.connect(save_profile)
        remove.clicked.connect(remove_profile)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        existing.setCurrentIndex(1 if profiles else 0)
        select(existing.currentIndex())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = [key for key, check in steps.items() if check.isChecked()]
        if not selected:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "PDFs für den Ablauf auswählen", "", "PDF-Dateien (*.pdf)")
        if paths:
            self.run_batch(paths, selected)

    def run_batch(self, paths, steps):
        folder = QFileDialog.getExistingDirectory(self, "Zielordner für die Kopien auswählen")
        if not folder:
            return
        plan = self.batch_preview(list(dict.fromkeys(Path(path).resolve() for path in paths)), Path(folder), steps)
        if not plan:
            return
        progress = QProgressDialog("PDFs werden bearbeitet …", "Abbrechen", 0, len(plan), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        count, failures = 0, []
        try:
            for index, (path, target) in enumerate(plan):
                progress.setValue(index)
                QApplication.processEvents()
                if progress.wasCanceled():
                    break
                if target.exists():
                    failures.append(f"{path.name}: Zieldatei existiert bereits.")
                    continue
                try:
                    with pdf.open(path) as doc:
                        if not doc.is_pdf or doc.needs_pass or not len(doc):
                            raise ValueError("Keine lesbare ungeschützte PDF.")
                        for page in doc:
                            QApplication.processEvents()
                            if progress.wasCanceled():
                                break
                            if "ocr" in steps and not page.get_text().strip():
                                try:
                                    if page.rotation:
                                        page.remove_rotation()
                                    recognize_page(page, "deu+eng", Path(__file__).with_name("tessdata"))
                                except (ValueError, RuntimeError) as exc:
                                    failures.append(f"{path.name}, Seite {page.number + 1}: {exc}")
                            if "rotate" in steps:
                                page.set_rotation((page.rotation + 90) % 360)
                        if progress.wasCanceled():
                            break
                        with tempfile.TemporaryDirectory(dir=folder) as temp:
                            candidate = Path(temp) / "Kopie.pdf"
                            if "compress" in steps:
                                compressed_copy(doc, candidate, "Kleine Datei")
                            else:
                                save_copy(doc, candidate)
                            candidate.rename(target)
                    count += 1
                except Exception as exc:
                    failures.append(f"{path.name}: {exc}")
        finally:
            progress.close()
        report = QMessageBox(self)
        report.setWindowTitle("Stapelverarbeitung")
        report.setText(f"Gespeicherte Kopien: {count}")
        if failures:
            report.setDetailedText("\n".join(failures))
        report.exec()


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
        if session.doc.needs_pass:
            self.error("Diese Funktion ist für passwortgeschützte PDFs deaktiviert. Normale Kopien behalten ihren Schutz.")
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

    def preview_send(self, doc, summary, title="Versandkopie prüfen", review_only=False):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
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
        if review_only:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("Kopie speichern")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(660, 720)
        show_page(0)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def dispatch_check(self):
        session = self.current
        if not session:
            return
        if session.doc.needs_pass:
            self.error("Der Versandcheck benötigt eine PDF ohne Passwortschutz.")
            return
        text, ok = QInputDialog.getMultiLineText(self, "Versandcheck", "Ein Suchbegriff pro Zeile. Leer lassen, um nur versteckte Inhalte zu entfernen.")
        if not ok:
            return
        terms = list(dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()))
        try:
            with pdf.open(stream=session.doc.tobytes(), filetype="pdf") as copy:
                candidates = list(redaction_candidates(copy, terms))
                review = self.review_candidates("Versandcheck", copy, candidates,
                                                f"{len(candidates)} Textbereiche gefunden. Markiere die Bereiche, die dauerhaft geschwärzt werden sollen.",
                                                ("Dokumenteigenschaften und XML-Metadaten entfernen",
                                                 "Eingebettete Dateien und Dateianhänge entfernen"))
                if not review or not (review[0] or any(review[1])):
                    return
                target = self.output_path(" - geprüft", "Geprüfte Versandkopie speichern")
                if target is None:
                    return
                by_page = {}
                for index, rect, _ in review[0]:
                    by_page.setdefault(index, []).append(rect)
                for index, rectangles in by_page.items():
                    page = copy[index]
                    check_pending_redactions(page)
                    for rect in rectangles:
                        page.add_redact_annot(rect, fill=(0, 0, 0), cross_out=False)
                    page.apply_redactions(images=pdf.PDF_REDACT_IMAGE_PIXELS,
                                          graphics=pdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)
                clean_hidden(copy, *review[1])
                with tempfile.TemporaryDirectory(dir=target.parent) as folder:
                    candidate = Path(folder) / "geprüft.pdf"
                    save_copy(copy, candidate)
                    with pdf.open(candidate) as saved:
                        findings = redaction_findings(saved, terms)
                        image_pages = [str(page.number + 1) for page in saved if page.get_image_info()]
                    os.replace(candidate, target)
            report = QMessageBox(self)
            report.setWindowTitle("Versandcheck abgeschlossen")
            report.setText(f"{target.name}\n{len(review[0])} Bereiche geschwärzt. "
                           f"{len(findings)} von {len(terms)} Suchbegriffen noch in prüfbaren Inhalten gefunden.")
            report.setInformativeText("Bildpixel, Vektorschrift ohne Textebene, Skripte und Inhalte verbleibender Anlagen wurden "
                                      "nicht durchsucht. Die Prüfung ersetzt die Sichtprüfung der Versandkopie nicht.")
            details = [f"{term}\n" + "\n".join(locations) for term, locations in findings.items()]
            if image_pages:
                details.append("Seiten mit Bildern zur Sichtprüfung: " + ", ".join(image_pages))
            if details:
                report.setDetailedText("\n\n".join(details))
            report.exec()
        except Exception as exc:
            self.error(exc)

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
        if session.doc.needs_pass:
            self.error("Diese Funktion ist für passwortgeschützte PDFs deaktiviert. Normale Kopien behalten ihren Schutz.")
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
        if session.doc.needs_pass:
            self.error("Diese Funktion ist für passwortgeschützte PDFs deaktiviert. Normale Kopien behalten ihren Schutz.")
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

    def review_ocr(self, doc, initial):
        dialog = QDialog(self)
        dialog.setWindowTitle("OCR-Korrektur")
        dialog.resize(1000, 690)
        layout = QVBoxLayout(dialog)
        notice = QLabel("Wähle eine Textzeile, um ihre Position im Scan zu sehen. "
                        "Die Korrektur ändert nur die unsichtbare Textebene.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        selector = QSpinBox()
        selector.setRange(1, len(doc))
        selector.setValue(initial + 1)
        selector.setPrefix("Seite ")
        layout.addWidget(selector)
        split = QSplitter()
        layout.addWidget(split, 1)
        area = QScrollArea()
        area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image = QLabel()
        area.setWidget(image)
        split.addWidget(area)
        rows = QTableWidget(0, 1)
        rows.setHorizontalHeaderLabels(["Erkannter Text · per Doppelklick bearbeiten"])
        rows.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        split.addWidget(rows)
        split.setSizes([490, 500])
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Korrekturen übernehmen")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        edits, lines = {}, []
        current = initial
        base = None
        scale = 1.0

        def remember():
            if not lines:
                return
            values = [rows.item(index, 0).text() for index in range(len(lines))]
            if values != [line[3] for line in lines]:
                edits[current] = values
            else:
                edits.pop(current, None)

        def highlight(index, *_):
            if base is None:
                return
            shown = QPixmap(base)
            if 0 <= index < len(lines):
                rect = lines[index][0] * doc[current].rotation_matrix
                painter = QPainter(shown)
                painter.fillRect(QRect(round(rect.x0 * scale), round(rect.y0 * scale), max(2, round(rect.width * scale)),
                                       max(2, round(rect.height * scale))), QColor(255, 190, 32, 125))
                painter.end()
            image.setPixmap(shown)
            image.resize(shown.size())

        def load(number):
            nonlocal lines, current, base, scale
            remember()
            current = number - 1
            page = doc[current]
            lines = ocr_lines(page)
            scale = min(470 / page.rect.width, 570 / page.rect.height)
            base = pixmap(page, scale)
            values = edits.get(current, [line[3] for line in lines])
            rows.blockSignals(True)
            rows.setRowCount(len(values))
            for index, value in enumerate(values):
                rows.setItem(index, 0, QTableWidgetItem(value))
            rows.setCurrentCell(0, 0)
            rows.blockSignals(False)
            notice.setText("Wähle eine Zeile zum Markieren. Ein Doppelklick ändert nur die unsichtbare Textebene."
                           if lines else "Diese Seite hat keine bearbeitbare Scan-Textebene. Führe bei Bedarf zuerst OCR aus.")
            highlight(0)

        selector.valueChanged.connect(load)
        rows.currentCellChanged.connect(highlight)
        load(initial + 1)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            remember()
            return edits
        return {}

    def correct_ocr(self):
        session = self.current
        if not session:
            return
        try:
            edits = self.review_ocr(session.doc, session.page)
            if not edits:
                return
            session.checkpoint("OCR-Text korrigieren")
            try:
                for page, values in edits.items():
                    replace_ocr_layer(session.doc[page], values)
            except Exception:
                session.undo()
                raise
            self.changed()
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
            session.checkpoint("Texterkennung ausführen")
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


    def text_at(self, page, point):
        target = next((span for direction, span in text_spans(page)
                       if direction == (1.0, 0.0) and span.get("alpha", 255)
                       and pdf.Rect(span["bbox"]).contains(point)), None)
        if not target:
            raise ValueError("Hier wurde kein horizontaler Text gefunden.")
        return target

    def correct_text(self, page, point):
        target = self.text_at(page, point)
        value, ok = QInputDialog.getText(self, "Text korrigieren",
                                        "Neuer Text (wird in einer Standardschrift gesetzt)",
                                        text=target["text"])
        if not ok or value == target["text"]:
            return False
        session = self.current
        session.checkpoint("Text korrigieren")
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
        self.mark_changed(self.current)
        self.write_recovery()
        if refresh:
            self.refresh_thumbs()
        self.render()

    def mark_changed(self, session):
        if session is self.current:
            self.clear_measure_draft()
        self.clear_page_cache(session)
        session.redo_stack.clear()
        session.changed = True
        session.recovery_dirty = True
        self.tabs.setTabText(self.sessions.index(session), session.path.name + " *")

    def transfer_page(self, source, page, destination, index, move=False):
        if (source is destination or source.doc.needs_pass or destination.doc.needs_pass
                or not 0 <= page < len(source.doc) or not 0 <= index <= len(destination.doc)):
            raise ValueError("Bitte zwei verschiedene ungeschützte PDFs und eine gültige Seite auswählen.")
        if move and len(source.doc) == 1:
            raise ValueError("Die letzte Seite eines Dokuments kann nur kopiert werden.")
        destination.checkpoint("Seite verschieben" if move else "Seite kopieren")
        source_checkpoint = None
        try:
            if move:
                source.checkpoint("Seite verschieben")
                source_checkpoint = source.history[-1]
            destination.doc.insert_pdf(source.doc, from_page=page, to_page=page, start_at=index)
            if move:
                source.doc.delete_page(page)
                source.page = min(source.page - int(source.page > page), len(source.doc) - 1)
        except Exception:
            if source_checkpoint is not None:
                source.undo()
            destination.undo()
            raise
        destination.page = index
        self.mark_changed(destination)
        if move:
            self.mark_changed(source)
            self.transfer_history.append((source, source_checkpoint, destination, destination.history[-1]))
        self.transfer_history[:] = [record for record in self.transfer_history if any(
            session in self.sessions and any(entry is marker for entry in session.history)
            for session, marker in ((record[0], record[1]), (record[2], record[3])))]
        self.write_recovery()
        self.refresh_thumbs()
        self.render()

    def page_transfer_dialog(self):
        while len(self.sessions) < 2:
            before = len(self.sessions)
            self.open_files()
            if len(self.sessions) == before:
                return
        dialog = QDialog(self)
        dialog.setWindowTitle("Seiten zwischen PDFs")
        dialog.resize(750, 550)
        layout = QVBoxLayout(dialog)
        info = QLabel("Ziehe eine Seite zwischen die Miniaturen des anderen Dokuments. "
                      "Links zu Seiten, die nicht mit übertragen werden, können entfallen.")
        info.setWordWrap(True)
        layout.addWidget(info)
        move = QCheckBox("Seite verschieben")
        layout.addWidget(move)
        columns = QHBoxLayout()
        layout.addLayout(columns, 1)
        selectors, lists = [], []

        def transfer(from_list, row, to_list, index):
            a, b = (selectors[lists.index(item)].currentIndex() for item in (from_list, to_list))
            if move.isChecked() and len(self.sessions[a].doc) == 1:
                self.error("Die letzte Seite eines Dokuments kann nur kopiert werden.")
                return False
            try:
                self.transfer_page(self.sessions[a], row, self.sessions[b], index, move.isChecked())
                refresh(lists.index(from_list))
                refresh(lists.index(to_list))
                to_list.setCurrentRow(index)
                return True
            except Exception as exc:
                self.error(exc)
                return False

        def refresh(side):
            listing = lists[side]
            doc = self.sessions[selectors[side].currentIndex()].doc
            listing.clear()
            for page in doc:
                item = QListWidgetItem(f"Seite {page.number + 1}")
                if page.number < 80:
                    scale = 78 / max(page.rect.width, page.rect.height)
                    item.setIcon(QIcon(pixmap(page, scale)))
                listing.addItem(item)
            listing.setCurrentRow(0)

        for side in range(2):
            column = QVBoxLayout()
            columns.addLayout(column, 1)
            selector = QComboBox()
            selector.addItems([session.path.name for session in self.sessions])
            selector.setCurrentIndex(side)
            selector.currentIndexChanged.connect(lambda _, index=side: refresh(index))
            selectors.append(selector)
            column.addWidget(selector)
            listing = TransferList(transfer)
            listing.setIconSize(QSize(78, 78))
            lists.append(listing)
            column.addWidget(listing, 1)
            refresh(side)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        for label, a, b in (("Auswahl nach rechts", 0, 1), ("Auswahl nach links", 1, 0)):
            button = buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            button.clicked.connect(lambda _, a=a, b=b: transfer(lists[a], lists[a].currentRow(), lists[b],
                                                                  max(0, lists[b].currentRow())))
        layout.addWidget(buttons)
        dialog.exec()

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
            session.checkpoint({"rotate": "Seite drehen", "move": "Seite umsortieren", "delete": "Seite löschen"}[operation])
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
                self.current.checkpoint("PDF anhängen")
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

    def search_folder(self):
        settings = QSettings("PDF Werkbank", "PDF Werkbank")
        folder = QFileDialog.getExistingDirectory(self, "PDF-Ordner durchsuchen",
                                                   settings.value("search/folder", "", str))
        if not folder:
            return
        options = self.folder_search_options(Path(folder))
        if options is None:
            return
        query, recursive, database, filters, fuzzy = options
        settings.setValue("search/folder", folder)
        progress = QProgressDialog("PDF-Dateien werden durchsucht …", "Abbrechen", 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        def tick(path):
            progress.setLabelText(path.name)
            QApplication.processEvents()
            return not progress.wasCanceled()

        try:
            if database:
                hits, skipped, scanned, state = indexed_matches(database, Path(folder), query, recursive, tick,
                                                                  filters=filters, fuzzy=fuzzy)
            else:
                hits, skipped, scanned, state = folder_matches(Path(folder), query, recursive, tick,
                                                                 filters=filters, fuzzy=fuzzy)
        except Exception as exc:
            self.error(exc)
            return
        finally:
            progress.close()
        dialog = QDialog(self)
        dialog.setWindowTitle("Suchtreffer im Ordner")
        dialog.resize(820, 460)
        layout = QVBoxLayout(dialog)
        action = "PDFs im Index neu eingelesen" if database else "PDFs eingelesen"
        stats = f"{len(hits)} Trefferseiten · {scanned} {action} · {skipped} übersprungen"
        if state:
            stats += " · " + state
        layout.addWidget(QLabel(stats))
        layout.addWidget(QLabel("Durchsucht wurde vorhandener Text. Bildseiten ohne OCR liefern keine Treffer."))
        rows = QListWidget()
        rows.setWordWrap(True)
        layout.addWidget(rows)
        for path, page, excerpt, *approximation in hits:
            possible = f"Möglicher Treffer ({approximation[0]}) · " if approximation and approximation[0] else ""
            item = QListWidgetItem(f"{possible}{path.relative_to(folder)} · Seite {page + 1}\n{excerpt}")
            item.setToolTip(excerpt)
            rows.addItem(item)
        rows.setCurrentRow(0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        jump = buttons.addButton("Fundstelle öffnen", QDialogButtonBox.ButtonRole.AcceptRole)
        jump.setEnabled(bool(hits))
        rows.itemDoubleClicked.connect(lambda _: dialog.accept())
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or rows.currentRow() < 0:
            return
        path, page, _, *approximation = hits[rows.currentRow()]
        existing = next((index for index, session in enumerate(self.sessions)
                         if session.path.resolve() == path.resolve()), None)
        if existing is not None:
            self.tabs.setCurrentIndex(existing)
        elif not self.open_path(str(path)):
            return
        if not 0 <= page < len(self.current.doc):
            self.statusBar().showMessage("Die PDF wurde seit der Suche geändert. Bitte erneut suchen.", 8000)
            return
        self.search_text = approximation[0] if approximation and approximation[0] else query.strip()
        self.thumbs.setCurrentRow(page)
        self.go_page(page)
        target = self.current.doc[page]
        boxes = search_rectangles(target, self.search_text)
        if boxes:
            box = boxes[0] * target.rotation_matrix
            self.scroll.ensureVisible(round(box.x0 * self.scale), round(box.y0 * self.scale), 25, 65)
        else:
            self.statusBar().showMessage("Die Fundstelle ist im aktuellen Dokumentstand nicht mehr auffindbar.", 8000)

    def folder_search_options(self, folder):
        dialog = QDialog(self)
        dialog.setWindowTitle("Ordnersuche")
        form = QFormLayout(dialog)
        query = QLineEdit()
        name = QLineEdit()
        directory = QLineEdit()
        since = QLineEdit()
        since.setPlaceholderText("TT.MM.JJJJ")
        recursive = QCheckBox("Unterordner einbeziehen")
        recursive.setChecked(True)
        indexed = QCheckBox("Lokalen Suchindex verwenden")
        indexed.setChecked(True)
        fuzzy = QCheckBox("Typische OCR-Verwechslungen in Nummern berücksichtigen")
        fuzzy.setChecked(True)
        notice = QLabel("Der Index speichert auslesbaren PDF-Text lokal auf diesem PC. "
                        "Für vertrauliche Ordner kannst du ohne Index suchen.")
        notice.setWordWrap(True)
        form.addRow("Ordner", QLabel(str(folder)))
        for label, field in (("Suchtext", query), ("Dateiname enthält", name),
                             ("Unterordner enthält", directory), ("Geändert seit", since)):
            form.addRow(label, field)
        for widget in (recursive, indexed, fuzzy, notice):
            form.addRow(widget)
        database = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF Werkbank" / "Suchindex" / "ordner-v1.sqlite"
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        reset = buttons.addButton("Index für diesen Ordner löschen", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Suchen")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def clear():
            try:
                clear_index(database, folder)
                indexed.setChecked(False)
                notice.setText("Der gespeicherte Index dieses Ordners wurde gelöscht. Die PDF-Dateien bleiben erhalten.")
            except Exception as exc:
                self.error(exc)

        reset.clicked.connect(clear)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                if not query.text().strip():
                    raise ValueError("Bitte einen Suchtext eingeben.")
                cutoff = datetime.strptime(since.text().strip(), "%d.%m.%Y").timestamp() if since.text().strip() else 0
                return (query.text().strip(), recursive.isChecked(), database if indexed.isChecked() else None,
                        (name.text().strip(), directory.text().strip(), round(cutoff * 1_000_000_000)), fuzzy.isChecked())
            except ValueError:
                notice.setText("Bitte einen Suchtext und ein Datum im Format TT.MM.JJJJ eingeben oder das Datum leer lassen.")

    def undo(self):
        self.change_history(False)

    def redo(self):
        self.change_history(True)

    def change_history(self, forward):
        if self.current:
            current = self.current
            sessions = [current]
            records, opposite = (self.transfer_redo, self.transfer_history) if forward else (
                self.transfer_history, self.transfer_redo)
            stack_name = "redo_stack" if forward else "history"
            other_name = "history" if forward else "redo_stack"
            stack = getattr(current, stack_name)
            if not stack:
                return
            label = stack[-1][3]
            matched = None
            for record in reversed(records):
                source, first, destination, second = record
                if (source is current and stack[-1] is first or destination is current and stack[-1] is second):
                    if any(session not in self.sessions or not getattr(session, stack_name)
                           or getattr(session, stack_name)[-1] is not entry
                           for session, entry in ((source, first), (destination, second))):
                        self.error("Für diese Änderung müssen beide PDFs geöffnet sein und dieselbe Verschiebung "
                                   "als nächsten Schritt im Verlauf haben.")
                        return
                    sessions = [source, destination]
                    matched = record
                    break
            for session in sessions:
                session.restore_edit(forward)
                self.clear_measure_draft()
                self.clear_page_cache(session)
                marker = " *" if session.changed else ""
                self.tabs.setTabText(self.sessions.index(session), session.path.name + marker)
            if matched is not None:
                records.remove(matched)
                a, b = sessions
                opposite.append((a, getattr(a, other_name)[-1], b, getattr(b, other_name)[-1]))
            self.write_recovery()
            self.refresh_thumbs()
            self.render()
            self.statusBar().showMessage(("Wiederholt: " if forward else "Rückgängig: ") + label, 4500)

    def edit_history(self):
        if not self.current:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Änderungsverlauf")
        layout = QVBoxLayout(dialog)
        rows = QListWidget()
        layout.addWidget(rows)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        back = buttons.addButton("Rückgängig", QDialogButtonBox.ButtonRole.ActionRole)
        forward = buttons.addButton("Wiederholen", QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)

        def refresh():
            rows.clear()
            rows.addItems(["Rückgängig · " + entry[3] for entry in reversed(self.current.history)]
                          + ["Wiederholen · " + entry[3] for entry in reversed(self.current.redo_stack)])
            back.setEnabled(bool(self.current.history))
            forward.setEnabled(bool(self.current.redo_stack))

        back.clicked.connect(lambda: (self.undo(), refresh()))
        forward.clicked.connect(lambda: (self.redo(), refresh()))
        buttons.rejected.connect(dialog.reject)
        refresh()
        dialog.resize(440, 330)
        dialog.exec()

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
            save_copy(session.doc, target, password=session.password, encryption=pdf.PDF_ENCRYPT_KEEP)
        except Exception as exc:
            self.error(exc)
            return False
        self.statusBar().showMessage(f"Kopie gespeichert: {Path(target).name}", 6500)
        session.changed = False
        session.recovery_path = Path(target).resolve()
        session.plan_source_stamp = file_signature(session.recovery_path)
        session.plan_saved_token = None
        self.write_recovery()
        for entry in session.history + session.redo_stack:
            entry[2] = True
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
            session = self.sessions.pop(index)
            self.clear_page_cache(session)
            session.doc.close()
            self.tabs.removeTab(index)
            self.switch_doc(self.tabs.currentIndex())
            self.write_recovery()

    def closeEvent(self, event):
        for index in range(len(self.sessions)):
            if not self.confirm_close(index):
                event.ignore()
                return
        if self.recovery:
            try:
                self.recovery.clear(self.recovery.folder, self.recovery.lock)
            except OSError as exc:
                self.error(exc)
            self.recovery = None
        watch = getattr(self, "_invoice_folder_window", None)
        if watch is not None:
            watch.done(QDialog.DialogCode.Rejected)
        if self.form_preview is not None:
            self.form_preview.reject()
        if self.open_work_dialog is not None:
            self.open_work_dialog.reject()
        event.accept()

    def booklet_dialog(self):
        session = self.current
        if not session:
            return
        duplex = QPrinter.DuplexMode.DuplexShortSide
        printers = [device for device in QPrinterInfo.availablePrinters()
                    if duplex in device.supportedDuplexModes()]
        printers.sort(key=lambda device: ("samsung" not in device.printerName().casefold(),
                                          device.printerName().casefold()))
        options = QDialog(self)
        options.setWindowTitle("Broschürendruck")
        form = QFormLayout(options)
        selection = QLineEdit(f"1-{len(session.doc)}")
        form.addRow("PDF-Seiten", selection)
        devices = QComboBox()
        devices.addItems([device.printerName() for device in printers] + ["Nur Druck-PDF speichern"])
        form.addRow("Ausgabe", devices)
        guide = QLabel("A4 quer, mittig falten. Beim beidseitigen Druck an der kurzen Kante wenden.")
        guide.setWordWrap(True)
        form.addRow(guide)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Faltvorschau")
        buttons.accepted.connect(options.accept)
        buttons.rejected.connect(options.reject)
        form.addRow(buttons)
        while options.exec() == QDialog.DialogCode.Accepted:
            try:
                indices = page_selection(selection.text(), len(session.doc))
                sheets = booklet_sheets(indices)
                printer = None
                margin = 28
                if devices.currentIndex() < len(printers):
                    device = printers[devices.currentIndex()]
                    printer = QPrinter(device, QPrinter.PrinterMode.HighResolution)
                    printer.setDocName(session.path.stem + " – Broschüre")
                    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
                    printer.setPageOrientation(QPageLayout.Orientation.Landscape)
                    printer.setPageOrder(QPrinter.PageOrder.FirstPageFirst)
                    printer.setDuplex(duplex)
                    printer.setCopyCount(1)
                    layout = printer.pageLayout()
                    if (layout.orientation() != QPageLayout.Orientation.Landscape
                            or layout.pageSize().id() != QPageSize.PageSizeId.A4 or printer.duplex() != duplex):
                        raise ValueError("Der Drucker hat A4 quer oder Duplex an der kurzen Kante nicht übernommen.")
                    layout.setUnits(QPageLayout.Unit.Point)
                    minimum = layout.minimumMargins()
                    margin = max(margin, minimum.left(), minimum.right(), minimum.top(), minimum.bottom())
                    printer.setFullPage(True)
                with impose_booklet(session.doc, sheets, margin) as booklet:
                    self.booklet_preview(booklet, sheets, printer)
                return
            except Exception as exc:
                self.error(exc)

    def booklet_preview(self, booklet, sheets, printer):
        dialog = QDialog(self)
        dialog.setWindowTitle("Faltvorschau")
        dialog.resize(930, 460)
        layout = QVBoxLayout(dialog)
        blanks = sum(index is None for sheet in sheets for side in sheet for index in side)
        layout.addWidget(QLabel(f"A4 quer · {len(sheets)} Blatt · {blanks} leere Heftseiten · Duplex an der kurzen Kante"))
        preview = QHBoxLayout()
        layout.addLayout(preview, 1)
        panels = []
        for title in ("Vorderseite", "Rückseite"):
            column = QVBoxLayout()
            preview.addLayout(column, 1)
            heading = QLabel(title)
            column.addWidget(heading, alignment=Qt.AlignmentFlag.AlignHCenter)
            picture = QLabel()
            column.addWidget(picture, alignment=Qt.AlignmentFlag.AlignHCenter)
            panels.append((heading, picture))
        row = QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(QLabel("Blatt"))
        sheet_number = QSpinBox()
        sheet_number.setRange(1, len(sheets))
        row.addWidget(sheet_number)
        row.addWidget(QLabel(f"von {len(sheets)}"))
        row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        save = buttons.addButton("Druck-PDF speichern", QDialogButtonBox.ButtonRole.ActionRole)
        print_button = buttons.addButton("Jetzt drucken", QDialogButtonBox.ButtonRole.AcceptRole)
        print_button.setEnabled(printer is not None)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def show(value):
            for side, ((heading, picture), pair) in enumerate(zip(panels, sheets[value - 1])):
                pages = ["Leer" if index is None else f"Seite {index + 1}" for index in pair]
                label = "Vorderseite" if side == 0 else "Rückseite"
                heading.setText(f"{label} · {pages[0]} | {pages[1]}")
                page = booklet[(value - 1) * 2 + side]
                image = pixmap(page, 410 / page.rect.width)
                painter = QPainter(image)
                painter.setPen(QPen(QColor("#3677a9"), 1, Qt.PenStyle.DashLine))
                middle = image.width() // 2
                painter.drawLine(middle, 0, middle, image.height())
                painter.end()
                picture.setPixmap(image)

        def save_pdf():
            target = self.output_path(" - Broschüre", "Druck-PDF speichern")
            if target:
                try:
                    save_copy(booklet, target)
                    self.statusBar().showMessage(f"Druck-PDF gespeichert: {target.name}", 6500)
                except Exception as exc:
                    self.error(exc)

        sheet_number.valueChanged.connect(show)
        save.clicked.connect(save_pdf)
        show(1)
        if dialog.exec() == QDialog.DialogCode.Accepted and printer is not None:
            print_pages(booklet, printer, range(len(booklet)))
            self.statusBar().showMessage("Broschüren-Druckauftrag übergeben.", 6500)

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


    def show_program_info(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Info zu PDF Werkbank")
        layout = QVBoxLayout(dialog)
        title = QLabel("PDF Werkbank " + ".".join(map(str, VERSION)))
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)
        details = QLabel("PDFs bearbeiten, scannen, prüfen, messen und drucken.\n"
                         "Quellcode unter AGPL-3.0-only.\n\n"
                         "Neu in dieser Version: Ordner und offene Formularläufe sichern "
                         "und nach einem Umzug neu zuordnen.")
        details.setWordWrap(True)
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        buttons.addButton("Update-Info", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(self.update_info)
        buttons.addButton("Lizenz anzeigen", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(
            lambda: self.show_program_license(dialog))
        buttons.addButton("Projekt auf GitHub", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/aheinen1971-cmd/pdf-werkbank")))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(550, 260)
        dialog.exec()

    def show_program_license(self, parent):
        try:
            content = Path(__file__).with_name("LICENSE").read_text(encoding="utf-8")
        except OSError as exc:
            self.error(exc)
            return
        dialog = QDialog(parent)
        dialog.setWindowTitle("Lizenz · PDF Werkbank")
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(content)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(720, 580)
        dialog.exec()


    def update_info(self):
        label = ".".join(map(str, VERSION))
        launcher = os.environ.get("PDF_WERKBANK_LAUNCHER")
        status = "Für diese gestartete EXE ist keine Projektsignatur nachgewiesen."
        if launcher:
            executable = Path(launcher)
            try:
                marker = executable.with_name(f"PDF-Werkbank-{label}-Signatur.json")
                verified_launcher(executable, label, marker if marker.is_file() else None)
                status = "Projektsignatur und EXE-Prüfsumme gültig. Freigabe: " + SIGNER
            except (OSError, ValueError) as exc:
                status += "\n" + str(exc)
        pending = prepared_update(VERSION)
        check = getattr(self, "update_result", "Noch keine Update-Suche in dieser Sitzung.")
        QMessageBox.information(self, "Update-Info",
                                f"Version {label}\n\n{status}\n\n{check}\n"
                                + (f"Signiertes Update bereit: {pending.name}\n" if pending else "")
                                + "\nWindows-Herausgebersignatur und Virenscan sind separate Prüfungen.")


    def check_online(self):
        self.statusBar().showMessage("Suche nach Updates …", 6000)
        self.release_check.check(manual=True)

    def show_online_update(self, update, success, manual):
        self.update_result = ("Update-Suche abgeschlossen." if success else "Die Update-Suche ist fehlgeschlagen.")
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
        elif result == 3:
            message = "Update abgelehnt. Die Projektsignatur fehlt oder ist ungültig."
        elif result == 4:
            message = "Update übersprungen. Diese Freigabe ist beim Start bereits fehlgeschlagen."
        else:
            message = "Update konnte nicht vorbereitet werden. Beim nächsten Start wird erneut geprüft."
        self.update_result = message
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
                "Die EXE und ihre signierte Freigabe müssen zusammen im Ordner liegen. Aktivieren?")
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
                label = ".".join(map(str, newer))
                try:
                    verified_launcher(candidate, label, candidate.with_name(f"PDF-Werkbank-{label}-Signatur.json"))
                except (OSError, ValueError):
                    continue
                matches.append((newer, candidate))
    return max(matches, default=(version, None))[1]


def signal_startup_ready():
    marker = os.environ.pop("PDF_WERKBANK_STARTUP_READY", "")
    if marker:
        Path(marker).write_text("bereit", encoding="utf-8")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Werkbank")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("pdf-werkbank.ico"))))
    settings = QSettings("PDF Werkbank", "PDF Werkbank")
    folder = settings.value("updates", "", str)
    label = ".".join(map(str, VERSION))
    active = settings.value("update/last-active-version", "", str)
    active_version = version_tuple(active)
    if active_version is None and VERSION == (1, 27, 0) and runtime_ready("1.26.0"):
        active, active_version = "1.26.0", (1, 26, 0)
    measurements = app_backup_root().with_name("Messungen")
    backup_problem = ""

    def secure_switch(target):
        nonlocal backup_problem
        if active_version is not None and version_tuple(target) <= active_version:
            return True
        try:
            automatic_backup(settings, measurements, label, target, refresh=True)
            return True
        except Exception as exc:
            backup_problem = f"Update zurückgestellt. Automatische Sicherung fehlgeschlagen: {exc}"
            return False
    forwarded = os.environ.pop("PDF_WERKBANK_UPDATE_FORWARDED", "")
    launcher = os.environ.get("PDF_WERKBANK_LAUNCHER")
    if launcher and not forwarded:
        update = prepared_update(VERSION)
        if update and secure_switch(update.stem.removeprefix("PDF-Werkbank-")):
            try:
                child = subprocess.Popen([str(update), *sys.argv[1:]])
                try:
                    if child.wait(timeout=3) == 0:
                        signal_startup_ready()
                        return 0
                except subprocess.TimeoutExpired:
                    signal_startup_ready()
                    return 0
            except OSError:
                pass
    if folder and not forwarded and (launcher or getattr(sys, "frozen", False)):
        try:
            update = find_update(folder)
            if (update and update.resolve() != Path(launcher or sys.executable).resolve()
                    and secure_switch(update.stem.removeprefix("PDF-Werkbank-"))):
                env = os.environ.copy()
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                env["PDF_WERKBANK_UPDATE_FORWARDED"] = "1"
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
                subprocess.Popen([str(update), *sys.argv[1:]], env=env, cwd=update.parent)
                signal_startup_ready()
                return 0
        except OSError:
            pass
    if active_version is not None and active_version < VERSION:
        try:
            automatic_backup(settings, measurements, active, label)
        except Exception:
            # The native starter keeps the last working version available.
            if launcher:
                return 23
            QMessageBox.warning(None, "Update zurückgestellt", "Die automatische Sicherung konnte nicht erstellt werden.")
            return 23
    window = Window()
    window.show()
    def load_initial_documents():
        if active_version is None or active_version <= VERSION:
            settings.setValue("update/last-active-version", label)
            settings.sync()
        signal_startup_ready()
        if backup_problem:
            window.update_result = backup_problem
            window.statusBar().showMessage(backup_problem, 15000)
        window.start_recovery(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF Werkbank" / "Wiederherstellung")
        arguments = sys.argv[1:]
        if arguments and arguments[0] in EXPLORER_COMMANDS:
            window.explorer_action(arguments[0], arguments[1:])
            return
        for path in arguments:
            if Path(path).is_file():
                window.open_path(path)
        if not arguments and settings.value("work-overview/start", True, bool):
            window.show_open_work()

    QTimer.singleShot(0, load_initial_documents)
    if settings.value("invoice-watch/folder", "", str):
        QTimer.singleShot(0, lambda: window.invoice_folder(show=False))
    QTimer.singleShot(1200, window.release_check.check)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

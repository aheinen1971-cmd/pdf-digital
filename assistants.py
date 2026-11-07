# SPDX-License-Identifier: AGPL-3.0-only
import math
from difflib import SequenceMatcher

import pymupdf as pdf


def scan_quality(page):
    scale = 360 / max(page.rect.width, page.rect.height)
    raster = page.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csGRAY)
    pixels = raster.samples
    width, height = raster.width, raster.height
    threshold = max(45, min(185, sorted(pixels)[len(pixels) // 2] - 30))
    # Pool small blocks so scanner noise does not make two captures look different.
    fingerprint = tuple(sum(pixels[y * width + x] < threshold
                            for y in range(top, bottom, 2) for x in range(left, right, 2))
                        for top, bottom in zip(range(0, height, 12), range(12, height + 12, 12))
                        for left, right in zip(range(0, width, 12), range(12, width + 12, 12))
                        if bottom <= height and right <= width)
    margin = max(3, min(width, height) // 25)
    points = [(x, y) for y in range(margin, height - margin, 2)
              for x in range(margin, width - margin, 2) if pixels[y * width + x] < threshold]
    if len(points) < max(10, width * height // 1800):
        return True, 0, fingerprint

    def score(angle):
        slope = math.tan(math.radians(angle))
        rows = [0] * (height + 2 * width)
        for x, y in points:
            rows[round(y - x * slope) + width] += 1
        return sum(count * count for count in rows)

    angles = range(-8, 9)
    best = max(angles, key=score)
    return False, best if best and score(best) > 1.14 * score(0) else 0, fingerprint


def similar_scan(left, right):
    if len(left) != len(right) or not any(left) or not any(right):
        return False
    difference = sum(abs(a - b) for a, b in zip(left, right))
    return difference <= 0.12 * max(sum(left), sum(right))


def append_scan(target, page, a4=False, angle=0):
    if not a4 and not angle:
        target.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
        return
    width, height = page.rect.width, page.rect.height
    if a4:
        width, height = pdf.paper_size("a4-l" if width > height else "a4")
    result = target.new_page(width=width, height=height)
    result.show_pdf_page(result.rect, page.parent, page.number, rotate=angle)


def text_changes(left, right):
    words = [page.get_text("words", sort=True) if page is not None else [] for page in (left, right)]
    matcher = SequenceMatcher(None, [word[4] for word in words[0]], [word[4] for word in words[1]])
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag != "equal":
            yield tag, words[0][a:b], words[1][c:d]


def redaction_candidates(doc, terms):
    for page in doc:
        seen = set()
        for term in terms:
            variants = dict.fromkeys((term, term.lower(), term.upper(), term.title()))
            for variant in variants:
                for quad in page.search_for(variant, quads=True):
                    rect = quad.rect
                    key = tuple(rect)
                    if key not in seen:
                        seen.add(key)
                        yield page.number, rect, term


def clean_hidden(doc, metadata, attachments):
    if metadata:
        doc.set_metadata({})
        doc.del_xml_metadata()
    if attachments:
        for name in doc.embfile_names():
            doc.embfile_del(name)
        for page in doc:
            for annot in list(page.annots(types=[pdf.PDF_ANNOT_FILE_ATTACHMENT])):
                page.delete_annot(annot)


def form_candidates(page):
    occupied = [widget.rect for widget in page.widgets()] + [annot.rect for annot in page.annots()]
    words = [pdf.Rect(word[:4]) for word in page.get_text("words")]
    candidates = []

    def add(kind, rect):
        rect = rect & (page.rect * page.derotation_matrix)
        if rect.is_empty or any(rect.intersects(box) for box in occupied + words):
            return
        occupied.append(rect)
        candidates.append((kind, rect))

    for drawing in page.get_drawings():
        if drawing["type"] not in ("s", "fs"):
            continue
        for item in drawing["items"]:
            if item[0] == "re":
                rect = item[1]
                if 7 <= rect.width <= 25 and 7 <= rect.height <= 25 and abs(rect.width - rect.height) <= 4:
                    add("Kontrollkästchen", rect)
            elif item[0] == "l":
                start, end = sorted(item[1:3], key=lambda point: point.x)
                if abs(start.y - end.y) <= 1 and 45 <= end.x - start.x <= 420:
                    add("Textfeld", pdf.Rect(start.x, start.y - 15, end.x, start.y))
    if page.get_image_info():
        for kind, rect in scanned_field_candidates(page):
            add(kind, rect)
    return candidates


def scanned_field_candidates(page):
    scale = min(1.5, 1200 / max(page.rect.width, page.rect.height))
    raster = page.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csGRAY, annots=False)
    pixels, width, height = raster.samples, raster.width, raster.height
    minimum = max(4, round(7 * scale))
    last_runs = set()

    def dark(x, y):
        return pixels[y * width + x] < 120

    for y in range(2, height - 2):
        row = pixels[y * width:(y + 1) * width]
        runs = set()
        start = 0
        for x, value in enumerate(row + b"\xff"):
            if value < 120:
                continue
            length = x - start
            if length >= minimum and start > 2 and x < width - 2:
                runs.add((start, x))
            start = x + 1
        for left, right in sorted(runs - last_runs):
            side = right - left
            if minimum <= side <= 25 * scale:
                for bottom in range(y + max(minimum, side - 2), min(height - 2, y + side + 3)):
                    if sum(dark(x, bottom) for x in range(left, right)) < 0.8 * side:
                        continue
                    if all(sum(any(dark(x, row) for x in range(edge - 1, edge + 2))
                               for row in range(y + 1, bottom)) >= 0.8 * (bottom - y - 1)
                           for edge in (left, right - 1)):
                        rect = pdf.Rect(left, y, right, bottom + 1)
                        yield "Kontrollkästchen", (rect / scale) * page.derotation_matrix
                        break
            elif 45 * scale <= side <= 420 * scale and y > 15 * scale:
                above = y - max(2, round(5 * scale))
                if sum(dark(x, above) for x in range(left, right)) <= 0.1 * side:
                    rect = pdf.Rect(left, y - 15 * scale, right, y)
                    yield "Textfeld", (rect / scale) * page.derotation_matrix
        last_runs = runs


def add_suggested_field(page, kind, rect):
    widget = pdf.Widget()
    widget.rect = rect
    widget.field_name = f"Feld_{page.number + 1}_{page.parent.get_new_xref()}"
    widget.field_type = pdf.PDF_WIDGET_TYPE_CHECKBOX if kind == "Kontrollkästchen" else pdf.PDF_WIDGET_TYPE_TEXT
    widget.text_fontsize = 10
    widget.field_border_width = 1
    widget.field_border_color = (0.2, 0.42, 0.64)
    page.add_widget(widget)

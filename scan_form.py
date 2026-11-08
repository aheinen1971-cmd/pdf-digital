# SPDX-License-Identifier: AGPL-3.0-only
import json
import math
import re

import pymupdf as pdf

from preferences import short_text


SCAN_FORM_TEMPLATES = "form-export/scan-field-templates"
FIELD_TYPES = {"Textfeld": pdf.PDF_WIDGET_TYPE_TEXT, "Kontrollkästchen": pdf.PDF_WIDGET_TYPE_CHECKBOX,
               "Dropdown": pdf.PDF_WIDGET_TYPE_COMBOBOX, "Liste": pdf.PDF_WIDGET_TYPE_LISTBOX}
ANCHOR_OFFSETS = [(x, y) for y in range(-10, 11, 2) for x in range(-10, 11, 2)]
ANCHOR_MARGIN = 13


def valid_anchor(anchor):
    if not isinstance(anchor, dict):
        return False
    point, patch = anchor.get("point"), anchor.get("patch")
    return (isinstance(point, list) and len(point) == 2
            and all(type(value) in (float, int) and 0 <= value <= 1 for value in point)
            and isinstance(patch, str) and re.fullmatch(rf"[0-9a-f]{{{2 * len(ANCHOR_OFFSETS)}}}", patch) is not None)


def anchor_image(page):
    scale = 360 / max(page.rect.width, page.rect.height)
    raster = page.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csGRAY, alpha=False, annots=False)
    if min(raster.width, raster.height) <= 2 * ANCHOR_MARGIN:
        raise ValueError("Die Seite ist für die Ausrichtpunkte zu schmal.")
    width, height = raster.width, raster.height
    samples = raster.samples
    horizontal = [0] * len(samples)
    for y in range(height):
        offset = y * width
        for x in range(1, width - 1):
            index = offset + x
            horizontal[index] = samples[index - 1] + samples[index] + samples[index + 1]
    smoothed = bytearray(samples)
    for y in range(1, height - 1):
        offset = y * width
        for x in range(1, width - 1):
            index = offset + x
            smoothed[index] = (horizontal[index - width] + horizontal[index] + horizontal[index + width]) // 9
    return smoothed, width, height


def patch_at(image, x, y, offsets=ANCHOR_OFFSETS):
    samples, width, _ = image
    return [samples[(y + dy) * width + x + dx] for dx, dy in offsets]


def capture_anchor(image, point):
    _, width, height = image
    x, y = round(point[0] * width), round(point[1] * height)
    if not ANCHOR_MARGIN <= x < width - ANCHOR_MARGIN or not ANCHOR_MARGIN <= y < height - ANCHOR_MARGIN:
        raise ValueError("Setze den Ausrichtpunkt etwas weiter vom Seitenrand entfernt.")
    patch = patch_at(image, x, y)
    if max(patch) - min(patch) < 65 or sum(pixel < 175 for pixel in patch) < 5:
        raise ValueError("Wähle eine gut sichtbare Ecke oder einen markanten Druckpunkt.")
    return {"point": [x / width, y / height], "patch": bytes(patch).hex()}


def locate_anchor(image, anchor):
    _, width, height = image
    source = bytes.fromhex(anchor["patch"])
    mean = sum(source) / len(source)
    reference = [value - mean for value in source]
    energy = sum(value * value for value in reference)
    if energy < 1:
        return None
    center_x, center_y = (round(anchor["point"][0] * width), round(anchor["point"][1] * height))
    left, right = max(ANCHOR_MARGIN, center_x - round(width * .15)), min(width - ANCHOR_MARGIN, center_x + round(width * .15) + 1)
    top, bottom = max(ANCHOR_MARGIN, center_y - round(height * .15)), min(height - ANCHOR_MARGIN, center_y + round(height * .15) + 1)

    def score(x, y, offsets=ANCHOR_OFFSETS):
        values = patch_at(image, x, y, offsets)
        total = sum(values)
        spread = sum(value * value for value in values) - total * total / len(values)
        if spread < 100:
            return 0
        return sum(weight * value for weight, value in zip(reference, values)) / math.sqrt(energy * spread)

    coarse = sorted(((score(x, y), x, y) for y in range(top, bottom, 2) for x in range(left, right, 2)), reverse=True)
    centers = []
    for quality, x, y in coarse:
        if quality < .25 or len(centers) >= 8:
            break
        if all((x - a) ** 2 + (y - b) ** 2 >= 36 for a, b in centers):
            centers.append((x, y))
    variants = [ANCHOR_OFFSETS]
    for angle, scale in ((-8, 1), (8, 1), (0, .9), (0, 1.1)):
        cos, sin = math.cos(math.radians(angle)) * scale, math.sin(math.radians(angle)) * scale
        variants.append([(round(cos * x - sin * y), round(sin * x + cos * y)) for x, y in ANCHOR_OFFSETS])
    refined = {}
    for x, y in centers:
        for b in range(max(top, y - 3), min(bottom, y + 4)):
            for a in range(max(left, x - 3), min(right, x + 4)):
                if (a, b) not in refined:
                    refined[a, b] = max(score(a, b, offsets) for offsets in variants)
    if not refined:
        return None
    (x, y), best = max(refined.items(), key=lambda item: item[1])
    alternative = max((quality for (a, b), quality in refined.items() if (a - x) ** 2 + (b - y) ** 2 >= 64), default=0)
    return [x / width, y / height] if best >= .70 and best - alternative >= .055 else None


def aligned_boxes(fields, anchors, points, aspect):
    def pixel(point):
        return complex(point[0] * aspect, point[1])

    original = [pixel(anchor["point"]) for anchor in anchors]
    target = [pixel(point) for point in points]
    if abs(original[1] - original[0]) < .12:
        raise ValueError("Die Ausrichtpunkte müssen weiter auseinanderliegen.")
    factor = (target[1] - target[0]) / (original[1] - original[0])
    if not .65 <= abs(factor) <= 1.45 or abs(math.degrees(math.atan2(factor.imag, factor.real))) > 20:
        raise ValueError("Die Punkte ergeben eine zu große Drehung oder Größenänderung.")

    boxes = []
    for field in fields:
        x0, y0, x1, y1 = field["box"]
        corners = [target[0] + (pixel((x, y)) - original[0]) * factor for x in (x0, x1) for y in (y0, y1)]
        xs, ys = [value.real / aspect for value in corners], [value.imag for value in corners]
        box = [min(xs), min(ys), max(xs), max(ys)]
        if not all(0 <= value <= 1 for value in box):
            raise ValueError("Die Ausrichtung würde mindestens ein Feld aus der Seite verschieben.")
        boxes.append(box)
    return boxes


def detect_scan_page(page, fields, anchors, raster=None, cache=None):
    raster = anchor_image(page) if raster is None else raster
    points = []
    for anchor in anchors:
        key = (*anchor["point"], anchor["patch"])
        if cache is None:
            point = locate_anchor(raster, anchor)
        else:
            if key not in cache:
                cache[key] = locate_anchor(raster, anchor)
            point = cache[key]
        if point is None:
            raise ValueError("Mindestens ein Ausrichtpunkt war nicht eindeutig.")
        points.append(point)
    return aligned_boxes(fields, anchors, points, page.rect.width / page.rect.height), points


def match_scan_templates(doc, templates):
    rasters, caches, matches = {}, {}, {}
    for name, fields in templates.items():
        pages = {}
        anchors = {}
        for field in fields:
            pages.setdefault(field["page"], []).append(field)
            if "anchors" in field:
                anchors[field["page"]] = field["anchors"]
        if pages.keys() != anchors.keys() or max(pages) >= len(doc):
            continue
        try:
            check_scan_target(doc, fields)
            placed = {}
            for index, items in pages.items():
                page = doc[index]
                if index not in rasters:
                    rasters[index] = anchor_image(page)
                    caches[index] = {}
                boxes, _ = detect_scan_page(page, items, anchors[index], rasters[index], caches[index])
                placed.update({field["name"]: box for field, box in zip(items, boxes)})
        except (RuntimeError, ValueError):
            continue
        matches[name] = [{**field, "box": placed[field["name"]]} for field in fields]
        if len(matches) > 1:
            break
    return matches


def read_scan_text(doc, tessdata):
    if len(doc) > 20:
        raise ValueError("Mehr als 20 Seiten. Die Excel-Zeile bitte von Hand zuordnen.")
    texts = []
    for page in doc:
        text = page.get_text(sort=True)
        if not text.strip():
            if not any((tessdata / f"{language}.traineddata").is_file() for language in ("deu", "eng")):
                raise ValueError("OCR-Sprachdaten fehlen. Die Excel-Zeile bitte von Hand zuordnen.")
            language = "+".join(language for language in ("deu", "eng")
                                if (tessdata / f"{language}.traineddata").is_file())
            recognized = page.get_textpage_ocr(language=language, dpi=160, full=True, tessdata=str(tessdata))
            text = page.get_text(sort=True, textpage=recognized)
        texts.append(text)
    return "\n".join(texts)


def validate_scan_templates(templates):
    if not isinstance(templates, dict) or len(templates) > 50:
        raise ValueError("Es können bis zu 50 Scan-Feldvorlagen gespeichert werden.")
    for title, fields in templates.items():
        if not short_text(title, 100) or not title.strip() or not isinstance(fields, list) or not 1 <= len(fields) <= 300:
            raise ValueError("Eine Scan-Feldvorlage braucht einen Namen und 1 bis 300 Felder.")
        names = set()
        anchored_pages = set()
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError("Ungültiges Feld in der Scan-Feldvorlage.")
            name, page, kind = field.get("name"), field.get("page"), field.get("kind")
            box, choices = field.get("box"), field.get("choices")
            if (not short_text(name, 250) or not name.strip() or name in names
                    or not short_text(field.get("label"), 250)
                    or type(page) is not int or not 0 <= page < 1000 or kind not in tuple(FIELD_TYPES)
                    or not isinstance(box, list) or len(box) != 4
                    or any(type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1 for value in box)
                    or not (box[0] < box[2] and box[1] < box[3]) or type(field.get("required")) is not bool
                    or not isinstance(choices, list) or len(choices) > 100 or any(not short_text(item, 250) for item in choices)
                    or (kind in ("Dropdown", "Liste") and not choices)
                    or type(field.get("maximum")) is not int or not 0 <= field["maximum"] <= 32767):
                raise ValueError("Ungültiges, doppeltes oder nicht unterstütztes Feld in der Scan-Feldvorlage.")
            names.add(name)
            if "anchors" in field:
                anchors = field["anchors"]
                if (page in anchored_pages or not isinstance(anchors, list) or len(anchors) != 2
                        or not all(valid_anchor(anchor) for anchor in anchors)):
                    raise ValueError("Ungültige Ausrichtpunkte in der Scan-Feldvorlage.")
                anchored_pages.add(page)
    if len(json.dumps(templates, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
        raise ValueError("Die Scan-Feldvorlagen sind zusammen zu groß.")
    return templates


def scan_form_templates(settings):
    return validate_scan_templates(json.loads(settings.value(SCAN_FORM_TEMPLATES, "{}", str)))


def capture_scan_fields(doc):
    kinds = {value: label for label, value in FIELD_TYPES.items()}
    fields = []
    for page in doc:
        for widget in page.widgets():
            kind = kinds.get(widget.field_type)
            if (kind is None or widget.field_flags & pdf.PDF_FIELD_IS_READ_ONLY
                    or (kind == "Textfeld" and widget.field_flags & pdf.PDF_TX_FIELD_IS_PASSWORD)):
                raise ValueError(f"Feld „{widget.field_name}“ kann nicht in einer ausfüllbaren Feldvorlage gespeichert werden.")
            rect = widget.rect * page.rotation_matrix
            page_rect = page.rect
            box = [(rect.x0 - page_rect.x0) / page_rect.width, (rect.y0 - page_rect.y0) / page_rect.height,
                   (rect.x1 - page_rect.x0) / page_rect.width, (rect.y1 - page_rect.y0) / page_rect.height]
            if any(not -1e-6 <= value <= 1 + 1e-6 for value in box):
                raise ValueError(f"Feld „{widget.field_name}“ liegt außerhalb der sichtbaren Seite.")
            fields.append({"name": widget.field_name, "label": widget.field_label or "", "page": page.number,
                           "kind": kind, "box": [max(0, min(1, value)) for value in box],
                           "required": bool(widget.field_flags & pdf.PDF_FIELD_IS_REQUIRED),
                           "choices": list(widget.choice_values or []), "maximum": widget.text_maxlen or 0})
    return validate_scan_templates({"Vorlage": fields})["Vorlage"]


def check_scan_target(doc, fields):
    if doc.needs_pass or not doc.permissions & pdf.PDF_PERM_MODIFY or doc.get_sigflags() > 0:
        raise ValueError("Bitte eine bearbeitbare, unsignierte PDF verwenden.")
    if any(field["page"] >= len(doc) for field in fields):
        raise ValueError("Die PDF hat weniger Seiten als die gewählte Feldvorlage benötigt.")
    existing = {widget.field_name for page in doc for widget in page.widgets()}
    conflicts = sorted(existing.intersection(field["name"] for field in fields))
    if conflicts:
        raise ValueError("Diese Formularfelder gibt es bereits: " + ", ".join(conflicts))


def apply_scan_fields(doc, fields):
    check_scan_target(doc, fields)
    for field in fields:
        page = doc[field["page"]]
        rect = page.rect
        x0, y0, x1, y1 = field["box"]
        display = pdf.Rect(rect.x0 + x0 * rect.width, rect.y0 + y0 * rect.height,
                           rect.x0 + x1 * rect.width, rect.y0 + y1 * rect.height)
        widget = pdf.Widget()
        widget.rect = display * page.derotation_matrix
        widget.field_name, widget.field_type = field["name"], FIELD_TYPES[field["kind"]]
        widget.field_label = field["label"]
        widget.field_flags = pdf.PDF_FIELD_IS_REQUIRED if field["required"] else 0
        widget.field_border_width = 1
        widget.field_border_color = (0.2, 0.42, 0.64)
        if field["choices"]:
            widget.choice_values = field["choices"]
        if field["kind"] == "Textfeld":
            widget.text_maxlen = field["maximum"]
        page.add_widget(widget)

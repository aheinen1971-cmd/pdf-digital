# SPDX-License-Identifier: AGPL-3.0-only
import json
import math

import pymupdf as pdf

from preferences import short_text


SCAN_FORM_TEMPLATES = "form-export/scan-field-templates"
FIELD_TYPES = {"Textfeld": pdf.PDF_WIDGET_TYPE_TEXT, "Kontrollkästchen": pdf.PDF_WIDGET_TYPE_CHECKBOX,
               "Dropdown": pdf.PDF_WIDGET_TYPE_COMBOBOX, "Liste": pdf.PDF_WIDGET_TYPE_LISTBOX}


def validate_scan_templates(templates):
    if not isinstance(templates, dict) or len(templates) > 50:
        raise ValueError("Es können bis zu 50 Scan-Feldvorlagen gespeichert werden.")
    for title, fields in templates.items():
        if not short_text(title, 100) or not title.strip() or not isinstance(fields, list) or not 1 <= len(fields) <= 300:
            raise ValueError("Eine Scan-Feldvorlage braucht einen Namen und 1 bis 300 Felder.")
        names = set()
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

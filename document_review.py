# SPDX-License-Identifier: AGPL-3.0-only
from datetime import date
from pathlib import Path

import pymupdf as pdf


def page_labels(page, name, header, footer, align, size, today):
    values = {"{Seite}": str(page.number + 1), "{Seiten}": str(len(page.parent)),
              "{Datum}": today.strftime("%d.%m.%Y"), "{Datei}": Path(name).stem}
    font = pdf.Font("helv")
    for is_header, template in ((True, header), (False, footer)):
        text = template.strip()
        for key, value in values.items():
            text = text.replace(key, value)
        if not text:
            continue
        try:
            text.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise ValueError("Die Kopf- oder Fußzeile enthält Zeichen außerhalb der Standardschrift.") from exc
        width = font.text_length(text, fontsize=size)
        margin = 20
        if width > page.rect.width - 2 * margin or page.rect.height < 2 * (margin + size):
            raise ValueError(f"Die Kopf- oder Fußzeile passt nicht auf Seite {page.number + 1}.")
        x = {"links": margin, "mittig": (page.rect.width - width) / 2,
             "rechts": page.rect.width - margin - width}[align]
        y = margin + size if is_header else page.rect.height - margin
        box = pdf.Rect(x, y - size * font.ascender, x + width, y - size * font.descender)
        yield text, pdf.Point(x, y) * page.derotation_matrix, box * page.derotation_matrix


def label_conflicts(page, labels):
    boxes = [pdf.Rect(word[:4]) for word in page.get_text("words")]
    boxes.extend(pdf.Rect(image["bbox"]) for image in page.get_image_info())
    boxes.extend(path["rect"] + (-1, -1, 1, 1) for path in page.get_drawings())
    boxes.extend(annot.rect for annot in page.annots())
    boxes.extend(widget.rect for widget in page.widgets())
    return any(box.intersects(content) for _, _, box in labels for content in boxes)


def insert_labels(page, labels, size):
    for text, point, _ in labels:
        page.insert_text(point, text, fontsize=size, rotate=page.rotation)


def comments(doc):
    excluded = {"Link", "Widget", "Popup"}
    for page in doc:
        for annot in page.annots():
            kind = annot.type[1]
            if kind not in excluded:
                info = annot.info
                yield page.number, annot.rect * page.rotation_matrix, kind, info.get("title", ""), info.get("content", "")


def attachments(doc):
    for name in doc.embfile_names():
        info = doc.embfile_info(name)
        yield name, info.get("ufilename") or info.get("filename") or name, None, None
    for page in doc:
        for annot in page.annots(types=[pdf.PDF_ANNOT_FILE_ATTACHMENT]):
            info = annot.file_info
            yield "", info.get("ufilename") or info.get("filename") or "Anlage", page.number, annot.xref

# SPDX-License-Identifier: AGPL-3.0-only
import re
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf as pdf

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def read_form_values(path):
    values = {}
    with pdf.open(path) as document:
        if not document.is_pdf or document.needs_pass:
            raise ValueError("Keine lesbare PDF oder Passwort erforderlich.")
        for page in document:
            for field in page.widgets():
                if field.field_type in (pdf.PDF_WIDGET_TYPE_BUTTON, pdf.PDF_WIDGET_TYPE_SIGNATURE):
                    continue
                if field.field_type == pdf.PDF_WIDGET_TYPE_TEXT and field.field_flags & pdf.PDF_TX_FIELD_IS_PASSWORD:
                    continue
                name = field.field_name
                if not name:
                    continue
                value = field.field_value
                if isinstance(value, (list, tuple)):
                    value = " | ".join(map(str, value))
                if field.field_type in (pdf.PDF_WIDGET_TYPE_CHECKBOX, pdf.PDF_WIDGET_TYPE_RADIOBUTTON) and value == "Off":
                    value = ""
                options = values.setdefault(name, {})
                if value not in (None, ""):
                    options[str(value)] = None
    return {name: " | ".join(options) for name, options in values.items()}


def safe_text(value):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", str(value or ""))[:32767]


def xml(element):
    return tostring(element, encoding="utf-8", xml_declaration=True)


def package(path, main, mime, contents):
    ct = Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    SubElement(ct, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    SubElement(ct, "Default", Extension="xml", ContentType="application/xml")
    for extension, image_type in (("png", "png"), ("jpg", "jpeg")):
        if any(name.endswith("." + extension) for name in contents):
            SubElement(ct, "Default", Extension=extension, ContentType="image/" + image_type)
    SubElement(ct, "Override", PartName="/" + main, ContentType=mime)
    for name in contents:
        if name.startswith("xl/worksheets/") and name.endswith(".xml"):
            SubElement(ct, "Override", PartName="/" + name,
                       ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
    links = Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    SubElement(links, "Relationship", Id="rId1", Target=main,
               Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument")
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", xml(ct))
        archive.writestr("_rels/.rels", xml(links))
        for name, item in contents.items():
            archive.writestr(name, item if isinstance(item, bytes) else xml(item))


def export_word(doc, path):
    document = Element("w:document", {"xmlns:w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})
    body = SubElement(document, "w:body")
    for index, page in enumerate(doc):
        if index:
            SubElement(SubElement(SubElement(body, "w:p"), "w:r"), "w:br", {"w:type": "page"})
        for line in page.get_text(sort=True).splitlines():
            if line.strip():
                text = SubElement(SubElement(SubElement(body, "w:p"), "w:r"), "w:t", {"xml:space": "preserve"})
                text.text = safe_text(line)
    name = "word/document.xml"
    package(path, name, "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            {name: document})


def export_word_layout(doc, path):
    document = Element("w:document", {
        "xmlns:w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "xmlns:r": OFFICE_NS,
        "xmlns:wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        "xmlns:a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "xmlns:pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    })
    body = SubElement(document, "w:body")
    links = Element("Relationships", xmlns=REL_NS)
    contents = {"word/document.xml": document, "word/_rels/document.xml.rels": links}
    image_count = 0

    def paragraph(box, previous_bottom, parent):
        para = SubElement(parent, "w:p")
        props = SubElement(para, "w:pPr")
        SubElement(props, "w:spacing", {"w:before": str(round(20 * max(0, box.y0 - previous_bottom))),
                                      "w:after": "0"})
        SubElement(props, "w:ind", {"w:left": str(round(max(0, box.x0 - 12) * 20))})
        return para

    def image_run(para, block, box, max_width):
        nonlocal image_count
        data = block["image"]
        extension = "jpg" if block["ext"] in ("jpg", "jpeg") else "png"
        if block["ext"] not in ("jpg", "jpeg", "png"):
            pix = pdf.Pixmap(data)
            if pix.colorspace.n > 3:
                pix = pdf.Pixmap(pdf.csRGB, pix)
            data = pix.tobytes("png")
        if block.get("mask"):
            base, mask = pdf.Pixmap(block["image"]), pdf.Pixmap(block["mask"])
            if base.colorspace.n > 3:
                base = pdf.Pixmap(pdf.csRGB, base)
            if base.width == mask.width and base.height == mask.height and not base.alpha:
                data = pdf.Pixmap(base, mask).tobytes("png")
                extension = "png"
        image_count += 1
        number = str(image_count)
        filename = f"media/bild{number}.{extension}"
        contents["word/" + filename] = data
        SubElement(links, "Relationship", Id="rId" + number, Target=filename, Type=OFFICE_NS + "/image")
        factor = min(1, max_width / box.width)
        size = {"cx": str(round(box.width * factor * 12700)), "cy": str(round(box.height * factor * 12700))}
        drawing = SubElement(SubElement(para, "w:r"), "w:drawing")
        inline = SubElement(drawing, "wp:inline")
        SubElement(inline, "wp:extent", size)
        SubElement(inline, "wp:docPr", id=number, name="Bild " + number)
        graphic = SubElement(SubElement(inline, "a:graphic"), "a:graphicData",
                             uri="http://schemas.openxmlformats.org/drawingml/2006/picture")
        picture = SubElement(graphic, "pic:pic")
        properties = SubElement(picture, "pic:nvPicPr")
        SubElement(properties, "pic:cNvPr", id=number, name="Bild " + number)
        SubElement(properties, "pic:cNvPicPr")
        fill = SubElement(picture, "pic:blipFill")
        SubElement(fill, "a:blip", {"r:embed": "rId" + number})
        SubElement(SubElement(fill, "a:stretch"), "a:fillRect")
        shape = SubElement(picture, "pic:spPr")
        transform = SubElement(shape, "a:xfrm")
        SubElement(transform, "a:off", x="0", y="0")
        SubElement(transform, "a:ext", size)
        SubElement(SubElement(shape, "a:prstGeom", prst="rect"), "a:avLst")

    for index, page in enumerate(doc):
        bounds = page.rect * page.derotation_matrix
        tables = page.find_tables().tables
        table_boxes = [pdf.Rect(table.bbox) for table in tables]
        items = [(box, "table", table) for box, table in zip(table_boxes, tables)]
        for block in page.get_text("dict")["blocks"]:
            if block["type"] == 1:
                items.append((pdf.Rect(block["bbox"]), "image", block))
            elif block["type"] == 0:
                for line in block["lines"]:
                    box = pdf.Rect(line["bbox"])
                    if not any(box.tl in region and box.br in region for region in table_boxes):
                        items.append((box, "text", line))
        bottom = 12
        for box, kind, item in sorted(items, key=lambda item: (round(item[0].y0, 1), item[0].x0)):
            if kind == "table":
                gap = box.y0 - bottom
                if gap > 3:
                    spacer = SubElement(SubElement(body, "w:p"), "w:pPr")
                    SubElement(spacer, "w:spacing", {"w:before": "0", "w:after": "0",
                                                   "w:line": str(round(gap * 20)), "w:lineRule": "exact"})
                table = SubElement(body, "w:tbl")
                props = SubElement(table, "w:tblPr")
                SubElement(props, "w:tblW", {"w:w": str(round(min(box.width, bounds.width - 24) * 20)), "w:type": "dxa"})
                SubElement(props, "w:tblInd", {"w:w": str(round(max(0, box.x0 - 12) * 20)), "w:type": "dxa"})
                borders = SubElement(props, "w:tblBorders")
                for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
                    SubElement(borders, "w:" + edge, {"w:val": "single", "w:sz": "4", "w:color": "888888"})
                grid = SubElement(table, "w:tblGrid")
                for _ in range(item.col_count):
                    SubElement(grid, "w:gridCol", {"w:w": str(round(box.width * 20 / item.col_count))})
                for values in item.extract():
                    row = SubElement(table, "w:tr")
                    for value in values:
                        cell = SubElement(row, "w:tc")
                        para = SubElement(cell, "w:p")
                        SubElement(SubElement(para, "w:r"), "w:t").text = safe_text(value)
            elif kind == "image" and not box.is_empty:
                image_run(paragraph(box, bottom, body), item, box, max(1, bounds.width - max(box.x0, 12) - 12))
            elif kind == "text":
                para = paragraph(box, bottom, body)
                for span in item["spans"]:
                    run = SubElement(para, "w:r")
                    style = SubElement(run, "w:rPr")
                    source_font = span["font"].split("+")[-1]
                    font = ("Arial" if source_font.startswith("Helvetica") else
                            "Times New Roman" if source_font.startswith("Times") else
                            "Courier New" if source_font.startswith("Courier") else source_font)
                    SubElement(style, "w:rFonts", {"w:ascii": font, "w:hAnsi": font})
                    if span["flags"] & 16:
                        SubElement(style, "w:b")
                    if span["flags"] & 2:
                        SubElement(style, "w:i")
                    SubElement(style, "w:color", {"w:val": f"{span['color']:06X}"})
                    SubElement(style, "w:sz", {"w:val": str(max(2, min(3276, round(span["size"] * 2))))})
                    SubElement(run, "w:t", {"xml:space": "preserve"}).text = safe_text(span["text"])
            bottom = max(bottom, box.y1)
        parent = body if index == len(doc) - 1 else SubElement(SubElement(body, "w:p"), "w:pPr")
        section = SubElement(parent, "w:sectPr")
        if index != len(doc) - 1:
            SubElement(section, "w:type", {"w:val": "nextPage"})
        SubElement(section, "w:pgSz", {"w:w": str(round(min(1584, bounds.width) * 20)),
                                      "w:h": str(round(min(1584, bounds.height) * 20))})
        SubElement(section, "w:pgMar", {"w:top": "240", "w:right": "240", "w:bottom": "240",
                                      "w:left": "240", "w:header": "0", "w:footer": "0", "w:gutter": "0"})
    package(path, "word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            contents)


def column_name(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def export_excel(doc, path):
    workbook = Element("workbook", xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                       attrib={"xmlns:r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"})
    sheets = SubElement(workbook, "sheets")
    links = Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    name = "xl/workbook.xml"
    contents = {name: workbook, "xl/_rels/workbook.xml.rels": links}
    for index, page in enumerate(doc, 1):
        tables = page.find_tables().tables
        rows = []
        if tables:
            for table in tables:
                if rows:
                    rows.append([])
                rows.extend(table.extract())
        else:
            rows = [[line] for line in page.get_text(sort=True).splitlines() if line.strip()]
        sheet = Element("worksheet", xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main")
        sheet_data = SubElement(sheet, "sheetData")
        for row_index, values in enumerate(rows, 1):
            row = SubElement(sheet_data, "row", r=str(row_index))
            for column, value in enumerate(values, 1):
                if value is not None:
                    cell = SubElement(row, "c", r=f"{column_name(column)}{row_index}", t="inlineStr")
                    SubElement(SubElement(cell, "is"), "t", {"xml:space": "preserve"}).text = safe_text(value)
        filename = f"worksheets/sheet{index}.xml"
        SubElement(sheets, "sheet", {"name": f"Seite {index}", "sheetId": str(index), "r:id": f"rId{index}"})
        SubElement(links, "Relationship", Id=f"rId{index}", Target=filename,
                   Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet")
        contents["xl/" + filename] = sheet
    package(path, name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml", contents)


def export_invoices(rows, path):
    export_linked_sheet(rows, path, "Rechnungen",
                        ("Rechnungsnummer", "Rechnungsdatum", "Betrag", "Quelldatei", "Fundseiten (Nr. / Datum / Betrag)"),
                        (24, 18, 22, 65, 42), 4)


def export_linked_sheet(rows, path, title, header, widths, source_column=None, extra_sheets=()):
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    workbook = Element("workbook", {"xmlns": namespace, "xmlns:r": OFFICE_NS})
    sheets = SubElement(workbook, "sheets")
    links = Element("Relationships", xmlns=REL_NS)
    name = "xl/workbook.xml"
    contents = {name: workbook, "xl/_rels/workbook.xml.rels": links}
    specifications = ((title, header, widths, rows, source_column), *extra_sheets)
    for number, (title, header, widths, rows, source_column) in enumerate(specifications, 1):
        ref = f"rId{number}"
        filename = f"worksheets/sheet{number}.xml"
        SubElement(sheets, "sheet", {"name": title, "sheetId": str(number), "r:id": ref})
        SubElement(links, "Relationship", Id=ref, Target=filename, Type=OFFICE_NS + "/worksheet")
        sheet = Element("worksheet", {"xmlns": namespace, "xmlns:r": OFFICE_NS})
        columns = SubElement(sheet, "cols")
        for index, width in enumerate(widths, 1):
            SubElement(columns, "col", min=str(index), max=str(index), width=str(width), customWidth="1")
        data = SubElement(sheet, "sheetData")
        hyperlinks = None
        sources = Element("Relationships", xmlns=REL_NS)
        for index, values in enumerate((header, *rows), 1):
            row = SubElement(data, "row", r=str(index))
            for column, value in enumerate(values, 1):
                address = f"{column_name(column)}{index}"
                if type(value) in (int, float):
                    SubElement(SubElement(row, "c", r=address, t="n"), "v").text = str(value)
                elif value is not None:
                    cell = SubElement(row, "c", r=address, t="inlineStr")
                    SubElement(SubElement(cell, "is"), "t", {"xml:space": "preserve"}).text = safe_text(value)
            if source_column and index > 1 and values[source_column - 1]:
                if hyperlinks is None:
                    hyperlinks = SubElement(sheet, "hyperlinks")
                key = f"rId{index}"
                column = column_name(source_column)
                SubElement(hyperlinks, "hyperlink", {"ref": f"{column}{index}", "r:id": key})
                SubElement(sources, "Relationship", Id=key, Target=Path(values[source_column - 1]).resolve().as_uri(),
                           TargetMode="External", Type=OFFICE_NS + "/hyperlink")
        contents["xl/" + filename] = sheet
        if hyperlinks is not None:
            contents[f"xl/worksheets/_rels/sheet{number}.xml.rels"] = sources
    package(path, name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml", contents)

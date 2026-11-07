# SPDX-License-Identifier: AGPL-3.0-only
import os
import posixpath
import re
from collections import Counter
from itertools import zip_longest
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf as pdf

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAX_FORM_ROWS = 500


def workbook_xml(archive, name):
    with archive.open(name) as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024 or b"\x00" in data or b"<!" in data:
        raise ValueError("Ein Tabellenbestandteil ist zu groß oder enthält nicht unterstütztes XML.")
    return fromstring(data)


def workbook_parts(archive):
    book = workbook_xml(archive, "xl/workbook.xml")
    relationships = workbook_xml(archive, "xl/_rels/workbook.xml.rels")
    parts, strings = {}, None
    for link in relationships.findall("{*}Relationship"):
        kind = link.get("Type", "").rsplit("/", 1)[-1]
        if kind not in ("worksheet", "sharedStrings") or link.get("TargetMode") == "External":
            continue
        target = link.attrib["Target"]
        name = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
        if not name.startswith("xl/") or "\\" in name:
            raise ValueError("Ungültiger Tabellenpfad in der Excel-Datei.")
        if kind == "worksheet":
            parts[link.attrib["Id"]] = name
        else:
            strings = name
    sheets = {}
    for sheet in book.findall("{*}sheets/{*}sheet"):
        if sheet.get("state", "visible") != "visible":
            continue
        relation = next((value for key, value in sheet.attrib.items() if key.endswith("}id")), None)
        if relation in parts:
            sheets[sheet.attrib["name"]] = parts[relation]
    if not sheets:
        raise ValueError("Die Excel-Datei enthält kein Tabellenblatt.")
    return sheets, strings


def workbook_sheets(path):
    with ZipFile(path) as archive:
        sheets, _ = workbook_parts(archive)
        return list(sheets)


def spreadsheet_text(node):
    if node is None:
        return ""
    return "".join(item.text or "" for item in [*node.findall("{*}t"), *node.findall("{*}r/{*}t")])


def read_form_sheet(path, sheet):
    with ZipFile(path) as archive:
        sheets, strings = workbook_parts(archive)
        shared = [spreadsheet_text(item) for item in workbook_xml(archive, strings).findall("{*}si")] if strings else []
        grid = workbook_xml(archive, sheets[sheet])
    headers, rows, previous = None, [], 0
    for number, row in enumerate(grid.findall("{*}sheetData/{*}row"), 1):
        line = int(row.get("r", number))
        if not previous < line <= 1048576:
            raise ValueError("Ungültige oder doppelte Zeilennummer in der Excel-Datei.")
        previous = line
        cells, issues = {}, []
        for cell in row.findall("{*}c"):
            address = cell.get("r", "")
            match = re.fullmatch(r"([A-Z]{1,3})[1-9][0-9]*", address)
            if match is None or match[1] in cells:
                raise ValueError("Ungültige Zelladresse in der Excel-Datei.")
            kind = cell.get("t", "n")
            value = cell.findtext("{*}v") or ""
            if cell.find("{*}f") is not None or kind == "e":
                issues.append(f"Formel oder Zellfehler in {address}")
                value = ""
            elif kind == "s":
                index = int(value)
                if not 0 <= index < len(shared):
                    raise ValueError("Ungültiger Textverweis in der Excel-Datei.")
                value = shared[index]
            elif kind == "inlineStr":
                value = spreadsheet_text(cell.find("{*}is"))
            elif kind == "b":
                value = {"0": "FALSE", "1": "TRUE"}[value]
            cells[match[1]] = value
        if not any(value.strip() for value in cells.values()) and not issues:
            continue
        if headers is None:
            headers = {column: value.strip() for column, value in cells.items() if value.strip()}
            if issues or len({name.casefold() for name in headers.values()}) != len(headers):
                raise ValueError("Die Kopfzeile braucht eindeutige Spaltennamen ohne Formeln.")
        else:
            rows.append((line, {name: cells.get(column, "") for column, name in headers.items()}, issues))
            if len(rows) > MAX_FORM_ROWS:
                raise ValueError(f"Bitte höchstens {MAX_FORM_ROWS} Datenzeilen auf einmal einlesen.")
    if not rows:
        raise ValueError("Das Tabellenblatt braucht eine Kopfzeile und mindestens eine Datenzeile.")
    return rows


def form_row_plan(doc, columns, rows, target, source):
    fields = set(name for page in doc for name, _, _ in form_fields(page))
    plan = []
    for line, row, issues in rows:
        issues = list(issues)
        values = {}
        for heading, aliases, required in columns:
            value = row.get(heading, "")
            matches = fields.intersection(aliases)
            if required and not value.strip():
                issues.append(f"Pflichtwert fehlt: {heading}")
            if value.strip() and not matches:
                issues.append(f"Kein PDF-Feld für {heading}")
            if value.strip():
                for name in sorted(matches):
                    if name in values and values[name] != value:
                        issues.append(f"Widersprüchliche Zuordnung: {name}")
                    values[name] = value
        if not values:
            issues.append("Keine Formularwerte zugeordnet")
        filename = next((value.strip() for name, value in row.items() if name.casefold() == "dateiname"), "")
        filename = filename or f"{source.stem}-{line:03d}"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        if len(filename) > 180 or os.path.isreserved(filename) or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename):
            issues.append("Ungültiger PDF-Dateiname")
            path = None
        else:
            path = target / filename
            if path.exists() or path.is_symlink():
                issues.append("Zieldatei existiert bereits")
        plan.append((line, filename, path, values, issues))
    counts = Counter(name.casefold() for _, name, path, _, _ in plan if path is not None)
    for _, name, path, _, issues in plan:
        if path is not None and counts[name.casefold()] > 1:
            issues.append("Doppelter Dateiname")
    return plan


def fill_form(doc, values):
    for page in doc:
        for field in page.widgets():
            name = field.field_name
            if name not in values:
                continue
            value = values[name]
            if field.field_flags & pdf.PDF_FIELD_IS_READ_ONLY:
                raise ValueError(f"PDF-Feld ist schreibgeschützt: {name}")
            if field.field_type == pdf.PDF_WIDGET_TYPE_TEXT and not field.field_flags & pdf.PDF_TX_FIELD_IS_PASSWORD:
                if field.text_maxlen and len(value) > field.text_maxlen:
                    raise ValueError(f"Wert ist für das PDF-Feld zu lang: {name}")
            elif field.field_type == pdf.PDF_WIDGET_TYPE_CHECKBOX:
                on = field.on_state()
                if on and (value == on or value.strip().casefold() in {"ja", "true", "1", "x"}):
                    value = on
                elif value.strip().casefold() in {"nein", "false", "0", "off"}:
                    value = "Off"
                else:
                    raise ValueError(f"Bitte Ja oder Nein für {name} angeben.")
            elif field.field_type in (pdf.PDF_WIDGET_TYPE_COMBOBOX, pdf.PDF_WIDGET_TYPE_LISTBOX):
                if value not in (field.choice_values or ()):
                    raise ValueError(f"Ungültige Auswahl für {name}: {value}")
            else:
                raise ValueError(f"Nicht unterstützter PDF-Feldtyp: {name}")
            field.field_value = value
            field.update()


def form_entry_columns(doc, mappings):
    choices = {}
    for page in doc:
        for field in page.widgets():
            if field.field_flags & pdf.PDF_FIELD_IS_READ_ONLY:
                continue
            if field.field_type == pdf.PDF_WIDGET_TYPE_CHECKBOX:
                options = ["Ja", "Nein"] if field.on_state() else []
            elif field.field_type in (pdf.PDF_WIDGET_TYPE_COMBOBOX, pdf.PDF_WIDGET_TYPE_LISTBOX):
                options = [value for value in (field.choice_values or []) if isinstance(value, str) and value.strip()]
            else:
                continue
            choices.setdefault(field.field_name, []).append(options)
    columns = []
    for heading, aliases, required in mappings:
        groups = [options for alias in aliases for options in choices.get(alias, [])]
        common = list(dict.fromkeys(value for value in groups[0] if all(value in group for group in groups))) if groups else []
        columns.append((heading, required, common))
    if all(heading.casefold() != "dateiname" for heading, _, _ in columns):
        columns.append(("Dateiname", False, []))
    return columns


def export_form_entry(path, columns):
    lists = [options for _, _, options in columns]
    lookup = list(zip_longest(*lists, fillvalue=None)) if any(lists) else []
    extra = (("_Auswahl", [""] * len(columns), [25] * len(columns), lookup, None),) if lookup else ()
    export_linked_sheet([], path, "Eingabe", [heading for heading, _, _ in columns], [28] * len(columns),
                        extra_sheets=extra, entry_columns=columns)


def form_fields(page):
    for field in page.widgets():
        if not field.field_name or field.field_type in (pdf.PDF_WIDGET_TYPE_BUTTON, pdf.PDF_WIDGET_TYPE_SIGNATURE):
            continue
        if field.field_type == pdf.PDF_WIDGET_TYPE_TEXT and field.field_flags & pdf.PDF_TX_FIELD_IS_PASSWORD:
            continue
        value = field.field_value
        if isinstance(value, (list, tuple)):
            value = " | ".join(map(str, value))
        if field.field_type in (pdf.PDF_WIDGET_TYPE_CHECKBOX, pdf.PDF_WIDGET_TYPE_RADIOBUTTON) and value == "Off":
            value = ""
        yield field.field_name, "" if value is None else str(value), field.rect


def read_form_values(path, separate=False):
    values = {}
    with pdf.open(path) as document:
        if not document.is_pdf or document.needs_pass:
            raise ValueError("Keine lesbare PDF oder Passwort erforderlich.")
        for page in document:
            for name, value, _ in form_fields(page):
                options = values.setdefault(name, {})
                if value:
                    options[value] = None
    if separate:
        return {name: tuple(options) for name, options in values.items()}
    return {name: " | ".join(options) for name, options in values.items()}


def map_form_values(fields, columns):
    values, issues = [], {}
    for index, (heading, aliases, required) in enumerate(columns):
        sources = [(alias, value) for alias in aliases for value in fields.get(alias, ()) if value.strip()]
        distinct = {value.strip() for _, value in sources}
        value = sources[0][1] if len(distinct) == 1 else None
        if len(distinct) > 1:
            details = "\n".join(f"{alias}: {text}" for alias, text in sources)
            issues[index] = (f"Widerspruch: {heading}", details)
        elif required and not sources:
            state = "leer" if any(alias in fields for alias in aliases) else "fehlt"
            issues[index] = (f"Pflichtwert {state}: {heading}", "\n".join(aliases))
        values.append(value)
    return values, issues


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
    if "xl/styles.xml" in contents:
        SubElement(ct, "Override", PartName="/xl/styles.xml",
                   ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml")
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


def export_linked_sheet(rows, path, title, header, widths, source_column=None, extra_sheets=(), entry_columns=None):
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    workbook = Element("workbook", {"xmlns": namespace, "xmlns:r": OFFICE_NS})
    sheets = SubElement(workbook, "sheets")
    links = Element("Relationships", xmlns=REL_NS)
    name = "xl/workbook.xml"
    contents = {name: workbook, "xl/_rels/workbook.xml.rels": links}
    if entry_columns:
        contents["xl/styles.xml"] = form_entry_styles(namespace)
        SubElement(links, "Relationship", Id="rIdStyles", Target="styles.xml", Type=OFFICE_NS + "/styles")
        if any(options for _, _, options in entry_columns):
            names = SubElement(workbook, "definedNames")
            for index, (_, _, options) in enumerate(entry_columns, 1):
                if options:
                    column = column_name(index)
                    SubElement(names, "definedName", name=f"Auswahl_{index}").text = (
                        f"'_Auswahl'!${column}$2:${column}${len(options) + 1}")
    specifications = ((title, header, widths, rows, source_column), *extra_sheets)
    for number, (title, header, widths, rows, source_column) in enumerate(specifications, 1):
        ref = f"rId{number}"
        filename = f"worksheets/sheet{number}.xml"
        metadata = {"name": title, "sheetId": str(number), "r:id": ref}
        if entry_columns and number > 1:
            metadata["state"] = "hidden"
        SubElement(sheets, "sheet", metadata)
        SubElement(links, "Relationship", Id=ref, Target=filename, Type=OFFICE_NS + "/worksheet")
        sheet = Element("worksheet", {"xmlns": namespace, "xmlns:r": OFFICE_NS})
        columns = SubElement(sheet, "cols")
        for index, width in enumerate(widths, 1):
            column = SubElement(columns, "col", min=str(index), max=str(index), width=str(width), customWidth="1")
            if entry_columns and number == 1:
                column.set("style", "1")
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
                    if entry_columns and number == 1 and index == 1:
                        cell.set("s", "2" if entry_columns[column - 1][1] else "3")
                    SubElement(SubElement(cell, "is"), "t", {"xml:space": "preserve"}).text = safe_text(value)
            if source_column and index > 1 and values[source_column - 1]:
                if hyperlinks is None:
                    hyperlinks = SubElement(sheet, "hyperlinks")
                key = f"rId{index}"
                column = column_name(source_column)
                SubElement(hyperlinks, "hyperlink", {"ref": f"{column}{index}", "r:id": key})
                SubElement(sources, "Relationship", Id=key, Target=Path(values[source_column - 1]).resolve().as_uri(),
                           TargetMode="External", Type=OFFICE_NS + "/hyperlink")
        if entry_columns and number == 1:
            add_entry_validation(sheet, entry_columns)
        contents["xl/" + filename] = sheet
        if hyperlinks is not None:
            contents[f"xl/worksheets/_rels/sheet{number}.xml.rels"] = sources
    package(path, name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml", contents)


def form_entry_styles(namespace):
    styles = Element("styleSheet", xmlns=namespace)
    fonts = SubElement(styles, "fonts", count="2")
    for bold in (False, True):
        font = SubElement(fonts, "font")
        if bold:
            SubElement(font, "b")
        SubElement(font, "sz", val="11")
        SubElement(font, "name", val="Calibri")
    fills = SubElement(styles, "fills", count="3")
    for pattern in ("none", "gray125", "solid"):
        fill = SubElement(SubElement(fills, "fill"), "patternFill", patternType=pattern)
        if pattern == "solid":
            SubElement(fill, "fgColor", rgb="FFFFE1A2")
            SubElement(fill, "bgColor", indexed="64")
    border = SubElement(SubElement(styles, "borders", count="1"), "border")
    for side in ("left", "right", "top", "bottom", "diagonal"):
        SubElement(border, side)
    base = {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"}
    SubElement(SubElement(styles, "cellStyleXfs", count="1"), "xf", base)
    formats = SubElement(styles, "cellXfs", count="4")
    SubElement(formats, "xf", {**base, "xfId": "0"})
    SubElement(formats, "xf", {**base, "xfId": "0", "numFmtId": "49", "applyNumberFormat": "1"})
    for fill in ("2", "0"):
        SubElement(formats, "xf", {**base, "xfId": "0", "fontId": "1", "applyFont": "1",
                                    "fillId": fill, "applyFill": "1"})
    SubElement(SubElement(styles, "cellStyles", count="1"), "cellStyle", name="Normal", xfId="0", builtinId="0")
    return styles


def add_entry_validation(sheet, columns):
    rules = SubElement(sheet, "dataValidations")
    for index, (_, required, options) in enumerate(columns, 1):
        if not required and not options:
            continue
        column = column_name(index)
        rule = SubElement(rules, "dataValidation", type="list" if options else "custom",
                          sqref=f"{column}2:{column}{MAX_FORM_ROWS + 1}", allowBlank="0" if required else "1",
                          showInputMessage="1", showErrorMessage="1", errorStyle="stop",
                          promptTitle="Pflichtangabe" if required else "Auswahl",
                          prompt="Bitte einen gültigen Wert eingeben.",
                          error="Bitte einen zulässigen Wert eingeben.")
        SubElement(rule, "formula1").text = f"Auswahl_{index}" if options else f"LEN(TRIM({column}2))>0"
    if len(rules):
        rules.set("count", str(len(rules)))
    else:
        sheet.remove(rules)

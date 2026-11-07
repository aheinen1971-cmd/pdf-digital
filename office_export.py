# SPDX-License-Identifier: AGPL-3.0-only
import re
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile


def safe_text(value):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", str(value or ""))[:32767]


def xml(element):
    return tostring(element, encoding="utf-8", xml_declaration=True)


def package(path, main, mime, contents):
    ct = Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    SubElement(ct, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    SubElement(ct, "Default", Extension="xml", ContentType="application/xml")
    SubElement(ct, "Override", PartName="/" + main, ContentType=mime)
    for name in contents:
        if name.startswith("xl/worksheets/"):
            SubElement(ct, "Override", PartName="/" + name,
                       ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
    links = Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    SubElement(links, "Relationship", Id="rId1", Target=main,
               Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument")
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", xml(ct))
        archive.writestr("_rels/.rels", xml(links))
        for name, item in contents.items():
            archive.writestr(name, xml(item))


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

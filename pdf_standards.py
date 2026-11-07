# SPDX-License-Identifier: AGPL-3.0-only
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import pymupdf as pdf


def object_ref(doc, definition):
    xref = doc.get_new_xref()
    doc.update_object(xref, definition)
    return xref


def reference(xref):
    return f"{xref} 0 R"


def page_image(source, dpi=180, clip=None):
    scale = min(dpi / 72, 4200 / max(source.rect.width, source.rect.height))
    return source.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csRGB, alpha=False, clip=clip)


def export_archive(source, path, color_profile):
    profile = Path(color_profile).read_bytes()
    if len(profile) < 128 or profile[16:20] != b"RGB " or profile[36:40] != b"acsp":
        raise ValueError("Das Windows-sRGB-Farbprofil ist ungültig.")
    with pdf.open() as doc:
        for original in source:
            page = doc.new_page(width=original.rect.width, height=original.rect.height)
            page.insert_image(page.rect, pixmap=page_image(original))
        profile_ref = object_ref(doc, "<< /N 3 /Alternate /DeviceRGB >>")
        doc.update_stream(profile_ref, profile)
        intent = object_ref(doc, "<< /Type /OutputIntent /S /GTS_PDFA1 "
                            "/OutputConditionIdentifier (sRGB IEC61966-2.1) /Info (sRGB IEC61966-2.1) "
                            f"/DestOutputProfile {reference(profile_ref)} >>")
        doc.xref_set_key(doc.pdf_catalog(), "OutputIntents", f"[{reference(intent)}]")
        doc.set_xml_metadata('''<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
      <pdfaid:part>2</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>''')
        doc.save(path, garbage=4, deflate=True)


def validate_standard(java, jar, path, flavour):
    if flavour not in ("2b", "ua1"):
        raise ValueError("Unbekanntes Prüfprofil.")
    result = subprocess.run([str(java), "-jar", str(jar), "--flavour", flavour, "--format", "mrr", str(path)],
                            capture_output=True, timeout=180,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        root = ElementTree.fromstring(result.stdout)
        report = root.find(".//validationReport")
        if report is None or report.get("isCompliant") not in ("true", "false"):
            raise ValueError()
    except (ElementTree.ParseError, ValueError) as exc:
        raise ValueError("veraPDF hat keinen gültigen Prüfbericht geliefert. "
                         + result.stderr.decode("utf-8", errors="replace")[-600:]) from exc
    details = [entry.findtext("description", "") for entry in report.findall(".//rule")
               if entry.get("status") == "failed"]
    return report.get("isCompliant") == "true", [detail for detail in details if detail]


def accessibility_blocks(page):
    result = []
    for block in page.get_text("dict", sort=True)["blocks"]:
        rect = (pdf.Rect(block["bbox"]) * page.rotation_matrix) & page.rect
        if rect.is_empty:
            continue
        if block["type"] == 1:
            result.append(("Figure", rect, ""))
        elif block["type"] == 0:
            text = "\n".join("".join(span["text"] for span in line["spans"]) for line in block["lines"]).strip()
            if text:
                result.append(("P", rect, text))
    if not result and page.get_drawings():
        result.append(("Figure", page.rect, ""))
    return result


def tag_stream(doc, xref, header):
    doc.update_stream(xref, header + b"\n" + doc.xref_stream(xref) + b"\nEMC\n")


def export_accessible(source, path, pages, title, language, font_path):
    with pdf.open() as doc:
        root = object_ref(doc, "<< /Type /StructTreeRoot >>")
        top = object_ref(doc, f"<< /Type /StructElem /S /Document /P {reference(root)} >>")
        tree = object_ref(doc, "<< >>")
        sections = []
        parents = []
        for index, (original, blocks) in enumerate(zip(source, pages, strict=True)):
            page = doc.new_page(width=original.rect.width, height=original.rect.height)
            page.insert_image(page.rect, pixmap=page_image(original))
            tag_stream(doc, page.get_contents()[-1], b"/Artifact BMC")
            page.insert_font(fontname="Werkbank", fontfile=str(font_path))
            section = object_ref(doc, f"<< /Type /StructElem /S /Sect /P {reference(top)} /Pg {reference(page.xref)} >>")
            children = []
            for role, box, text in blocks:
                if role not in ("P", "H1", "H2", "H3", "H4", "H5", "H6", "Figure"):
                    raise ValueError("Unbekannte Dokumentstruktur.")
                marker = len(children)
                if role == "Figure":
                    page.insert_image(box, pixmap=page_image(original, clip=box))
                    extra = " /Alt " + pdf.get_pdf_str(text)
                else:
                    point = pdf.Point(box.x0, min(box.y1, box.y0 + 8))
                    page.insert_text(point, " ".join(text.split()), fontsize=6, fontname="Werkbank", render_mode=3)
                    extra = " /ActualText " + pdf.get_pdf_str(text)
                tag_stream(doc, page.get_contents()[-1], f"/{role} <</MCID {marker}>> BDC".encode("ascii"))
                children.append(object_ref(doc, f"<< /Type /StructElem /S /{role} /P {reference(section)} "
                                           f"/Pg {reference(page.xref)} /K {marker}{extra} >>"))
            array = "[" + " ".join(map(reference, children)) + "]"
            doc.xref_set_key(section, "K", array)
            doc.xref_set_key(page.xref, "StructParents", str(index))
            doc.xref_set_key(page.xref, "Tabs", "/S")
            parents.extend((str(index), array))
            sections.append(reference(section))
        catalog = doc.pdf_catalog()
        doc.xref_set_key(top, "K", "[" + " ".join(sections) + "]")
        doc.xref_set_key(tree, "Nums", "[" + " ".join(parents) + "]")
        doc.xref_set_key(root, "K", reference(top))
        doc.xref_set_key(root, "ParentTree", reference(tree))
        doc.xref_set_key(root, "ParentTreeNextKey", str(len(doc)))
        doc.xref_set_key(catalog, "StructTreeRoot", reference(root))
        doc.xref_set_key(catalog, "MarkInfo", "<< /Marked true >>")
        doc.xref_set_key(catalog, "Lang", pdf.get_pdf_str(language))
        doc.xref_set_key(catalog, "ViewerPreferences", "<< /DisplayDocTitle true >>")
        doc.set_metadata({"title": title})
        doc.set_xml_metadata(f'''<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about="" xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/"
                     xmlns:dc="http://purl.org/dc/elements/1.1/">
      <pdfuaid:part>1</pdfuaid:part>
      <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{escape(title)}</rdf:li></rdf:Alt></dc:title>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>''')
        doc.subset_fonts()
        for xref in range(1, doc.xref_length()):
            if (doc.xref_get_key(xref, "Subtype")[1] == "/CIDFontType2"
                    and doc.xref_get_key(xref, "CIDToGIDMap")[0] == "null"):
                doc.xref_set_key(xref, "CIDToGIDMap", "/Identity")
        doc.save(path, garbage=4, deflate=True)

# SPDX-License-Identifier: AGPL-3.0-only
import base64
import os
import re
from pathlib import Path

import pymupdf as pdf
from ocr_text import correction_layer


def booklet_sheets(indices):
    pages = list(indices)
    if not pages:
        raise ValueError("Bitte mindestens eine Seite auswählen.")
    pages.extend([None] * (-len(pages) % 4))
    return [((pages[-1 - 2 * sheet], pages[2 * sheet]),
             (pages[2 * sheet + 1], pages[-2 - 2 * sheet]))
            for sheet in range(len(pages) // 4)]


def impose_booklet(source, sheets, margin=28, gutter=10):
    width, height = pdf.paper_size("a4-l")
    halves = (pdf.Rect(margin, margin, width / 2 - gutter, height - margin),
              pdf.Rect(width / 2 + gutter, margin, width - margin, height - margin))
    if any(rect.is_empty for rect in halves):
        raise ValueError("Der Druckbereich ist zu klein für eine Broschüre.")
    result = pdf.open()
    try:
        with pdf.open(stream=source.tobytes(), filetype="pdf") as copy:
            copy.bake(annots=True, widgets=True)
            for index in {index for sheet in sheets for side in sheet for index in side if index is not None}:
                if copy[index].rotation:
                    copy[index].remove_rotation()
            for sheet in sheets:
                for side in sheet:
                    page = result.new_page(width=width, height=height)
                    for area, index in zip(halves, side):
                        if index is not None and copy[index].read_contents().strip():
                            page.show_pdf_page(area, copy, index)
        return result
    except Exception:
        result.close()
        raise


def crop_page(page, selection):
    visible = page.rect * page.derotation_matrix
    rectangle = pdf.Rect(selection) & visible
    if rectangle.width < 4 or rectangle.height < 4:
        raise ValueError("Bitte einen größeren Ausschnitt aufziehen.")
    position = page.cropbox.tl - visible.tl
    rectangle += (position.x, position.y, position.x, position.y)
    page.set_cropbox(rectangle)


def ocr_lines(page):
    if (not page.get_image_info() or any(run["type"] != 3 for run in page.get_texttrace())
            or page.get_links() or any(page.widgets())):
        return []
    flags = pdf.TEXTFLAGS_DICT & ~pdf.TEXT_PRESERVE_IMAGES
    lines = [(pdf.Rect(line["bbox"]), pdf.Point(line["spans"][0]["origin"]), line["spans"][0]["size"], text)
            for block in page.get_text("dict", flags=flags, sort=False)["blocks"]
            for line in block.get("lines", ())
            if line["dir"] == (1.0, 0.0) and line["spans"]
            and (text := "".join(span["text"] for span in line["spans"]).strip())]
    merged = []
    for box, origin, size, text in lines:
        if merged and abs(merged[-1][1].y-origin.y) < 0.5 and abs(merged[-1][2]-size) < 0.5:
            old = merged[-1]
            merged[-1] = (old[0] | box, old[1], size, old[3] + ' ' + text)
        else:
            merged.append((box, origin, size, text))
    return merged


def replace_ocr_layer(page, values):
    lines = ocr_lines(page)
    if not lines or len(lines) != len(values):
        raise ValueError("Die OCR-Ebene kann nicht eindeutig bearbeitet werden.")
    if any(annot.type[0] == pdf.PDF_ANNOT_REDACT for annot in page.annots()):
        raise ValueError("Bitte zuerst vorgemerkte Schwärzungen bearbeiten.")
    flags = pdf.TEXTFLAGS_DICT & ~pdf.TEXT_PRESERVE_IMAGES
    if any(line["dir"] != (1.0, 0.0) for block in page.get_text("dict", flags=flags)["blocks"]
           for line in block.get("lines", ())):
        raise ValueError("Die Seite enthält gedrehten OCR-Text.")
    values = [value.strip() for value in values]
    if any(len(value) > 10000 or any(ord(char) < 32 for char in value) for value in values):
        raise ValueError("Bitte pro Zeile höchstens 10000 Zeichen ohne Steuerzeichen eingeben.")
    bounds = page.rect * page.derotation_matrix
    with correction_layer(bounds, lines, values) as layer:
        scale = min(1.3, 1800 / max(page.rect.width, page.rect.height))
        matrix = pdf.Matrix(scale, scale)
        before = page.get_pixmap(matrix=matrix).digest
        page.add_redact_annot(bounds, fill=False, cross_out=False)
        page.apply_redactions(images=pdf.PDF_REDACT_IMAGE_NONE, graphics=pdf.PDF_REDACT_LINE_ART_NONE)
        if any(values):
            page.show_pdf_page(bounds, layer, 0)
    if page.get_pixmap(matrix=matrix).digest != before:
        raise ValueError("Die OCR-Korrektur hat das sichtbare Seitenbild verändert und wurde verworfen.")


def fixed_form(source):
    if source.needs_pass or source.get_sigflags() > 0:
        raise ValueError("Bitte eine ungeschützte PDF ohne digitale Signatur auswählen.")
    copy = pdf.open(stream=source.tobytes(), filetype="pdf")
    try:
        if copy.xref_get_key(copy.pdf_catalog(), "AcroForm/XFA")[0] != "null":
            raise ValueError("Dieses dynamische XFA-Formular wird nicht unterstützt.")
        pages, filled, empty = {}, 0, 0
        for page in copy:
            fields = list(page.widgets())
            if not fields:
                continue
            for field in fields:
                kind = field.field_type
                if kind == pdf.PDF_WIDGET_TYPE_SIGNATURE:
                    raise ValueError("Eine feste Kopie von Signaturfeldern wird nicht unterstützt.")
                value = field.field_value
                if kind != pdf.PDF_WIDGET_TYPE_BUTTON:
                    if value is None or value == "" or value == "Off":
                        empty += 1
                    else:
                        filled += 1
                if kind in (pdf.PDF_WIDGET_TYPE_TEXT, pdf.PDF_WIDGET_TYPE_COMBOBOX, pdf.PDF_WIDGET_TYPE_LISTBOX):
                    field.update()
                appearance, _ = copy.xref_get_key(field.xref, "AP/N")
                if appearance == "null" and value not in (None, "", "Off"):
                    raise ValueError(f"Das Feld „{field.field_name}“ hat keine prüfbare sichtbare Darstellung.")
            scale = min(1.3, 1800 / max(page.rect.width, page.rect.height))
            matrix = pdf.Matrix(scale, scale)
            pages[page.number] = (matrix, copy[page.number].get_pixmap(matrix=matrix).digest)
            del field, fields
        if not pages:
            raise ValueError("Die PDF enthält keine ausfüllbaren Formularfelder.")
        del page
        copy.bake(annots=False, widgets=True)
        if any(any(page.widgets()) for page in copy):
            raise ValueError("Ein Formularfeld konnte nicht in die Seite übernommen werden.")
        if copy.xref_get_key(copy.pdf_catalog(), "AcroForm/Fields")[1] not in ("null", "[]"):
            raise ValueError("Der Formularfeldbaum konnte nicht entfernt werden.")
        for number, (matrix, original) in pages.items():
            if copy[number].get_pixmap(matrix=matrix).digest != original:
                raise ValueError(f"Die sichtbaren Formularwerte auf Seite {number + 1} konnten nicht eindeutig geprüft werden.")
        embed_checkbox_font(copy, pages)
        return copy, filled, empty
    except Exception:
        copy.close()
        raise


def embed_checkbox_font(doc, pages):
    fonts = {font[0] for number in pages for font in doc[number].get_fonts(full=True)
             if font[3] == "ZapfDingbats" and doc.xref_get_key(font[0], "FontDescriptor")[0] == "null"}
    if not fonts:
        return
    # PDF viewers may lack the standard Dingbats font used by checkboxes.
    stream = doc.get_new_xref()
    doc.update_object(stream, "<< /Subtype /Type1C >>")
    doc.update_stream(stream, pdf.Font("zadb").buffer)
    descriptor = doc.get_new_xref()
    doc.update_object(descriptor, "<< /Type /FontDescriptor /FontName /ZapfDingbats /Flags 4 "
                      "/FontBBox [-1 -144 981 819] /ItalicAngle 0 /Ascent 819 /Descent -144 "
                      f"/CapHeight 819 /StemV 80 /FontFile3 {stream} 0 R >>")
    for xref in fonts:
        doc.xref_set_key(xref, "Encoding", "null")
        doc.xref_set_key(xref, "FontDescriptor", f"{descriptor} 0 R")


NUMBER = rb"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
IMAGE = re.compile(rb"(?<!\S)q\s+(" + NUMBER + rb"(?:\s+" + NUMBER
                   + rb"){5})\s+cm\s+/([^\s/<>\[\](){}%]+)\s+Do\s+Q(?=\s|$)")


def close_coordinates(first, second):
    return all(abs(a - b) < 0.03 for a, b in zip(first, second))


def image_at(page, point):
    images = [item for item in page.get_image_info(xrefs=True) if pdf.Rect(item["bbox"]).contains(point)]
    if not images:
        raise ValueError("Hier wurde kein PDF-Bild gefunden.")
    if len(images) != 1:
        raise ValueError("Hier überlagern sich mehrere Bilder. Bitte einen eindeutigen Bereich anklicken.")
    return images[0]


def edit_image(page, selected, offset=None, replacement=None):
    if (offset is None) == (replacement is None):
        raise ValueError("Genau eine Bildänderung auswählen.")
    doc = page.parent
    page_matrix = page.transformation_matrix
    resources = {image[7].encode("utf-8"): image[0] for image in page.get_images(full=True)
                 if image[-1] == 0}
    original = page.read_contents()
    matches = []
    for match in IMAGE.finditer(original):
        if resources.get(match[2]) != selected["xref"]:
            continue
        matrix = pdf.Matrix(*(float(token) for token in match[1].split()))
        display = pdf.Matrix(1, 0, 0, -1, 0, 1) * matrix * page_matrix
        if close_coordinates(display, selected["transform"]):
            matches.append((match, matrix))
    if len(matches) != 1:
        raise ValueError("Dieses Bild ist komplex eingebettet. Es kann in dieser Version nicht einzeln geändert werden.")
    match, matrix = matches[0]
    other_images = page.get_image_info(xrefs=True)
    expected = [item["bbox"] for item in other_images]
    image_index = next(index for index, item in enumerate(other_images) if item["number"] == selected["number"])
    expected_digest = selected["digest"]
    if offset is not None:
        target = pdf.Rect(selected["bbox"]) + (offset.x, offset.y, offset.x, offset.y)
        if not (page.rect * page.derotation_matrix).contains(target):
            raise ValueError("Das verschobene Bild muss vollständig auf der sichtbaren Seite liegen.")
        inverse = ~page_matrix
        shift = offset * inverse - pdf.Point(0, 0) * inverse
        matrix.e += shift.x
        matrix.f += shift.y
        expected[image_index] = tuple(target)
        replacement_bytes = " ".join(f"{value:.6f}" for value in matrix).encode("ascii")
        start, end = match.span(1)
    else:
        image = pdf.Pixmap(replacement)
        if image.width * image.height > 60_000_000:
            raise ValueError("Das Ersatzbild ist zu groß.")
        previous = set(resources)
        xref = page.insert_image(pdf.Rect(0, 0, 10, 10), pixmap=image)
        names = [image[7].encode("utf-8") for image in page.get_images(full=True)
                 if image[0] == xref and image[-1] == 0 and image[7].encode("utf-8") not in previous]
        if len(names) != 1:
            raise ValueError("Das Ersatzbild konnte nicht eindeutig eingebettet werden.")
        replacement_bytes = names[0]
        start, end = match.span(2)
        expected_digest = None
    stream = doc.get_new_xref()
    doc.update_object(stream, "<<>>")
    doc.update_stream(stream, original[:start] + replacement_bytes + original[end:])
    page.set_contents(stream)
    rendered = doc[page.number].get_image_info(xrefs=True)
    if (len(rendered) != len(expected)
            or not all(close_coordinates(image["bbox"], bbox) for image, bbox in zip(rendered, expected))
            or not all(image["digest"] == original_image["digest"]
                       for index, (image, original_image) in enumerate(zip(rendered, other_images))
                       if index != image_index or expected_digest is not None)
            or (replacement is not None and rendered[image_index]["xref"] != xref)):
        raise ValueError("Die Bildänderung konnte nicht eindeutig geprüft werden.")


def scan_command(path, device_id="", feeder=0, samsung=False):
    script = r"""
$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $manager = New-Object -ComObject WIA.DeviceManager
    $scanners = @($manager.DeviceInfos | Where-Object { $_.Type -eq 1 })
    if ($env:PDF_WERKBANK_SAMSUNG -eq '1') {
        $scanners = @($scanners | Where-Object { $_.Properties.Item('Name').Value -match 'Samsung|CLX[- ]?3300' })
    }
    if ($env:PDF_WERKBANK_SCAN_ID) {
        $scanners = @($scanners | Where-Object { $_.DeviceID -eq $env:PDF_WERKBANK_SCAN_ID })
    }
    if ($scanners.Count -eq 0) { exit 3 }
    if ($scanners.Count -gt 1) {
        $choices = @($scanners | ForEach-Object { @{id=$_.DeviceID; name=$_.Properties.Item('Name').Value} })
        [Console]::WriteLine((ConvertTo-Json -InputObject $choices -Compress))
        exit 4
    }
    $device = $scanners[0].Connect()
    if ($env:PDF_WERKBANK_FEEDER -ne '0') {
        function Get-Property($owner, [int]$number) {
            $owner.Properties | Where-Object {$_.PropertyID -eq $number} | Select-Object -First 1
        }
        $item = $null
        foreach ($candidate in $device.Items) {
            $category = Get-Property $candidate 4125
            if ($category -and [string]$category.Value -match 'fe131934-f84c-42ad-8da4-6129cddd7288') {
                $item = $candidate
                break
            }
        }
        $owner = $item
        if ($null -eq $item) {
            $caps = Get-Property $device 3086
            $select = Get-Property $device 3088
            if (!$caps -or -not ([int]$caps.Value -band 1) -or !$select -or $select.IsReadOnly) { exit 5 }
            $select.Value = 1
            $owner = $device
            $item = $device.Items.Item(1)
        }
        $pages = Get-Property $owner 3096
        if (!$pages -or ($pages.IsReadOnly -and [int]$pages.Value -ne 1)) { exit 5 }
        if (!$pages.IsReadOnly) { $pages.Value = 1 }
        $partialDir = Join-Path $env:PDF_WERKBANK_SCAN_FILE 'unvollstaendig'
        [IO.Directory]::CreateDirectory($partialDir) | Out-Null
        for ($index = 1; $index -le [int]$env:PDF_WERKBANK_FEEDER; $index++) {
            try {
                $image = $item.Transfer('{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}')
            } catch {
                if (($_.Exception.HResult -eq -2145320957) -or ($_.Exception.InnerException -and
                     $_.Exception.InnerException.HResult -eq -2145320957)) {
                    if ($index -eq 1) { exit 6 }
                    exit 0
                }
                throw
            }
            if ($null -eq $image) { exit 2 }
            $name = '{0:0000}-{1}-{2}' -f $index,[int]$image.HorizontalResolution,[int]$image.VerticalResolution
            $partial = Join-Path $partialDir ($name + '.png')
            $image.SaveFile($partial)
            [IO.File]::Move($partial, (Join-Path $env:PDF_WERKBANK_SCAN_FILE ($name + '.png')))
        }
        exit 0
    }
    $image = $device.Items.Item(1).Transfer('{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}')
    if ($null -eq $image) { exit 2 }

    $image.SaveFile($env:PDF_WERKBANK_SCAN_FILE)
    [Console]::WriteLine(('{0},{1}' -f [int]$image.HorizontalResolution,[int]$image.VerticalResolution))
    exit 0
} catch {
    if ($_.Exception.HResult -eq -2147023673) { exit 2 }
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = [str(shell), "-NoLogo", "-NoProfile", "-Sta", "-EncodedCommand",
               base64.b64encode(script.encode("utf-16-le")).decode("ascii")]
    return command, dict(os.environ, PDF_WERKBANK_SCAN_FILE=str(path), PDF_WERKBANK_SCAN_ID=device_id,
                         PDF_WERKBANK_FEEDER=str(feeder), PDF_WERKBANK_SAMSUNG=str(int(samsung)))


def twain_command(executable, output=None, device=None, feeder=True):
    if output is None:
        return [str(executable), "--listdevices", "--driver", "twain"]
    return [str(executable), "--output", str(output), "--noprofile", "--driver", "twain", "--device", device,
            "--source", "feeder" if feeder else "glass", "--dpi", "300", "--pagesize", "a4"]


def feeder_pages(folder):
    for path in sorted(folder.glob("*.png")):
        match = re.fullmatch(r"\d{4}-(\d+)-(\d+)\.png", path.name)
        if match:
            yield path, tuple(map(int, match.groups()))


def scanned_document(path, dpi):
    image = pdf.Pixmap(str(path))
    xres, yres = dpi if dpi and all(30 <= value <= 2400 for value in dpi) else (image.xres, image.yres)
    width = image.width * 72 / max(30, xres)
    height = image.height * 72 / max(30, yres)
    document = pdf.open()
    try:
        page = document.new_page(width=width, height=height)
        page.insert_image(page.rect, pixmap=image)
        return document
    except Exception:
        document.close()
        raise


def missing_required_fields(doc):
    empty = {}
    completed = set()
    for page in doc:
        for field in page.widgets():
            if not field.field_flags & pdf.PDF_FIELD_IS_REQUIRED:
                continue
            if field.field_type == pdf.PDF_WIDGET_TYPE_BUTTON:
                continue
            name = field.field_name or ""
            key = name or field.xref
            value = field.field_value
            values = value if isinstance(value, (list, tuple)) else [value]
            choice = field.field_type in (pdf.PDF_WIDGET_TYPE_CHECKBOX, pdf.PDF_WIDGET_TYPE_RADIOBUTTON)
            filled = any(item is not None and str(item).strip() and (not choice or item not in ("Off", False))
                         for item in values)
            if filled:
                completed.add(key)
            else:
                empty.setdefault(key, (page.number, pdf.Rect(field.rect), name or "(Ohne Namen)"))
    return [entry for key, entry in empty.items() if key not in completed]

# SPDX-License-Identifier: AGPL-3.0-only
"""Invisible Unicode correction layer, using Tesseract's embedded GlyphLessFont.

The tiny font in ocr_glyphless.b64 is extracted from Tesseract's PDF renderer
(Apache-2.0, https://github.com/tesseract-ocr/tesseract).
"""
import base64
import hashlib
import re
import unicodedata
from pathlib import Path
import pymupdf as pdf


def correction_layer(bounds, lines, values):
    doc = pdf.open()
    try:
        page = doc.new_page(width=bounds.width, height=bounds.height)
        chars = list(dict.fromkeys(''.join(values)))
        if len(chars) > 65000:
            raise ValueError('Too many distinct OCR characters')
        ids = {char: index+1 for index,char in enumerate(chars)}
        font = page.insert_font(fontname='OCRUnicode', fontbuffer=base64.b64decode(
            Path(__file__).with_name('ocr_glyphless.b64').read_bytes()))
        descendant = int(re.search(r'(\d+) 0 R',doc.xref_get_key(font,'DescendantFonts')[1])[1])
        name='/OCRCorrected'+hashlib.sha256(''.join(chars).encode('utf-8')).hexdigest()[:16]
        doc.xref_set_key(font,'BaseFont',name)
        doc.xref_set_key(descendant,'BaseFont',name)
        descriptor=int(doc.xref_get_key(descendant,'FontDescriptor')[1].split()[0])
        doc.xref_set_key(descriptor,'FontName',name)
        mapping=doc.get_new_xref();doc.update_object(mapping,'<<>>')
        doc.update_stream(mapping,b'\0\1'*(len(chars)+1))
        doc.xref_set_key(descendant,'CIDToGIDMap',f'{mapping} 0 R')
        doc.xref_set_key(descendant,'DW','500');doc.xref_set_key(descendant,'W','null')
        cmap=['/CIDInit /ProcSet findresource begin','12 dict begin','begincmap',
              '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def',
              '/CMapName /OCRUnicode def','/CMapType 2 def',
              '1 begincodespacerange','<0000> <FFFF>','endcodespacerange']
        for start in range(0,len(chars),100):
            chunk=chars[start:start+100];cmap.append(f'{len(chunk)} beginbfchar')
            cmap.extend(f'<{ids[c]:04x}> <{c.encode("utf-16-be").hex()}>' for c in chunk)
            cmap.append('endbfchar')
        cmap+=['endcmap','CMapName currentdict /CMap defineresource pop','end','end']
        unicode_ref=doc.get_new_xref();doc.update_object(unicode_ref,'<<>>')
        doc.update_stream(unicode_ref,'\n'.join(cmap).encode('ascii'))
        doc.xref_set_key(font,'ToUnicode',f'{unicode_ref} 0 R')
        content=['q','BT','3 Tr']
        for (box,origin,size,_),value in zip(lines,values):
            if not value:continue
            # Fit longer corrections; do not enlarge a short replacement beyond
            # the original line height (selection boxes must stay near the scan).
            width=min(box.width/len(value),size/2)
            rtl=next((unicodedata.bidirectional(c) in ('R','AL') for c in value
                      if unicodedata.bidirectional(c) in ('L','R','AL')),False)
            x=box.x1 if rtl else box.x0
            # Logical content order, with individual character positions. Numeric
            # runs remain left-to-right even within right-to-left documents.
            runs=[]
            for char in value:
                bidi=unicodedata.bidirectional(char)
                direction=True if bidi in ('R','AL') else False if bidi in ('L','EN','AN') else rtl
                if runs and runs[-1][1]==direction:
                    runs[-1]=(runs[-1][0]+char,direction)
                else:runs.append((char,direction))
            for token,direction in runs:
                left=x-len(token)*width if rtl else x
                for offset,char in enumerate(token):
                    pos=left+width*(len(token)-offset-1 if direction else offset)
                    content.append(f'/OCRUnicode {2*width:.8f} Tf 100 Tz 1 0 0 1 {pos:.8f} {bounds.height-origin.y:.8f} Tm <{ids[char]:04x}> Tj')
                x+=(-1 if rtl else 1)*len(token)*width
        content+=['ET','Q']
        stream=doc.get_new_xref();doc.update_object(stream,'<<>>');doc.update_stream(stream,'\n'.join(content).encode('ascii'))
        page.set_contents(stream)
        return doc
    except Exception:
        doc.close();raise

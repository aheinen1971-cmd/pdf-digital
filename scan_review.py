# SPDX-License-Identifier: AGPL-3.0-only
"""Conservative raster heuristics; hints are not a scan quality certificate."""
from collections import Counter
import pymupdf as pdf

BLUR = 'Möglicherweise unscharf: Vorlage ruhig halten und mit höherer Auflösung neu scannen.'
CONTRAST = 'Schwacher Kontrast: Beleuchtung oder Kontrasteinstellung beim Scannen verbessern.'
DARK = 'Sehr dunkler Scan: Beleuchtung und Hintergrund prüfen.'
NOTICE = 'Nur Prüfhinweise. Fotos und kleine Schrift können abweichende Ergebnisse liefern. Das Original bleibt unverändert.'


def raster_hints(pixels, width, height):
    if width < 3 or height < 3 or len(pixels) != width * height:
        return []
    histogram = Counter(pixels)
    def percentile(fraction):
        total = 0
        for value in range(256):
            total += histogram[value]
            if total >= max(1, len(pixels) * fraction):
                return value
        return 255
    spread = percentile(.995) - percentile(.005)
    hints = []
    if 8 < spread < 75:
        hints.append(CONTRAST)
    if percentile(.5) < 140:
        hints.append(DARK)
    if spread > 30:
        laplacian = gradient = 0
        for y in range(1, height-1, 2):
            for x in range(1, width-1, 2):
                i = y*width+x
                l, r, t, b = pixels[i-1], pixels[i+1], pixels[i-width], pixels[i+width]
                laplacian += (l+r+t+b-4*pixels[i])**2
                gradient += (l-r)**2 + (t-b)**2
        if gradient > width*height and laplacian / gradient < .18:
            hints.append(BLUR)
    return hints


def quality_hints(page):
    scale = min(150/72, 1600/max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=pdf.Matrix(scale,scale), colorspace=pdf.csGRAY, alpha=False, annots=False)
    return raster_hints(pix.samples, pix.width, pix.height)


def show_quality(parent, page):
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox, QApplication
    from PySide6.QtCore import Qt
    from assistants import scan_quality
    tr = parent.ui
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        hints = quality_hints(page)
        blank, angle, _ = scan_quality(page)
    finally:
        QApplication.restoreOverrideCursor()
    if blank: hints.insert(0, 'Möglicherweise leer')
    if angle: hints.append('Möglicherweise schief: Seitenausrichtung prüfen.')
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr('Scanqualität prüfen'))
    dialog.setLayoutDirection(parent.layoutDirection())
    dialog.resize(640,320)
    layout = QVBoxLayout(dialog)
    notice = QLabel(tr(NOTICE));notice.setWordWrap(True);layout.addWidget(notice)
    for hint in hints or ['Keine zusätzlichen Auffälligkeiten erkannt. Bitte den Scan trotzdem ansehen.']:
        label = QLabel(tr(hint));label.setWordWrap(True);layout.addWidget(label)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr('Schließen'))
    buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    dialog.exec()

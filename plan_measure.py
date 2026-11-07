# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
import math
import os
from dataclasses import dataclass, field

import pymupdf as pdf

from filing import file_signature
from update_worker import digest_file


METRES_PER_POINT = 0.0254 / 72
MEASURE_MODES = ("Kalibrieren", "Strecke messen", "Fläche messen")
MAX_VERTICES = 300
EPSILON = 1e-8


def distance(first, second):
    return math.hypot(second.x - first.x, second.y - first.y)


def calibrated_scale(first, second, metres):
    length = distance(first, second)
    if length <= EPSILON or not math.isfinite(metres) or metres <= 0:
        raise ValueError("Bitte zwei verschiedene Punkte und eine positive Referenzlänge wählen.")
    return metres / length


def cross(a, b, c):
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def intersect(a, b, c, d):
    def orientation(start, end, point):
        value = cross(start, end, point)
        return 1 if value > EPSILON else -1 if value < -EPSILON else 0

    def within(start, end, point):
        return (min(start.x, end.x) - EPSILON <= point.x <= max(start.x, end.x) + EPSILON
                and min(start.y, end.y) - EPSILON <= point.y <= max(start.y, end.y) + EPSILON)

    ab_c, ab_d, cd_a, cd_b = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
    return ((ab_c * ab_d < 0 and cd_a * cd_b < 0)
            or (ab_c == 0 and within(a, b, c)) or (ab_d == 0 and within(a, b, d))
            or (cd_a == 0 and within(c, d, a)) or (cd_b == 0 and within(c, d, b)))


def polygon_area(points):
    count = len(points)
    if count < 3 or count > MAX_VERTICES:
        raise ValueError(f"Für eine Fläche bitte 3 bis {MAX_VERTICES} Eckpunkte setzen.")
    for index, point in enumerate(points):
        previous, following = points[index - 1], points[(index + 1) % count]
        if distance(point, following) <= EPSILON:
            raise ValueError("Zwei aufeinanderfolgende Eckpunkte liegen übereinander.")
        if abs(cross(previous, point, following)) <= EPSILON:
            dot = ((previous.x - point.x) * (following.x - point.x)
                   + (previous.y - point.y) * (following.y - point.y))
            if dot > EPSILON:
                raise ValueError("Zwei benachbarte Kanten überlappen.")
        for other in range(index + 2, count):
            if index == 0 and other == count - 1:
                continue
            if intersect(point, following, points[other], points[(other + 1) % count]):
                raise ValueError("Die Umrisslinien berühren oder kreuzen sich. Bitte die Eckpunkte am Rand entlang setzen.")
    area = abs(math.fsum(cross(points[0], points[index], points[index + 1]) for index in range(1, count - 1))) / 2
    if area <= EPSILON:
        raise ValueError("Die Eckpunkte umschließen keine Fläche.")
    return area


def format_measure(value, unit):
    digits = 3 if value >= 0.001 else 6
    number = f"{value:,.{digits}f}".translate(str.maketrans(",.", ".,")).rstrip("0").rstrip(",")
    if number == "0" and value > 0:
        number = "< 0,000001"
    return f"{number} {unit}"


@dataclass(frozen=True)
class Measurement:
    points: tuple
    amount: float
    area: bool = False
    name: str = ""

    def label(self, scale):
        return (format_measure(self.amount * scale ** 2, "m²") if self.area
                else format_measure(self.amount * scale, "m"))


    def caption(self, scale):
        value = self.label(scale)
        return f"{self.name} – {value}" if self.name else value


@dataclass
class PlanMeasurements:
    scale: float = 0
    marks: list = field(default_factory=list)


MAX_PLAN_BYTES = 4 * 1024 * 1024


def plan_page_key(page):
    return page.xref, tuple(page.cropbox), tuple(page.mediabox)


def plan_entries(doc, measurements):
    for page in doc:
        plan = measurements.get(plan_page_key(page))
        if plan and plan.scale:
            yield page.number, plan


def store_path(root, source, stamp, create=False):
    key = hashlib.sha256(os.path.normcase(str(source.resolve())).encode("utf-8")).hexdigest()
    directory = root / key
    if not create and not directory.is_dir():
        return None
    if stamp is None or file_signature(source) != stamp:
        raise ValueError("Die PDF auf dem Datenträger wurde geändert. Bitte erneut öffnen.")
    digest = digest_file(source)
    if file_signature(source) != stamp:
        raise ValueError("Die PDF auf dem Datenträger wurde geändert. Bitte erneut öffnen.")
    return directory / (digest + ".json")


def stored_bytes(path):
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_PLAN_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(data) > MAX_PLAN_BYTES:
        raise ValueError("Die gespeicherte Messliste ist zu groß.")
    return data


def plan_token(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def encode_plans(doc, plans):
    pages = []
    for index, plan in plan_entries(doc, plans):
        marks = [{"name": mark.name, "area": mark.area,
                  "points": [[point.x, point.y] for point in mark.points]} for mark in plan.marks]
        pages.append({"page": index, "scale": plan.scale, "marks": marks})
    data = json.dumps({"version": 1, "pages": pages}, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_PLAN_BYTES:
        raise ValueError("Die Messliste ist zu groß.")
    return data


def decode_plans(doc, data):
    parsed = json.loads(data)
    if not isinstance(parsed, dict) or parsed.get("version") != 1 or not isinstance(parsed.get("pages"), list):
        raise ValueError("Die gespeicherte Messliste ist ungültig.")
    plans = {}
    for entry in parsed["pages"]:
        if not isinstance(entry, dict):
            raise ValueError("Ungültiger Seiteneintrag in der Messliste.")
        index, scale, marks = entry.get("page"), entry.get("scale"), entry.get("marks")
        if (type(index) is not int or not 0 <= index < len(doc) or type(scale) not in (int, float)
                or not 0 < scale <= 1e15 or not isinstance(marks, list)):
            raise ValueError("Ungültige Seite oder ungültiger Maßstab in der Messliste.")
        page = doc[index]
        key = plan_page_key(page)
        if key in plans:
            raise ValueError("Doppelter Seiteneintrag in der Messliste.")
        bounds = page.rect * page.derotation_matrix
        plan = PlanMeasurements(scale)
        for entry_mark in marks:
            if not isinstance(entry_mark, dict):
                raise ValueError("Ungültige Messung in der Messliste.")
            name, area, points = entry_mark.get("name"), entry_mark.get("area"), entry_mark.get("points")
            if (not isinstance(name, str) or len(name) > 80 or any(ord(char) < 32 for char in name)
                    or type(area) is not bool or not isinstance(points, list)
                    or not (3 <= len(points) <= MAX_VERTICES if area else len(points) == 2)):
                raise ValueError("Ungültiger Name oder Umriss in der Messliste.")
            if any(not isinstance(point, list) or len(point) != 2 or any(
                    type(value) not in (int, float) or not -1e7 <= value <= 1e7 for value in point) for point in points):
                raise ValueError("Ungültige Koordinaten in der Messliste.")
            vertices = tuple(pdf.Point(point) for point in points)
            if any(not bounds.contains(point) for point in vertices):
                raise ValueError("Ein Messpunkt liegt außerhalb der PDF-Seite.")
            amount = polygon_area(vertices) if area else distance(*vertices)
            if amount <= EPSILON:
                raise ValueError("Die Messung hat keine Ausdehnung.")
            plan.marks.append(Measurement(vertices, amount, area, name))
        plans[key] = plan
    return plans


def draw_plan_copy(source, measurements, font_path):
    if source.needs_pass or source.is_encrypted or not source.permissions & pdf.PDF_PERM_MODIFY:
        raise ValueError("Bitte eine PDF ohne Bearbeitungsschutz auswählen.")
    if source.get_sigflags() > 0:
        raise ValueError("Eine Maßkopie wird nur für PDFs ohne digitale Signatur erstellt.")
    font = pdf.Font(fontfile=str(font_path))
    color = (0.0, 0.35, 0.56)
    output = pdf.open(stream=source.tobytes(), filetype="pdf")
    try:
        for index, plan in plan_entries(source, measurements):
            if not plan.marks:
                continue
            page = output[index]
            used = {entry[4] for entry in page.get_fonts()}
            alias = "WerkbankMass"
            while alias in used:
                alias += "M"
            page.insert_font(fontname=alias, fontbuffer=font.buffer)
            labels = [pdf.Rect(block[:4]) * page.rotation_matrix for block in
                      page.get_text("blocks", flags=pdf.TEXTFLAGS_BLOCKS & ~pdf.TEXT_PRESERVE_IMAGES)]
            for mark in plan.marks:
                shape = page.new_shape()
                shape.draw_polyline(mark.points)
                shape.finish(color=color, width=1.25, closePath=mark.area,
                             fill=(0.10, 0.55, 0.78) if mark.area else None, fill_opacity=0.18)
                for point in mark.points:
                    shape.draw_circle(point, 2.2)
                    shape.finish(color=color, fill=(1, 1, 1), width=0.8)
                shape.commit()
            for mark in plan.marks:
                label = mark.caption(plan.scale)
                if any(not font.has_glyph(ord(char)) for char in label):
                    raise ValueError("Ein Messname enthält Zeichen, die in der PDF-Schrift fehlen.")
                visible = [point * page.rotation_matrix for point in mark.points]
                center_x = (min(point.x for point in visible) + max(point.x for point in visible)) / 2
                center_y = (min(point.y for point in visible) + max(point.y for point in visible)) / 2
                width = font.text_length(label, fontsize=1)
                size = min(10, (page.rect.width - 12) / width, (page.rect.height - 8) / 1.5)
                if size < 5:
                    raise ValueError("Ein Messname ist für die verfügbare Seitenbreite zu lang.")
                width = width * size + 8
                height = size + 6
                left = max(1, min(center_x - width / 2, page.rect.width - width - 1))
                for offset in (0, -1, 1, -2, 2, -3, 3):
                    top = max(1, min(center_y + offset * (height + 2) - height - 2, page.rect.height - height - 1))
                    box = pdf.Rect(left, top, left + width, top + height)
                    if not any(box.intersects(old) for old in labels):
                        break
                labels.append(box)
                page.draw_rect(box * page.derotation_matrix, color=color, fill=(1, 1, 1), width=0.65)
                point = pdf.Point(box.x0 + 4, box.y0 + size + 1) * page.derotation_matrix
                page.insert_text(point, label, fontsize=size, fontname=alias, color=color, rotate=page.rotation)
        output.subset_fonts()
        return output
    except Exception:
        output.close()
        raise

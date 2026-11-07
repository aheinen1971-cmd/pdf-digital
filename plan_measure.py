# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass, field, replace

import pymupdf as pdf

from filing import file_signature
from update_worker import digest_file


METRES_PER_POINT = 0.0254 / 72
MEASURE_MODES = ("Kalibrieren", "Strecke messen", "Fläche messen", "Aussparung messen", "Symbole zählen", "Messpunkt verschieben")
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
    area = ring_area(points)
    if area <= EPSILON:
        raise ValueError("Die Eckpunkte umschließen keine Fläche.")
    return area


def ring_area(points):
    return abs(math.fsum(cross(points[0], points[index], points[index + 1])
                         for index in range(1, len(points) - 1))) / 2


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
    cutouts: tuple = ()
    room: str = ""
    trade: str = ""

    def measured_area(self):
        return self.amount - math.fsum(ring_area(ring) for ring in self.cutouts)

    def label(self, scale):
        return (format_measure(self.measured_area() * scale ** 2, "m²") if self.area
                else format_measure(self.amount * scale, "m"))


    def caption(self, scale):
        value = self.label(scale)
        return f"{self.name} – {value}" if self.name else value


@dataclass(frozen=True)
class CountedSymbol:
    point: pdf.Point
    category: str
    room: str = ""
    trade: str = ""


@dataclass
class PlanMeasurements:
    scale: float = 0
    marks: list = field(default_factory=list)
    symbols: list = field(default_factory=list)


MAX_PLAN_BYTES = 4 * 1024 * 1024


def plan_page_key(page):
    return page.xref, tuple(page.cropbox), tuple(page.mediabox)


def plan_entries(doc, measurements):
    for page in doc:
        plan = measurements.get(plan_page_key(page))
        if plan and (plan.scale or plan.symbols):
            yield page.number, plan


def store_path(root, source, stamp, create=False):
    key = hashlib.sha256(os.path.normcase(str(source.resolve())).encode("utf-8")).hexdigest()
    directory = root / key
    if not create and not directory.is_dir() and not (root / "Importiert").is_dir():
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
                  "points": [[point.x, point.y] for point in mark.points],
                  "cutouts": [[[point.x, point.y] for point in ring] for ring in mark.cutouts],
                  "room": mark.room, "trade": mark.trade}
                 for mark in plan.marks]
        symbols = [[symbol.category, symbol.point.x, symbol.point.y, symbol.room, symbol.trade]
                   for symbol in plan.symbols]
        pages.append({"page": index, "scale": plan.scale, "marks": marks, "symbols": symbols})
    data = json.dumps({"version": 1, "pages": pages}, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_PLAN_BYTES:
        raise ValueError("Die Messliste ist zu groß.")
    return data


def checked_vertex(point, bounds):
    if (not isinstance(point, list) or len(point) != 2 or any(
            type(value) not in (int, float) or not -1e7 <= value <= 1e7 for value in point)):
        raise ValueError("Ungültige Koordinaten in der Messliste.")
    vertex = pdf.Point(point)
    if not bounds.contains(vertex):
        raise ValueError("Ein Messpunkt liegt außerhalb der PDF-Seite.")
    return vertex


def plan_groups(room, trade):
    if any(not isinstance(value, str) or len(value) > 80 or any(ord(char) < 32 for char in value)
           for value in (room, trade)):
        raise ValueError("Ungültiger Raum oder ungültiges Gewerk in der Messliste.")
    return " ".join(room.split()), " ".join(trade.split())


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
                or not 0 <= scale <= 1e15 or not isinstance(marks, list) or (marks and not scale)):
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
            vertices = tuple(checked_vertex(point, bounds) for point in points)
            amount = polygon_area(vertices) if area else distance(*vertices)
            if amount <= EPSILON:
                raise ValueError("Die Messung hat keine Ausdehnung.")
            room, trade = plan_groups(entry_mark.get("room", ""), entry_mark.get("trade", ""))
            mark = Measurement(vertices, amount, area, name, room=room, trade=trade)
            cutouts = entry_mark.get("cutouts", [])
            if not isinstance(cutouts, list) or len(cutouts) > 100:
                raise ValueError("Ungültige Aussparungen in der Messliste.")
            for ring in cutouts:
                if not isinstance(ring, list) or not 3 <= len(ring) <= MAX_VERTICES:
                    raise ValueError("Ungültige Aussparung in der Messliste.")
                mark = add_cutout(mark, tuple(checked_vertex(point, bounds) for point in ring))
            plan.marks.append(mark)
        symbols = entry.get("symbols", [])
        if not isinstance(symbols, list):
            raise ValueError("Ungültige Symbole in der Messliste.")
        for symbol in symbols:
            if (not isinstance(symbol, list) or len(symbol) not in (3, 5) or not isinstance(symbol[0], str)
                    or not 0 < len(symbol[0]) <= 40 or any(ord(char) < 32 for char in symbol[0])):
                raise ValueError("Ungültiges Symbol in der Messliste.")
            room, trade = plan_groups(*symbol[3:]) if len(symbol) == 5 else ("", "")
            plan.symbols.append(CountedSymbol(checked_vertex(symbol[1:3], bounds), symbol[0], room, trade))
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
            if not plan.marks and not plan.symbols:
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
                for ring in (mark.points, *mark.cutouts):
                    shape.draw_polyline((*ring, ring[0]) if mark.area else ring)
                shape.finish(color=color, width=1.25, closePath=False, even_odd=mark.area,
                             fill=(0.10, 0.55, 0.78) if mark.area else None, fill_opacity=0.18)
                for point in mark.points:
                    shape.draw_circle(point, 2.2)
                    shape.finish(color=color, fill=(1, 1, 1), width=0.8)
                shape.commit()
            captions = [(mark.points, mark.caption(plan.scale)) for mark in plan.marks]
            for symbol, number in numbered_symbols(plan.symbols):
                page.draw_circle(symbol.point, 3, color=color, fill=(1, 1, 1), width=0.9)
                captions.append(((symbol.point,), f"{symbol.category} {number}"))
            for points, label in captions:
                if any(not font.has_glyph(ord(char)) for char in label):
                    raise ValueError("Ein Messname enthält Zeichen, die in der PDF-Schrift fehlen.")
                visible = [point * page.rotation_matrix for point in points]
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


def vector_geometry(page):
    points, segments = [], []
    for path in page.get_drawings():
        for item in path["items"]:
            kind = item[0]
            if kind == "l":
                edges = [item[1:]]
            elif kind in ("re", "qu"):
                box = item[1]
                ring = (box.tl, box.tr, box.br, box.bl)
                edges = list(zip(ring, (*ring[1:], ring[0])))
            else:
                if kind == "c":
                    points.extend((item[1], item[-1]))
                continue
            segments.extend(edges)
            for edge in edges:
                points.extend(edge)
            if len(points) >= 40000:
                return points, segments
    return points, segments


def snap_point(point, geometry, tolerance):
    vertices, segments = geometry
    nearest = None
    best = tolerance
    for vertex in vertices:
        gap = distance(point, vertex)
        if gap <= best:
            best, nearest = gap, vertex
    if nearest is not None:
        return nearest
    for first, last in segments:
        dx, dy = last.x - first.x, last.y - first.y
        length = dx * dx + dy * dy
        if not length:
            continue
        ratio = ((point.x - first.x) * dx + (point.y - first.y) * dy) / length
        if 0 < ratio < 1:
            projected = pdf.Point(first.x + ratio * dx, first.y + ratio * dy)
            gap = distance(point, projected)
            if gap <= best:
                best, nearest = gap, projected
    return nearest


def alignment(fixed, moving):
    first, last = fixed
    start, end = moving
    fx, fy = last[0] - first[0], last[1] - first[1]
    mx, my = end[0] - start[0], end[1] - start[1]
    norm = mx * mx + my * my
    if min(norm, fx * fx + fy * fy) < 1:
        raise ValueError("Bitte zwei gut unterscheidbare Referenzpunkte je Plan setzen.")
    a, b = (fx * mx + fy * my) / norm, (fy * mx - fx * my) / norm
    return a, b, first[0] - a * start[0] + b * start[1], first[1] - b * start[0] - a * start[1]


def encode_project(doc, measurements, digest):
    data = json.dumps({"project": 1, "pdf_sha256": digest,
                       "plans": json.loads(encode_plans(doc, measurements))}, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_PLAN_BYTES:
        raise ValueError("Das Messprojekt ist zu groß.")
    return data


def decode_project(doc, data, digest):
    project = json.loads(data)
    if not isinstance(project, dict) or project.get("project") != 1 or "plans" not in project:
        raise ValueError("Diese Datei ist kein Messprojekt der PDF Werkbank.")
    if project.get("pdf_sha256") != digest:
        raise ValueError("Das Messprojekt gehört zu einer anderen PDF-Fassung.")
    return decode_plans(doc, json.dumps(project["plans"]))


def measurement_details(doc, measurements):
    for index, plan in plan_entries(doc, measurements):
        for mark in plan.marks:
            group = mark.room, mark.trade
            if mark.area:
                yield (index + 1, mark.name, mark.measured_area() * plan.scale ** 2, "m²", *group, "Flächen")
                prefix = mark.name + " · " if mark.name else ""
                yield (index + 1, prefix + "Umfang", polygon_perimeter(mark.points) * plan.scale, "m", *group, "Umfänge")
                if mark.cutouts:
                    inner = math.fsum(polygon_perimeter(ring) for ring in mark.cutouts) * plan.scale
                    yield (index + 1, prefix + "Innenkanten", inner, "m", *group, "Innenkanten")
            else:
                yield (index + 1, mark.name, mark.amount * plan.scale, "m", *group, "Strecken")
        counts = Counter((symbol.category, symbol.room, symbol.trade) for symbol in plan.symbols)
        for (category, room, trade), count in counts.items():
            yield (index + 1, category, count, "Stk.", room, trade, "Stückzahlen")


def measurement_rows(doc, measurements, grouped=False):
    rows = []
    totals = {name: [] for name in ("Umfänge", "Innenkanten", "Strecken", "Flächen")}
    symbols = Counter()
    for detail in measurement_details(doc, measurements):
        _, name, value, _, _, _, kind = detail
        rows.append(detail[:6] if grouped else detail[:4])
        if kind == "Stückzahlen":
            symbols[name] += value
        else:
            totals[kind].append(value)
    rows.extend((None, "Summe " + category, count, "Stk.") for category, count in symbols.items())
    rows.extend((None, "Summe " + label, math.fsum(values), "m²" if label == "Flächen" else "m")
                for label, values in totals.items() if values)
    return rows


def measurement_group_totals(doc, measurements):
    groups = {}
    kinds = ("Flächen", "Strecken", "Stückzahlen", "Umfänge", "Innenkanten")
    for _, _, value, _, room, trade, kind in measurement_details(doc, measurements):
        key = room.casefold(), trade.casefold()
        if key not in groups:
            groups[key] = ((room, trade), {name: [] for name in kinds})
        groups[key][1][kind].append(value)
    return [(*groups[key][0], *(sum(groups[key][1][kind]) if kind == "Stückzahlen"
                                else math.fsum(groups[key][1][kind]) for kind in kinds))
            for key in sorted(groups)]


def polygon_perimeter(points):
    return math.fsum(distance(point, points[index - 1]) for index, point in enumerate(points))


def contains_point(ring, point):
    inside = False
    for index, end in enumerate(ring):
        start = ring[index - 1]
        if (start.y > point.y) != (end.y > point.y):
            x = start.x + (end.x - start.x) * (point.y - start.y) / (end.y - start.y)
            inside ^= point.x < x
    return inside


def rings_cross(first, second):
    return any(intersect(point, first[i - 1], other, second[j - 1])
               for i, point in enumerate(first) for j, other in enumerate(second))


def add_cutout(mark, ring):
    if not mark.area:
        raise ValueError("Bitte zuerst eine Fläche messen.")
    if len(mark.cutouts) >= 100:
        raise ValueError("Pro Fläche sind höchstens 100 Aussparungen möglich.")
    polygon_area(ring)
    if not contains_point(mark.points, ring[0]) or rings_cross(mark.points, ring):
        raise ValueError("Die Aussparung muss vollständig innerhalb der Fläche liegen.")
    for other in mark.cutouts:
        if contains_point(other, ring[0]) or contains_point(ring, other[0]) or rings_cross(other, ring):
            raise ValueError("Aussparungen dürfen sich nicht berühren oder überlappen.")
    return replace(mark, cutouts=(*mark.cutouts, ring))


def numbered_symbols(symbols):
    counts = Counter()
    for symbol in symbols:
        counts[symbol.category] += 1
        yield symbol, counts[symbol.category]


def move_vertex(mark, ring_index, point_index, point):
    rings = [list(ring) for ring in (mark.points, *mark.cutouts)]
    rings[ring_index][point_index] = point
    outer = tuple(rings[0])
    amount = polygon_area(outer) if mark.area else distance(*outer)
    if amount <= EPSILON:
        raise ValueError("Die Messung hat keine Ausdehnung.")
    updated = replace(mark, points=outer, amount=amount, cutouts=())
    for ring in rings[1:]:
        updated = add_cutout(updated, tuple(ring))
    return updated

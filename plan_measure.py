# SPDX-License-Identifier: AGPL-3.0-only
import math
from dataclasses import dataclass, field


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

    def label(self, scale):
        return (format_measure(self.amount * scale ** 2, "m²") if self.area
                else format_measure(self.amount * scale, "m"))


@dataclass
class PlanMeasurements:
    scale: float = 0
    marks: list = field(default_factory=list)

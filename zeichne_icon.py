# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from PIL import Image, ImageDraw

# Gezeichnete Buchstaben halten die Vorlage unabhängig von Windows-Schriftdateien.
image = Image.new("RGBA", (256, 256))
draw = ImageDraw.Draw(image)
draw.polygon([(47, 17), (157, 17), (207, 67), (207, 230), (47, 230)], fill="#761a20")
draw.polygon([(40, 12), (151, 12), (201, 62), (201, 225), (40, 225)], fill="#fff8f6")
draw.polygon([(151, 12), (201, 62), (151, 62)], fill="#f2a0a2")
draw.rounded_rectangle((22, 103, 234, 192), radius=15, fill="#b51f31")
white = "#ffffff"
width = 11
for points in [
    [(58, 169), (58, 126), (83, 126), (90, 133), (90, 143), (83, 149), (58, 149)],
    [(111, 126), (111, 168), (130, 168), (144, 155), (144, 139), (130, 126), (111, 126)],
    [(168, 169), (168, 126), (197, 126)],
    [(168, 148), (192, 148)],
]:
    draw.line(points, fill=white, width=width, joint="curve")
image.save(Path(__file__).with_name("pdf-werkbank.ico"), sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])

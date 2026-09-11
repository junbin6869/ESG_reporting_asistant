from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUT_DIR = Path(__file__).resolve().parent
WIDTH, HEIGHT = 2400, 1350

BG = "#F5F7FB"
WHITE = "#FFFFFF"
INK = "#15243A"
MUTED = "#5F6E82"
LINE = "#8795A8"
BORDER = "#D6DEE9"
DIVIDER = "#E4E9F1"
BLUE = "#2F6FED"
BLUE_SOFT = "#EAF1FF"
TEAL = "#149B94"
TEAL_SOFT = "#E7F7F5"
GREEN = "#269A68"
GREEN_SOFT = "#E9F7F0"
AMBER = "#E99A2C"
AMBER_SOFT = "#FFF3DF"
PURPLE = "#7357D9"
PURPLE_SOFT = "#F0ECFF"
RED = "#D85656"
RED_SOFT = "#FDEBEC"
SHADOW = "#DDE3EC"

FONT_DIR = Path("C:/Windows/Fonts")
FONT_REGULAR = FONT_DIR / "segoeui.ttf"
FONT_SEMIBOLD = FONT_DIR / "seguisb.ttf"
FONT_BOLD = FONT_DIR / "segoeuib.ttf"


def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    path = {
        "regular": FONT_REGULAR,
        "semibold": FONT_SEMIBOLD,
        "bold": FONT_BOLD,
    }[weight]
    return ImageFont.truetype(str(path), size=size)


def text_size(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont):
    box = draw.textbbox((0, 0), text, font=face)
    return box[2] - box[0], box[3] - box[1]


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    face: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if text_size(draw, candidate, face)[0] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    face: ImageFont.FreeTypeFont,
    fill: str,
    *,
    align: str = "center",
    valign: str = "center",
    spacing: int = 7,
) -> None:
    x1, y1, x2, y2 = box
    lines = wrap_text(draw, text, face, x2 - x1)
    heights = [text_size(draw, line or " ", face)[1] for line in lines]
    total_height = sum(heights) + spacing * max(0, len(lines) - 1)
    if valign == "top":
        y = y1
    elif valign == "bottom":
        y = y2 - total_height
    else:
        y = y1 + (y2 - y1 - total_height) / 2
    for line, line_height in zip(lines, heights):
        width = text_size(draw, line, face)[0]
        if align == "left":
            x = x1
        elif align == "right":
            x = x2 - width
        else:
            x = x1 + (x2 - x1 - width) / 2
        draw.text((x, y), line, font=face, fill=fill)
        y += line_height + spacing


def draw_wrapped_fit(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    *,
    size: int,
    min_size: int,
    weight: str,
    fill: str,
    align: str = "center",
    valign: str = "center",
    spacing: int = 6,
) -> None:
    x1, y1, x2, y2 = box
    chosen = font(size, weight)
    for candidate_size in range(size, min_size - 1, -1):
        candidate = font(candidate_size, weight)
        lines = wrap_text(draw, text, candidate, x2 - x1)
        heights = [text_size(draw, line or " ", candidate)[1] for line in lines]
        total_height = sum(heights) + spacing * max(0, len(lines) - 1)
        if total_height <= y2 - y1:
            chosen = candidate
            break
    draw_wrapped(
        draw,
        box,
        text,
        chosen,
        fill,
        align=align,
        valign=valign,
        spacing=spacing,
    )


def rounded_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: str = WHITE,
    outline: str = BORDER,
    *,
    radius: int = 28,
    shadow: bool = True,
    width: int = 2,
) -> None:
    x1, y1, x2, y2 = box
    if shadow:
        draw.rounded_rectangle(
            (x1 + 7, y1 + 9, x2 + 7, y2 + 9),
            radius=radius,
            fill=SHADOW,
        )
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=fill,
        outline=outline,
        width=width,
    )


def node(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    body: str = "",
    *,
    fill: str = WHITE,
    outline: str = BORDER,
    accent: str | None = None,
    tag: str | None = None,
    title_size: int = 28,
    body_size: int = 22,
) -> None:
    rounded_panel(draw, box, fill=fill, outline=outline)
    x1, y1, x2, y2 = box
    if accent:
        draw.rounded_rectangle(
            (x1, y1, x1 + 11, y2),
            radius=6,
            fill=accent,
        )
    if tag:
        tag_face = font(17, "bold")
        tag_width = text_size(draw, tag, tag_face)[0] + 28
        draw.rounded_rectangle(
            (x1 + 20, y1 + 16, x1 + 20 + tag_width, y1 + 49),
            radius=16,
            fill=accent or BLUE,
        )
        draw.text(
            (x1 + 34, y1 + 22),
            tag,
            font=tag_face,
            fill=WHITE,
        )
        title_top = y1 + 61
    else:
        title_top = y1 + 20
    if body:
        draw_wrapped_fit(
            draw,
            (x1 + 28, title_top, x2 - 28, title_top + 64),
            title,
            size=title_size,
            min_size=21,
            weight="semibold",
            fill=INK,
        )
        draw_wrapped_fit(
            draw,
            (x1 + 28, title_top + 68, x2 - 28, y2 - 15),
            body,
            size=body_size,
            min_size=16,
            weight="regular",
            fill=MUTED,
            valign="top",
            spacing=5,
        )
    else:
        draw_wrapped_fit(
            draw,
            (x1 + 28, title_top, x2 - 28, y2 - 18),
            title,
            size=title_size,
            min_size=21,
            weight="semibold",
            fill=INK,
        )


def diamond(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    half_width: int,
    half_height: int,
    text: str,
    *,
    fill: str = AMBER_SOFT,
    outline: str = AMBER,
) -> None:
    cx, cy = center
    points = [
        (cx, cy - half_height),
        (cx + half_width, cy),
        (cx, cy + half_height),
        (cx - half_width, cy),
    ]
    shadow_points = [(x + 7, y + 9) for x, y in points]
    draw.polygon(shadow_points, fill=SHADOW)
    draw.polygon(points, fill=fill, outline=outline)
    draw.line(points + [points[0]], fill=outline, width=3, joint="curve")
    draw_wrapped(
        draw,
        (cx - half_width + 25, cy - half_height + 23, cx + half_width - 25, cy + half_height - 23),
        text,
        font(25, "semibold"),
        INK,
    )


def arrow_head(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: str,
    width: int,
) -> None:
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 16 + width
    left = (
        end[0] - size * math.cos(angle - math.pi / 6),
        end[1] - size * math.sin(angle - math.pi / 6),
    )
    right = (
        end[0] - size * math.cos(angle + math.pi / 6),
        end[1] - size * math.sin(angle + math.pi / 6),
    )
    draw.polygon([end, left, right], fill=color)


def dashed_segment(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: str,
    width: int,
    dash: int = 16,
    gap: int = 11,
) -> None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return
    ux, uy = dx / length, dy / length
    distance = 0.0
    while distance < length:
        segment_end = min(distance + dash, length)
        p1 = (start[0] + ux * distance, start[1] + uy * distance)
        p2 = (start[0] + ux * segment_end, start[1] + uy * segment_end)
        draw.line((p1, p2), fill=color, width=width)
        distance += dash + gap


def arrow(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    *,
    color: str = LINE,
    width: int = 5,
    dashed: bool = False,
    label: str | None = None,
    label_at: tuple[int, int] | None = None,
) -> None:
    for start, end in zip(points, points[1:]):
        if dashed:
            dashed_segment(draw, start, end, color, width)
        else:
            draw.line((start, end), fill=color, width=width, joint="curve")
    arrow_head(draw, points[-2], points[-1], color, width)
    if label and label_at:
        face = font(18, "semibold")
        label_w = text_size(draw, label, face)[0] + 30
        x, y = label_at
        draw.rounded_rectangle(
            (x - label_w / 2, y - 17, x + label_w / 2, y + 17),
            radius=17,
            fill=BG,
        )
        draw.text(
            (x - text_size(draw, label, face)[0] / 2, y - 12),
            label,
            font=face,
            fill=MUTED,
        )


def phase_header(
    draw: ImageDraw.ImageDraw,
    x1: int,
    x2: int,
    number: str,
    title: str,
    accent: str,
) -> None:
    draw.rounded_rectangle((x1, 220, x1 + 46, 266), radius=23, fill=accent)
    draw_wrapped(
        draw,
        (x1, 220, x1 + 46, 266),
        number,
        font(20, "bold"),
        WHITE,
    )
    draw.text((x1 + 60, 224), title, font=font(24, "semibold"), fill=INK)
    draw.line((x1, 283, x2, 283), fill=DIVIDER, width=3)


def header(
    draw: ImageDraw.ImageDraw,
    kicker: str,
    title: str,
    subtitle: str,
    accent: str,
) -> None:
    draw.rounded_rectangle((80, 50, 460, 95), radius=22, fill=accent)
    draw_wrapped(
        draw,
        (98, 50, 442, 95),
        kicker,
        font(18, "bold"),
        WHITE,
    )
    draw.text((80, 108), title, font=font(54, "bold"), fill=INK)
    draw.text((80, 174), subtitle, font=font(23), fill=MUTED)


def footer(draw: ImageDraw.ImageDraw, text: str) -> None:
    draw.line((80, 1282, WIDTH - 80, 1282), fill=DIVIDER, width=2)
    draw.text((80, 1300), text, font=font(19), fill=MUTED)


def cylinder(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    body: str,
) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1 + 7, y1 + 9, x2 + 7, y2 + 9), radius=26, fill=SHADOW)
    draw.rectangle((x1, y1 + 20, x2, y2 - 20), fill=PURPLE_SOFT, outline=PURPLE, width=3)
    draw.ellipse((x1, y1, x2, y1 + 42), fill=PURPLE_SOFT, outline=PURPLE, width=3)
    draw.ellipse((x1, y2 - 42, x2, y2), fill=PURPLE_SOFT, outline=PURPLE, width=3)
    draw_wrapped(
        draw,
        (x1 + 20, y1 + 38, x2 - 20, y1 + 82),
        title,
        font(22, "semibold"),
        INK,
    )
    draw_wrapped(
        draw,
        (x1 + 20, y1 + 86, x2 - 20, y2 - 22),
        body,
        font(18),
        MUTED,
        valign="top",
        spacing=4,
    )


def make_invoice_flow() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    header(
        draw,
        "ESG ASSISTANT  •  SYSTEM FLOW 01",
        "Invoice Upload & ESG Classification",
        "From AutoCount e-Invoice data to human-reviewed ESG evidence",
        BLUE,
    )

    phase_header(draw, 80, 420, "1", "Data source", BLUE)
    phase_header(draw, 455, 1270, "2", "Intake & validation", TEAL)
    phase_header(draw, 1310, 1900, "3", "Import & classification", PURPLE)
    phase_header(draw, 1940, 2320, "4", "Human review", GREEN)
    for x in (437, 1290, 1920):
        draw.line((x, 300, x, 1245), fill=DIVIDER, width=2)

    node(
        draw,
        (90, 320, 395, 495),
        "AutoCount e-Invoice Data",
        "Requested JSON / API export",
        fill=BLUE_SOFT,
        outline=BLUE,
        accent=BLUE,
        tag="PROPOSED",
        title_size=27,
        body_size=21,
    )
    node(
        draw,
        (90, 650, 395, 810),
        "Finance / ESG User",
        "Current JSON and PDF file upload",
        accent=TEAL,
        title_size=28,
    )
    node(
        draw,
        (490, 450, 730, 590),
        "Invoice Intake",
        "Accept and route uploaded data",
        fill=TEAL_SOFT,
        outline=TEAL,
        accent=TEAL,
    )
    diamond(draw, (850, 520), 105, 88, "Input\nformat?")
    node(
        draw,
        (995, 300, 1245, 485),
        "JSON Records",
        "Parse, normalize and validate. Invalid items are skipped and reported.",
        fill=BLUE_SOFT,
        outline=BLUE,
        accent=BLUE,
        title_size=27,
        body_size=20,
    )
    node(
        draw,
        (995, 610, 1245, 815),
        "PDF Invoice",
        "Extract text, use OCR if needed, then let the user confirm or correct fields.",
        fill=TEAL_SOFT,
        outline=TEAL,
        accent=TEAL,
        title_size=27,
        body_size=20,
    )

    node(
        draw,
        (1350, 310, 1655, 475),
        "Import & Version",
        "Create, update or keep the invoice version",
        accent=PURPLE,
        title_size=28,
    )
    node(
        draw,
        (1350, 535, 1655, 680),
        "Queue Classification",
        "Background job processing",
        fill=PURPLE_SOFT,
        outline=PURPLE,
        accent=PURPLE,
        title_size=26,
        body_size=20,
    )
    node(
        draw,
        (1350, 745, 1655, 895),
        "Retrieve ESG Evidence",
        "Top guideline evidence chunks",
        fill=PURPLE_SOFT,
        outline=PURPLE,
        accent=PURPLE,
        title_size=26,
        body_size=20,
    )
    cylinder(
        draw,
        (1695, 730, 1875, 905),
        "Bursa ESG",
        "Local guideline vector database",
    )
    node(
        draw,
        (1350, 965, 1875, 1135),
        "AI ESG Classification",
        "Environmental • Social • Governance • Non-ESG\nConfidence, reason and evidence • Retry up to 3 attempts",
        fill=PURPLE_SOFT,
        outline=PURPLE,
        accent=PURPLE,
        title_size=30,
        body_size=21,
    )

    node(
        draw,
        (1970, 655, 2295, 805),
        "Pending Result",
        "Category, confidence and evidence available",
        fill=AMBER_SOFT,
        outline=AMBER,
        accent=AMBER,
        title_size=28,
        body_size=20,
    )
    node(
        draw,
        (1970, 855, 2295, 1015),
        "Human Review",
        "Confirm or correct category, reason and evidence",
        fill=GREEN_SOFT,
        outline=GREEN,
        accent=GREEN,
        title_size=28,
        body_size=20,
    )
    node(
        draw,
        (1970, 1065, 2295, 1225),
        "Reviewed Invoice\nReady for reporting",
        "",
        fill=GREEN_SOFT,
        outline=GREEN,
        accent=GREEN,
        tag="READY",
        title_size=29,
        body_size=20,
    )

    arrow(
        draw,
        [(395, 408), (445, 408), (445, 490), (490, 490)],
        color=BLUE,
        dashed=True,
        label="Proposed data handoff",
        label_at=(465, 370),
    )
    arrow(
        draw,
        [(395, 730), (445, 730), (445, 550), (490, 550)],
        color=TEAL,
    )
    arrow(draw, [(730, 520), (745, 520)], color=TEAL)
    arrow(
        draw,
        [(955, 475), (975, 475), (975, 392), (995, 392)],
        color=BLUE,
        label="JSON",
        label_at=(968, 440),
    )
    arrow(
        draw,
        [(850, 608), (850, 712), (995, 712)],
        color=TEAL,
        label="PDF",
        label_at=(895, 690),
    )
    arrow(
        draw,
        [(1245, 392), (1295, 392), (1295, 355), (1350, 355)],
        color=BLUE,
    )
    arrow(
        draw,
        [(1245, 712), (1295, 712), (1295, 405), (1350, 405)],
        color=TEAL,
    )
    arrow(draw, [(1502, 475), (1502, 535)], color=PURPLE)
    arrow(draw, [(1502, 680), (1502, 745)], color=PURPLE)
    arrow(draw, [(1695, 815), (1655, 815)], color=PURPLE, dashed=True)
    arrow(draw, [(1502, 895), (1502, 965)], color=PURPLE)
    arrow(
        draw,
        [(1875, 1050), (1925, 1050), (1925, 730), (1970, 730)],
        color=PURPLE,
    )
    arrow(draw, [(2132, 805), (2132, 855)], color=GREEN)
    arrow(draw, [(2132, 1015), (2132, 1065)], color=GREEN)

    footer(
        draw,
        "Solid line = implemented workflow   •   Dashed line = proposed AutoCount connection   •   Only Reviewed invoices feed report generation",
    )
    return image


def make_report_flow() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    header(
        draw,
        "ESG ASSISTANT  •  SYSTEM FLOW 02",
        "Agentic ESG Report Generation",
        "Reviewed invoices only • evidence-led research • draft output with traceability",
        GREEN,
    )

    phase_header(draw, 80, 500, "1", "Report setup", BLUE)
    phase_header(draw, 535, 1470, "2", "Evidence preparation", PURPLE)
    phase_header(draw, 1510, 1880, "3", "Draft & validation", AMBER)
    phase_header(draw, 1920, 2320, "4", "Save & deliver", GREEN)
    for x in (517, 1490, 1900):
        draw.line((x, 300, x, 1245), fill=DIVIDER, width=2)

    node(
        draw,
        (115, 330, 465, 485),
        "Select Title & Year",
        "Define the reporting period",
        fill=BLUE_SOFT,
        outline=BLUE,
        accent=BLUE,
        title_size=30,
        body_size=21,
    )
    node(
        draw,
        (115, 620, 465, 775),
        "Start Report Agent",
        "Create a durable background run",
        fill=BLUE_SOFT,
        outline=BLUE,
        accent=BLUE,
        title_size=29,
        body_size=21,
    )

    node(
        draw,
        (585, 310, 865, 465),
        "Load Reviewed Invoices",
        "Filter by the selected reporting period",
        accent=PURPLE,
        title_size=27,
        body_size=20,
    )
    node(
        draw,
        (925, 310, 1215, 465),
        "Build ESG Summary",
        "Counts, spend, evidence coverage and references",
        accent=PURPLE,
        title_size=27,
        body_size=20,
    )
    diamond(draw, (1370, 390), 110, 94, "Evidence\nsufficient?")

    cylinder(
        draw,
        (610, 680, 875, 855),
        "Bursa ESG Guidelines",
        "Local Chroma vector database",
    )
    node(
        draw,
        (960, 680, 1285, 845),
        "Focused Guideline Search",
        "Retrieve and deduplicate supporting evidence",
        fill=PURPLE_SOFT,
        outline=PURPLE,
        accent=PURPLE,
        title_size=27,
        body_size=20,
    )
    draw.rounded_rectangle((975, 880, 1270, 930), radius=25, fill=PURPLE_SOFT)
    draw_wrapped(
        draw,
        (990, 880, 1255, 930),
        "Up to 8 searches • 16 reasoning loops",
        font(18, "semibold"),
        PURPLE,
    )

    node(
        draw,
        (1515, 690, 1790, 835),
        "Evidence Limits",
        "Disclose remaining evidence limitations",
        fill=AMBER_SOFT,
        outline=AMBER,
        accent=AMBER,
        title_size=26,
        body_size=20,
    )
    node(
        draw,
        (1515, 310, 1690, 470),
        "Generate ESG Draft",
        "Complete evidence-led report",
        fill=AMBER_SOFT,
        outline=AMBER,
        accent=AMBER,
        title_size=28,
        body_size=20,
    )
    diamond(draw, (1810, 390), 95, 85, "Structure\nvalid?")
    node(
        draw,
        (1550, 945, 1845, 1110),
        "Run Failed",
        "Mark step failed and display the error",
        fill=RED_SOFT,
        outline=RED,
        accent=RED,
        title_size=27,
        body_size=19,
    )

    node(
        draw,
        (1960, 470, 2285, 635),
        "Save Draft + Version 1",
        "Persist report and complete the agent run",
        fill=GREEN_SOFT,
        outline=GREEN,
        accent=GREEN,
        title_size=28,
        body_size=20,
    )
    node(
        draw,
        (1960, 740, 2285, 915),
        "Report Preview & History",
        "Review the saved report in the application",
        fill=GREEN_SOFT,
        outline=GREEN,
        accent=GREEN,
        title_size=27,
        body_size=20,
    )
    node(
        draw,
        (1960, 1000, 2285, 1170),
        "Download PDF\nPresentation-ready output",
        "",
        fill=GREEN_SOFT,
        outline=GREEN,
        accent=GREEN,
        tag="OUTPUT",
        title_size=28,
        body_size=20,
    )

    arrow(draw, [(290, 485), (290, 620)], color=BLUE)
    arrow(
        draw,
        [(465, 697), (540, 697), (540, 387), (585, 387)],
        color=BLUE,
    )
    arrow(draw, [(865, 387), (925, 387)], color=PURPLE)
    arrow(draw, [(1215, 387), (1260, 387)], color=PURPLE)
    arrow(
        draw,
        [(1480, 390), (1515, 390)],
        color=AMBER,
        label="Yes",
        label_at=(1500, 350),
    )
    arrow(
        draw,
        [(1370, 484), (1370, 760), (1285, 760)],
        color=PURPLE,
        label="No • search available",
        label_at=(1370, 620),
    )
    arrow(draw, [(875, 767), (960, 767)], color=PURPLE, dashed=True)
    arrow(
        draw,
        [(1120, 680), (1120, 575), (1370, 575), (1370, 484)],
        color=PURPLE,
        label="Add evidence",
        label_at=(1235, 550),
    )
    arrow(
        draw,
        [(1480, 430), (1498, 430), (1498, 762), (1515, 762)],
        color=AMBER,
        label="No • budget exhausted",
        label_at=(1580, 640),
    )
    arrow(
        draw,
        [(1652, 690), (1652, 470)],
        color=AMBER,
    )
    arrow(draw, [(1690, 390), (1715, 390)], color=AMBER)
    arrow(
        draw,
        [(1905, 390), (1935, 390), (1935, 550), (1960, 550)],
        color=GREEN,
        label="Yes",
        label_at=(1935, 435),
    )
    arrow(
        draw,
        [(1810, 475), (1810, 965)],
        color=RED,
        label="No",
        label_at=(1845, 790),
    )
    arrow(draw, [(2122, 635), (2122, 740)], color=GREEN)
    arrow(draw, [(2122, 915), (2122, 1000)], color=GREEN)

    rounded_panel(
        draw,
        (585, 1030, 1385, 1155),
        fill=WHITE,
        outline=DIVIDER,
        shadow=False,
        radius=24,
    )
    draw.text((620, 1057), "LIVE AGENT STATE", font=font(18, "bold"), fill=BLUE)
    draw.text(
        (620, 1093),
        "Frontend polls every second until the run is completed or failed.",
        font=font(22),
        fill=MUTED,
    )
    arrow(
        draw,
        [(465, 720), (545, 720), (545, 1092), (585, 1092)],
        color=BLUE,
        dashed=True,
    )

    footer(
        draw,
        "The system saves a Draft report; required disclosures and evidence markers are validated before delivery",
    )
    return image


def main() -> None:
    invoice = make_invoice_flow()
    report = make_report_flow()
    invoice.save(OUT_DIR / "invoice-upload-flow.png", format="PNG", optimize=True)
    report.save(OUT_DIR / "report-generation-flow.png", format="PNG", optimize=True)
    print(f"Created {OUT_DIR / 'invoice-upload-flow.png'}")
    print(f"Created {OUT_DIR / 'report-generation-flow.png'}")


if __name__ == "__main__":
    main()

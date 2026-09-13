#!/usr/bin/env python3
"""Render assets/example_report.png for the README.

Every number below is fabricated. It illustrates the shape and magnitude of
what `biome_audit.py report` surfaces without publishing any maintainer's
real entity-graph, sync, or content-stream data.
Regenerate after editing EXAMPLE_SECTIONS: python3 assets/generate_example_report.py
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUTPUT_PATH = Path(__file__).parent / "example_report.png"

WIDTH = 1100
PADDING_X = 56
PADDING_TOP = 48
PADDING_BOTTOM = 48
LINE_HEIGHT = 34
SECTION_GAP = 26
BULLET_GAP = 14
BULLET_INDENT = 30

BACKGROUND = "#16171b"
CAPTION_COLOR = "#8a8d94"
HEADER_COLOR = "#f2f2f3"
BODY_COLOR = "#d7d8db"
BOLD_COLOR = "#ffffff"
BULLET_COLOR = "#6b6f78"

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
FONT_REGULAR = ImageFont.truetype(str(FONT_DIR / "Arial.ttf"), 19)
FONT_BOLD = ImageFont.truetype(str(FONT_DIR / "Arial Bold.ttf"), 19)
FONT_HEADER = ImageFont.truetype(str(FONT_DIR / "Arial Bold.ttf"), 24)
FONT_CAPTION = ImageFont.truetype(str(FONT_DIR / "Arial.ttf"), 16)

EXAMPLE_SECTIONS = [
    (
        "Entity graph",
        [
            (
                "**312 people** resolved in the on-device identity graph from "
                "Mail/Messages/Contacts/Calendar cross-referencing -- 198 with "
                "a full name, 61 with a phone number, 4 with an email address."
            ),
            (
                "The machine's owner gets their own row in the exact same "
                "table as everyone else, flagged only by an isCurrentUser bit."
            ),
            (
                "**6 named locations** in the graph -- not necessarily home or "
                "work; these can come from calendar or browsing interests "
                "just as easily as places you've physically been."
            ),
            (
                "**512 software/executable entities** ever seen running on "
                "the machine."
            ),
        ],
    ),
    (
        "Sync",
        [
            (
                "**3 devices** share this Biome graph through Apple's actual "
                "CloudKit infrastructure (CKRecord/CKZone/CKAtom tables) -- "
                "not just local peer-to-peer like the older knowledgeC sync."
            ),
            (
                "**68,400+ CloudKit sync atoms** accumulated so far -- live, "
                "continuously-updating replication, not a one-time snapshot."
            ),
            (
                "Both remote peers last synced **under a minute** before this "
                "report ran."
            ),
        ],
    ),
    (
        "Content-bearing streams",
        [
            (
                "**2 of 30** populated streams are flagged content-bearing -- "
                "they can hold verbatim text (Notes, Mail, Messages, Safari "
                "page text, Siri queries), not just usage timestamps."
            ),
            (
                "One held live data at report time: a real note's full text, "
                "referenced by its actual on-device UUID."
            ),
            (
                "This tool deliberately never decodes or prints that text -- "
                "only that it exists, and how large the live portion is."
            ),
        ],
    ),
]

_BOLD_SPLIT = re.compile(r"(\*\*.*?\*\*)")


def parse_bold_segments(text: str) -> list[tuple[str, bool]]:
    segments = []
    for part in _BOLD_SPLIT.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            segments.append((part[2:-2], True))
        else:
            segments.append((part, False))
    return segments


def word_tokens(text: str) -> list[tuple[str, bool]]:
    tokens = []
    for content, bold in parse_bold_segments(text):
        for word in content.split(" "):
            if word:
                tokens.append((word, bold))
    return tokens


def wrap_tokens(
    tokens: list[tuple[str, bool]], draw: ImageDraw.ImageDraw, max_width: int
) -> list[list[tuple[str, bool]]]:
    lines: list[list[tuple[str, bool]]] = [[]]
    x = 0
    space_width = draw.textlength(" ", font=FONT_REGULAR)
    for word, bold in tokens:
        font = FONT_BOLD if bold else FONT_REGULAR
        word_width = draw.textlength(word, font=font)
        if lines[-1] and x + word_width > max_width:
            lines.append([])
            x = 0
        lines[-1].append((word, bold))
        x += word_width + space_width
    return lines


def draw_bullet(
    draw: ImageDraw.ImageDraw, x: int, y: int, text: str, max_width: int
) -> int:
    draw.text((x, y), "•", font=FONT_REGULAR, fill=BULLET_COLOR)
    text_x = x + BULLET_INDENT
    tokens = word_tokens(text)
    lines = wrap_tokens(tokens, draw, max_width - BULLET_INDENT)
    space_width = draw.textlength(" ", font=FONT_REGULAR)
    for line in lines:
        cursor_x = text_x
        for word, bold in line:
            font = FONT_BOLD if bold else FONT_REGULAR
            color = BOLD_COLOR if bold else BODY_COLOR
            draw.text((cursor_x, y), word, font=font, fill=color)
            cursor_x += draw.textlength(word, font=font) + space_width
        y += LINE_HEIGHT
    return y


def render() -> None:
    scratch = Image.new("RGB", (10, 10))
    scratch_draw = ImageDraw.Draw(scratch)
    max_text_width = WIDTH - PADDING_X * 2

    y = PADDING_TOP + 26
    for title, bullets in EXAMPLE_SECTIONS:
        y += 22 + SECTION_GAP
        for bullet in bullets:
            lines = wrap_tokens(
                word_tokens(bullet), scratch_draw, max_text_width - BULLET_INDENT
            )
            y += LINE_HEIGHT * len(lines) + BULLET_GAP
    height = y + PADDING_BOTTOM

    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    y = PADDING_TOP
    draw.text(
        (PADDING_X, y),
        "EXAMPLE FINDINGS -- SYNTHETIC DATA, NOT FROM ANY REAL DEVICE",
        font=FONT_CAPTION,
        fill=CAPTION_COLOR,
    )
    y += 26 + SECTION_GAP

    for title, bullets in EXAMPLE_SECTIONS:
        draw.text((PADDING_X, y), title, font=FONT_HEADER, fill=HEADER_COLOR)
        y += 22 + SECTION_GAP
        for bullet in bullets:
            y = draw_bullet(draw, PADDING_X, y, bullet, max_text_width)
            y += BULLET_GAP

    image.save(OUTPUT_PATH)
    print(f"wrote {OUTPUT_PATH} ({WIDTH}x{height})")


if __name__ == "__main__":
    render()

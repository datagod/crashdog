"""CrashDog banner rendering — gtop-style block art with per-line blue gradient."""

from __future__ import annotations

import shutil
import subprocess
from typing import Iterable

RST = "\033[0m"
BOLD = "\033[1m"

# Royal blue → dark navy (16 shades; low green keeps it blue, not cyan)
BANNER_BLUE_GRADIENT = [
    "#1E45FF",
    "#1C41F2",
    "#1B3EE5",
    "#1A3BD9",
    "#1838CC",
    "#1735C0",
    "#1632B3",
    "#142FA6",
    "#132B9A",
    "#12288D",
    "#102581",
    "#0F2274",
    "#0E1F67",
    "#0C1C5B",
    "#0B194E",
    "#0A1642",
]

GRADIENT_START = (0x1E, 0x45, 0xFF)
GRADIENT_END = (0x0A, 0x16, 0x42)

# Filled block glyphs in toilet mono12 (and gtop █)
SOLID_GLYPHS = frozenset("█▄▀▐▌■▓▒░")


def _hex_to_ansi(hex_color: str) -> str:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return ""
    red = int(value[0:2], 16)
    green = int(value[2:4], 16)
    blue = int(value[4:6], 16)
    return f"\033[38;2;{red};{green};{blue}m"


def _gray_ansi(level: int) -> str:
    level = max(0, min(255, level))
    return f"\033[38;2;{level};{level};{level}m"


def _gradient_hex(line_count: int) -> list[str]:
    palette = BANNER_BLUE_GRADIENT
    if line_count <= len(palette):
        if line_count == 1:
            return [palette[0]]
        indices = [
            round(index * (len(palette) - 1) / (line_count - 1))
            for index in range(line_count)
        ]
        return [palette[index] for index in indices]
    out: list[str] = []
    for index in range(line_count):
        ratio = index / max(line_count - 1, 1)
        red = int(GRADIENT_START[0] + (GRADIENT_END[0] - GRADIENT_START[0]) * ratio)
        green = int(GRADIENT_START[1] + (GRADIENT_END[1] - GRADIENT_START[1]) * ratio)
        blue = int(GRADIENT_START[2] + (GRADIENT_END[2] - GRADIENT_START[2]) * ratio)
        out.append(f"#{red:02X}{green:02X}{blue:02X}")
    return out


def _toilet_lines(text: str = "CrashDog") -> list[str] | None:
    if not shutil.which("toilet"):
        return None
    try:
        result = subprocess.run(
            ["toilet", "-f", "mono12", text],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return [line.rstrip() for line in result.stdout.splitlines() if line.strip()]


def _banner_src() -> list[tuple[str, str]]:
    lines = _toilet_lines()
    if not lines:
        fallback = [
            "  ____                     ____            ",
            " / ___| __ _ _ __ ___  ___|  _ \\  ___   __ ",
            "| |   / _` | '__/ _ \\/ _ \\ | | |/ _ \\ / _|",
            "| |__| (_| | | |  __/ (_) | |_| | (_) | (_|",
            " \\____\\__,_|_|  \\___|\\___/|____/ \\___/ \\__|",
        ]
        colors = _gradient_hex(len(fallback))
        return list(zip(colors, fallback, strict=False))
    colors = _gradient_hex(len(lines))
    return list(zip(colors, lines, strict=False))


def _render_line(hex_fg: str, art: str, line_index: int) -> str:
    """Render one banner line gtop-style: solids in blue, outlines in gray."""
    fg = _hex_to_ansi(hex_fg)
    gray = _gray_ansi(120 - line_index * 12)
    out: list[str] = []
    current = ""

    def emit(color: str, text: str) -> None:
        nonlocal current
        if not text:
            return
        if color != current:
            out.append(color)
            current = color
        out.append(text)

    for char in art:
        if char == " ":
            emit(RST, " ")
            current = ""
        elif char in SOLID_GLYPHS:
            emit(fg, char)
        else:
            emit(gray, char)

    emit(RST, "")
    return "".join(out)


def subtitle_ansi() -> str:
    """Mid-gradient blue for subtitles and secondary text."""
    return _hex_to_ansi("#1632B3")


def banner_text(use_color: bool = True) -> str:
    src = _banner_src()
    if not use_color:
        return "\n".join(line for _, line in src)

    rendered = [f"{BOLD}{_render_line(hex_fg, art, index)}{RST}" for index, (hex_fg, art) in enumerate(src)]
    return "\n".join(rendered)
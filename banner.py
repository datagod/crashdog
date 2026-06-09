"""CrashDog banner rendering — gtop-style block art with per-line red gradient."""

from __future__ import annotations

import shutil
import subprocess
from typing import Iterable

RST = "\033[0m"
BOLD = "\033[1m"

# gtop Banner_src red gradient (#E62525 bright → darker)
GTOP_RED_GRADIENT = [
    "#E62525",
    "#CD2121",
    "#B31D1D",
    "#9A1919",
    "#801414",
    "#6E1212",
    "#5C1010",
    "#4A0E0E",
]

# Filled block glyphs in toilet mono12 (and gtop █)
SOLID_GLYPHS = frozenset("█▄▀▐▌■▓▒░")


def _truecolor_to_256(red: int, green: int, blue: int) -> int:
    if red == green == blue:
        return 232 + round(red / 10) if red > 7 else round(red / 10)
    return (
        16
        + 36 * round(red / 51)
        + 6 * round(green / 51)
        + round(blue / 51)
    )


def _hex_to_ansi(hex_color: str) -> str:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return ""
    red = int(value[0:2], 16)
    green = int(value[2:4], 16)
    blue = int(value[4:6], 16)
    code = _truecolor_to_256(red, green, blue)
    return f"\033[38;5;{code}m"


def _gray_ansi(level: int) -> str:
    level = max(0, min(255, level))
    code = _truecolor_to_256(level, level, level)
    return f"\033[38;5;{code}m"


def _gradient_hex(line_count: int) -> list[str]:
    if line_count <= len(GTOP_RED_GRADIENT):
        return GTOP_RED_GRADIENT[:line_count]
    out: list[str] = []
    for index in range(line_count):
        ratio = index / max(line_count - 1, 1)
        start = (0xE6, 0x25, 0x25)
        end = (0x4A, 0x0E, 0x0E)
        red = int(start[0] + (end[0] - start[0]) * ratio)
        green = int(start[1] + (end[1] - start[1]) * ratio)
        blue = int(start[2] + (end[2] - start[2]) * ratio)
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
        return [
            ("#E62525", "  ____                     ____            "),
            ("#CD2121", " / ___| __ _ _ __ ___  ___|  _ \\  ___   __ "),
            ("#B31D1D", "| |   / _` | '__/ _ \\/ _ \\ | | |/ _ \\ / _|"),
            ("#9A1919", "| |__| (_| | | |  __/ (_) | |_| | (_) | (_|"),
            ("#801414", " \\____\\__,_|_|  \\___|\\___/|____/ \\___/ \\__|"),
        ]
    colors = _gradient_hex(len(lines))
    return list(zip(colors, lines, strict=False))


def _render_line(hex_fg: str, art: str, line_index: int) -> str:
    """Render one banner line gtop-style: solids in red, outlines in gray."""
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


def banner_text(use_color: bool = True) -> str:
    src = _banner_src()
    if not use_color:
        return "\n".join(line for _, line in src)

    rendered = [f"{BOLD}{_render_line(hex_fg, art, index)}{RST}" for index, (hex_fg, art) in enumerate(src)]
    return "\n".join(rendered)
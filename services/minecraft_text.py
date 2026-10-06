"""Minecraft legacy/hex and JSON chat formatting, kept separate from plain fallback text."""

import re
from dataclasses import dataclass, replace

COLORS = {
    "0": "#000000",
    "1": "#0000AA",
    "2": "#00AA00",
    "3": "#00AAAA",
    "4": "#AA0000",
    "5": "#AA00AA",
    "6": "#FFAA00",
    "7": "#AAAAAA",
    "8": "#555555",
    "9": "#5555FF",
    "a": "#55FF55",
    "b": "#55FFFF",
    "c": "#FF5555",
    "d": "#FF55FF",
    "e": "#FFFF55",
    "f": "#FFFFFF",
}
NAMED_COLORS = dict(
    zip(
        (
            "black",
            "dark_blue",
            "dark_green",
            "dark_aqua",
            "dark_red",
            "dark_purple",
            "gold",
            "gray",
            "dark_gray",
            "blue",
            "green",
            "aqua",
            "red",
            "light_purple",
            "yellow",
            "white",
        ),
        COLORS.values(),
    )
)


@dataclass(frozen=True)
class TextStyle:
    color: str = "#E2ECE5"
    bold: bool = False
    italic: bool = False
    underlined: bool = False
    strikethrough: bool = False


@dataclass(frozen=True)
class TextRun:
    text: str
    style: TextStyle = TextStyle()


def _legacy(text: str, initial: TextStyle) -> list[TextRun]:
    style, buffer, runs, index = initial, [], [], 0

    def flush():
        if buffer:
            runs.append(TextRun("".join(buffer), style))
            buffer.clear()

    while index < len(text):
        if text[index] == "§" and index + 1 < len(text):
            code = text[index + 1].lower()
            hex_match = re.match(r"§[xX]((?:§[0-9a-fA-F]){6})|§#([0-9a-fA-F]{6})", text[index:])
            if hex_match:
                flush()
                color = hex_match[1].replace("§", "") if hex_match[1] else hex_match[2]
                style = TextStyle(color="#" + color.upper())
                index += len(hex_match[0])
                continue
            if code in COLORS or code in "rlomnk":
                flush()
                if code in COLORS:
                    style = TextStyle(color=COLORS[code])
                elif code == "r":
                    style = TextStyle()
                elif code != "k":
                    style = replace(
                        style,
                        **{
                            "l": {"bold": True},
                            "o": {"italic": True},
                            "n": {"underlined": True},
                            "m": {"strikethrough": True},
                        }[code],
                    )
                index += 2
                continue
        buffer.append(text[index])
        index += 1
    flush()
    return runs


def motd_runs(value, initial: TextStyle = TextStyle()) -> list[TextRun]:
    """Parse strings, lists, and inherited JSON chat components without discarding colors."""
    if isinstance(value, str):
        return _legacy(value.replace("\r\n", "\n").replace("\r", "\n"), initial)
    if isinstance(value, list):
        return [run for component in value for run in motd_runs(component, initial)]
    if not isinstance(value, dict):
        return [] if value is None else [TextRun(str(value), initial)]
    style = initial
    color = value.get("color")
    if color == "reset":
        style = TextStyle()
    elif isinstance(color, str):
        color = NAMED_COLORS.get(color, color)
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            style = replace(style, color=color.upper())
    for key in ("bold", "italic", "underlined", "strikethrough"):
        if isinstance(value.get(key), bool):
            style = replace(style, **{key: value[key]})
    runs = motd_runs(value.get("text", value.get("fallback", value.get("translate", ""))), style)
    for component in value.get("extra", []) or []:
        runs.extend(motd_runs(component, style))
    return runs

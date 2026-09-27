#!/usr/bin/env python3
"""RDS character set codec (IEC 62106 / EN 50067, Annex E, table E.1).

The on-air RDS/RBDS text encoding is a fixed 8-bit repertoire that is NOT
Latin-1: e.g. 0x24 is '¤' while '$' lives at 0xAB, and all accented letters
sit at different positions than in Latin-1/UTF-8.

This module accepts arbitrary Unicode (e.g. UTF-8 from JSON configs) and maps
it to the RDS repertoire, with graceful fallbacks:
  1. direct table lookup
  2. diacritic-stripped base letter (NFKD decomposition)
  3. space
"""

from __future__ import annotations

import unicodedata
from typing import Iterable

# EN 50067:1998, Annex E (table E.1), 256 entries, code -> Unicode char.
_DECODE_TABLE: tuple = (
    # 0x00 - 0x0F
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    "\n",
    " ",
    " ",
    "\r",
    " ",
    " ",
    # 0x10 - 0x1F
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    " ",
    "\u00ad",
    # 0x20 - 0x2F
    " ",
    "!",
    '"',
    "#",
    "¤",
    "%",
    "&",
    "'",
    "(",
    ")",
    "*",
    "+",
    ",",
    "-",
    ".",
    "/",
    # 0x30 - 0x3F
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    ":",
    ";",
    "<",
    "=",
    ">",
    "?",
    # 0x40 - 0x4F
    "@",
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "I",
    "J",
    "K",
    "L",
    "M",
    "N",
    "O",
    # 0x50 - 0x5F
    "P",
    "Q",
    "R",
    "S",
    "T",
    "U",
    "V",
    "W",
    "X",
    "Y",
    "Z",
    "[",
    "\\",
    "]",
    "―",
    "_",
    # 0x60 - 0x6F
    "‖",
    "a",
    "b",
    "c",
    "d",
    "e",
    "f",
    "g",
    "h",
    "i",
    "j",
    "k",
    "l",
    "m",
    "n",
    "o",
    # 0x70 - 0x7F
    "p",
    "q",
    "r",
    "s",
    "t",
    "u",
    "v",
    "w",
    "x",
    "y",
    "z",
    "{",
    "|",
    "}",
    "¯",
    " ",
    # 0x80 - 0x8F
    "á",
    "à",
    "é",
    "è",
    "í",
    "ì",
    "ó",
    "ò",
    "ú",
    "ù",
    "Ñ",
    "Ç",
    "Ş",
    "β",
    "¡",
    "Ĳ",
    # 0x90 - 0x9F
    "â",
    "ä",
    "ê",
    "ë",
    "î",
    "ï",
    "ô",
    "ö",
    "û",
    "ü",
    "ñ",
    "ç",
    "ş",
    "ǧ",
    "ı",
    "ĳ",
    # 0xA0 - 0xAF
    "ª",
    "α",
    "©",
    "‰",
    "Ǧ",
    "ě",
    "ň",
    "ő",
    "π",
    "€",
    "£",
    "$",
    "←",
    "↑",
    "→",
    "↓",
    # 0xB0 - 0xBF
    "º",
    "¹",
    "²",
    "³",
    "±",
    "İ",
    "ń",
    "ű",
    "µ",
    "¿",
    "÷",
    "°",
    "¼",
    "½",
    "¾",
    "§",
    # 0xC0 - 0xCF
    "Á",
    "À",
    "É",
    "È",
    "Í",
    "Ì",
    "Ó",
    "Ò",
    "Ú",
    "Ù",
    "Ř",
    "Č",
    "Š",
    "Ž",
    "Ð",
    "Ŀ",
    # 0xD0 - 0xDF
    "Â",
    "Ä",
    "Ê",
    "Ë",
    "Î",
    "Ï",
    "Ô",
    "Ö",
    "Û",
    "Ü",
    "ř",
    "č",
    "š",
    "ž",
    "đ",
    "ŀ",
    # 0xE0 - 0xEF
    "Ã",
    "Å",
    "Æ",
    "Œ",
    "ŷ",
    "Ý",
    "Õ",
    "Ø",
    "Þ",
    "Ŋ",
    "Ŕ",
    "Ć",
    "Ś",
    "Ź",
    "Ŧ",
    "ð",
    # 0xF0 - 0xFF
    "ã",
    "å",
    "æ",
    "œ",
    "ŵ",
    "ý",
    "õ",
    "ø",
    "þ",
    "ŋ",
    "ŕ",
    "ć",
    "ś",
    "ź",
    "ŧ",
    " ",
)

_ENCODE_MAP = {}
for _code, _char in enumerate(_DECODE_TABLE):
    _ENCODE_MAP.setdefault(_char, _code)
# Space occurs many times in the table; the canonical space is 0x20.
_ENCODE_MAP[" "] = 0x20


def encode(text: str) -> list[int]:
    """Encode a Unicode string to RDS character codes (Annex E, table E.1)."""
    out: list[int] = []
    # NFC first so combining sequences (e.g. e + U+0301) become precomposed
    # characters (é) that exist in the RDS table.
    for ch in unicodedata.normalize("NFC", text):
        code = _ENCODE_MAP.get(ch)
        if code is None:
            # Fall back to the diacritic-stripped base letter, e.g. 'ė' -> 'e'
            stripped = "".join(
                c
                for c in unicodedata.normalize("NFKD", ch)
                if not unicodedata.combining(c)
            )
            if stripped:
                code = _ENCODE_MAP.get(stripped, _ENCODE_MAP.get(stripped[0]))
            if code is None:
                code = 0x20  # space
        out.append(code)
    return out


def decode(data: bytes | bytearray | Iterable[int]) -> str:
    """Decode RDS character codes to a Unicode string (for display/logging)."""
    return "".join(_DECODE_TABLE[b & 0xFF] for b in data)

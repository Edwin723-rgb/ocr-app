"""OCR Tesseract con reconstrucción por líneas (layout) y filtro de ruido de sellos."""
from __future__ import annotations

import re
from typing import Callable, Optional

import pytesseract
from PIL import Image
from pytesseract import Output

_MIN_CONF = 28
_SPANISH_VOWEL = re.compile(r"[aeiouáéíóú]", re.I)
_WORD = re.compile(r"\b[\wáéíóúÁÉÍÓÚñÑ]{4,}\b")


def _spanish_word_ratio(text: str) -> float:
    words = re.findall(r"\b[\wáéíóúÁÉÍÓÚñÑüÜ]+\b", text)
    if not words:
        return 0.0
    spanish = sum(1 for w in words if _SPANISH_VOWEL.search(w) or len(w) >= 5)
    return spanish / len(words)


def looks_like_stamp_noise(line: str) -> bool:
    """Heurística ligera para líneas de sellos/firmas ilegibles (sin depender de main)."""
    plain = (line or "").strip()
    if len(plain) < 4:
        return True
    if plain.startswith("#"):
        return False
    if re.search(
        r"(?i)\b(RFC|CURP|CLAVE|SECCI[oó]N|TRIBUNAL|DEMANDA|CONTRATO|ART[ií]CULO|CL[aá]USULA)\b",
        plain,
    ):
        return False
    tokens = re.findall(r"\S+", plain)
    if not tokens:
        return True
    tiny = sum(1 for t in tokens if len(t) <= 3)
    if len(tokens) >= 5 and tiny / len(tokens) > 0.55 and _spanish_word_ratio(plain) < 0.35:
        return True
    if len(tokens) >= 6 and _spanish_word_ratio(plain) < 0.2:
        return True
    if re.search(r"(\b[A-Za-z]{1,2}\b[\s|]{1,3}){5,}", plain) and _spanish_word_ratio(plain) < 0.32:
        return True
    if len(plain) >= 14 and _spanish_word_ratio(plain) < 0.12:
        letters = sum(1 for c in plain if c.isalpha())
        if letters >= 8:
            return True
    return False


def extract_text_with_layout(
    image: Image.Image,
    lang: str,
    config: str,
    *,
    min_conf: int = _MIN_CONF,
    line_filter: Optional[Callable[[str], bool]] = None,
) -> str:
    """Reconstruye texto línea por línea según bloques de Tesseract."""
    reject = line_filter or looks_like_stamp_noise
    try:
        data = pytesseract.image_to_data(
            image, lang=lang, config=config, output_type=Output.DICT
        )
    except Exception:
        return ""

    lines: dict[tuple[int, int, int], list[tuple[int, str, float]]] = {}
    n = len(data.get("text") or [])
    for i in range(n):
        token = (data["text"][i] or "").strip()
        if not token:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if 0 <= conf < min_conf:
            continue
        key = (
            int(data["block_num"][i]),
            int(data["par_num"][i]),
            int(data["line_num"][i]),
        )
        left = int(data["left"][i])
        lines.setdefault(key, []).append((left, token, conf))

    output: list[str] = []
    for key in sorted(lines.keys()):
        parts = sorted(lines[key], key=lambda item: item[0])
        line_text = " ".join(token for _, token, _ in parts).strip()
        if not line_text or reject(line_text):
            continue
        output.append(line_text)
    return "\n".join(output)

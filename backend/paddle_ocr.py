"""OCR PaddleOCR con instancia lazy y reconstrucción por líneas."""
from __future__ import annotations

import os
import threading
from typing import Callable, Optional

import numpy as np
from PIL import Image

import tesseract_layout

OCR_PADDLE_LANG = (os.getenv("OCR_PADDLE_LANG") or "es").strip().lower()
OCR_PADDLE_DEVICE = (os.getenv("OCR_PADDLE_DEVICE") or "cpu").strip().lower()
OCR_PADDLE_DET_LIMIT = max(640, int(os.getenv("OCR_PADDLE_DET_LIMIT", "4000")))

_MIN_SCORE = 0.28
_LINE_Y_TOLERANCE = 18

_instance = None
_instance_lang: Optional[str] = None
_lock = threading.RLock()
_available: Optional[bool] = None


def paddle_ocr_available() -> bool:
    global _available
    if _available is not None:
        return _available
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        _available = True
    except Exception:
        _available = False
    return _available


def _resolve_paddle_lang(lang: str | None) -> str:
    code = (lang or OCR_PADDLE_LANG or "es").strip().lower()
    if code in ("spa", "spanish"):
        return "es"
    if code in ("eng", "english"):
        return "en"
    return code.split("_", 1)[0]


def _get_paddle_ocr(lang: str):
    global _instance, _instance_lang
    resolved = _resolve_paddle_lang(lang)
    with _lock:
        if _instance is not None and _instance_lang == resolved:
            return _instance
        from paddleocr import PaddleOCR

        kwargs: dict = {
            "lang": resolved,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "text_det_limit_side_len": OCR_PADDLE_DET_LIMIT,
        }
        if OCR_PADDLE_DEVICE and OCR_PADDLE_DEVICE != "cpu":
            kwargs["device"] = OCR_PADDLE_DEVICE
        _instance = PaddleOCR(**kwargs)
        _instance_lang = resolved
        return _instance


def _image_to_array(image: Image.Image) -> np.ndarray:
    if image.mode != "RGB":
        image = image.convert("RGB")
    return np.asarray(image)


def _box_to_xyxy(box) -> list[float]:
    if box is None:
        return [0.0, 0.0, 0.0, 0.0]
    arr = np.asarray(box, dtype=float)
    if arr.size == 0:
        return [0.0, 0.0, 0.0, 0.0]
    if arr.ndim == 2 and arr.shape[0] >= 2 and arr.shape[1] >= 2:
        xs = arr[:, 0]
        ys = arr[:, 1]
        return [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]
    flat = arr.reshape(-1)
    if len(flat) >= 4:
        return [float(flat[0]), float(flat[1]), float(flat[2]), float(flat[3])]
    return [0.0, 0.0, 0.0, 0.0]


def _box_top_left(box) -> tuple[float, float]:
    if box is None:
        return 0.0, 0.0
    arr = np.asarray(box)
    if arr.size == 0:
        return 0.0, 0.0
    if arr.ndim == 2:
        ys = arr[:, 1]
        xs = arr[:, 0]
        return float(xs.min()), float(ys.min())
    if arr.ndim == 1 and len(arr) >= 4:
        return float(arr[0]), float(arr[1])
    return 0.0, 0.0


def _group_paddle_lines(
    texts: list[str],
    scores: list[float],
    boxes,
    *,
    line_filter: Optional[Callable[[str], bool]] = None,
) -> tuple[str, float]:
    reject = line_filter or tesseract_layout.looks_like_stamp_noise
    items: list[tuple[float, float, str, float]] = []
    box_list = list(boxes or [])
    for idx, text in enumerate(texts or []):
        token = (text or "").strip()
        if not token:
            continue
        try:
            conf = float(scores[idx]) if idx < len(scores or []) else 0.0
        except (TypeError, ValueError, IndexError):
            conf = 0.0
        if conf < _MIN_SCORE:
            continue
        box = box_list[idx] if idx < len(box_list) else None
        left, top = _box_top_left(box)
        items.append((top, left, token, conf))

    if not items:
        return "", 0.0

    items.sort(key=lambda item: (item[0], item[1]))
    lines: list[list[tuple[float, str, float]]] = []
    for top, left, token, conf in items:
        placed = False
        for group in lines:
            ref_top = group[0][0]
            if abs(top - ref_top) <= _LINE_Y_TOLERANCE:
                group.append((left, token, conf))
                placed = True
                break
        if not placed:
            lines.append([(left, token, conf)])

    output: list[str] = []
    confidences: list[float] = []
    for group in lines:
        group.sort(key=lambda item: item[0])
        line_text = " ".join(token for _, token, _ in group).strip()
        if not line_text or reject(line_text):
            continue
        output.append(line_text)
        confidences.extend(conf for _, _, conf in group)

    avg_conf = (sum(confidences) / len(confidences) * 100.0) if confidences else 0.0
    return "\n".join(output), avg_conf


def extract_text_with_paddle(
    image: Image.Image,
    lang: str = "es",
    *,
    line_filter: Optional[Callable[[str], bool]] = None,
) -> tuple[str, float]:
    """Ejecuta PaddleOCR y devuelve (texto plano, confianza media 0-100)."""
    if not paddle_ocr_available():
        return "", 0.0
    try:
        with _lock:
            engine = _get_paddle_ocr(lang)
            result = engine.predict(_image_to_array(image))
    except Exception:
        return "", 0.0

    if not result:
        return "", 0.0

    page = result[0] if isinstance(result, list) else result
    if not isinstance(page, dict):
        return "", 0.0

    return _group_paddle_lines(
        page.get("rec_texts") or [],
        page.get("rec_scores") or [],
        page.get("rec_boxes") or page.get("rec_polys"),
        line_filter=line_filter,
    )


def extract_page_layout(
    image: Image.Image,
    lang: str = "es",
    *,
    line_filter: Optional[Callable[[str], bool]] = None,
) -> dict:
    """Ejecuta PaddleOCR y devuelve texto, bloques con cajas y tamaño de imagen."""
    empty = {"text": "", "confidence": 0.0, "blocks": [], "image_size": [0, 0]}
    if not paddle_ocr_available():
        return empty

    width, height = image.size
    image_size = [width, height]
    reject = line_filter or tesseract_layout.looks_like_stamp_noise

    try:
        with _lock:
            engine = _get_paddle_ocr(lang)
            result = engine.predict(_image_to_array(image))
    except Exception:
        return {**empty, "image_size": image_size}

    if not result:
        return {**empty, "image_size": image_size}

    page = result[0] if isinstance(result, list) else result
    if not isinstance(page, dict):
        return {**empty, "image_size": image_size}

    texts = page.get("rec_texts") or []
    scores = page.get("rec_scores") or []
    boxes = page.get("rec_boxes") or page.get("rec_polys") or []
    box_list = list(boxes)

    blocks: list[dict] = []
    confidences: list[float] = []
    for idx, text in enumerate(texts):
        token = (text or "").strip()
        if not token:
            continue
        try:
            score = float(scores[idx]) if idx < len(scores) else 0.0
        except (TypeError, ValueError, IndexError):
            score = 0.0
        if score < _MIN_SCORE:
            continue
        if reject(token):
            continue
        box = box_list[idx] if idx < len(box_list) else None
        xyxy = _box_to_xyxy(box)
        blocks.append({
            "id": len(blocks),
            "text": token,
            "score": round(score, 4),
            "label": "text",
            "box": [round(v, 1) for v in xyxy],
        })
        confidences.append(score)

    plain_text, avg_conf = _group_paddle_lines(
        texts,
        scores,
        boxes,
        line_filter=line_filter,
    )
    return {
        "text": plain_text,
        "confidence": avg_conf,
        "blocks": blocks,
        "image_size": image_size,
    }

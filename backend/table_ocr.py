"""OCR de tablas con rejilla (hojas de cálculo escaneadas o exportadas a PDF)."""
from __future__ import annotations

import re
from typing import Any, Optional

import numpy as np
import pytesseract
from PIL import Image, ImageOps
from pytesseract import Output

_TABLE_HEADER_HINTS = re.compile(
    r"(?i)\b(fecha|nombre|concepto|pago|importe|monto|cantidad|descripci[oó]n|precio|total)\b"
)
_DATE_CELL = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_MONEY_CELL = re.compile(r"\$\s*[\d,]+(?:\.\d{2})?")


def _normalize_cell(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\n", " ")).strip()
    return text.replace("|", "/").strip()


def table_to_markdown(table: list[list[Any]]) -> str:
    rows: list[list[str]] = []
    for row in table or []:
        normalized = [_normalize_cell(cell) for cell in row]
        if any(normalized):
            rows.append(normalized)
    if len(rows) < 2:
        return ""

    col_count = max(len(row) for row in rows)
    padded = [row + [""] * (col_count - len(row)) for row in rows]
    header = padded[0]
    if not any(header):
        header = [f"Columna {idx + 1}" for idx in range(col_count)]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * col_count) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return "\n".join(lines)


def _upscale(image: Image.Image, min_edge: int = 2800) -> Image.Image:
    edge = max(image.size) if image.size else 0
    if edge >= min_edge:
        return image
    scale = min_edge / float(edge)
    return image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )


def _cluster_positions(positions: list[int], *, min_gap: int) -> list[int]:
    if not positions:
        return []
    clusters: list[list[int]] = [[positions[0]]]
    for pos in positions[1:]:
        if pos - clusters[-1][-1] <= min_gap:
            clusters[-1].append(pos)
        else:
            clusters.append([pos])
    return [sum(cluster) // len(cluster) for cluster in clusters]


def _longest_run(values: np.ndarray, *, min_length: int) -> int:
    best = 0
    current = 0
    for value in values:
        if value:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best if best >= min_length else 0


def _whiten_grid_lines(image: Image.Image, h_bounds: list[int], v_bounds: list[int], *, thickness: int = 3) -> Image.Image:
    """Elimina la rejilla impresa para que el OCR no lea bordes como texto."""
    if not h_bounds and not v_bounds:
        return image
    gray = image.convert("L")
    arr = np.array(gray)
    h, w = arr.shape
    half = max(1, thickness // 2)
    for y in h_bounds:
        y0, y1 = max(0, y - half), min(h, y + half + 1)
        arr[y0:y1, :] = 255
    for x in v_bounds:
        x0, x1 = max(0, x - half), min(w, x + half + 1)
        arr[:, x0:x1] = 255
    return Image.fromarray(arr)


def detect_grid_lines(image: Image.Image) -> tuple[list[int], list[int]]:
    """Detecta líneas horizontales/verticales de una rejilla tipo Excel."""
    gray = np.array(image.convert("L"))
    h, w = gray.shape
    if h < 40 or w < 40:
        return [], []

    dark = gray < 175

    # Filas: línea horizontal = mucha tinta a lo ancho (no solo texto).
    row_density = dark.mean(axis=1)
    row_thresh = max(0.22, float(np.quantile(row_density, 0.92)))
    h_lines = [int(y) for y in np.where(row_density >= row_thresh)[0]]

    # Columnas: trazo vertical largo (bordes de tabla).
    min_v_span = max(int(h * 0.72), 70)
    v_lines: list[int] = []
    for x in range(w):
        if _longest_run(dark[:, x], min_length=min_v_span):
            v_lines.append(x)

    row_tol = max(4, int(h * 0.002))
    col_tol = max(4, int(w * 0.002))
    h_bounds = _cluster_positions(h_lines, min_gap=row_tol)
    v_bounds = _cluster_positions(v_lines, min_gap=col_tol)

    if len(h_bounds) < 3 or len(v_bounds) < 3:
        return [], []

    def _dedupe(bounds: list[int], min_dist: int) -> list[int]:
        out: list[int] = []
        for b in bounds:
            if not out or b - out[-1] >= min_dist:
                out.append(b)
        return out

    def _adaptive_row_dedupe(bounds: list[int]) -> list[int]:
        if len(bounds) <= 2:
            return bounds
        gaps = sorted(bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1))
        median_gap = gaps[len(gaps) // 2]
        min_dist = max(14, int(median_gap * 0.45))
        return _dedupe(bounds, min_dist)

    def _adaptive_col_dedupe(bounds: list[int], page_width: int) -> list[int]:
        if len(bounds) <= 2:
            return bounds
        gaps = sorted(bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1))
        median_gap = gaps[len(gaps) // 2] if gaps else int(page_width * 0.08)
        min_dist = max(28, int(median_gap * 0.55))
        return _dedupe(bounds, min_dist)

    h_bounds = _adaptive_row_dedupe(h_bounds)
    v_bounds = _adaptive_col_dedupe(v_bounds, w)
    v_bounds = _merge_narrow_columns(v_bounds, w)
    v_bounds = _cap_column_count(v_bounds, w, max_cols=9)
    return h_bounds, v_bounds


def _cap_column_count(bounds: list[int], page_width: int, *, max_cols: int = 9) -> list[int]:
    """Evita columnas fantasma cuando la rejilla se detecta demasiado fina."""
    while len(bounds) > max_cols + 1 and len(bounds) >= 3:
        # Fusionar la columna más estrecha con su vecina.
        narrow_idx = 0
        narrow_width = page_width
        for idx in range(len(bounds) - 1):
            width = bounds[idx + 1] - bounds[idx]
            if width < narrow_width:
                narrow_width = width
                narrow_idx = idx
        if narrow_width >= max(40, int(page_width * 0.06)):
            break
        remove_at = narrow_idx + 1
        if remove_at >= len(bounds) - 1:
            bounds.pop(remove_at - 1)
        else:
            bounds.pop(remove_at)
    return bounds


def _merge_narrow_columns(bounds: list[int], page_width: int) -> list[int]:
    """Fusiona columnas demasiado estrechas (artefactos de la rejilla)."""
    if len(bounds) < 3:
        return bounds
    result = list(bounds)
    min_width = max(28, int(page_width * 0.045))
    changed = True
    while changed and len(result) >= 3:
        changed = False
        narrow_idx = -1
        narrow_width = page_width
        for idx in range(len(result) - 1):
            width = result[idx + 1] - result[idx]
            if width < narrow_width:
                narrow_width = width
                narrow_idx = idx
        if narrow_idx >= 0 and narrow_width < min_width:
            del result[narrow_idx + 1]
            changed = True
    return result


def image_has_table_grid(image: Image.Image) -> bool:
    h_bounds, v_bounds = detect_grid_lines(_upscale(image))
    return len(h_bounds) >= 4 and len(v_bounds) >= 4


_TABLE_HEADER_KEYS = (
    "FECHA", "NOMBRE", "CONCEPTO", "PAGO", "IMPORTE", "PAGADO", "MONTO", "REAL", "ROBADO",
)


def _header_hits(text: str) -> int:
    upper = (text or "").upper()
    return sum(1 for key in _TABLE_HEADER_KEYS if key in upper)


def ocr_spreadsheet_by_words(image: Image.Image, lang: str = "spa") -> str:
    """Detecta columnas por encabezados FECHA/NOMBRE/... y agrupa palabras por filas."""
    scaled = _upscale(image)
    words = _extract_words(scaled, lang)
    if len(words) < 24:
        return ""

    rows = _cluster_rows(words)
    header_idx = -1
    header_words: list[dict[str, float]] = []

    for idx, row in enumerate(rows[:10]):
        joined = " ".join(w["text"] for w in row)
        if _header_hits(joined) >= 3:
            header_idx = idx
            header_words = sorted(row, key=lambda w: w["left"])
            break

    if header_idx < 0 or len(header_words) < 3:
        return ""

    # Unir fragmentos OCR del encabezado en columnas reconocibles.
    merged_headers: list[tuple[float, float, str]] = []
    for word in header_words:
        token = word["text"].upper()
        if merged_headers and _header_hits(token) == 0 and len(token) <= 3:
            prev = merged_headers[-1]
            merged_headers[-1] = (prev[0], word["right"], f"{prev[2]} {word['text']}".strip())
        else:
            merged_headers.append((word["left"], word["right"], word["text"]))

    if len(merged_headers) < 3:
        return ""

    centers = [(left + right) / 2 for left, right, _ in merged_headers]
    bounds = [0.0]
    bounds.extend((centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1))
    bounds.append(float(scaled.width))
    col_count = len(merged_headers)
    header_cells = [_normalize_cell(name) for _, _, name in merged_headers]

    body: list[list[str]] = []
    for row in rows[header_idx + 1 :]:
        cells = [""] * col_count
        for word in row:
            cx = word["left"] + word["width"] / 2
            col = col_count - 1
            for idx in range(col_count):
                if bounds[idx] <= cx < bounds[idx + 1]:
                    col = idx
                    break
            cells[col] = f"{cells[col]} {word['text']}".strip() if cells[col] else word["text"]
        cells = [_normalize_cell(c) for c in cells]
        if not any(cells):
            continue
        if _header_hits(" ".join(cells)) >= 3:
            continue
        body.append(cells)

    if len(body) < 1:
        return ""

    table = [header_cells] + body
    table = _trim_empty_table_edges(table)
    return table_to_markdown(table)


def page_looks_like_spreadsheet(image: Image.Image, lang: str = "spa") -> bool:
    """Heurística extra: encabezados FECHA/NOMBRE/CONCEPTO aunque la rejilla sea difícil."""
    import scan_pages

    return scan_pages.page_looks_like_spreadsheet(image, lang=lang)


def _ocr_cell(cell_image: Image.Image, lang: str) -> str:
    if cell_image.width < 8 or cell_image.height < 8:
        return ""
    prepared = ImageOps.autocontrast(cell_image.convert("L"), cutoff=1)
    prepared = prepared.point(lambda v: 255 if v > 210 else (0 if v < 120 else v))
    text = pytesseract.image_to_string(
        prepared,
        lang=lang,
        config="--oem 1 --psm 7 -c preserve_interword_spaces=1",
    )
    return _normalize_cell(text)


def _assign_word_to_column(word: dict[str, float], bounds: list[int]) -> int:
    cx = word["left"] + word["width"] / 2
    col = len(bounds) - 2
    for idx in range(len(bounds) - 1):
        if bounds[idx] <= cx < bounds[idx + 1]:
            col = idx
            break
    return max(0, col)


def ocr_row_grid_table(image: Image.Image, lang: str = "spa") -> str:
    """OCR por filas completas: más fiable que celda a celda en hojas escaneadas."""
    scaled = _upscale(image)
    h_bounds, v_bounds = detect_grid_lines(scaled)
    if len(h_bounds) < 3 or len(v_bounds) < 3:
        return ""

    cleaned = _whiten_grid_lines(scaled, h_bounds, v_bounds)
    col_count = len(v_bounds) - 1
    table: list[list[str]] = []
    row_pad = 3

    for row_idx in range(len(h_bounds) - 1):
        y0, y1 = h_bounds[row_idx], h_bounds[row_idx + 1]
        if y1 - y0 < 14:
            continue
        y_crop0 = min(cleaned.height - 1, y0 + row_pad)
        y_crop1 = max(y_crop0 + 1, y1 - row_pad)
        row_img = cleaned.crop((0, y_crop0, cleaned.width, y_crop1))
        words = _extract_words(row_img, lang)
        if not words:
            continue
        cells = [""] * col_count
        for word in words:
            col = _assign_word_to_column(word, v_bounds)
            if col >= col_count:
                col = col_count - 1
            cells[col] = f"{cells[col]} {word['text']}".strip() if cells[col] else word["text"]
        cells = [_normalize_cell(c) for c in cells]
        if any(cells):
            table.append(cells)

    table = _trim_empty_table_edges(table)
    table = _merge_header_row(table)
    return table_to_markdown(table)


def _merge_header_row(table: list[list[str]]) -> list[list[str]]:
    """Une filas partidas del encabezado (FECHA / NOMBRE / … en varias líneas)."""
    if len(table) < 2:
        return table
    header_text = " ".join(" ".join(row) for row in table[:3]).upper()
    if not _TABLE_HEADER_HINTS.search(header_text):
        return table

    merged_header: list[str] = []
    for row in table[:3]:
        for cell in row:
            cell = _normalize_cell(cell)
            if cell and cell not in merged_header:
                merged_header.append(cell)
    if len(merged_header) < 3:
        return table

    body = table[1:]
    while body and sum(1 for c in body[0] if c.strip()) <= 2:
        body.pop(0)
    target_cols = len(merged_header)
    normalized_body: list[list[str]] = []
    for row in body:
        if len(row) < target_cols:
            row = row + [""] * (target_cols - len(row))
        elif len(row) > target_cols:
            extras = row[target_cols - 1 :]
            row = row[: target_cols - 1] + [" ".join(extras).strip()]
        normalized_body.append(row)
    return [merged_header] + normalized_body


def ocr_grid_table(image: Image.Image, lang: str = "spa") -> str:
    """OCR celda a celda usando la rejilla detectada."""
    scaled = _upscale(image)
    h_bounds, v_bounds = detect_grid_lines(scaled)
    if len(h_bounds) < 3 or len(v_bounds) < 3:
        return ""

    cleaned = _whiten_grid_lines(scaled, h_bounds, v_bounds)
    table: list[list[str]] = []
    pad = 3
    for row_idx in range(len(h_bounds) - 1):
        y0, y1 = h_bounds[row_idx], h_bounds[row_idx + 1]
        if y1 - y0 < 14:
            continue
        row_cells: list[str] = []
        for col_idx in range(len(v_bounds) - 1):
            x0, x1 = v_bounds[col_idx], v_bounds[col_idx + 1]
            if x1 - x0 < 14:
                row_cells.append("")
                continue
            cell = cleaned.crop((
                min(cleaned.width - 1, x0 + pad),
                min(cleaned.height - 1, y0 + pad),
                max(x0 + pad + 1, x1 - pad),
                max(y0 + pad + 1, y1 - pad),
            ))
            row_cells.append(_ocr_cell(cell, lang))
        if any(row_cells):
            table.append(row_cells)

    table = _trim_empty_table_edges(table)
    table = _merge_header_row(table)
    return table_to_markdown(table)


def _trim_empty_table_edges(table: list[list[str]]) -> list[list[str]]:
    if not table:
        return table
    col_count = max(len(row) for row in table)
    padded = [row + [""] * (col_count - len(row)) for row in table]

    while padded and not any(padded[0]):
        padded.pop(0)
    while padded and not any(padded[-1]):
        padded.pop()

    if not padded:
        return []

    empty_cols = set()
    for col in range(col_count):
        if not any(row[col].strip() for row in padded):
            empty_cols.add(col)
    if empty_cols:
        padded = [[cell for idx, cell in enumerate(row) if idx not in empty_cols] for row in padded]
    return padded


def _extract_words(image: Image.Image, lang: str) -> list[dict[str, float]]:
    data = pytesseract.image_to_data(
        image,
        lang=lang,
        config="--oem 1 --psm 6 -c preserve_interword_spaces=1",
        output_type=Output.DICT,
    )
    words: list[dict[str, float]] = []
    for i, raw in enumerate(data.get("text", [])):
        text = (raw or "").strip()
        try:
            conf = int(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not text or conf < 15:
            continue
        left = float(data["left"][i])
        top = float(data["top"][i])
        width = float(data["width"][i])
        height = float(data["height"][i])
        words.append({
            "text": text,
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "right": left + width,
            "cy": top + height / 2,
        })
    return words


def _cluster_rows(words: list[dict[str, float]]) -> list[list[dict[str, float]]]:
    if not words:
        return []
    heights = [w["height"] for w in words if w["height"] > 0]
    y_tol = max(10.0, (sum(heights) / len(heights)) * 0.55 if heights else 14.0)
    ordered = sorted(words, key=lambda w: (w["cy"], w["left"]))
    rows: list[list[dict[str, float]]] = [[ordered[0]]]
    for word in ordered[1:]:
        avg_cy = sum(item["cy"] for item in rows[-1]) / len(rows[-1])
        if abs(word["cy"] - avg_cy) <= y_tol:
            rows[-1].append(word)
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda w: w["left"])
    return rows


def _infer_column_bounds(rows: list[list[dict[str, float]]], width: int) -> list[float]:
    gap_mids: list[tuple[float, float]] = []
    for row in rows[: min(8, len(rows))]:
        for idx in range(len(row) - 1):
            gap = row[idx + 1]["left"] - row[idx]["right"]
            if gap >= 14:
                gap_mids.append((gap, (row[idx]["right"] + row[idx + 1]["left"]) / 2))

    if gap_mids:
        gap_mids.sort(key=lambda item: item[0], reverse=True)
        mids = sorted({round(mid, 1) for _, mid in gap_mids[:12]})
        bounds = [0.0]
        for mid in mids:
            if mid - bounds[-1] >= 28:
                bounds.append(mid)
        bounds.append(float(width))
        if len(bounds) >= 5:
            return bounds

    centers = sorted(w["left"] + w["width"] / 2 for row in rows for w in row)
    if len(centers) < 8:
        return [0.0, float(width)]
    quantiles = np.quantile(centers, np.linspace(0, 1, min(8, max(4, len(centers) // 6))))
    bounds = [0.0]
    for q in quantiles[1:-1]:
        if q - bounds[-1] >= 30:
            bounds.append(float(q))
    bounds.append(float(width))
    return bounds


def ocr_cluster_table(image: Image.Image, lang: str = "spa") -> str:
    """Fallback: agrupa palabras OCR en filas/columnas por posición."""
    scaled = _upscale(image)
    words = _extract_words(scaled, lang)
    rows = _cluster_rows(words)
    if len(rows) < 3:
        return ""

    bounds = _infer_column_bounds(rows, scaled.width)
    col_count = max(1, len(bounds) - 1)
    table: list[list[str]] = []
    for row in rows:
        cells = [""] * col_count
        for word in row:
            cx = word["left"] + word["width"] / 2
            col = col_count - 1
            for idx in range(col_count):
                if bounds[idx] <= cx < bounds[idx + 1]:
                    col = idx
                    break
            cells[col] = f"{cells[col]} {word['text']}".strip() if cells[col] else word["text"]
        table.append(cells)
    return table_to_markdown(_trim_empty_table_edges(table))


def _cell_looks_garbage(cell: str) -> bool:
    text = _normalize_cell(cell)
    if not text:
        return False
    if len(text) <= 2 and not text.isdigit():
        return True
    alnum = sum(1 for c in text if c.isalnum() or c in "$.,/-")
    if alnum / max(len(text), 1) < 0.45:
        return True
    if re.fullmatch(r"[\W_]{1,4}", text):
        return True
    return False


def _looks_like_payroll_table(markdown: str) -> bool:
    """Tabla de gastos/nómina (FECHA, NOMBRE, CONCEPTO, IMPORTE), no formularios con cuadros."""
    upper = (markdown or "").upper()
    keys = sum(1 for key in ("FECHA", "NOMBRE", "CONCEPTO", "IMPORTE", "PAGO") if key in upper)
    if keys < 3:
        return False
    if "FECHA" not in upper or "NOMBRE" not in upper or "CONCEPTO" not in upper:
        return False
    if not (_DATE_CELL.search(markdown) or _MONEY_CELL.search(markdown)):
        if "IMPORTE" not in upper and "PAGO" not in upper:
            return False
    lines = [ln for ln in markdown.splitlines() if ln.strip().startswith("|") and "---" not in ln]
    if not lines:
        return False
    cols = lines[0].count("|") - 1
    return 3 <= cols <= 10


def _table_content_quality(markdown: str) -> float:
    lines = [line for line in markdown.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return 0.0
    body_lines = [line for line in lines if "---" not in line]
    cells: list[str] = []
    for line in body_lines:
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        cells.extend(parts)
    non_empty = [c for c in cells if c]
    if not non_empty:
        return 0.0
    empty_ratio = 1.0 - (len(non_empty) / max(len(cells), 1))
    col_count = lines[0].count("|") - 1
    if col_count > 8 and empty_ratio > 0.35:
        return 0.0
    good = sum(1 for c in non_empty if not _cell_looks_garbage(c) and len(c) >= 2)
    quality = good / len(non_empty)
    if _DATE_CELL.search(markdown):
        quality += 0.15
    if _MONEY_CELL.search(markdown):
        quality += 0.15
    if _TABLE_HEADER_HINTS.search(markdown):
        quality += 0.1
    return min(1.0, quality)


def score_table_markdown(markdown: str) -> float:
    if not markdown or "|" not in markdown:
        return 0.0
    lines = [line for line in markdown.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return 0.0
    cols = lines[0].count("|") - 1
    if cols > 10 and not _looks_like_payroll_table(markdown):
        return 0.0
    content_q = _table_content_quality(markdown)
    if content_q < 0.28:
        return 0.0
    body = "\n".join(lines)
    score = min(len(lines), 80) * 0.8 * content_q
    if _TABLE_HEADER_HINTS.search(body):
        score += 24 * content_q
    if _DATE_CELL.search(body):
        score += 18
    if _MONEY_CELL.search(body):
        score += 22
    cols = lines[0].count("|") - 1
    if 4 <= cols <= 9:
        score += 12
    elif cols > 10:
        score -= 18
    if cols > 8 and not _looks_like_payroll_table(markdown):
        return min(score, 12.0)
    return score


def ocr_image_to_table_markdown(image: Image.Image, lang: str = "spa") -> str:
    """Intenta reconstruir una tabla Markdown fiel al PDF escaneado."""
    if image is None:
        return ""

    candidates: list[tuple[str, float]] = []
    for fn in (ocr_spreadsheet_by_words, ocr_row_grid_table, ocr_grid_table, ocr_cluster_table):
        try:
            md = fn(image, lang=lang)
        except Exception:
            md = ""
        if md:
            candidates.append((md, score_table_markdown(md)))

    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[1], reverse=True)
    best_md, best_score = candidates[0]
    if best_score < 28:
        return ""
    if not _looks_like_payroll_table(best_md):
        lines = [ln for ln in best_md.splitlines() if ln.strip().startswith("|")]
        if lines and lines[0].count("|") - 1 > 8:
            return ""
    return best_md

import json
import os
import re
import uuid
import shutil
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from PIL import Image, ImageFilter, ImageOps, ImageEnhance, ImageStat
from dotenv import load_dotenv

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pytesseract
    from pytesseract import Output
except ImportError:
    pytesseract = None
    Output = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None

load_dotenv()


def is_cloud_runtime() -> bool:
    return bool(os.getenv("VERCEL"))


def has_tesseract() -> bool:
    return pytesseract is not None and not is_cloud_runtime()

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY              = os.getenv("OCR_API_KEY", "sci-ocr-2024")
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY", "")
def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_data_dir(name: str) -> Path:
    if os.getenv("VERCEL"):
        return Path("/tmp") / f"ocr-{name}"
    return Path(name)


UPLOAD_DIR           = Path(os.getenv("OCR_UPLOAD_DIR", str(_resolve_data_dir("uploads"))))
OUTPUT_DIR           = Path(os.getenv("OCR_OUTPUT_DIR", str(_resolve_data_dir("outputs"))))
MAX_UPLOAD_MB        = int(os.getenv("MAX_UPLOAD_MB", "4" if os.getenv("VERCEL") else "300"))
MAX_UPLOAD_BYTES     = MAX_UPLOAD_MB * 1024 * 1024
MARKITDOWN_MIN_CHARS = 100
SOFT_PAGE_WARNING    = 30
SOFT_SIZE_WARNING_MB = 20
OCR_RENDER_DPI       = int(os.getenv("OCR_RENDER_DPI", "300"))
OCR_UPSCALE_MIN_EDGE = int(os.getenv("OCR_UPSCALE_MIN_EDGE", "1600"))
OCR_CHUNK_PAGES      = 12
OCR_MIN_PAGE_CHARS   = 24
OCR_BALANCED_GOOD_SCORE = float(os.getenv("OCR_BALANCED_GOOD_SCORE", "40"))
OCR_TESSERACT_WORKERS = max(1, min(8, int(os.getenv("OCR_TESSERACT_WORKERS", "4"))))
OCR_GEMINI_WORKERS    = max(1, min(4, int(os.getenv("OCR_GEMINI_WORKERS", "2"))))
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


def normalize_ocr_mode(mode: Optional[str]) -> str:
    m = (mode or "balanced").strip().lower()
    if m in ("fast", "rapid", "speed", "rapido"):
        return "fast"
    if m in ("quality", "high", "best", "max", "calidad"):
        return "quality"
    return "balanced"


def ocr_dpi_for_mode(mode: str) -> int:
    if mode == "fast":
        return int(os.getenv("OCR_DPI_FAST", "200"))
    if mode == "balanced":
        return int(os.getenv("OCR_DPI_BALANCED", "240"))
    return int(os.getenv("OCR_DPI_QUALITY", str(OCR_RENDER_DPI)))


def upscale_min_edge_for_mode(mode: str) -> int:
    if mode == "fast":
        return int(os.getenv("OCR_UPSCALE_FAST", "1200"))
    if mode == "balanced":
        return int(os.getenv("OCR_UPSCALE_BALANCED", "1400"))
    return OCR_UPSCALE_MIN_EDGE

_default_tesseract = (
    shutil.which("tesseract")
    if os.getenv("VERCEL")
    else r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
TESSERACT_CMD = os.getenv("TESSERACT_CMD", _default_tesseract or "tesseract")
if pytesseract and TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

if GEMINI_API_KEY and genai is not None:
    genai.configure(api_key=GEMINI_API_KEY)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="SCI OCR API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    if request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"detail": f"Archivo demasiado grande. Límite: {MAX_UPLOAD_MB} MB."},
                status_code=413
            )
    return await call_next(request)

def _frontend_dir() -> Path:
    root = _project_root()
    for candidate in (root / "frontend", Path(__file__).resolve().parent.parent / "frontend"):
        if candidate.exists():
            return candidate
    return root / "frontend"


frontend_path = _frontend_dir()
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")

# ── Auth ──────────────────────────────────────────────────────────────────────
def check_api_key(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="API Key inválida")

# ── Normalización y salida estructurada ───────────────────────────────────────
def count_pdf_pages(file_path: Path) -> int:
    if file_path.suffix.lower() != ".pdf":
        return 1
    if fitz is None:
        return 1
    try:
        with fitz.open(str(file_path)) as doc:
            return max(len(doc), 1)
    except Exception:
        return 1

def normalize_markdown(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\f", "\n\n---\n\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()

def normalize_whitespace(text: str) -> str:
    text = (text or "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def minimum_pdf_text_chars(file_path: Path) -> int:
    pages = count_pdf_pages(file_path)
    return min(MARKITDOWN_MIN_CHARS, max(36, pages * 18))

def strip_markdown_syntax(markdown: str) -> str:
    text = normalize_markdown(markdown)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\|?(?:\s*:?-+:?\s*\|)+\s*$", "", text, flags=re.MULTILINE)
    text = text.replace("|", " ")
    text = text.replace("**", "").replace("__", "")
    text = text.replace("*", "").replace("_", "")
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"\n\s*---\s*\n", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def split_markdown_pages(markdown: str) -> list[dict[str, Any]]:
    normalized = normalize_markdown(markdown)
    if not normalized:
        return []
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*---\s*\n", normalized) if chunk.strip()]
    if len(chunks) <= 1:
        return []
    return [
        {
            "index": index,
            "markdown": chunk,
            "text": strip_markdown_syntax(chunk),
        }
        for index, chunk in enumerate(chunks)
    ]

def join_pages_markdown(pages: list[dict[str, Any]]) -> str:
    return "\n\n---\n\n".join(page["markdown"].strip() for page in pages if page.get("markdown", "").strip()).strip()

def join_pages_text(pages: list[dict[str, Any]]) -> str:
    if not pages:
        return ""
    if len(pages) == 1:
        return pages[0]["text"].strip()

    blocks = []
    total = len(pages)
    for index, page in enumerate(pages, 1):
        body = page.get("text", "").strip()
        if body:
            blocks.append(f"--- Página {index}/{total} ---\n{body}")
    return "\n\n".join(blocks).strip()

def append_warning(warnings: list[str], warning: str):
    if warning and warning not in warnings:
        warnings.append(warning)

def text_quality_score(text: str) -> float:
    plain = strip_markdown_syntax(text)
    if not plain:
        return 0.0

    total = len(plain)
    useful = len(re.findall(r"[a-zA-ZáéíóúÁÉÍÓÚñÑüÜ0-9\s,\.;:\!\?\(\)\"\'\-\/%$#@&]", plain))
    useful_ratio = useful / total if total else 0.0
    words = len(re.findall(r"\b[\wáéíóúÁÉÍÓÚñÑüÜ]+\b", plain))
    lines = len([line for line in plain.split("\n") if line.strip()])

    return useful_ratio * 55 + min(total, 3000) / 60 + min(words, 400) / 10 + min(lines, 80) / 4

def normalize_table_cell(value: Any) -> str:
    cell = normalize_whitespace(str(value or ""))
    return cell.replace("\n", " ").replace("|", "/").strip()

def table_to_markdown(table: list[list[Any]]) -> str:
    rows = []
    for row in table or []:
        normalized_row = [normalize_table_cell(cell) for cell in row]
        if any(cell for cell in normalized_row):
            rows.append(normalized_row)

    if len(rows) < 2:
        return ""

    col_count = max(len(row) for row in rows)
    padded = [row + [""] * (col_count - len(row)) for row in rows]
    header = padded[0]
    if not any(header):
        header = [f"Columna {idx + 1}" for idx in range(col_count)]

    separator = ["---"] * col_count
    body = padded[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)

def extract_pdf_tables_markdown(file_path: Path) -> dict[int, list[str]]:
    table_map: dict[int, list[str]] = {}
    try:
        import pdfplumber
    except ImportError:
        return table_map
    try:
        with pdfplumber.open(str(file_path)) as pdf:
            for page_index, page in enumerate(pdf.pages):
                extracted = page.extract_tables({
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 3,
                    "intersection_tolerance": 3,
                }) or []
                page_tables = [table_to_markdown(table) for table in extracted]
                page_tables = [table for table in page_tables if table]
                if page_tables:
                    table_map[page_index] = page_tables
    except Exception:
        return {}
    return table_map


def iter_pdf_page_chunks(pdf_path: Path, dpi: int, chunk_size: int = OCR_CHUNK_PAGES):
    if fitz is None:
        raise RuntimeError("PyMuPDF no está disponible en este entorno.")
    doc = fitz.open(str(pdf_path))
    try:
        total = len(doc)
        for chunk_start in range(0, total, chunk_size):
            chunk_end = min(chunk_start + chunk_size, total)
            batch: list[tuple[int, Image.Image]] = []
            for page_index in range(chunk_start, chunk_end):
                page = doc.load_page(page_index)
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                batch.append((page_index, image))
            yield batch
    finally:
        doc.close()


def image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

def build_result(
    method: str,
    markdown: str,
    *,
    pages: Optional[list[dict[str, Any]]] = None,
    file_path: Optional[Path] = None,
    warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    normalized_pages = []
    for index, page in enumerate(pages or []):
        page_markdown = normalize_markdown(page.get("markdown") or page.get("text") or "")
        if not page_markdown:
            continue
        page_text = strip_markdown_syntax(page.get("text") or page_markdown)
        normalized_pages.append({
            "index": index,
            "markdown": page_markdown,
            "text": page_text,
        })

    normalized_markdown = normalize_markdown(markdown)
    if not normalized_pages and normalized_markdown:
        normalized_pages = split_markdown_pages(normalized_markdown)

    if normalized_pages and not normalized_markdown:
        normalized_markdown = join_pages_markdown(normalized_pages)

    plain_text = join_pages_text(normalized_pages) if normalized_pages else strip_markdown_syntax(normalized_markdown)

    total_pages = len(normalized_pages)
    if total_pages == 0:
        if file_path and file_path.suffix.lower() == ".pdf":
            total_pages = count_pdf_pages(file_path)
        else:
            total_pages = 1 if normalized_markdown or plain_text else 0

    if not normalized_pages and normalized_markdown:
        normalized_pages = [{
            "index": 0,
            "markdown": normalized_markdown,
            "text": plain_text,
        }]

    result_warnings = list(warnings or [])
    if file_path and file_path.exists():
        size_mb = file_path.stat().st_size / (1024 * 1024)
        if size_mb >= SOFT_SIZE_WARNING_MB:
            append_warning(
                result_warnings,
                f"Archivo pesado ({size_mb:.1f} MB). Si el resultado sale incompleto, intenta dividir el documento."
            )
        if file_path.suffix.lower() == ".pdf" and total_pages > SOFT_PAGE_WARNING:
            append_warning(
                result_warnings,
                f"PDF largo ({total_pages} páginas). Revisa el resultado por secciones para validar tablas y texto."
            )

    return {
        "method": method,
        "text": plain_text,
        "markdown": normalized_markdown,
        "pages": normalized_pages,
        "total_pages": total_pages,
        "warnings": result_warnings,
    }

def try_digital_pdf(file_path: Path) -> Optional[dict[str, Any]]:
    if fitz is None or is_cloud_runtime():
        return None
    try:
        table_map = extract_pdf_tables_markdown(file_path)
        pages = []
        total_chars = 0
        text_pages = 0
        table_count = sum(len(tables) for tables in table_map.values())

        with fitz.open(str(file_path)) as doc:
            for page_index, page in enumerate(doc):
                raw_text = normalize_whitespace(page.get_text("text", sort=True))
                text_block = normalize_markdown(raw_text)
                page_parts = []

                if len(strip_markdown_syntax(text_block)) >= OCR_MIN_PAGE_CHARS:
                    page_parts.append(text_block)
                    text_pages += 1

                page_tables = table_map.get(page_index, [])
                if page_tables:
                    if page_parts:
                        page_parts.append("## Tablas detectadas")
                    page_parts.extend(page_tables)

                page_markdown = "\n\n".join(part for part in page_parts if part.strip()).strip()
                if page_markdown:
                    pages.append({
                        "index": page_index,
                        "markdown": page_markdown,
                    })
                    total_chars += len(strip_markdown_syntax(page_markdown))

        if total_chars < minimum_pdf_text_chars(file_path) or text_pages == 0:
            return None

        warnings = []
        if table_count:
            append_warning(warnings, f"Se detectaron {table_count} tablas en el PDF digital.")

        return build_result(
            "digital_pdf",
            join_pages_markdown(pages),
            pages=pages,
            file_path=file_path,
            warnings=warnings,
        )
    except Exception:
        return None

# ── Markitdown ────────────────────────────────────────────────────────────────
def try_markitdown(file_path: Path) -> Optional[dict[str, Any]]:
    if is_cloud_runtime():
        return None
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(str(file_path))
        text = result.text_content.strip()
        if len(text) >= minimum_pdf_text_chars(file_path):
            return build_result("markitdown", text, file_path=file_path)
        return None
    except Exception:
        return None

# ── Gemini OCR ────────────────────────────────────────────────────────────────
GEMINI_MARKDOWN_PROMPT = (
    "Transcribe este documento a Markdown fiel al original. "
    "Conserva encabezados, listas, numeración, saltos y tablas. "
    "Si detectas una tabla, devuélvela como tabla Markdown válida. "
    "No inventes texto ni completes palabras dudosas con suposiciones. "
    "No agregues explicaciones. Devuelve solo el Markdown del documento."
)

def try_gemini_page(image: Image.Image) -> str:
    png_bytes = image_to_png_bytes(image)
    img_b64 = base64.b64encode(png_bytes).decode()
    model = genai.GenerativeModel("gemini-2.0-flash")
    response = model.generate_content([
        {
            "inline_data": {
                "mime_type": "image/png",
                "data": img_b64
            }
        },
        GEMINI_MARKDOWN_PROMPT,
    ])
    return normalize_markdown(response.text if response.text else "")

def try_gemini(file_path: Path, lang: str = "spa", *, mode: str = "balanced") -> Optional[dict[str, Any]]:
    if not GEMINI_API_KEY or genai is None:
        return None
    mode = normalize_ocr_mode(mode)
    dpi = ocr_dpi_for_mode(mode)
    try:
        suffix = file_path.suffix.lower()
        pages: list[dict[str, Any]] = []
        warnings: list[str] = []
        local_fallback_pages = 0

        def gemini_job(item: tuple[int, Image.Image]) -> tuple[int, str]:
            page_index, image = item
            return page_index, try_gemini_page(image)

        if suffix == ".pdf":
            total_pages = count_pdf_pages(file_path)
            if total_pages > OCR_CHUNK_PAGES:
                append_warning(
                    warnings,
                    f"PDF grande procesado por bloques internos de {OCR_CHUNK_PAGES} páginas para mejorar estabilidad."
                )
            for batch in iter_pdf_page_chunks(file_path, dpi=dpi, chunk_size=OCR_CHUNK_PAGES):
                by_idx = {idx: im for idx, im in batch}
                workers = min(OCR_GEMINI_WORKERS, len(batch))
                if workers <= 1:
                    gemini_results = [gemini_job(item) for item in batch]
                else:
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        gemini_results = list(executor.map(gemini_job, batch))

                for page_index, page_markdown in sorted(gemini_results, key=lambda item: item[0]):
                    image = by_idx[page_index]
                    final_md = page_markdown
                    if has_tesseract() and (
                        text_quality_score(page_markdown) < 28
                        or len(strip_markdown_syntax(page_markdown)) < OCR_MIN_PAGE_CHARS
                    ):
                        local_markdown = ocr_image_to_markdown(image, lang=lang, mode=mode)
                        if local_markdown and text_quality_score(local_markdown) > text_quality_score(page_markdown) + 8:
                            final_md = local_markdown
                            local_fallback_pages += 1
                    if final_md:
                        pages.append({
                            "index": page_index,
                            "markdown": final_md,
                        })
        else:
            image = Image.open(file_path)
            page_markdown = try_gemini_page(image)
            if has_tesseract() and (
                text_quality_score(page_markdown) < 28
                or len(strip_markdown_syntax(page_markdown)) < OCR_MIN_PAGE_CHARS
            ):
                local_markdown = ocr_image_to_markdown(image, lang=lang, mode=mode)
                if local_markdown and text_quality_score(local_markdown) > text_quality_score(page_markdown) + 8:
                    page_markdown = local_markdown
                    local_fallback_pages += 1
            if page_markdown:
                pages.append({
                    "index": 0,
                    "markdown": page_markdown,
                })

        if not pages:
            return None

        if local_fallback_pages:
            append_warning(
                warnings,
                f"{local_fallback_pages} página(s) se resolvieron con OCR local reforzado porque la salida IA fue débil."
            )

        return build_result("gemini", join_pages_markdown(pages), pages=pages, file_path=file_path, warnings=warnings)
    except Exception as e:
        print(f"Gemini error: {e}")
        return None

# ── Preprocesado de imagen ────────────────────────────────────────────────────
def upscale_for_ocr(image: Image.Image, *, min_edge: Optional[int] = None) -> Image.Image:
    target = min_edge if min_edge is not None else OCR_UPSCALE_MIN_EDGE
    img = ImageOps.exif_transpose(image).convert("L")
    edge = min(img.size) if img.size else 0
    if edge and edge < target:
        scale = min(2.5, target / float(edge))
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return img

def correct_orientation(image: Image.Image) -> Image.Image:
    if not pytesseract:
        return image
    try:
        osd = pytesseract.image_to_osd(image, config="--psm 0")
        match = re.search(r"Rotate:\s+(\d+)", osd)
        if match:
            rotate = int(match.group(1)) % 360
            if rotate:
                return image.rotate(-rotate, expand=True, fillcolor=255)
    except Exception:
        pass
    return image

def preprocess_variants(image: Image.Image, *, skip_osd: bool = False, min_edge: Optional[int] = None) -> dict[str, Image.Image]:
    base = upscale_for_ocr(image, min_edge=min_edge)
    base = ImageOps.autocontrast(base, cutoff=1)
    if not skip_osd:
        base = correct_orientation(base)

    soft = ImageEnhance.Contrast(base).enhance(1.25)
    soft = soft.filter(ImageFilter.MedianFilter(size=3))

    strong = ImageEnhance.Contrast(base).enhance(1.85)
    strong = ImageEnhance.Sharpness(strong).enhance(1.7)
    strong = strong.filter(ImageFilter.SHARPEN)

    mean_value = ImageStat.Stat(strong).mean[0] if strong.size else 150
    threshold = max(115, min(190, int(mean_value * 0.92)))
    binary = strong.point(lambda value: 255 if value > threshold else 0, "L")

    return {
        "base": base,
        "soft": soft,
        "strong": strong,
        "binary": binary,
    }

# ── Limpieza de texto ─────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        total = len(stripped)
        useful = len(re.findall(r'[a-zA-ZáéíóúÁÉÍÓÚñÑüÜ0-9\s,\.;\:\!\?\(\)\"\'\-\/]', stripped))
        ratio = useful / total if total > 0 else 0
        if ratio < 0.55 and total < 40:
            continue
        line_clean = re.sub(r'[^\x20-\x7EáéíóúÁÉÍÓÚñÑüÜ«»°\n]', '', stripped)
        line_clean = line_clean.strip()
        if line_clean:
            cleaned.append(line_clean)
    return "\n".join(cleaned)

# ── OCR Tesseract ─────────────────────────────────────────────────────────────
LOCAL_OCR_CONFIGS = [
    r"--oem 3 --psm 6 -c preserve_interword_spaces=1",
    r"--oem 3 --psm 4 -c preserve_interword_spaces=1",
    r"--oem 3 --psm 11 -c preserve_interword_spaces=1",
]

def run_ocr_candidate(image: Image.Image, lang: str, config: str) -> dict[str, Any]:
    if not pytesseract:
        return {"text": "", "confidence": 0.0, "score": 0.0}
    raw_text = pytesseract.image_to_string(image, lang=lang, config=config)
    cleaned = clean_text(raw_text)
    if not cleaned:
        return {"text": "", "confidence": 0.0, "score": 0.0}

    confidence = 0.0
    try:
        data = pytesseract.image_to_data(image, lang=lang, config=config, output_type=Output.DICT)
        confidences = [float(conf) for conf in data.get("conf", []) if conf not in {"-1", "-1.0", ""}]
        if confidences:
            confidence = sum(confidences) / len(confidences)
    except Exception:
        confidence = 0.0

    score = text_quality_score(cleaned) + confidence * 1.35
    return {
        "text": cleaned,
        "confidence": confidence,
        "score": score,
    }

def ocr_image_to_markdown(image: Image.Image, lang: str = "spa", *, mode: str = "balanced") -> str:
    if not has_tesseract():
        return ""
    mode = normalize_ocr_mode(mode)
    min_edge = upscale_min_edge_for_mode(mode)

    if mode == "fast":
        img = upscale_for_ocr(image, min_edge=min_edge)
        img = ImageOps.autocontrast(img, cutoff=1)
        best_candidate = {"text": "", "confidence": 0.0, "score": 0.0}
        for config in LOCAL_OCR_CONFIGS[:2]:
            try:
                candidate = run_ocr_candidate(img, lang=lang, config=config)
            except Exception:
                continue
            if candidate["score"] > best_candidate["score"]:
                best_candidate = candidate
        return normalize_markdown(best_candidate["text"])

    if mode == "quality":
        best_candidate = {"text": "", "confidence": 0.0, "score": 0.0}
        for variant in preprocess_variants(image, skip_osd=False, min_edge=min_edge).values():
            for config in LOCAL_OCR_CONFIGS:
                try:
                    candidate = run_ocr_candidate(variant, lang=lang, config=config)
                except Exception:
                    continue
                if candidate["score"] > best_candidate["score"]:
                    best_candidate = candidate
        return normalize_markdown(best_candidate["text"])

    best_quick = {"text": "", "confidence": 0.0, "score": 0.0}
    img_quick = upscale_for_ocr(image, min_edge=min_edge)
    img_quick = ImageOps.autocontrast(img_quick, cutoff=1)
    try:
        best_quick = run_ocr_candidate(img_quick, lang=lang, config=LOCAL_OCR_CONFIGS[0])
    except Exception:
        pass

    quick_plain = strip_markdown_syntax(normalize_markdown(best_quick["text"]))
    if best_quick["score"] >= OCR_BALANCED_GOOD_SCORE and len(quick_plain) >= OCR_MIN_PAGE_CHARS * 2:
        return normalize_markdown(best_quick["text"])

    best_full = {"text": "", "confidence": 0.0, "score": 0.0}
    for variant in preprocess_variants(image, skip_osd=False, min_edge=min_edge).values():
        for config in LOCAL_OCR_CONFIGS:
            try:
                candidate = run_ocr_candidate(variant, lang=lang, config=config)
            except Exception:
                continue
            if candidate["score"] > best_full["score"]:
                best_full = candidate

    chosen = best_full if best_full["score"] >= best_quick["score"] else best_quick
    return normalize_markdown(chosen["text"])

def process_with_tesseract(file_path: Path, lang: str = "spa", *, mode: str = "balanced") -> dict[str, Any]:
    if not has_tesseract():
        raise RuntimeError("Tesseract no está disponible en este entorno.")
    mode = normalize_ocr_mode(mode)
    dpi = ocr_dpi_for_mode(mode)
    suffix = file_path.suffix.lower()
    pages: list[dict[str, Any]] = []
    warnings: list[str] = []

    def tesseract_job(item: tuple[int, Image.Image]) -> tuple[int, str]:
        page_index, img = item
        text = ocr_image_to_markdown(img, lang=lang, mode=mode)
        return page_index, text

    if suffix == ".pdf":
        total_pages = count_pdf_pages(file_path)
        if total_pages > OCR_CHUNK_PAGES:
            append_warning(
                warnings,
                f"PDF largo procesado por bloques internos de {OCR_CHUNK_PAGES} páginas para ahorrar memoria."
            )
        for batch in iter_pdf_page_chunks(file_path, dpi=dpi, chunk_size=OCR_CHUNK_PAGES):
            workers = min(OCR_TESSERACT_WORKERS, len(batch))
            if workers <= 1:
                results = [tesseract_job(item) for item in batch]
            else:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    results = list(executor.map(tesseract_job, batch))
            for page_index, text in sorted(results, key=lambda item: item[0]):
                if text:
                    pages.append({
                        "index": page_index,
                        "markdown": text,
                    })
    elif suffix in {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}:
        img = Image.open(file_path)
        text = ocr_image_to_markdown(img, lang=lang, mode=mode)
        if text:
            pages.append({
                "index": 0,
                "markdown": text,
            })
    else:
        raise ValueError(f"Tipo no soportado: {suffix}")
    return build_result("tesseract", join_pages_markdown(pages), pages=pages, file_path=file_path, warnings=warnings)

# ── Procesador principal ──────────────────────────────────────────────────────
def process_file(file_path: Path, lang: str = "spa", *, mode: str = "balanced") -> dict[str, Any]:
    suffix = file_path.suffix.lower()
    mode = normalize_ocr_mode(mode)

    if is_cloud_runtime():
        if not GEMINI_API_KEY or genai is None:
            raise RuntimeError(
                "En Vercel debes configurar GEMINI_API_KEY en Settings → Environment Variables."
            )
        if suffix == ".pdf" and fitz is None:
            raise RuntimeError("PyMuPDF no está disponible en el servidor.")
        result = try_gemini(file_path, lang=lang, mode=mode)
        if result:
            return result
        raise RuntimeError("No se pudo procesar el documento con Gemini.")

    # 1. PDF digital con texto y tablas
    if suffix == ".pdf":
        result = try_digital_pdf(file_path)
        if result:
            return result

    # 2. Markitdown — fallback para PDFs digitales
    if suffix == ".pdf":
        result = try_markitdown(file_path)
        if result:
            return result

    # 3. Gemini — PDFs escaneados con IA (rápido, alta calidad)
    if GEMINI_API_KEY:
        result = try_gemini(file_path, lang=lang, mode=mode)
        if result:
            return result

    # 4. Tesseract — fallback local reforzado
    return process_with_tesseract(file_path, lang=lang, mode=mode)

# ── Exportadores ──────────────────────────────────────────────────────────────
def safe_download_stem(filename: str) -> str:
    stem = Path(filename or "documento").stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip()
    return stem[:200] if stem else "documento"


def save_job_meta(job_id: str, original_filename: str) -> None:
    meta_path = OUTPUT_DIR / f"{job_id}.meta.json"
    meta_path.write_text(
        json.dumps({"filename": original_filename, "stem": safe_download_stem(original_filename)}),
        encoding="utf-8",
    )


def load_job_meta(job_id: str) -> dict[str, str]:
    meta_path = OUTPUT_DIR / f"{job_id}.meta.json"
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_txt(text: str, path: Path):
    path.write_text(text, encoding="utf-8")

def save_md(markdown: str, path: Path):
    content = normalize_markdown(markdown)
    if not content:
        content = "# Resultado OCR"
    path.write_text(content, encoding="utf-8")

def save_docx(markdown: str, path: Path):
    from docx import Document
    from docx.shared import Pt

    content = normalize_markdown(markdown)
    doc = Document()
    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Arial"
    normal_style.font.size = Pt(10.5)

    def add_markdown_paragraph(target_doc: Document, text: str):
        paragraph = target_doc.add_paragraph()
        paragraph.add_run(text)

    lines = content.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            doc.add_heading(heading.group(2).strip(), level=level)
            index += 1
            continue

        if stripped == "---":
            doc.add_page_break()
            index += 1
            continue

        if "|" in stripped and index + 1 < len(lines) and re.match(r"^\s*\|?(?:\s*:?-+:?\s*\|)+\s*$", lines[index + 1].strip()):
            header_cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            rows = []
            index += 2
            while index < len(lines):
                row_line = lines[index].strip()
                if not row_line or "|" not in row_line:
                    break
                rows.append([cell.strip() for cell in row_line.strip("|").split("|")])
                index += 1

            table = doc.add_table(rows=1, cols=len(header_cells))
            table.style = "Table Grid"
            for col, value in enumerate(header_cells):
                table.rows[0].cells[col].text = value
            for row_values in rows:
                row = table.add_row().cells
                for col in range(len(header_cells)):
                    row[col].text = row_values[col] if col < len(row_values) else ""
            continue

        if re.match(r"^\s*[-*]\s+", stripped):
            doc.add_paragraph(re.sub(r"^\s*[-*]\s+", "", stripped), style="List Bullet")
            index += 1
            continue

        if re.match(r"^\s*\d+\.\s+", stripped):
            doc.add_paragraph(re.sub(r"^\s*\d+\.\s+", "", stripped), style="List Number")
            index += 1
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if (
                not next_line
                or next_line == "---"
                or re.match(r"^(#{1,3})\s+", next_line)
                or re.match(r"^\s*[-*]\s+", next_line)
                or re.match(r"^\s*\d+\.\s+", next_line)
                or ("|" in next_line and index + 1 < len(lines) and re.match(r"^\s*\|?(?:\s*:?-+:?\s*\|)+\s*$", lines[index + 1].strip()))
            ):
                break
            paragraph_lines.append(next_line)
            index += 1

        add_markdown_paragraph(doc, " ".join(paragraph_lines))

    doc.save(str(path))

def save_pdf(text: str, path: Path):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Helvetica", size=11)
    for line in text.split("\n"):
        try:
            pdf.multi_cell(0, 8, line)
        except Exception:
            pdf.multi_cell(0, 8, line.encode("latin-1", errors="replace").decode("latin-1"))
    pdf.output(str(path))

# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    index = _frontend_dir() / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>SCI OCR API</h1><p>Frontend no encontrado.</p>")

@app.get("/config")
async def get_config():
    return {
        "max_upload_mb": MAX_UPLOAD_MB,
        "cloud_runtime": is_cloud_runtime(),
        "gemini_enabled": bool(GEMINI_API_KEY and genai is not None),
        "tesseract_enabled": has_tesseract(),
        "ocr_modes": [
            {"id": "fast", "label": "Rápido", "dpi_pdf": ocr_dpi_for_mode("fast")},
            {"id": "balanced", "label": "Equilibrado", "dpi_pdf": ocr_dpi_for_mode("balanced")},
            {"id": "quality", "label": "Máxima calidad", "dpi_pdf": ocr_dpi_for_mode("quality")},
        ],
    }

@app.post("/ocr")
async def ocr_endpoint(
    file: UploadFile = File(...),
    lang: str = Query("spa"),
    mode: str = Query("balanced"),
    x_api_key: Optional[str] = Header(None),
):
    check_api_key(x_api_key)

    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"Tipo no permitido: {suffix}")

    job_id = str(uuid.uuid4())[:8]
    upload_path = UPLOAD_DIR / f"{job_id}{suffix}"

    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    effective_mode = normalize_ocr_mode(mode)
    try:
        result = process_file(upload_path, lang=lang, mode=effective_mode)
    except Exception as e:
        raise HTTPException(500, f"Error al procesar: {str(e)}")
    finally:
        upload_path.unlink(missing_ok=True)

    original_filename = file.filename or "documento"
    save_job_meta(job_id, original_filename)

    base = OUTPUT_DIR / job_id
    save_txt(result["text"], base.with_suffix(".txt"))
    save_md(result["markdown"], base.with_suffix(".md"))
    try:
        save_pdf(result["text"], base.with_suffix(".pdf"))
        pdf_ok = True
    except Exception:
        pdf_ok = False
    try:
        save_docx(result["markdown"], base.with_suffix(".docx"))
        docx_ok = True
    except Exception:
        docx_ok = False

    download_stem = safe_download_stem(original_filename)

    return {
        "job_id": job_id,
        "source_filename": original_filename,
        **result,
        "ocr_mode": effective_mode,
        "downloads": {
            "txt": f"/download/{job_id}/txt",
            "md":  f"/download/{job_id}/md",
            "pdf": f"/download/{job_id}/pdf" if pdf_ok else None,
            "docx": f"/download/{job_id}/docx" if docx_ok else None,
        },
        "download_names": {
            "md": f"{download_stem}.md",
        },
    }

@app.get("/download/{job_id}/{fmt}")
async def download(
    job_id: str,
    fmt: str,
    x_api_key: Optional[str] = Header(None),
    key: Optional[str] = Query(None),
):
    effective_key = x_api_key or key
    if API_KEY and effective_key != API_KEY:
        raise HTTPException(403, "API Key inválida")

    ext_map   = {"txt": ".txt", "md": ".md", "pdf": ".pdf", "docx": ".docx"}
    media_map = {
        "txt": "text/plain",
        "md": "text/markdown",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    if fmt not in ext_map:
        raise HTTPException(400, "Formato inválido")

    path = OUTPUT_DIR / f"{job_id}{ext_map[fmt]}"
    if not path.exists():
        raise HTTPException(404, "Archivo no encontrado")

    download_name = f"ocr_{job_id}{ext_map[fmt]}"
    if fmt == "md":
        meta = load_job_meta(job_id)
        stem = meta.get("stem") or safe_download_stem(meta.get("filename", ""))
        download_name = f"{stem}.md"

    return FileResponse(path, media_type=media_map[fmt], filename=download_name)

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("OCR_HOST", "0.0.0.0")
    port = int(os.getenv("OCR_PORT", "8000"))
    reload = os.getenv("OCR_RELOAD", "0").strip().lower() in ("1", "true", "yes")
    uvicorn.run("main:app", host=host, port=port, reload=reload)
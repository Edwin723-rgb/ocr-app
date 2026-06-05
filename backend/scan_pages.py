"""Clasificación de páginas escaneadas: recibos, credenciales, actas, hojas de cálculo."""
from __future__ import annotations

import re

import pytesseract
from PIL import Image

PAGE_SPREADSHEET = "spreadsheet"
PAGE_RECEIPT = "receipt"
PAGE_CREDENTIAL = "credential"
PAGE_FORM = "form_document"
PAGE_CHECKLIST = "checklist"
PAGE_CV = "cv"
PAGE_CONTRACT = "contract"
PAGE_GENERIC = "generic"

_CHECKLIST_MARKERS = re.compile(
    r"(?i)(check\s*list|checoklist|requisitos\s+de\s+contrataci[oó]n|documentos?\s+requeridos?)"
)
_CREDENTIAL_MARKERS = re.compile(
    r"(?i)\b("
    r"instituto\s+nacional\s+elector(?:al)?|"
    r"credencial\s+para\s+votar|"
    r"clave\s+de\s+elector|"
    r"\bine\b|\bife\b|"
    r"elector(?:a)?\s+de\s+la\s+secci[oó]n"
    r")\b"
)
_FORM_MARKERS = re.compile(
    r"(?i)\b("
    r"acta\s+de\s+nacimiento|"
    r"registro\s+civil|"
    r"identificador\s+electr[oó]nico|"
    r"datos\s+de\s+la\s+persona\s+registrada|"
    r"datos\s+de\s+filiaci[oó]n|"
    r"copia\s+certificada|"
    r"estados\s+unidos\s+mexicanos"
    r")\b"
)
_RECEIPT_MARKERS = re.compile(
    r"(?i)\b("
    r"tel(?:mex|cel|nor)|"
    r"telefonos?\s+de\s+mexico|"
    r"\bcfe\b|"
    r"total\s+a\s+pagar|"
    r"exigido\s+pagar|"
    r"estado\s+de\s+cuenta|"
    r"recibo\s+de|"
    r"comprobante\s+de\s+pago|"
    r"fecha\s+l[ií]mite\s+de\s+pago|"
    r"numero?\s+de\s+servicio|"
    r"n[uú]mero\s+de\s+cuenta|"
    r"resumen\s+del\s+estado"
    r")\b"
)
_SPREADSHEET_BLOCK = re.compile(
    r"(?i)\b("
    r"acta\s+de\s+nacimiento|"
    r"registro\s+civil|"
    r"identificador\s+electr[oó]nico|"
    r"credencial|"
    r"instituto\s+nacional\s+elector|"
    r"tel(?:mex|cel)|"
    r"\bcfe\b|"
    r"estado\s+de\s+cuenta|"
    r"checklist|"
    r"requisitos\s+de\s+contrataci[oó]n|"
    r"curriculum|"
    r"experiencia\s+laboral|"
    r"solicitud\s+de\s+empleo"
    r")\b"
)
_MRZ_LINE = re.compile(r"[A-Z0-9<]{24,}")
_ID_CARD_LAYOUT = re.compile(
    r"(?i)\b("
    r"secci[oó]n\s+\d{4}|"
    r"vigencia|"
    r"elector|"
    r"curp"
    r")\b"
)
_TABLE_HEADER_HINTS = re.compile(
    r"(?i)\b(fecha|nombre|concepto|pago|importe|monto|cantidad|descripci[oó]n|precio|total)\b"
)
_SPREADSHEET_KEYS = ("FECHA", "NOMBRE", "CONCEPTO", "IMPORTE", "PAGO")
_CV_MARKERS = re.compile(
    r"(?i)\b("
    r"curriculum|"
    r"curr[ií]culum|"
    r"experiencia\s+laboral|"
    r"formaci[oó]n|"
    r"informaci[oó]n\s+de\s+contacto|"
    r"mi\s+perfil|"
    r"datos\s+personales"
    r")\b"
)
_CONTRACT_MARKERS = re.compile(
    r"(?i)\b("
    r"contrato(?:\s+de)?|"
    r"arrendamiento|"
    r"compraventa|"
    r"prestaci[oó]n\s+de\s+servicios|"
    r"confidencialidad|"
    r"cl[aá]usula|"
    r"comparecen|"
    r"las\s+partes|"
    r"por\s+una\s+parte|"
    r"por\s+otra\s+parte|"
    r"objeto\s+del\s+contrato|"
    r"convenio|"
    r"acuerdo\s+de|"
    r"contratante|"
    r"contratista|"
    r"arrendador|"
    r"arrendatario|"
    r"vendedor|"
    r"comprador|"
    r"jurisdicci[oó]n|"
    r"terminaci[oó]n|"
    r"vigencia|"
    r"instrumento|"
    r"otorgamiento|"
    r"testigo|"
    r"anexo\s+[a-z0-9]|"
    r"whereas|"
    r"hereby|"
    r"party|"
    r"agreement"
    r")\b"
)
_CONTRACT_STRONG = (
    "cláusula",
    "clausula",
    "comparecen",
    "las partes",
    "contrato de",
    "arrendamiento",
    "compraventa",
    "prestación de servicios",
    "objeto del contrato",
)


def _looks_like_contract(text: str) -> bool:
    stripped = (text or "").strip()
    if len(stripped) < 40:
        return False
    hits = len(_CONTRACT_MARKERS.findall(text))
    if hits >= 2:
        return True
    lower = stripped.lower()
    return any(marker in lower for marker in _CONTRACT_STRONG) and hits >= 1


def _looks_like_employment_checklist(text: str) -> bool:
    lower = (text or "").lower()
    markers = (
        "contrataci",
        "requisit",
        "requisflos",
        "checklist",
        "checoklist",
        "curriculum",
        "comprobante de domicilio",
        "acta de nacimiento",
        "solicitud de empleo",
    )
    return sum(1 for marker in markers if marker in lower) >= 2


def _preview_text(image: Image.Image, lang: str = "spa") -> str:
    preview = image.copy()
    preview.thumbnail((1800, 2400), Image.Resampling.LANCZOS)
    return pytesseract.image_to_string(
        preview,
        lang=lang,
        config="--oem 1 --psm 6",
    ) or ""


def guess_visual_page_kind(image: Image.Image, lang: str = "spa", *, preview: str | None = None) -> str | None:
    """Cuando el OCR previo no lee bien una foto de INE/recibo, inferir por pistas visuales."""
    text = preview if preview is not None else _preview_text(image, lang)
    words = re.findall(r"\b[\wáéíóúÁÉÍÓÚñ]{3,}\b", text)
    upper = text.upper()
    if _MRZ_LINE.search(text):
        return PAGE_CREDENTIAL
    if len(words) < 40:
        id_hits = sum(
            1
            for token in ("DOMICILIO", "ELECTOR", "CURP", "VIGENCIA", "NOMBRE", "SECCIÓN", "SECCION")
            if token in upper
        )
        if id_hits >= 2 or (_ID_CARD_LAYOUT.search(text) and id_hits >= 1):
            return PAGE_CREDENTIAL
        if _RECEIPT_MARKERS.search(text):
            return PAGE_RECEIPT
        if _FORM_MARKERS.search(text):
            return PAGE_FORM
    return None


def classify_scan_page(image: Image.Image, lang: str = "spa", *, preview: str | None = None) -> str:
    """Clasifica una página escaneada para elegir OCR y formato de salida."""
    text = preview if preview is not None else _preview_text(image, lang)

    if _CHECKLIST_MARKERS.search(text) or _looks_like_employment_checklist(text):
        return PAGE_CHECKLIST
    if _CREDENTIAL_MARKERS.search(text):
        return PAGE_CREDENTIAL
    if _MRZ_LINE.search(text) and _ID_CARD_LAYOUT.search(text):
        return PAGE_CREDENTIAL
    if _FORM_MARKERS.search(text):
        return PAGE_FORM
    if _RECEIPT_MARKERS.search(text):
        return PAGE_RECEIPT
    if _CV_MARKERS.search(text):
        return PAGE_CV
    if _looks_like_contract(text):
        return PAGE_CONTRACT
    if _looks_like_spreadsheet(image, text):
        return PAGE_SPREADSHEET
    visual = guess_visual_page_kind(image, lang=lang, preview=text)
    if visual:
        return visual
    return PAGE_GENERIC


def _has_payroll_spreadsheet_headers(text: str) -> bool:
    upper = (text or "").upper()
    return (
        "FECHA" in upper
        and "NOMBRE" in upper
        and "CONCEPTO" in upper
        and ("IMPORTE" in upper or "PAGO" in upper)
    )


def _looks_like_spreadsheet(image: Image.Image, text: str) -> bool:
    """Solo hojas de gastos/nómina (tipo Lefort), no actas ni recibos con cuadros."""
    if _SPREADSHEET_BLOCK.search(text):
        return False

    upper = text.upper()
    key_hits = sum(1 for key in _SPREADSHEET_KEYS if key in upper)
    if _has_payroll_spreadsheet_headers(text):
        return True

    header_hits = len(_TABLE_HEADER_HINTS.findall(text))
    if key_hits < 3 or header_hits < 4:
        return False

    from table_ocr import image_has_table_grid

    return image_has_table_grid(image) and key_hits >= 3 and header_hits >= 4


def page_needs_legal_vision(image: Image.Image, lang: str = "spa", *, page_kind: str | None = None) -> bool:
    """Contratos, documentos legales y escaneos genéricos (estilo OCR avanzado unificado)."""
    kind = page_kind or classify_scan_page(image, lang=lang)
    if kind in (
        PAGE_RECEIPT,
        PAGE_CREDENTIAL,
        PAGE_FORM,
        PAGE_CHECKLIST,
        PAGE_CV,
        PAGE_CONTRACT,
    ):
        return True
    if kind == PAGE_SPREADSHEET:
        return False
    if kind == PAGE_GENERIC:
        return True
    return False


def page_needs_docs_vision(image: Image.Image, lang: str = "spa", *, page_kind: str | None = None) -> bool:
    """Recibos, credenciales, actas y escaneos con OCR local muy pobre."""
    kind = page_kind or classify_scan_page(image, lang=lang)
    if kind in (PAGE_RECEIPT, PAGE_CREDENTIAL, PAGE_FORM, PAGE_CHECKLIST, PAGE_CV, PAGE_CONTRACT):
        return True
    if kind != PAGE_GENERIC:
        return False
    text = _preview_text(image, lang)
    words = re.findall(r"\b[\wáéíóúÁÉÍÓÚñ]{4,}\b", text)
    stripped = re.sub(r"\s+", " ", text).strip()
    if len(stripped) < 100:
        return True
    if len(words) < 10 and len(stripped) > 50:
        return True
    return False


def page_looks_like_spreadsheet(image: Image.Image, lang: str = "spa") -> bool:
    """Compatibilidad con table_ocr: solo hojas de cálculo / nómina tipo Lefort."""
    return classify_scan_page(image, lang=lang) == PAGE_SPREADSHEET


def page_allows_tesseract_table(page_kind: str) -> bool:
    """OCR por rejilla Tesseract solo en hojas de gastos; no en expedientes."""
    return page_kind == PAGE_SPREADSHEET

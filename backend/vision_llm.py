"""OCR por visión con Gemini, OpenAI (GPT) y Anthropic (Claude)."""
from __future__ import annotations
import base64
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Any, Callable, Optional
import httpx
from PIL import Image

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
OCR_VISION_PROVIDER = (os.getenv("OCR_VISION_PROVIDER") or "auto").strip().lower()
OCR_VISION_MODE = (os.getenv("OCR_VISION_MODE") or "parallel").strip().lower()
OCR_VISION_PRIORITY = [
    p.strip().lower()
    for p in (os.getenv("OCR_VISION_PRIORITY") or "anthropic,openai,gemini").split(",")
    if p.strip()
]
OCR_VISION_ACTIVE_PROVIDERS = [
    p.strip().lower()
    for p in (os.getenv("OCR_VISION_ACTIVE_PROVIDERS") or "").split(",")
    if p.strip()
]
OPENAI_VISION_MODEL = (os.getenv("OPENAI_VISION_MODEL") or "gpt-4o").strip()
ANTHROPIC_VISION_MODEL = (os.getenv("ANTHROPIC_VISION_MODEL") or "claude-sonnet-4-6").strip()
GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or "gemini-2.0-flash").strip()
_VISION_TIMEOUT = float(os.getenv("OCR_VISION_TIMEOUT", "120"))
_OCR_SYSTEM_PROMPT = (
    "Eres software OCR embebido en un escáner documental (no un chatbot). "
    "Tu única salida es el texto impreso visible de la imagen, en Markdown. "
    "Debes transcribir TODO lo impreso: nombres, CURP, RFC, domicilios, folios y cláusulas, tal cual. "
    "Está prohibido negarte por privacidad, advertir sobre datos personales, pedir autorización "
    "u ofrecer análisis legal o redacción de cláusulas. "
    "Prohibido texto tuyo que no esté en la imagen."
)
_QUOTA_COOLDOWN_SEC = float(os.getenv("OCR_VISION_QUOTA_COOLDOWN", "300"))
_provider_disabled_until: dict[str, float] = {}
_last_parallel_run: dict[str, Any] = {}
_QUOTA_HINTS = (
    "quota",
    "rate limit",
    "rate_limit",
    "insufficient",
    "billing",
    "exceeded",
    "resource_exhausted",
    "too many requests",
    "overloaded",
)


def configured_providers() -> list[str]:
    if os.getenv("OCR_VISION_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return []
    providers: list[str] = []
    if ANTHROPIC_API_KEY:
        providers.append("anthropic")
    if OPENAI_API_KEY:
        providers.append("openai")
    if GEMINI_API_KEY:
        providers.append("gemini")
    if OCR_VISION_ACTIVE_PROVIDERS:
        providers = [p for p in providers if p in OCR_VISION_ACTIVE_PROVIDERS]
    return providers


def providers_for_tables() -> list[str]:
    """Claude/GPT/Gemini solo para páginas-tabla aunque OCR_VISION_ENABLED=0."""
    if os.getenv("OCR_VISION_TABLES_ONLY", "1").strip().lower() in ("0", "false", "no"):
        return []
    return _scoped_vision_providers()


def providers_for_documents() -> list[str]:
    """IA para recibos, credenciales y actas aunque OCR_VISION_ENABLED=0."""
    if os.getenv("OCR_VISION_DOCS_ONLY", "1").strip().lower() in ("0", "false", "no"):
        return []
    return _scoped_vision_providers()


def providers_for_legal() -> list[str]:
    """IA para contratos y escaneos legales genéricos (OCR avanzado) sin OCR_VISION_ENABLED=1."""
    if os.getenv("OCR_VISION_LEGAL_ONLY", "1").strip().lower() in ("0", "false", "no"):
        return []
    return _scoped_vision_providers()


def _scoped_vision_providers() -> list[str]:
    providers: list[str] = []
    if ANTHROPIC_API_KEY:
        providers.append("anthropic")
    if OPENAI_API_KEY:
        providers.append("openai")
    if GEMINI_API_KEY:
        providers.append("gemini")
    if OCR_VISION_ACTIVE_PROVIDERS:
        filtered = [p for p in providers if p in OCR_VISION_ACTIVE_PROVIDERS]
        if filtered:
            providers = filtered
    return providers

def resolve_provider_chain(
    requested: Optional[str] = None,
    *,
    pool: Optional[list[str]] = None,
) -> list[str]:
    available = list(pool if pool is not None else configured_providers())
    if not available:
        return []
    choice = (requested or OCR_VISION_PROVIDER or "auto").strip().lower()
    if choice and choice != "auto":
        if choice in available:
            return [choice]
        choice = "auto"
    chain: list[str] = [p for p in OCR_VISION_PRIORITY if p in available]
    for provider in available:
        if provider not in chain:
            chain.append(provider)
    return chain

def use_parallel_mode(requested: Optional[str] = None) -> bool:
    if OCR_VISION_MODE == "single":
        return False
    choice = (requested or "").strip().lower()
    if choice and choice != "auto":
        return False
    return True

def provider_labels() -> dict[str, str]:
    labels = {
        "auto": (
            "Automático (Gemini+GPT+Claude en paralelo)"
            if use_parallel_mode()
            else "Automático (mejor disponible)"
        ),
        "gemini": f"Gemini ({GEMINI_MODEL})",
        "openai": f"OpenAI ({OPENAI_VISION_MODEL})",
        "anthropic": f"Claude ({ANTHROPIC_VISION_MODEL})",
    }
    return labels

def _fit_image_for_vision(image: Image.Image, max_edge: int = 4096) -> Image.Image:
    """APIs de visión rechazan imágenes enormes; reducir conservando legibilidad."""
    width, height = image.size
    longest = max(width, height)
    if longest <= max_edge:
        return image
    scale = max_edge / float(longest)
    return image.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.Resampling.LANCZOS,
    )


def _image_to_png_b64(image: Image.Image) -> str:
    buffer = BytesIO()
    _fit_image_for_vision(image).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")

def _extract_openai_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content if block.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()
    return ""

def _extract_anthropic_text(payload: dict) -> str:
    parts = []
    for block in payload.get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            parts.append(str(block["text"]))
    return "\n".join(parts).strip()

def _provider_disabled(name: str) -> bool:
    until = _provider_disabled_until.get(name, 0.0)
    if until and time.monotonic() < until:
        return True
    if until:
        _provider_disabled_until.pop(name, None)
    return False

def _mark_provider_quota(name: str) -> None:
    _provider_disabled_until[name] = time.monotonic() + _QUOTA_COOLDOWN_SEC

def _response_body_text(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc).lower()
    try:
        if hasattr(response, "text"):
            return (response.text or "").lower()
        if hasattr(response, "json"):
            return str(response.json()).lower()
    except Exception:
        pass
    return str(exc).lower()

def is_quota_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (402, 429):
            return True
        if status == 403:
            body = _response_body_text(exc)
            return any(hint in body for hint in _QUOTA_HINTS)
    body = _response_body_text(exc)
    if any(hint in body for hint in _QUOTA_HINTS):
        return True
    name = type(exc).__name__.lower()
    if "resourceexhausted" in name or "ratelimit" in name:
        return True
    return False

def _call_openai(image: Image.Image, prompt: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY no configurada")
    b64 = _image_to_png_b64(image)
    body = {
        "model": OPENAI_VISION_MODEL,
        "temperature": 0,
        "max_tokens": 8192,
        "messages": [
            {"role": "system", "content": _OCR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
    }
    with httpx.Client(timeout=_VISION_TIMEOUT) as client:
        response = client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        response.raise_for_status()
        return _extract_openai_text(response.json())

def _call_anthropic(image: Image.Image, prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY no configurada")
    b64 = _image_to_png_b64(image)
    body = {
        "model": ANTHROPIC_VISION_MODEL,
        "max_tokens": 8192,
        "temperature": 0,
        "system": _OCR_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    with httpx.Client(timeout=_VISION_TIMEOUT) as client:
        response = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=body,
        )
        response.raise_for_status()
        return _extract_anthropic_text(response.json())

def _call_gemini(image: Image.Image, prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY no configurada")
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)
    png_bytes = base64.b64decode(_image_to_png_b64(image))
    response = model.generate_content(
        [
            {"inline_data": {"mime_type": "image/png", "data": png_bytes}},
            prompt,
        ],
        generation_config={"temperature": 0},
    )
    return (response.text or "").strip()

def _call_provider(name: str, image: Image.Image, prompt: str) -> str:
    if name == "openai":
        return _call_openai(image, prompt)
    if name == "anthropic":
        return _call_anthropic(image, prompt)
    if name == "gemini":
        return _call_gemini(image, prompt)
    raise ValueError(f"Proveedor desconocido: {name}")

def _spanish_word_ratio(text: str) -> float:
    words = re.findall(r"\b[\wáéíóúÁÉÍÓÚñÑüÜ]+\b", text)
    if not words:
        return 0.0
    spanish = sum(
        1 for w in words if re.search(r"[áéíóúÁÉÍÓÚñÑ]", w) or len(w) >= 4
    )
    return spanish / len(words)

def _default_quality_score(text: str) -> float:
    plain = re.sub(r"[#*|_>`\[\]()]", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return 0.0
    total = len(plain)
    words = len(re.findall(r"\b[\wáéíóúÁÉÍÓÚñÑüÜ]+\b", plain))
    return min(total, 3000) / 60 + min(words, 400) / 10 + _spanish_word_ratio(plain) * 20

def pick_best_ocr_result(
    results: list[tuple[str, str]],
    score_fn: Optional[Callable[[str], float]] = None,
) -> tuple[str, str]:
    """Elige el mejor markdown entre resultados (texto, proveedor)."""
    scorer = score_fn or _default_quality_score
    ranked: list[tuple[float, int, float, str, str]] = []
    for text, provider in results:
        plain_len = len(text.strip())
        if plain_len < 8:
            continue
        score = scorer(text)
        ranked.append((score, plain_len, _spanish_word_ratio(text), text, provider))
    if not ranked:
        return "", ""
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    best_score = ranked[0][0]
    tied = [item for item in ranked if abs(item[0] - best_score) < 0.5]
    winners = [item[4] for item in tied]
    label = "parallel:" + "+".join(sorted(set(winners)))
    return tied[0][3], label

def _ocr_one_provider(
    name: str,
    image: Image.Image,
    prompt: str,
    *,
    sanitize: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    status = {"provider": name, "status": "error", "text": "", "error": None}
    if _provider_disabled(name):
        status["status"] = "quota_cooldown"
        return status
    try:
        raw = _call_provider(name, image, prompt)
        text = (raw or "").strip()
        if sanitize and text:
            text = sanitize(text).strip()
        if text:
            status["status"] = "success"
            status["text"] = text
            return status
        status["status"] = "empty"
        return status
    except Exception as exc:
        if is_quota_error(exc):
            _mark_provider_quota(name)
            status["status"] = "quota_exceeded"
            status["error"] = str(exc)
            return status
        status["error"] = str(exc)
        return status

def ocr_page_parallel(
    image: Image.Image,
    prompt: str,
    *,
    provider: Optional[str] = None,
    provider_pool: Optional[list[str]] = None,
    sanitize: Optional[Callable[[str], str]] = None,
    score_fn: Optional[Callable[[str], float]] = None,
) -> tuple[str, str, dict]:
    """
    Ejecuta todos los proveedores configurados en paralelo.
    Devuelve (markdown, etiqueta_origen, meta).
    """
    chain = [p for p in resolve_provider_chain(provider, pool=provider_pool) if not _provider_disabled(p)]
    meta: dict[str, Any] = {
        "mode": "parallel",
        "providers": {},
        "quota_warnings": [],
        "successful": [],
    }
    if not chain:
        return "", "", meta
    statuses: list[dict[str, Any]] = []
    workers = min(len(chain), 3)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_ocr_one_provider, name, image, prompt, sanitize=sanitize): name
            for name in chain
        }
        for future in as_completed(futures):
            statuses.append(future.result())
    successful: list[tuple[str, str]] = []
    quota_names: list[str] = []
    for st in statuses:
        name = st["provider"]
        meta["providers"][name] = st["status"]
        if st["status"] == "success" and st.get("text"):
            successful.append((st["text"], name))
            meta["successful"].append(name)
        elif st["status"] == "quota_exceeded":
            quota_names.append(name)
    if quota_names:
        names = ", ".join(sorted(quota_names))
        meta["quota_warnings"].append(
            f"Cupo o límite alcanzado en {names}; se usaron los demás proveedores en paralelo."
        )
    _last_parallel_run.clear()
    _last_parallel_run.update(meta)
    if not successful:
        return "", "", meta
    text, label = pick_best_ocr_result(successful, score_fn=score_fn)
    return text, label, meta

def ocr_page_with_providers(
    image: Image.Image,
    prompt: str,
    *,
    provider: Optional[str] = None,
    provider_pool: Optional[list[str]] = None,
    sanitize: Optional[Callable[[str], str]] = None,
) -> tuple[str, Optional[str]]:
    """
    OCR secuencial (modo single o proveedor forzado).
    Devuelve (markdown, nombre_proveedor_usado).
    """
    last_error: Optional[Exception] = None
    for name in resolve_provider_chain(provider, pool=provider_pool):
        if _provider_disabled(name):
            continue
        try:
            raw = _call_provider(name, image, prompt)
            text = raw.strip()
            if not text:
                continue
            if sanitize:
                text = sanitize(text)
            if text.strip():
                return text, name
        except Exception as exc:
            if is_quota_error(exc):
                _mark_provider_quota(name)
            last_error = exc
            continue
    if last_error:
        raise last_error
    return "", None

def ocr_page_vision(
    image: Image.Image,
    prompt: str,
    *,
    provider: Optional[str] = None,
    provider_pool: Optional[list[str]] = None,
    sanitize: Optional[Callable[[str], str]] = None,
    score_fn: Optional[Callable[[str], float]] = None,
) -> tuple[str, str, dict]:
    """Punto de entrada: paralelo por defecto o secuencial si está forzado."""
    if use_parallel_mode(provider) and provider_pool is None:
        return ocr_page_parallel(
            image,
            prompt,
            provider=provider,
            sanitize=sanitize,
            score_fn=score_fn,
        )
    text, used = ocr_page_with_providers(
        image,
        prompt,
        provider=provider,
        provider_pool=provider_pool,
        sanitize=sanitize,
    )
    return text, used or "", {"mode": "single", "providers": {used: "success"} if used else {}}

def parallel_vision_status() -> dict:
    now = time.monotonic()
    cooldown = {
        name: max(0, int(until - now))
        for name, until in _provider_disabled_until.items()
        if until > now
    }
    return {
        "vision_mode": OCR_VISION_MODE,
        "parallel_default": use_parallel_mode(),
        "provider_cooldown_sec": cooldown,
        "last_run": dict(_last_parallel_run),
    }

def vision_status() -> dict:
    configured = configured_providers()
    parallel = parallel_vision_status()
    return {
        "vision_enabled": bool(configured),
        "vision_providers": configured,
        "vision_default": OCR_VISION_PROVIDER,
        "vision_mode": OCR_VISION_MODE,
        "vision_parallel": use_parallel_mode(),
        "vision_priority": OCR_VISION_PRIORITY,
        "vision_active_providers": OCR_VISION_ACTIVE_PROVIDERS or configured,
        "vision_labels": provider_labels(),
        "openai_model": OPENAI_VISION_MODEL if OPENAI_API_KEY else None,
        "anthropic_model": ANTHROPIC_VISION_MODEL if ANTHROPIC_API_KEY else None,
        "gemini_model": GEMINI_MODEL if GEMINI_API_KEY else None,
        **parallel,
    }

"""Unir, dividir y comprimir Markdown."""
from __future__ import annotations

import re
from typing import Any


def merge_markdown_chunks(chunks: list[str], *, separator: str = "\n\n---\n\n") -> str:
    parts = [c.strip() for c in chunks if c and c.strip()]
    if not parts:
        raise ValueError("No hay contenido para unir.")
    return separator.join(parts).strip() + "\n"


def compress_markdown(text: str) -> str:
    """Reduce tamaño sin perder estructura básica."""
    if not text:
        return ""
    lines = text.split("\n")
    out: list[str] = []
    blank_run = 0
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            blank_run += 1
            if blank_run <= 1:
                out.append("")
            continue
        blank_run = 0
        stripped = re.sub(r"[ \t]+", " ", stripped)
        out.append(stripped)
    result = "\n".join(out).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result + "\n" if result else ""


def _section_title(content: str, index: int) -> str:
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line).strip()[:80] or f"Sección {index + 1}"
        if line.startswith("---") and "Página" in line:
            return line.replace("---", "").strip()[:80]
        if line.startswith("## Parte:"):
            return line.replace("##", "").strip()[:80]
    preview = re.sub(r"\s+", " ", content.strip())[:60]
    return preview + ("…" if len(content.strip()) > 60 else "") or f"Sección {index + 1}"


def split_sections(text: str) -> list[dict[str, Any]]:
    """Divide MD en secciones editables."""
    normalized = (text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []

    parts = re.split(r"\n\s*---\s*\n", normalized)
    if len(parts) <= 1:
        parts = re.split(r"(?=\n##\s)", normalized)
        parts = [p for p in parts if p.strip()]

    if len(parts) <= 1:
        return [{
            "id": "0",
            "title": _section_title(normalized, 0),
            "content": normalized,
            "chars": len(normalized),
        }]

    sections: list[dict[str, Any]] = []
    for i, part in enumerate(parts):
        body = part.strip()
        if not body:
            continue
        sections.append({
            "id": str(i),
            "title": _section_title(body, i),
            "content": body,
            "chars": len(body),
        })
    return sections


def apply_section_edits(text: str, keep_ids: list[str]) -> str:
    sections = split_sections(text)
    if not sections:
        return compress_markdown(text)
    keep_set = {str(i) for i in keep_ids}
    if not keep_set:
        raise ValueError("Debes conservar al menos una sección.")
    kept = [s["content"] for s in sections if s["id"] in keep_set]
    if not kept:
        raise ValueError("IDs de sección no válidos.")
    return merge_markdown_chunks(kept)


def size_stats(text: str) -> dict[str, Any]:
    raw = text or ""
    compressed = compress_markdown(raw)
    return {
        "chars": len(raw),
        "chars_compressed": len(compressed),
        "saved_pct": round((1 - len(compressed) / len(raw)) * 100, 1) if raw else 0,
    }

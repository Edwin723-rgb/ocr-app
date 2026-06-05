"""Lotes por rangos de páginas: fuente PDF, partes y fusión MD."""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import fitz

BATCHES_DIR_NAME = "batches"


def batches_dir(jobs_dir: Path) -> Path:
    path = jobs_dir / BATCHES_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def batch_path(jobs_dir: Path, batch_id: str) -> Path:
    return batches_dir(jobs_dir) / f"{batch_id}.json"


def load_batch(jobs_dir: Path, batch_id: str) -> Optional[dict[str, Any]]:
    path = batch_path(jobs_dir, batch_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_batch(jobs_dir: Path, batch_id: str, data: dict[str, Any]) -> None:
    data["batch_id"] = batch_id
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    batch_path(jobs_dir, batch_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def create_batch(
    jobs_dir: Path,
    *,
    source_id: str,
    source_filename: str,
    total_pages: int,
) -> str:
    import uuid

    batch_id = str(uuid.uuid4())[:8]
    save_batch(
        jobs_dir,
        batch_id,
        {
            "source_id": source_id,
            "source_filename": source_filename,
            "total_pages": total_pages,
            "parts": [],
            "processing_mode": "ranges",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return batch_id


def normalize_page_range(
    page_from: int,
    page_to: Optional[int],
    total_pages: int,
) -> tuple[int, int]:
    total_pages = max(1, int(total_pages))
    pf = max(1, int(page_from))
    pt = total_pages if page_to is None else int(page_to)
    pt = min(total_pages, max(pf, pt))
    if pf > pt:
        raise ValueError(f"Rango inválido: página {pf} es mayor que {pt}.")
    return pf, pt


def extract_pdf_slice(
    source_path: Path,
    page_from: int,
    page_to: int,
    dest_path: Path,
) -> None:
    """Extrae páginas [page_from, page_to] (1-indexadas) a un PDF temporal."""
    extract_pdf_pages(source_path, list(range(page_from, page_to + 1)), dest_path)


def parse_page_spec(spec: str, total_pages: int) -> list[int]:
    """Convierte '1,3,5-10' en lista ordenada de páginas 1-indexadas."""
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("Indica al menos una página (ej. 1,3,5-10).")
    total_pages = max(1, int(total_pages))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            if len(bounds) != 2 or not bounds[0].strip() or not bounds[1].strip():
                raise ValueError(f"Rango inválido: {part}")
            start = int(bounds[0].strip())
            end = int(bounds[1].strip())
            if start > end:
                raise ValueError(f"Rango inválido: {part} (inicio mayor que fin).")
            for page in range(start, end + 1):
                if page < 1 or page > total_pages:
                    raise ValueError(f"La página {page} no existe (total: {total_pages}).")
                pages.add(page)
        else:
            page = int(part)
            if page < 1 or page > total_pages:
                raise ValueError(f"La página {page} no existe (total: {total_pages}).")
            pages.add(page)
    if not pages:
        raise ValueError("No se reconoció ninguna página válida.")
    return sorted(pages)


def resolve_pages_to_keep(
    total_pages: int,
    *,
    keep_pages: Optional[str] = None,
    remove_pages: Optional[str] = None,
) -> list[int]:
    """Devuelve páginas 1-indexadas a conservar según keep_pages o remove_pages."""
    total_pages = max(1, int(total_pages))
    keep_spec = (keep_pages or "").strip()
    remove_spec = (remove_pages or "").strip()
    if keep_spec and remove_spec:
        raise ValueError("Usa keep_pages o remove_pages, no ambos a la vez.")
    if keep_spec:
        kept = parse_page_spec(keep_spec, total_pages)
    elif remove_spec:
        removed = set(parse_page_spec(remove_spec, total_pages))
        kept = [p for p in range(1, total_pages + 1) if p not in removed]
    else:
        raise ValueError("Indica keep_pages o remove_pages.")
    if not kept:
        raise ValueError("Quedaría un PDF sin páginas. Revisa la selección.")
    return kept


PDF_SAVE_OPTS: dict[str, object] = {
    "deflate": True,
    "garbage": 4,
    "clean": True,
}


def save_pdf_compressed(source_path: Path, dest_path: Path, *, quality: str = "high") -> None:
    """Reescribe un PDF con compresión. quality: high (mejor imagen) | medium (más ligero)."""
    quality = (quality or "high").strip().lower()
    if quality not in ("medium", "high"):
        quality = "high"
    with fitz.open(str(source_path)) as doc:
        if quality == "medium":
            try:
                doc.rewrite_images(dpi_threshold=160, dpi_target=110)
            except (AttributeError, RuntimeError, ValueError):
                pass
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(dest_path), **PDF_SAVE_OPTS)


def merge_pdf_files(source_paths: list[Path], dest_path: Path) -> int:
    """Une varios PDF en orden. Devuelve el total de páginas."""
    if not source_paths:
        raise ValueError("No hay archivos PDF para unir.")
    out = fitz.open()
    try:
        for path in source_paths:
            with fitz.open(str(path)) as doc:
                out.insert_pdf(doc)
        if len(out) == 0:
            raise ValueError("El PDF resultante quedó vacío.")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(dest_path), **PDF_SAVE_OPTS)
        return len(out)
    finally:
        out.close()


def extract_pdf_pages(
    source_path: Path,
    page_numbers: list[int],
    dest_path: Path,
) -> None:
    """Extrae páginas concretas (1-indexadas, en orden) a un PDF."""
    if not page_numbers:
        raise ValueError("No hay páginas para extraer.")
    with fitz.open(str(source_path)) as src:
        total = len(src)
        out = fitz.open()
        try:
            for page_num in page_numbers:
                if page_num < 1 or page_num > total:
                    raise ValueError(f"La página {page_num} no existe (total: {total}).")
                out.insert_pdf(src, from_page=page_num - 1, to_page=page_num - 1)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            out.save(str(dest_path), **PDF_SAVE_OPTS)
        finally:
            out.close()


def add_batch_part(
    jobs_dir: Path,
    batch_id: str,
    *,
    job_id: str,
    page_from: int,
    page_to: int,
) -> dict[str, Any]:
    batch = load_batch(jobs_dir, batch_id)
    if not batch:
        raise KeyError(f"Lote {batch_id} no encontrado")
    part = {
        "job_id": job_id,
        "page_from": page_from,
        "page_to": page_to,
        "label": f"páginas {page_from}-{page_to}",
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    parts: list[dict[str, Any]] = list(batch.get("parts") or [])
    parts = [p for p in parts if not (p.get("page_from") == page_from and p.get("page_to") == page_to)]
    parts.append(part)
    parts.sort(key=lambda item: (item.get("page_from", 0), item.get("page_to", 0)))
    batch["parts"] = parts
    save_batch(jobs_dir, batch_id, batch)
    return part


def update_batch_part_status(
    jobs_dir: Path,
    batch_id: str,
    job_id: str,
    status: str,
) -> None:
    batch = load_batch(jobs_dir, batch_id)
    if not batch:
        return
    for part in batch.get("parts") or []:
        if part.get("job_id") == job_id:
            part["status"] = status
    save_batch(jobs_dir, batch_id, batch)


def sync_batch_from_jobs(jobs_dir: Path, batch_id: str, read_job_state) -> dict[str, Any]:
    batch = load_batch(jobs_dir, batch_id)
    if not batch:
        return {}
    for part in batch.get("parts") or []:
        job_id = part.get("job_id")
        if not job_id:
            continue
        state = read_job_state(job_id)
        if state:
            part["status"] = state.get("status", part.get("status"))
            part["progress"] = state.get("progress", 0)
            part["detail"] = state.get("detail", "")
    completed = sum(1 for p in batch.get("parts") or [] if p.get("status") == "completed")
    batch["parts_completed"] = completed
    batch["parts_total"] = len(batch.get("parts") or [])
    batch["merge_ready"] = completed > 0 and completed == batch["parts_total"]
    total_pages = int(batch.get("total_pages") or 0)
    full_parts = [
        p for p in batch.get("parts") or []
        if p.get("status") == "completed"
        and int(p.get("page_from") or 0) == 1
        and int(p.get("page_to") or 0) >= total_pages
    ]
    batch["full_document_ready"] = bool(full_parts)
    if full_parts:
        batch["full_document_job_id"] = full_parts[0].get("job_id")
    save_batch(jobs_dir, batch_id, batch)
    return batch


def merge_batch_markdown(
    jobs_dir: Path,
    batch_id: str,
    output_dir: Path,
    read_job_state,
) -> str:
    batch = sync_batch_from_jobs(jobs_dir, batch_id, read_job_state)
    if not batch:
        raise FileNotFoundError("Lote no encontrado")
    parts = sorted(batch.get("parts") or [], key=lambda p: p.get("page_from", 0))
    chunks: list[str] = []
    stem = Path(batch.get("source_filename") or "documento").stem
    chunks.append(f"# {stem}\n")
    chunks.append(
        f"\n> Documento completo por partes — {batch.get('total_pages', '?')} páginas en el PDF original.\n"
    )
    for part in parts:
        if part.get("status") != "completed":
            continue
        job_id = part.get("job_id")
        md_path = output_dir / f"{job_id}.md"
        if not md_path.exists():
            continue
        pf, pt = part.get("page_from"), part.get("page_to")
        chunks.append(f"\n\n---\n\n## Parte: páginas {pf}–{pt}\n\n")
        chunks.append(md_path.read_text(encoding="utf-8").strip())
    if len(chunks) <= 2:
        raise ValueError("Aún no hay partes completadas para fusionar.")
    return "\n".join(chunks).strip() + "\n"


def build_batch_zip(
    jobs_dir: Path,
    batch_id: str,
    output_dir: Path,
    zip_dest: Path,
    read_job_state,
) -> Path:
    batch = sync_batch_from_jobs(jobs_dir, batch_id, read_job_state)
    if not batch:
        raise FileNotFoundError("Lote no encontrado")
    stem = Path(batch.get("source_filename") or "documento").stem
    with zipfile.ZipFile(zip_dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for part in sorted(batch.get("parts") or [], key=lambda p: p.get("page_from", 0)):
            if part.get("status") != "completed":
                continue
            job_id = part.get("job_id")
            label = f"{stem}_p{part.get('page_from')}-{part.get('page_to')}"
            for ext in (".md", ".txt"):
                path = output_dir / f"{job_id}{ext}"
                if path.exists():
                    zf.write(path, arcname=f"{label}{ext}")
    if not zip_dest.exists() or zip_dest.stat().st_size == 0:
        raise ValueError("No hay archivos completados para comprimir.")
    return zip_dest


def suggest_page_ranges(total_pages: int, chunk_size: int = 50) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    start = 1
    while start <= total_pages:
        end = min(total_pages, start + chunk_size - 1)
        ranges.append({"page_from": start, "page_to": end})
        start = end + 1
    return ranges

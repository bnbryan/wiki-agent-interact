import re
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File

from ..config import (
    CATEGORIES,
    CATEGORY_EXTENSIONS,
    MAX_FILE_SIZE,
    TEXT_CATEGORIES,
    category_dir,
)
from ..db import get_db

router = APIRouter(prefix="/api/wiki", tags=["wiki"])

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._一-鿿-]+")

_IMAGE_EXTS = CATEGORY_EXTENSIONS["images"]
_PDF_EXTS = CATEGORY_EXTENSIONS["pdfs"]


def _safe_filename(name: str) -> tuple[str, str]:
    """Return (safe_filename, lowercased_ext_with_dot)."""
    name = Path(name).name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        raise HTTPException(400, "Filename must have an extension")
    ext_lower = f".{ext.lower()}"
    cleaned = _SAFE_NAME.sub("_", stem).strip("_") or "wiki"
    return f"{cleaned}{ext_lower}", ext_lower


def _resolve_category(requested: str | None, ext: str) -> str:
    """Decide the final category.

    Images and PDFs are auto-routed by extension regardless of `requested`.
    Text uploads must declare one of TEXT_CATEGORIES.
    """
    if ext in _IMAGE_EXTS:
        return "images"
    if ext in _PDF_EXTS:
        return "pdfs"
    if requested not in TEXT_CATEGORIES:
        raise HTTPException(
            400,
            f"For text files you must select a category, one of {list(TEXT_CATEGORIES)}",
        )
    if ext not in CATEGORY_EXTENSIONS[requested]:
        raise HTTPException(
            400,
            f"Extension {ext} is not allowed for category '{requested}'. "
            f"Allowed: {sorted(CATEGORY_EXTENSIONS[requested])}",
        )
    return requested


@router.post("/upload")
async def upload_wiki(
    file: UploadFile = File(...),
    category: str | None = Form(None),
    db: aiosqlite.Connection = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(400, "Missing filename")

    safe, ext = _safe_filename(file.filename)
    final_cat = _resolve_category(category, ext)

    dst_dir = category_dir(final_cat)
    dst = dst_dir / safe
    if dst.exists():
        raise HTTPException(409, f"{final_cat}/{safe} already exists")

    data = await file.read()
    if len(data) == 0:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE} bytes")

    dst.write_bytes(data)

    try:
        await db.execute(
            "INSERT INTO wikis (category, filename, original, size_bytes) VALUES (?, ?, ?, ?)",
            (final_cat, safe, file.filename, len(data)),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        # Race: another concurrent upload won.
        dst.unlink(missing_ok=True)
        raise HTTPException(409, f"{final_cat}/{safe} already exists")

    return {
        "filename": safe,
        "category": final_cat,
        "size": len(data),
    }


@router.get("/list")
async def list_wikis(db: aiosqlite.Connection = Depends(get_db)):
    cur = await db.execute(
        "SELECT category, filename, original, size_bytes, uploaded_at "
        "FROM wikis ORDER BY uploaded_at DESC"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/categories")
async def get_categories():
    return {
        "all": list(CATEGORIES),
        "text": list(TEXT_CATEGORIES),
        "extensions": {k: sorted(v) for k, v in CATEGORY_EXTENSIONS.items()},
    }


@router.delete("/{category}/{filename}")
async def delete_wiki(
    category: str,
    filename: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    if category not in CATEGORIES:
        raise HTTPException(404, "Unknown category")
    safe = Path(filename).name
    if safe != filename:
        raise HTTPException(400, "Invalid filename")

    (category_dir(category) / safe).unlink(missing_ok=True)

    await db.execute(
        "DELETE FROM wikis WHERE category = ? AND filename = ?",
        (category, safe),
    )
    await db.commit()
    return {"deleted": f"{category}/{safe}"}

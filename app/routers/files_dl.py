"""条款文件下载：原始 PDF / OCR 文字版。"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import UPLOAD_DIR
from app.database import get_db
from app.models import UploadedFile

router = APIRouter(prefix="/api/files", tags=["files"])


class RenameIn(BaseModel):
    name: str


@router.put("/{file_id}/rename")
def rename_file(file_id: int, body: RenameIn, db: Session = Depends(get_db)):
    """改显示名（不动磁盘文件）。条款分析里的 source_doc 标注在下次重新分析时更新。"""
    rec = db.get(UploadedFile, file_id)
    if not rec:
        raise HTTPException(404, "文件不存在")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if rec.media_type == "pdf" and not name.lower().endswith(".pdf"):
        name += ".pdf"
    rec.original_name = name[:255]
    db.commit()
    return {"ok": True, "name": rec.original_name}


def _attachment_headers(filename: str) -> dict:
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}


@router.get("/{file_id}/download")
def download_file(file_id: int, db: Session = Depends(get_db)):
    rec = db.get(UploadedFile, file_id)
    if not rec:
        raise HTTPException(404, "文件不存在")
    path = UPLOAD_DIR / rec.stored_path
    if not path.exists():
        raise HTTPException(404, "文件已丢失")
    media = "application/pdf" if rec.media_type == "pdf" else "application/octet-stream"
    return FileResponse(path, media_type=media, headers=_attachment_headers(rec.original_name))


@router.get("/{file_id}/ocr-text")
def download_ocr_text(file_id: int, db: Session = Depends(get_db)):
    rec = db.get(UploadedFile, file_id)
    if not rec:
        raise HTTPException(404, "文件不存在")
    cache = UPLOAD_DIR / f"{rec.sha256}.ocr.txt"
    if not cache.exists():
        raise HTTPException(404, "该文件还没有 OCR 识别结果（分析一次后即可下载）")
    text = cache.read_text(encoding="utf-8").replace("\x0c", "\n\n")
    base = rec.original_name.rsplit(".", 1)[0]
    return PlainTextResponse(
        text, media_type="text/plain; charset=utf-8",
        headers=_attachment_headers(f"{base}_文字版.txt"),
    )

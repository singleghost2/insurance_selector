"""上传文件保存、sha256 去重、类型判断。"""
import hashlib
import re

from sqlalchemy.orm import Session

from app.config import UPLOAD_DIR
from app.models import UploadedFile

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class UnsupportedFileError(ValueError):
    pass


def _safe_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)[:120] or "file"


def detect_media_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if any(lower.endswith(ext) for ext in IMAGE_EXTS):
        return "image"
    raise UnsupportedFileError(f"不支持的文件类型：{filename}（仅支持 PDF 和 jpg/png/webp 图片）")


def save_upload(db: Session, filename: str, content: bytes, purpose: str,
                sha: str | None = None) -> tuple[UploadedFile, bool]:
    """保存上传文件。返回 (记录, 是否已存在的旧文件)。

    sha 可外部指定（如链接导入时按源图片内容计算，避免生成的 PDF 因时间戳字节不同而绕过去重）。
    """
    media_type = detect_media_type(filename)
    sha = sha or hashlib.sha256(content).hexdigest()

    existing = (
        db.query(UploadedFile).filter(UploadedFile.sha256 == sha, UploadedFile.purpose == purpose).first()
    )
    if existing:
        return existing, True

    stored_name = f"{sha[:8]}_{_safe_name(filename)}"
    (UPLOAD_DIR / stored_name).write_bytes(content)

    rec = UploadedFile(
        original_name=filename,
        stored_path=stored_name,
        media_type=media_type,
        purpose=purpose,
        sha256=sha,
        size_bytes=len(content),
    )
    db.add(rec)
    db.flush()
    return rec, False


def file_path(rec: UploadedFile):
    return UPLOAD_DIR / rec.stored_path

"""PDF 处理管线：文字/扫描判定、按页抽文本、分块、扫描页转写。"""
import logging
from pathlib import Path

import fitz  # PyMuPDF

from app.config import UPLOAD_DIR, settings
from app.llm import client as llm
from app.llm import prompts

logger = logging.getLogger(__name__)

PAGE_MARK = "【第{n}页】"


def estimate_tokens(text: str) -> int:
    """中文场景粗估：1 字符 ≈ 0.75 token，留余量。"""
    return int(len(text) * 0.75)


def inspect_pdf(path: Path) -> tuple[int, bool]:
    """返回 (页数, 是否扫描版)。判定：平均每页<80有效字符 或 有文本页占比<30%。"""
    with fitz.open(path) as doc:
        page_count = len(doc)
        if page_count == 0:
            raise ValueError("PDF 没有任何页面")
        char_counts = [len("".join(page.get_text().split())) for page in doc]
    pages_with_text = sum(1 for c in char_counts if c >= 80)
    avg_chars = sum(char_counts) / page_count
    is_scanned = avg_chars < 80 or (pages_with_text / page_count) < 0.3
    return page_count, is_scanned


def extract_text_pages(path: Path) -> list[str]:
    """逐页抽文本，每页带【第N页】标记。"""
    pages = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            pages.append(PAGE_MARK.format(n=i) + "\n" + page.get_text().strip())
    return pages


def render_pages_png(path: Path, page_numbers: list[int], dpi: int = 150) -> list[bytes]:
    """渲染指定页（1-based）为 PNG bytes。"""
    images = []
    with fitz.open(path) as doc:
        for n in page_numbers:
            pix = doc[n - 1].get_pixmap(dpi=dpi)
            images.append(pix.tobytes("png"))
    return images


def ocr_scanned_pdf(path: Path, sha256: str, page_count: int, progress_cb=None) -> list[str]:
    """扫描版 PDF：视觉模型逐批(2页/批)转写，带缓存。返回带页码标记的每页文本。"""
    cache = UPLOAD_DIR / f"{sha256}.ocr.txt"
    if cache.exists():
        logger.info("命中 OCR 缓存：%s", cache.name)
        return cache.read_text(encoding="utf-8").split("\x0c")

    if page_count > settings.max_scanned_pages:
        raise ValueError(f"扫描版 PDF 共 {page_count} 页，超过上限 {settings.max_scanned_pages} 页")

    batch_size = 2
    pages_text: list[str] = []
    batches = [list(range(i, min(i + batch_size, page_count + 1))) for i in range(1, page_count + 1, batch_size)]
    for bi, batch in enumerate(batches):
        if progress_cb:
            progress_cb(int(bi / len(batches) * 100), f"正在识别扫描页 {batch[0]}-{batch[-1]}/{page_count}")
        images = render_pages_png(path, batch)
        text = llm.vision_text(
            prompts.OCR_SYSTEM,
            images,
            f"以上共 {len(batch)} 页，起始页码为第 {batch[0]} 页。请按要求逐页转写。",
        )
        pages_text.append(text.strip())

    cache.write_text("\x0c".join(pages_text), encoding="utf-8")
    return pages_text


def get_policy_full_text(path: Path, sha256: str, is_scanned: bool, page_count: int, progress_cb=None) -> str:
    """获取条款全文（文字版直接抽取；扫描版走视觉转写）。"""
    if is_scanned:
        pages = ocr_scanned_pdf(path, sha256, page_count, progress_cb)
    else:
        pages = extract_text_pages(path)
    return "\n\n".join(pages)


def split_pages_into_chunks(pages: list[str], chunk_token_limit: int) -> list[str]:
    """按页边界切块，块间重叠 1 页。"""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for page in pages:
        pt = estimate_tokens(page)
        if current and current_tokens + pt > chunk_token_limit:
            chunks.append("\n\n".join(current))
            current = [current[-1]]  # 重叠上一页
            current_tokens = estimate_tokens(current[0])
        current.append(page)
        current_tokens += pt
    if current:
        chunks.append("\n\n".join(current))
    return chunks

"""从蚂蚁保等图片式条款页面导入：解析 list JSON → 下载图片 → 拼成 PDF 入库。

蚂蚁保条款链接形如：
https://render.alipay.com/p/yuyan/.../index.html?...&list=https%3A%2F%2Fgw.alipayobjects.com%2F...%2Fxxx.json
其中 list 参数指向的 JSON 结构为 {"result": [{"imgs": [图片URL, ...]}]}，图片按页序排列。
"""
import hashlib
import logging
from urllib.parse import parse_qs, unquote, urlparse

import fitz
import httpx
from sqlalchemy.orm import Session

from app.models import UploadedFile
from app.services import files

logger = logging.getLogger(__name__)

MAX_IMAGES = 200
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


class PolicyImportError(ValueError):
    pass


def extract_list_url(page_url: str) -> str:
    """从条款页面 URL 中取出 list 参数指向的 JSON 地址。也接受直接粘贴 JSON 地址。"""
    page_url = page_url.strip()
    parsed = urlparse(page_url)
    if parsed.scheme not in ("http", "https"):
        raise PolicyImportError("请粘贴以 http(s):// 开头的完整链接")

    if parsed.path.endswith(".json"):
        return page_url

    qs = parse_qs(parsed.query)
    if "list" in qs and qs["list"]:
        list_url = unquote(qs["list"][0])
        if urlparse(list_url).scheme in ("http", "https"):
            return list_url
    raise PolicyImportError(
        "链接中没有找到条款图片清单（list 参数）。请从蚂蚁保「保险条款」页面复制完整链接，"
        "它应包含 list=https%3A%2F%2F... 这样的参数"
    )


def fetch_image_urls(page_url: str) -> list[str]:
    list_url = extract_list_url(page_url)
    try:
        resp = httpx.get(list_url, headers={"User-Agent": _UA}, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise PolicyImportError(f"获取条款图片清单失败：{e}") from e
    except ValueError as e:
        raise PolicyImportError("条款清单不是合法的 JSON，链接可能不对") from e

    urls: list[str] = []
    for item in data.get("result", []) or []:
        urls.extend(item.get("imgs", []) or [])
    urls = [u for u in urls if isinstance(u, str) and u.startswith("http")]
    if not urls:
        raise PolicyImportError("条款清单里没有图片，链接可能不对")
    if len(urls) > MAX_IMAGES:
        raise PolicyImportError(f"条款共 {len(urls)} 页，超过 {MAX_IMAGES} 页上限")
    return urls


def download_images(urls: list[str], progress_cb=None) -> list[bytes]:
    images: list[bytes] = []
    with httpx.Client(headers={"User-Agent": _UA}, timeout=60, follow_redirects=True) as client:
        for i, url in enumerate(urls):
            if progress_cb:
                progress_cb(int(i / len(urls) * 100), f"正在下载条款图片 {i + 1}/{len(urls)}")
            resp = client.get(url)
            if resp.status_code != 200 or not resp.content:
                raise PolicyImportError(f"下载第 {i + 1} 页图片失败（HTTP {resp.status_code}）")
            images.append(resp.content)
    return images


def images_to_pdf(images: list[bytes]) -> bytes:
    """每张图片一页，按原始尺寸拼成 PDF。"""
    doc = fitz.open()
    for i, img_bytes in enumerate(images):
        try:
            img = fitz.open(stream=img_bytes)
            rect = img[0].rect
            img.close()
        except Exception as e:
            raise PolicyImportError(f"第 {i + 1} 页图片无法解析：{e}") from e
        page = doc.new_page(width=rect.width, height=rect.height)
        page.insert_image(rect, stream=img_bytes)
    return doc.tobytes()


def import_from_url(db: Session, page_url: str, name: str | None = None) -> tuple[UploadedFile, bool]:
    """完整导入：返回 (UploadedFile, 是否已存在)。去重基于源图片内容的 sha256。"""
    urls = fetch_image_urls(page_url)

    # 先按图片 URL 清单做一次快速去重探测（URL 含内容指纹，基本稳定）
    url_sha = hashlib.sha256("\n".join(urls).encode()).hexdigest()
    existing = (
        db.query(UploadedFile)
        .filter(UploadedFile.sha256 == url_sha, UploadedFile.purpose == "policy")
        .first()
    )
    if existing:
        return existing, True

    images = download_images(urls)
    pdf_bytes = images_to_pdf(images)
    clean = (name or "").strip()
    if clean.lower().endswith(".pdf"):
        clean = clean[:-4].strip()
    filename = (clean or f"蚂蚁保条款导入_{url_sha[:8]}") + ".pdf"
    file_rec, existed = files.save_upload(db, filename, pdf_bytes, "policy", sha=url_sha)
    if not existed:
        file_rec.page_count = len(images)
        file_rec.is_scanned = True
        db.commit()
    return file_rec, existed

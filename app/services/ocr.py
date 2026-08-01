"""统一 OCR 入口，OCR_ENGINE 三选一：
- paddle_api：PaddleOCR 官方云端 API（PaddleOCR-VL，默认，质量最好，需 token）
- paddle：本地 PaddleOCR（免费离线，需 pip install paddlepaddle paddleocr）
- vision：视觉大模型（需要供应商有视觉模型）
"""
import json
import logging
import threading
import time
from pathlib import Path

import httpx

from app.config import settings
from app.llm import prompts

logger = logging.getLogger(__name__)

PAGE_MARK = "【第{n}页】"


# ---------- PaddleOCR 云端 API ----------

def _paddle_api_ocr_file(file_bytes: bytes, filename: str, progress_cb=None) -> list[str]:
    """提交单个文件（PDF 或图片）到 PaddleOCR 云端 API，返回每页文本（不带页码标记）。"""
    token = settings.paddle_ocr_api_token
    if not token:
        raise RuntimeError("未配置 PADDLE_OCR_API_TOKEN，请在 .env 中填写（或改用其他 OCR_ENGINE）")
    headers = {"Authorization": f"bearer {token}"}
    optional = {"useDocOrientationClassify": False, "useDocUnwarping": False, "useChartRecognition": False}

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            settings.paddle_ocr_api_url,
            headers=headers,
            data={"model": settings.paddle_ocr_api_model, "optionalPayload": json.dumps(optional)},
            files={"file": (filename, file_bytes)},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"PaddleOCR API 提交失败（HTTP {resp.status_code}）：{resp.text[:300]}")
        job_id = resp.json()["data"]["jobId"]
        logger.info("PaddleOCR API 任务已提交：%s", job_id)

        deadline = time.monotonic() + 1800  # 30 分钟上限
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError("PaddleOCR API 识别超时（30 分钟）")
            time.sleep(5)
            r = client.get(f"{settings.paddle_ocr_api_url}/{job_id}", headers=headers)
            if r.status_code != 200:
                continue  # 偶发抖动，下轮重查
            data = r.json()["data"]
            state = data.get("state")
            if state == "done":
                json_url = data["resultUrl"]["jsonUrl"]
                break
            if state == "failed":
                raise RuntimeError(f"PaddleOCR API 识别失败：{data.get('errorMsg', '未知原因')}")
            if progress_cb and state == "running":
                prog = data.get("extractProgress") or {}
                total, done_pages = prog.get("totalPages"), prog.get("extractedPages")
                if total:
                    progress_cb(int((done_pages or 0) / total * 100),
                                f"云端 OCR 识别中 {done_pages or 0}/{total} 页")

        jr = client.get(json_url)
        jr.raise_for_status()

    pages: list[str] = []
    for line in jr.text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        result = json.loads(line).get("result", {})
        for res in result.get("layoutParsingResults", []):
            pages.append(((res.get("markdown") or {}).get("text") or "").strip())
    if not pages:
        raise RuntimeError("PaddleOCR API 返回了空结果")
    return pages


# ---------- 本地 PaddleOCR ----------

_paddle_lock = threading.Lock()
_paddle_ocr = None


def _get_paddle():
    global _paddle_ocr
    if _paddle_ocr is None:
        with _paddle_lock:
            if _paddle_ocr is None:
                try:
                    from paddleocr import PaddleOCR
                except ImportError as e:
                    raise RuntimeError(
                        "本地 PaddleOCR 未安装。请执行 pip install paddlepaddle paddleocr，"
                        "或在 .env 中设置 OCR_ENGINE=paddle_api / vision"
                    ) from e
                logger.info("正在初始化本地 PaddleOCR（首次运行会下载模型）…")
                try:  # paddleocr 3.x
                    _paddle_ocr = PaddleOCR(
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                        lang="ch",
                    )
                except TypeError:  # paddleocr 2.x
                    _paddle_ocr = PaddleOCR(use_angle_cls=False, show_log=False, lang="ch")
    return _paddle_ocr


def _paddle_local_ocr_one(img_bytes: bytes) -> str:
    import cv2
    import numpy as np

    arr = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("图片解码失败")
    ocr = _get_paddle()

    if hasattr(ocr, "predict"):  # paddleocr 3.x
        results = ocr.predict(arr)
        lines: list[str] = []
        for res in results:
            texts = None
            try:
                texts = res["rec_texts"]
            except (TypeError, KeyError):
                j = getattr(res, "json", None)
                if isinstance(j, dict):
                    texts = j.get("res", {}).get("rec_texts")
            if texts:
                lines.extend(texts)
        return "\n".join(lines)

    results = ocr.ocr(arr, cls=False)  # paddleocr 2.x
    lines = []
    for page in results or []:
        for item in page or []:
            lines.append(item[1][0])
    return "\n".join(lines)


# ---------- 统一入口 ----------

def _add_marks(pages: list[str], start_page: int) -> list[str]:
    return [PAGE_MARK.format(n=start_page + i) + "\n" + t for i, t in enumerate(pages)]


def ocr_page_images(images: list[bytes], start_page: int = 1, progress_cb=None) -> list[str]:
    """把一组页面图片 OCR 成带【第N页】标记的文本列表。"""
    engine = settings.ocr_engine
    if engine == "vision":
        from app.llm import client as llm

        batch_size = 2
        pages: list[str] = []
        total = len(images)
        for bi in range(0, total, batch_size):
            batch = images[bi : bi + batch_size]
            first = start_page + bi
            if progress_cb:
                progress_cb(int(bi / total * 100),
                            f"视觉模型识别第 {first}-{first + len(batch) - 1} 页（共 {total} 页）")
            text = llm.vision_text(
                prompts.OCR_SYSTEM, batch,
                f"以上共 {len(batch)} 页，起始页码为第 {first} 页。请按要求逐页转写。",
            )
            pages.append(text.strip())
        return pages

    if engine == "paddle_api":
        pages = []
        for i, img in enumerate(images):
            if progress_cb:
                progress_cb(int(i / len(images) * 100), f"云端 OCR 识别第 {i + 1}/{len(images)} 张图片")
            pages.extend(_paddle_api_ocr_file(img, f"page_{start_page + i}.png"))
        return _add_marks(pages, start_page)

    # 本地 paddle
    pages = []
    for i, img in enumerate(images):
        if progress_cb:
            progress_cb(int(i / len(images) * 100),
                        f"本地 OCR 识别第 {start_page + i} 页（共 {len(images)} 页）")
        pages.append(_paddle_local_ocr_one(img))
    return _add_marks(pages, start_page)


def ocr_pdf(path: Path, page_count: int, progress_cb=None) -> list[str]:
    """整本扫描版 PDF OCR，返回带页码标记的每页文本。paddle_api 整本直传，其余引擎逐页渲染。"""
    if settings.ocr_engine == "paddle_api":
        pages = _paddle_api_ocr_file(path.read_bytes(), path.name, progress_cb)
        return _add_marks(pages, 1)

    from app.services import pdf_pipeline

    images = pdf_pipeline.render_pages_png(path, list(range(1, page_count + 1)))
    return ocr_page_images(images, start_page=1, progress_cb=progress_cb)


def ocr_to_text(file_bytes: bytes, filename: str, progress_cb=None) -> str:
    """单个图片/文件 OCR 成纯文本（健康材料用，不带页码标记）。"""
    if settings.ocr_engine == "paddle_api":
        return "\n\n".join(_paddle_api_ocr_file(file_bytes, filename, progress_cb))
    if settings.ocr_engine == "vision":
        from app.llm import client as llm

        return llm.vision_text(prompts.OCR_SYSTEM, [file_bytes], "请忠实转写以上材料中的全部文字。")
    return _paddle_local_ocr_one(file_bytes)

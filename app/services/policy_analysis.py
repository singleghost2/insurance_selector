"""条款分析编排：全文直接分析 or map-reduce 两分支。"""
import json
import logging
import re

from sqlalchemy.orm import Session

from app.config import settings
from app.llm import client as llm
from app.llm import prompts
from app.models import AnalysisTask, InsuranceProduct, KeyClause, CLAUSE_CATEGORIES
from app.schemas import ChunkExtractResult, PolicyAnalysisResult
from app.services import files, pdf_pipeline

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def verify_quote(quote: str | None, full_text_normalized: str) -> bool | None:
    """引用核实：去空白后做子串匹配；长引用取前60字符匹配。"""
    if not quote:
        return None
    q = _normalize(quote)
    if len(q) < 8:
        return None
    return q in full_text_normalized or q[:60] in full_text_normalized


def analyze(db: Session, task: AnalysisTask, update_progress) -> None:
    product = db.get(InsuranceProduct, task.target_id)
    if product is None:
        raise ValueError(f"产品 {task.target_id} 不存在")
    file_rec = product.file
    path = files.file_path(file_rec)

    update_progress(5, "正在读取 PDF")
    if file_rec.page_count is None or file_rec.is_scanned is None:
        file_rec.page_count, file_rec.is_scanned = pdf_pipeline.inspect_pdf(path)
        db.commit()

    if file_rec.is_scanned:
        def ocr_progress(pct, msg):
            # OCR 占总进度的 5-60%
            update_progress(5 + int(pct * 0.55), msg)
        pages = pdf_pipeline.ocr_scanned_pdf(path, file_rec.sha256, file_rec.page_count, ocr_progress)
    else:
        pages = pdf_pipeline.extract_text_pages(path)

    full_text = "\n\n".join(pages)
    total_tokens = pdf_pipeline.estimate_tokens(full_text)
    logger.info("条款全文约 %d tokens（%d 页）", total_tokens, len(pages))

    if total_tokens <= settings.full_text_token_limit:
        update_progress(65, "正在分析条款全文（可能需要几分钟）")
        result = llm.chat_json(
            prompts.POLICY_ANALYSIS_SYSTEM,
            f"以下是保险条款全文：\n\n{full_text}",
            PolicyAnalysisResult,
        )
    else:
        result = _map_reduce(pages, update_progress)

    update_progress(95, "正在保存分析结果")
    _save_result(db, product, result, _normalize(full_text))


def _map_reduce(pages: list[str], update_progress) -> PolicyAnalysisResult:
    chunks = pdf_pipeline.split_pages_into_chunks(pages, settings.chunk_token_limit)
    all_hits = []
    for i, chunk in enumerate(chunks):
        update_progress(10 + int(i / len(chunks) * 70), f"正在分析第 {i + 1}/{len(chunks)} 块条款")
        result: ChunkExtractResult = llm.chat_json(
            prompts.CHUNK_EXTRACT_SYSTEM,
            f"以下是条款片段：\n\n{chunk}",
            ChunkExtractResult,
        )
        all_hits.extend(h.model_dump() for h in result.hits)

    update_progress(85, "正在综合所有片段生成最终分析")
    return llm.chat_json(
        prompts.REDUCE_SYSTEM,
        "以下是从条款全文中抽取的关键条款摘录集合：\n\n" + json.dumps(all_hits, ensure_ascii=False, indent=1),
        PolicyAnalysisResult,
    )


def _save_result(db: Session, product: InsuranceProduct, result: PolicyAnalysisResult,
                 full_text_normalized: str) -> None:
    info = result.basic_info
    product.name = info.name
    product.company = info.company
    product.coverage_amount = info.coverage_amount
    product.deductible = info.deductible
    product.guaranteed_renewal = info.guaranteed_renewal
    product.pros_json = json.dumps(result.pros, ensure_ascii=False)
    product.cons_json = json.dumps(result.cons, ensure_ascii=False)
    product.analysis_json = result.model_dump_json()

    # 重新分析时清掉旧条款
    db.query(KeyClause).filter(KeyClause.product_id == product.id).delete()

    order = {cat: i for i, cat in enumerate(CLAUSE_CATEGORIES)}
    for i, item in enumerate(result.key_clauses):
        category = item.category if item.category in CLAUSE_CATEGORIES else "other"
        db.add(KeyClause(
            product_id=product.id,
            category=category,
            title=item.title[:200],
            summary=item.summary,
            risk_level=item.risk_level if item.risk_level in ("high", "medium", "low", "info") else "info",
            quote=item.quote,
            quote_verified=verify_quote(item.quote, full_text_normalized),
            page_no=item.page_no,
            sort_order=order.get(category, 99) * 100 + i,
        ))
    db.commit()

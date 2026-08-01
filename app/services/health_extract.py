"""健康材料解析编排：图片/扫描PDF 走视觉模型，文字版 PDF 走文本模型。"""
import logging
from datetime import date

from sqlalchemy.orm import Session

from app.llm import client as llm
from app.llm import prompts
from app.models import AnalysisTask, HealthFinding, HealthRecord, FINDING_KINDS
from app.schemas import HealthExtractResult
from app.services import files, pdf_pipeline

logger = logging.getLogger(__name__)

# 视觉模型单次请求的最大页数（体检报告通常页数不多）
MAX_VISION_PAGES = 8


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def extract(db: Session, task: AnalysisTask, update_progress) -> None:
    record = db.get(HealthRecord, task.target_id)
    if record is None:
        raise ValueError(f"健康记录 {task.target_id} 不存在")
    file_rec = record.file
    path = files.file_path(file_rec)

    user_prompt = "请从以上材料中抽取结构化健康信息。"

    if file_rec.media_type == "image":
        update_progress(20, "正在用视觉模型解析图片")
        mime = "image/jpeg" if file_rec.original_name.lower().endswith((".jpg", ".jpeg")) else "image/png"
        result: HealthExtractResult = llm.vision_json(
            prompts.HEALTH_EXTRACT_SYSTEM, [path.read_bytes()], user_prompt, HealthExtractResult, mime=mime,
        )
    else:
        if file_rec.page_count is None or file_rec.is_scanned is None:
            file_rec.page_count, file_rec.is_scanned = pdf_pipeline.inspect_pdf(path)
            db.commit()
        if file_rec.is_scanned:
            if file_rec.page_count > MAX_VISION_PAGES:
                raise ValueError(
                    f"扫描版健康材料共 {file_rec.page_count} 页，超过 {MAX_VISION_PAGES} 页上限，"
                    "请拆分后分次上传"
                )
            update_progress(20, f"正在用视觉模型解析扫描件（{file_rec.page_count} 页）")
            images = pdf_pipeline.render_pages_png(path, list(range(1, file_rec.page_count + 1)))
            result = llm.vision_json(prompts.HEALTH_EXTRACT_SYSTEM, images, user_prompt, HealthExtractResult)
        else:
            update_progress(20, "正在解析 PDF 文本")
            text = "\n\n".join(pdf_pipeline.extract_text_pages(path))
            result = llm.chat_json(
                prompts.HEALTH_EXTRACT_SYSTEM,
                f"以下是材料文本：\n\n{text}\n\n{user_prompt}",
                HealthExtractResult,
            )

    update_progress(90, "正在保存健康档案")
    record.record_type = result.record_type
    record.exam_date = parse_date(result.exam_date)
    record.institution = result.institution
    record.conclusion = result.conclusion
    record.raw_extract_json = result.model_dump_json()

    # 重析时清掉本记录旧的非人工 findings（保留人工修正项）
    db.query(HealthFinding).filter(
        HealthFinding.record_id == record.id, HealthFinding.is_manual.is_(False)
    ).delete()

    for f in result.findings:
        db.add(HealthFinding(
            record_id=record.id,
            member_id=record.member_id,
            kind=f.kind if f.kind in FINDING_KINDS else "abnormal_indicator",
            name=f.name[:200],
            value=f.value,
            reference_range=f.reference_range,
            flag=f.flag,
            note=f.note,
            finding_date=record.exam_date,
        ))
    db.commit()

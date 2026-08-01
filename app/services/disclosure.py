"""健康告知辅助：产品健康告知条款 x 成员健康档案。"""
import json

from sqlalchemy.orm import Session

from app.llm import client as llm
from app.llm import prompts
from app.models import AnalysisTask, DisclosureCheck, HealthFinding, KeyClause, FINDING_KINDS
from app.schemas import DisclosureResult


def check(db: Session, task: AnalysisTask, update_progress) -> None:
    dc = db.get(DisclosureCheck, task.target_id)
    if dc is None:
        raise ValueError(f"告知核对 {task.target_id} 不存在")

    clauses = (
        db.query(KeyClause)
        .filter(KeyClause.product_id == dc.product_id, KeyClause.category == "health_disclosure")
        .all()
    )
    disclosure_text = "\n\n".join(
        f"{c.title}\n{c.summary}" + (f"\n原文：{c.quote}" if c.quote else "") for c in clauses
    ) or "（该产品分析结果中未提取到健康告知条款，请按常见百万医疗险健康告知的通用询问事项进行核对）"

    findings = db.query(HealthFinding).filter(HealthFinding.member_id == dc.member_id).all()
    if not findings:
        raise ValueError("该成员还没有任何健康档案记录，请先上传体检报告或手工添加健康记录")

    findings_lines = []
    for f in findings:
        parts = [f"[{FINDING_KINDS.get(f.kind, f.kind)}] {f.name}"]
        if f.value:
            parts.append(f"数值 {f.value}")
        if f.reference_range:
            parts.append(f"参考 {f.reference_range}")
        if f.flag:
            parts.append({"high": "偏高", "low": "偏低", "positive": "阳性/检出"}.get(f.flag, f.flag))
        if f.finding_date:
            parts.append(str(f.finding_date))
        if f.note:
            parts.append(f.note)
        findings_lines.append("，".join(parts))

    member = dc.member
    member_desc = member.name
    if member.birth_year:
        member_desc += f"（{member.birth_year}年出生）"
    if member.notes:
        member_desc += f"，备注：{member.notes}"

    update_progress(30, "正在核对健康告知")
    result: DisclosureResult = llm.chat_json(
        prompts.DISCLOSURE_SYSTEM,
        f"## 产品健康告知条款\n{disclosure_text}\n\n"
        f"## 成员信息\n{member_desc}\n\n"
        f"## 健康档案记录\n" + "\n".join(f"- {line}" for line in findings_lines),
        DisclosureResult,
    )
    dc.result_json = result.model_dump_json()
    db.commit()

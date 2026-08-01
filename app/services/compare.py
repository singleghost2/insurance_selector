"""自选对比：对比表数据组装 + LLM 对比总结。"""
import json

from sqlalchemy.orm import Session

from app.llm import client as llm
from app.llm import prompts
from app.models import AnalysisTask, InsuranceProduct, KeyClause, CLAUSE_CATEGORIES


def build_compare_table(db: Session, products: list[InsuranceProduct]) -> list[dict]:
    """行=维度（基本信息 + 各条款类别），列=产品。"""
    rows = [
        {"label": "保险公司", "cells": [p.company or "—" for p in products]},
        {"label": "保额", "cells": [p.coverage_amount or "—" for p in products]},
        {"label": "免赔额", "cells": [p.deductible or "—" for p in products]},
        {"label": "续保", "cells": [p.guaranteed_renewal or "—" for p in products]},
    ]
    clause_map: dict[tuple[int, str], list[KeyClause]] = {}
    for p in products:
        for c in p.clauses:
            clause_map.setdefault((p.id, c.category), []).append(c)

    for cat, cat_name in CLAUSE_CATEGORIES.items():
        if cat == "other":
            continue
        cells = []
        risks = []
        for p in products:
            items = clause_map.get((p.id, cat), [])
            if items:
                cells.append("；".join(f"{c.title}：{c.summary}" for c in items))
                levels = [c.risk_level for c in items]
                risks.append("high" if "high" in levels else ("medium" if "medium" in levels else "info"))
            else:
                cells.append("条款未提及")
                risks.append("medium")
        rows.append({"label": cat_name, "cells": cells, "risks": risks})
    return rows


def summarize(db: Session, task: AnalysisTask, update_progress) -> None:
    ids = json.loads(task.result_json or "{}").get("product_ids", [])
    products = db.query(InsuranceProduct).filter(InsuranceProduct.id.in_(ids)).all()
    if len(products) < 2:
        raise ValueError("至少需要 2 个已完成分析的产品才能对比")

    payload = []
    for p in products:
        payload.append({
            "产品": p.display_name,
            "分析": json.loads(p.analysis_json) if p.analysis_json else None,
        })

    update_progress(30, "正在生成对比总结")
    md = llm.chat_text(
        prompts.COMPARE_SYSTEM,
        "以下是候选产品的结构化分析结果：\n\n" + json.dumps(payload, ensure_ascii=False),
    )
    task.result_json = json.dumps({"product_ids": ids, "markdown": md}, ensure_ascii=False)
    db.commit()

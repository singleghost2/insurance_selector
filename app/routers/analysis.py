"""健康告知核对 + 对比总结的任务触发，以及 LLM 连通性调试。"""
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AnalysisTask, DisclosureCheck, FamilyMember, InsuranceProduct
from app.schemas import CompareSummaryIn, DisclosureIn
from app.services.task_runner import run_task

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/disclosure")
def start_disclosure(body: DisclosureIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if not db.get(FamilyMember, body.member_id):
        raise HTTPException(404, "成员不存在")
    product = db.get(InsuranceProduct, body.product_id)
    if not product or product.status != "done":
        raise HTTPException(400, "该产品还没有完成条款分析")

    dc = (
        db.query(DisclosureCheck)
        .filter(DisclosureCheck.member_id == body.member_id, DisclosureCheck.product_id == body.product_id)
        .first()
    )
    if dc is None:
        dc = DisclosureCheck(member_id=body.member_id, product_id=body.product_id)
        db.add(dc)
    dc.status = "pending"
    dc.result_json = None
    db.commit()

    task = AnalysisTask(task_type="disclosure", target_id=dc.id, progress_msg="排队中")
    db.add(task)
    db.commit()
    background_tasks.add_task(run_task, task.id)
    return {"task_id": task.id}


@router.post("/compare/summary")
def start_compare_summary(body: CompareSummaryIn, background_tasks: BackgroundTasks,
                          db: Session = Depends(get_db)):
    products = (
        db.query(InsuranceProduct)
        .filter(InsuranceProduct.id.in_(body.product_ids), InsuranceProduct.status == "done")
        .all()
    )
    if len(products) < 2:
        raise HTTPException(400, "至少选择 2 个已完成分析的产品")

    task = AnalysisTask(
        task_type="compare_summary",
        result_json=json.dumps({"product_ids": [p.id for p in products]}),
        progress_msg="排队中",
    )
    db.add(task)
    db.commit()
    task.target_id = task.id
    db.commit()
    background_tasks.add_task(run_task, task.id)
    return {"task_id": task.id}


class _PingResult(BaseModel):
    ok: bool = Field(description="固定为 true")
    model_hint: str = Field(description="你是什么模型，一句话")


@router.get("/debug/llm-ping")
def llm_ping():
    from app.llm.client import chat_json

    result = _PingResult.model_validate(
        chat_json("你是连通性测试助手。", "请按 schema 返回 JSON，ok 填 true。", _PingResult).model_dump()
    )
    return result.model_dump()

"""后台任务统一封装：独立 session、状态流转、异常兜底。"""
import logging
import traceback
from datetime import datetime

from app.database import SessionLocal
from app.models import AnalysisTask, DisclosureCheck, HealthRecord, InsuranceProduct

logger = logging.getLogger(__name__)

# task_type -> (实体模型, 成功后跳转 URL 模板)
_ENTITY_MAP = {
    "policy_analysis": InsuranceProduct,
    "health_extract": HealthRecord,
    "disclosure": DisclosureCheck,
    "compare_summary": None,
}


def redirect_url_for(task: AnalysisTask, db) -> str | None:
    if task.status != "success":
        return None
    if task.task_type == "policy_analysis":
        return f"/products/{task.target_id}"
    if task.task_type == "health_extract":
        rec = db.get(HealthRecord, task.target_id)
        return f"/members/{rec.member_id}" if rec else "/"
    if task.task_type == "disclosure":
        check = db.get(DisclosureCheck, task.target_id)
        return f"/members/{check.member_id}/disclosure/{check.product_id}" if check else "/"
    if task.task_type == "compare_summary":
        return "/compare"
    return "/"


def run_task(task_id: int) -> None:
    """BackgroundTasks 入口。延迟 import 编排函数避免循环依赖。"""
    from app.services import compare, disclosure, health_extract, policy_analysis

    handlers = {
        "policy_analysis": policy_analysis.analyze,
        "health_extract": health_extract.extract,
        "disclosure": disclosure.check,
        "compare_summary": compare.summarize,
    }

    db = SessionLocal()
    try:
        task = db.get(AnalysisTask, task_id)
        if task is None or task.status not in ("pending",):
            return
        task.status = "running"
        task.started_at = datetime.now()
        db.commit()

        def update_progress(pct: int, msg: str = ""):
            task.progress = max(0, min(100, pct))
            if msg:
                task.progress_msg = msg[:500]
            db.commit()

        _set_entity_status(db, task, "analyzing")
        try:
            handlers[task.task_type](db, task, update_progress)
            task.status = "success"
            task.progress = 100
            _set_entity_status(db, task, "done")
        except Exception as e:
            logger.exception("任务 %d (%s) 失败", task_id, task.task_type)
            db.rollback()
            task = db.get(AnalysisTask, task_id)
            task.status = "failed"
            task.error = f"{type(e).__name__}: {str(e)[:1500]}"
            _set_entity_status(db, task, "failed")
        task.finished_at = datetime.now()
        db.commit()
    except Exception:
        logger.error("任务框架异常：%s", traceback.format_exc())
    finally:
        db.close()


def _set_entity_status(db, task: AnalysisTask, status: str) -> None:
    model = _ENTITY_MAP.get(task.task_type)
    if model is None or task.target_id is None:
        return
    entity = db.get(model, task.target_id)
    if entity is not None and hasattr(entity, "status"):
        entity.status = status


def fail_orphan_tasks() -> None:
    """应用启动时：把 running/pending 的任务标记为 failed（进程重启导致中断）。"""
    db = SessionLocal()
    try:
        orphans = db.query(AnalysisTask).filter(AnalysisTask.status.in_(["pending", "running"])).all()
        for t in orphans:
            t.status = "failed"
            t.error = "应用重启导致任务中断，请重试"
            t.finished_at = datetime.now()
            _set_entity_status(db, t, "failed")
        if orphans:
            logger.warning("已清理 %d 个中断任务", len(orphans))
        db.commit()
    finally:
        db.close()

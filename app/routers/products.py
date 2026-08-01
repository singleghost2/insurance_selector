from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AnalysisTask, InsuranceProduct, UploadedFile
from app.services import files
from app.services.task_runner import run_task

router = APIRouter(prefix="/api/products", tags=["products"])


def _create_analysis_task(db: Session, product_id: int) -> AnalysisTask:
    task = AnalysisTask(task_type="policy_analysis", target_id=product_id, progress_msg="排队中")
    db.add(task)
    db.commit()
    return task


@router.post("/upload")
async def upload_policy(background_tasks: BackgroundTasks, file: UploadFile,
                        db: Session = Depends(get_db)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "文件为空")
    try:
        file_rec, existed = files.save_upload(db, file.filename or "policy.pdf", content, "policy")
    except files.UnsupportedFileError as e:
        raise HTTPException(400, str(e))
    if file_rec.media_type != "pdf":
        raise HTTPException(400, "保险条款请上传 PDF 文件")

    if existed:
        product = db.query(InsuranceProduct).filter(InsuranceProduct.file_id == file_rec.id).first()
        if product:
            if product.status == "done":
                return {"duplicate": True, "product_id": product.id,
                        "message": "该条款已分析过，直接查看结果", "redirect_url": f"/products/{product.id}"}
            running = (
                db.query(AnalysisTask)
                .filter(AnalysisTask.task_type == "policy_analysis",
                        AnalysisTask.target_id == product.id,
                        AnalysisTask.status.in_(["pending", "running"]))
                .first()
            )
            if running:
                return {"duplicate": True, "task_id": running.id, "product_id": product.id}
            # 上次失败：重建任务
            product.status = "pending"
            task = _create_analysis_task(db, product.id)
            background_tasks.add_task(run_task, task.id)
            return {"task_id": task.id, "product_id": product.id}

    product = InsuranceProduct(file_id=file_rec.id)
    db.add(product)
    db.commit()
    task = _create_analysis_task(db, product.id)
    background_tasks.add_task(run_task, task.id)
    return {"task_id": task.id, "product_id": product.id}


@router.post("/{product_id}/reanalyze")
def reanalyze(product_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    product = db.get(InsuranceProduct, product_id)
    if not product:
        raise HTTPException(404, "产品不存在")
    running = (
        db.query(AnalysisTask)
        .filter(AnalysisTask.task_type == "policy_analysis", AnalysisTask.target_id == product_id,
                AnalysisTask.status.in_(["pending", "running"]))
        .first()
    )
    if running:
        return {"task_id": running.id, "product_id": product_id}
    product.status = "pending"
    task = _create_analysis_task(db, product_id)
    background_tasks.add_task(run_task, task.id)
    return {"task_id": task.id, "product_id": product_id}


@router.post("/{product_id}/shortlist")
def toggle_shortlist(product_id: int, db: Session = Depends(get_db)):
    product = db.get(InsuranceProduct, product_id)
    if not product:
        raise HTTPException(404, "产品不存在")
    product.is_shortlisted = not product.is_shortlisted
    product.shortlisted_at = datetime.now() if product.is_shortlisted else None
    db.commit()
    return {"is_shortlisted": product.is_shortlisted}


@router.delete("/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(InsuranceProduct, product_id)
    if not product:
        raise HTTPException(404, "产品不存在")
    db.delete(product)
    db.commit()
    return {"ok": True}

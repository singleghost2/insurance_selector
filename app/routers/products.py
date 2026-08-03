from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AnalysisTask, InsuranceProduct, ProductDocument, UploadedFile
from app.services import files, policy_import
from app.services.task_runner import run_task

router = APIRouter(prefix="/api/products", tags=["products"])


def _create_analysis_task(db: Session, product_id: int) -> AnalysisTask:
    task = AnalysisTask(task_type="policy_analysis", target_id=product_id, progress_msg="排队中")
    db.add(task)
    db.commit()
    return task


def _start_analysis(db: Session, background_tasks: BackgroundTasks, product: InsuranceProduct) -> dict:
    running = (
        db.query(AnalysisTask)
        .filter(AnalysisTask.task_type == "policy_analysis",
                AnalysisTask.target_id == product.id,
                AnalysisTask.status.in_(["pending", "running"]))
        .first()
    )
    if running:
        return {"task_id": running.id, "product_id": product.id}
    product.status = "pending"
    task = _create_analysis_task(db, product.id)
    background_tasks.add_task(run_task, task.id)
    return {"task_id": task.id, "product_id": product.id}


def _products_of_file(db: Session, file_id: int) -> list[int]:
    return [d.product_id for d in db.query(ProductDocument).filter(ProductDocument.file_id == file_id).all()]


def _attach_file(db: Session, product: InsuranceProduct, file_rec: UploadedFile) -> bool:
    """把条款文件挂到产品下（同一文件可被多个产品共享）。已在本产品则跳过。返回是否新挂载。"""
    exists = (
        db.query(ProductDocument)
        .filter(ProductDocument.product_id == product.id, ProductDocument.file_id == file_rec.id)
        .first()
    )
    if exists:
        return False
    next_order = len(product.documents)
    db.add(ProductDocument(product_id=product.id, file_id=file_rec.id, sort_order=next_order))
    db.flush()
    db.refresh(product)
    return True


async def _save_policy_files(db: Session, uploads: list[UploadFile]) -> list[tuple[UploadedFile, bool]]:
    saved = []
    for up in uploads:
        content = await up.read()
        if not content:
            continue
        try:
            file_rec, existed = files.save_upload(db, up.filename or "policy.pdf", content, "policy")
        except files.UnsupportedFileError as e:
            raise HTTPException(400, str(e))
        if file_rec.media_type != "pdf":
            raise HTTPException(400, f"《{up.filename}》不是 PDF：保险条款请上传 PDF 文件")
        saved.append((file_rec, existed))
    if not saved:
        raise HTTPException(400, "没有有效的文件")
    return saved


@router.post("/upload")
async def upload_policy(background_tasks: BackgroundTasks,
                        uploads: list[UploadFile] = [],
                        name: str | None = Form(None),
                        product_id: int | None = Form(None),
                        db: Session = Depends(get_db)):
    """上传条款 PDF（可多选）。product_id 为空则新建产品并自动分析；否则追加到该产品（不自动分析）。"""
    saved = await _save_policy_files(db, uploads)

    if product_id is not None:
        product = db.get(InsuranceProduct, product_id)
        if not product:
            raise HTTPException(404, "产品不存在")
        added = sum(1 for file_rec, _ in saved if _attach_file(db, product, file_rec))
        db.commit()
        return {"product_id": product.id, "added": added,
                "message": (f"已添加 {added} 份条款文件。文件齐了之后点「重新分析」即可更新分析结果。"
                            if added else "所选文件已都在该产品中，无需重复添加。"),
                "redirect_url": f"/products/{product.id}"}

    # 新建产品：若所有文件恰好都已属于同一个产品，大概率是重复提交，直接跳过去
    # （条款文件允许被多个产品共享，需要共用文件时从产品详情页「追加」即可）
    owner_sets = [set(_products_of_file(db, file_rec.id)) for file_rec, _ in saved]
    common = set.intersection(*owner_sets) if owner_sets else set()
    if common and all(s for s in owner_sets):
        pid = sorted(common)[0]
        return {"duplicate": True, "product_id": pid,
                "message": "这些条款文件已经全部导入过（同属一个产品），直接查看该产品。"
                           "如果确实要用它们再建一个产品，请先建产品再从详情页追加文件。",
                "redirect_url": f"/products/{pid}"}

    product = InsuranceProduct(name=(name.strip() if name and name.strip() else None))
    db.add(product)
    db.flush()
    for file_rec, _ in saved:
        _attach_file(db, product, file_rec)
    db.commit()
    return _start_analysis(db, background_tasks, product)


class ImportUrlIn(BaseModel):
    url: str
    name: str | None = None       # 产品名称（新建产品时用）
    doc_name: str | None = None   # 条款文件名称；不填则退回产品名称，再退回默认名
    product_id: int | None = None


@router.post("/import-url")
def import_policy_url(body: ImportUrlIn, background_tasks: BackgroundTasks,
                      db: Session = Depends(get_db)):
    """从蚂蚁保等图片式条款链接导入。product_id 为空则新建产品并自动分析；否则追加（不自动分析）。"""
    try:
        file_rec, existed = policy_import.import_from_url(db, body.url, body.doc_name or body.name)
    except policy_import.PolicyImportError as e:
        raise HTTPException(400, str(e))

    if body.product_id is not None:
        product = db.get(InsuranceProduct, body.product_id)
        if not product:
            raise HTTPException(404, "产品不存在")
        added = _attach_file(db, product, file_rec)
        db.commit()
        return {"product_id": product.id, "added": int(added),
                "message": ("已添加该条款文件。文件齐了之后点「重新分析」即可更新分析结果。"
                            if added else "该条款文件已在此产品中"),
                "redirect_url": f"/products/{product.id}"}

    owners = _products_of_file(db, file_rec.id)
    if owners:
        return {"duplicate": True, "product_id": owners[0],
                "message": "该条款链接已导入过，直接查看对应产品。"
                           "如需把它加入其他产品，请到那个产品的详情页用「通过链接添加」。",
                "redirect_url": f"/products/{owners[0]}"}

    product = InsuranceProduct(name=(body.name.strip() if body.name and body.name.strip() else None))
    db.add(product)
    db.flush()
    _attach_file(db, product, file_rec)
    db.commit()
    return _start_analysis(db, background_tasks, product)


@router.delete("/{product_id}/documents/{doc_id}")
def delete_document(product_id: int, doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(ProductDocument, doc_id)
    if not doc or doc.product_id != product_id:
        raise HTTPException(404, "条款文件不存在")
    db.delete(doc)
    db.commit()
    return {"ok": True, "message": "已移除，记得重新分析以更新结果"}


@router.post("/{product_id}/reanalyze")
def reanalyze(product_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    product = db.get(InsuranceProduct, product_id)
    if not product:
        raise HTTPException(404, "产品不存在")
    if not product.documents:
        raise HTTPException(400, "该产品还没有条款文件，请先添加")
    return _start_analysis(db, background_tasks, product)


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

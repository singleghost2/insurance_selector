"""所有服务端渲染页面。"""
import json
from itertools import groupby

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    AnalysisTask,
    DisclosureCheck,
    FamilyMember,
    HealthFinding,
    HealthRecord,
    InsuranceProduct,
    CLAUSE_CATEGORIES,
    FINDING_KINDS,
)
from app.services.compare import build_compare_table
from app.templating import templates

router = APIRouter(tags=["pages"])


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    members = db.query(FamilyMember).order_by(FamilyMember.id).all()
    shortlisted = (
        db.query(InsuranceProduct).filter(InsuranceProduct.is_shortlisted.is_(True)).order_by(InsuranceProduct.id).all()
    )
    finding_counts = {
        m.id: db.query(HealthFinding).filter(HealthFinding.member_id == m.id).count() for m in members
    }
    return templates.TemplateResponse(request, "index.html", {
        "members": members, "shortlisted": shortlisted, "finding_counts": finding_counts,
    })


@router.get("/members/{member_id}")
def member_detail(member_id: int, request: Request, db: Session = Depends(get_db)):
    member = db.get(FamilyMember, member_id)
    if not member:
        raise HTTPException(404, "成员不存在")
    records = (
        db.query(HealthRecord).filter(HealthRecord.member_id == member_id)
        .order_by(HealthRecord.created_at.desc()).all()
    )
    findings = (
        db.query(HealthFinding).filter(HealthFinding.member_id == member_id)
        .order_by(HealthFinding.finding_date.desc().nullslast(), HealthFinding.id.desc()).all()
    )
    # 按日期分组的时间线
    timeline = [
        (d, list(items))
        for d, items in groupby(findings, key=lambda f: f.finding_date)
    ]
    done_products = (
        db.query(InsuranceProduct).filter(InsuranceProduct.status == "done")
        .order_by(InsuranceProduct.is_shortlisted.desc(), InsuranceProduct.id).all()
    )
    checks = {
        c.product_id: c for c in db.query(DisclosureCheck).filter(DisclosureCheck.member_id == member_id).all()
    }
    return templates.TemplateResponse(request, "members/detail.html", {
        "member": member, "records": records, "timeline": timeline,
        "done_products": done_products, "checks": checks, "FINDING_KINDS": FINDING_KINDS,
    })


@router.get("/products")
def product_list(request: Request, db: Session = Depends(get_db)):
    products = db.query(InsuranceProduct).order_by(InsuranceProduct.id.desc()).all()
    # 失败/进行中的产品带上最近任务，便于跳转等待页或重试
    latest_tasks = {}
    for p in products:
        if p.status in ("pending", "analyzing", "failed"):
            t = (
                db.query(AnalysisTask)
                .filter(AnalysisTask.task_type == "policy_analysis", AnalysisTask.target_id == p.id)
                .order_by(AnalysisTask.id.desc()).first()
            )
            if t:
                latest_tasks[p.id] = t
    return templates.TemplateResponse(request, "products/list.html", {
        "products": products, "latest_tasks": latest_tasks,
    })


@router.get("/products/upload")
def product_upload_page(request: Request):
    return templates.TemplateResponse(request, "products/upload.html", {})


@router.get("/products/{product_id}")
def product_detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    from app.config import UPLOAD_DIR

    product = db.get(InsuranceProduct, product_id)
    if not product:
        raise HTTPException(404, "产品不存在")
    has_ocr = {d.id: (UPLOAD_DIR / f"{d.file.sha256}.ocr.txt").exists() for d in product.documents}
    grouped: list[tuple[str, list]] = []
    for cat, cat_name in CLAUSE_CATEGORIES.items():
        items = [c for c in product.clauses if c.category == cat]
        if items:
            grouped.append((cat_name, items))
    return templates.TemplateResponse(request, "products/detail.html", {
        "product": product,
        "grouped_clauses": grouped,
        "has_ocr": has_ocr,
        "pros": json.loads(product.pros_json or "[]"),
        "cons": json.loads(product.cons_json or "[]"),
    })


@router.get("/compare")
def compare_page(request: Request, db: Session = Depends(get_db)):
    products = (
        db.query(InsuranceProduct)
        .filter(InsuranceProduct.is_shortlisted.is_(True), InsuranceProduct.status == "done")
        .order_by(InsuranceProduct.id).all()
    )
    rows = build_compare_table(db, products) if products else []
    latest_summary = (
        db.query(AnalysisTask)
        .filter(AnalysisTask.task_type == "compare_summary", AnalysisTask.status == "success")
        .order_by(AnalysisTask.id.desc()).first()
    )
    summary_md = None
    if latest_summary and latest_summary.result_json:
        summary_md = json.loads(latest_summary.result_json).get("markdown")
    return templates.TemplateResponse(request, "compare.html", {
        "products": products, "rows": rows, "summary_md": summary_md,
    })


@router.get("/members/{member_id}/disclosure/{product_id}")
def disclosure_page(member_id: int, product_id: int, request: Request, db: Session = Depends(get_db)):
    member = db.get(FamilyMember, member_id)
    product = db.get(InsuranceProduct, product_id)
    if not member or not product:
        raise HTTPException(404, "成员或产品不存在")
    check = (
        db.query(DisclosureCheck)
        .filter(DisclosureCheck.member_id == member_id, DisclosureCheck.product_id == product_id)
        .first()
    )
    result = json.loads(check.result_json) if check and check.result_json else None
    return templates.TemplateResponse(request, "disclosure.html", {
        "member": member, "product": product, "check": check, "result": result,
    })


@router.get("/tasks/{task_id}/wait")
def task_wait(task_id: int, request: Request, db: Session = Depends(get_db)):
    task = db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return templates.TemplateResponse(request, "task_wait.html", {"task": task})

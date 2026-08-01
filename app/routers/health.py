from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AnalysisTask, FamilyMember, HealthFinding, HealthRecord, FINDING_KINDS
from app.schemas import FindingIn, RecordMetaIn
from app.services import files
from app.services.health_extract import parse_date
from app.services.task_runner import run_task

router = APIRouter(prefix="/api/health", tags=["health"])


@router.post("/upload")
async def upload_health(background_tasks: BackgroundTasks,
                        member_id: int = Form(...),
                        uploads: list[UploadFile] = [],
                        db: Session = Depends(get_db)):
    member = db.get(FamilyMember, member_id)
    if not member:
        raise HTTPException(404, "成员不存在")
    if not uploads:
        raise HTTPException(400, "请选择至少一个文件")

    task_ids = []
    for up in uploads:
        content = await up.read()
        if not content:
            continue
        try:
            file_rec, _ = files.save_upload(db, up.filename or "health", content, "health")
        except files.UnsupportedFileError as e:
            raise HTTPException(400, str(e))

        record = HealthRecord(member_id=member_id, file_id=file_rec.id)
        db.add(record)
        db.commit()
        task = AnalysisTask(task_type="health_extract", target_id=record.id, progress_msg="排队中")
        db.add(task)
        db.commit()
        background_tasks.add_task(run_task, task.id)
        task_ids.append(task.id)

    if not task_ids:
        raise HTTPException(400, "没有可解析的文件")
    return {"task_ids": task_ids, "task_id": task_ids[0], "member_id": member_id}


@router.post("/records/{record_id}/reanalyze")
def reanalyze_record(record_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    record = db.get(HealthRecord, record_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    record.status = "pending"
    task = AnalysisTask(task_type="health_extract", target_id=record_id, progress_msg="排队中")
    db.add(task)
    db.commit()
    background_tasks.add_task(run_task, task.id)
    return {"task_id": task.id}


@router.put("/records/{record_id}")
def update_record(record_id: int, body: RecordMetaIn, db: Session = Depends(get_db)):
    record = db.get(HealthRecord, record_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    if body.record_type is not None:
        record.record_type = body.record_type
    if body.exam_date is not None:
        record.exam_date = parse_date(body.exam_date)
    if body.institution is not None:
        record.institution = body.institution
    if body.conclusion is not None:
        record.conclusion = body.conclusion
    db.commit()
    return {"ok": True}


@router.delete("/records/{record_id}")
def delete_record(record_id: int, db: Session = Depends(get_db)):
    record = db.get(HealthRecord, record_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    db.delete(record)
    db.commit()
    return {"ok": True}


@router.post("/members/{member_id}/findings")
def add_finding(member_id: int, body: FindingIn, db: Session = Depends(get_db)):
    member = db.get(FamilyMember, member_id)
    if not member:
        raise HTTPException(404, "成员不存在")
    f = HealthFinding(
        member_id=member_id,
        kind=body.kind if body.kind in FINDING_KINDS else "history",
        name=body.name,
        value=body.value,
        reference_range=body.reference_range,
        flag=body.flag,
        note=body.note,
        finding_date=parse_date(body.finding_date),
        is_manual=True,
    )
    db.add(f)
    db.commit()
    return {"id": f.id}


@router.put("/findings/{finding_id}")
def update_finding(finding_id: int, body: FindingIn, db: Session = Depends(get_db)):
    f = db.get(HealthFinding, finding_id)
    if not f:
        raise HTTPException(404, "记录不存在")
    f.kind = body.kind if body.kind in FINDING_KINDS else f.kind
    f.name = body.name
    f.value = body.value
    f.reference_range = body.reference_range
    f.flag = body.flag
    f.note = body.note
    if body.finding_date is not None:
        f.finding_date = parse_date(body.finding_date)
    f.is_manual = True
    db.commit()
    return {"ok": True}


@router.delete("/findings/{finding_id}")
def delete_finding(finding_id: int, db: Session = Depends(get_db)):
    f = db.get(HealthFinding, finding_id)
    if not f:
        raise HTTPException(404, "记录不存在")
    db.delete(f)
    db.commit()
    return {"ok": True}

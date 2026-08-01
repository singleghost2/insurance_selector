from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FamilyMember
from app.schemas import MemberIn

router = APIRouter(prefix="/api/members", tags=["members"])


@router.post("")
def create_member(body: MemberIn, db: Session = Depends(get_db)):
    m = FamilyMember(**body.model_dump())
    db.add(m)
    db.commit()
    return {"id": m.id}


@router.put("/{member_id}")
def update_member(member_id: int, body: MemberIn, db: Session = Depends(get_db)):
    m = db.get(FamilyMember, member_id)
    if not m:
        raise HTTPException(404, "成员不存在")
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/{member_id}")
def delete_member(member_id: int, db: Session = Depends(get_db)):
    m = db.get(FamilyMember, member_id)
    if not m:
        raise HTTPException(404, "成员不存在")
    db.delete(m)
    db.commit()
    return {"ok": True}

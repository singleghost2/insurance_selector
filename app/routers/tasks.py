from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AnalysisTask
from app.services.task_runner import redirect_url_for

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return {
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "progress_msg": task.progress_msg,
        "error": task.error,
        "redirect_url": redirect_url_for(task, db),
    }

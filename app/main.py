import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine
from app import models  # noqa: F401  确保模型注册到 Base
from app.routers import analysis, health, members, pages, products, tasks
from app.services.task_runner import fail_orphan_tasks
from app.templating import templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    fail_orphan_tasks()
    yield


app = FastAPI(title="百万医疗险智能选购助手", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(pages.router)
app.include_router(members.router)
app.include_router(products.router)
app.include_router(health.router)
app.include_router(analysis.router)
app.include_router(tasks.router)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": getattr(exc, "detail", "Not Found")}, status_code=404)
    return HTMLResponse(
        f"<h3>页面不存在</h3><p>{getattr(exc, 'detail', '')}</p><a href='/'>返回首页</a>", status_code=404
    )

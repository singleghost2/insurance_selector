import json
from pathlib import Path

import markdown as md
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

RISK_LABELS = {"high": "高风险", "medium": "需注意", "low": "略逊", "info": "中性/优势"}
STATUS_LABELS = {"pending": "等待中", "analyzing": "分析中", "done": "已完成", "failed": "失败"}


def from_json(s: str | None):
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


templates.env.filters["from_json"] = from_json
templates.env.filters["markdown"] = lambda s: md.markdown(s or "", extensions=["tables"])
templates.env.globals["RISK_LABELS"] = RISK_LABELS
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS

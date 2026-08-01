from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_text_model: str = "qwen-plus"
    llm_vision_model: str = "qwen-vl-max"
    llm_timeout: int = 300
    llm_json_mode: bool = True

    # 条款全文直接分析的 token 上限，超过走 map-reduce
    full_text_token_limit: int = 80_000
    # map 阶段每块目标 token 数
    chunk_token_limit: int = 30_000
    # 扫描版 PDF 页数上限
    max_scanned_pages: int = 120


settings = Settings()

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

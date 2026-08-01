"""唯一直接调用 openai 包的模块。提供带 JSON 校验重试的文本/视觉两个入口。"""
import base64
import json
import logging
import re

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.llm_api_key:
            raise RuntimeError("未配置 LLM_API_KEY，请在项目根目录创建 .env（参考 .env.example）")
        _client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout,
        )
    return _client


class LLMError(RuntimeError):
    pass


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json_text(text: str) -> str:
    """从回复中提取 JSON：优先 ```json 代码块，其次首个 { 到末尾 }。"""
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


@retry(
    retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _chat_once(messages: list[dict], model: str, json_mode: bool) -> str:
    kwargs: dict = {}
    if json_mode and settings.llm_json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = get_client().chat.completions.create(model=model, messages=messages, **kwargs)
    content = resp.choices[0].message.content
    if not content:
        raise LLMError("模型返回了空内容")
    return content


def _chat_validated(messages: list[dict], schema: type[BaseModel], model: str) -> BaseModel:
    """调用 + JSON 解析 + pydantic 校验；失败把错误回传给模型修正，最多 2 次。"""
    convo = list(messages)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            raw = _chat_once(convo, model=model, json_mode=True)
        except APIStatusError as e:
            raise LLMError(f"LLM 接口错误（HTTP {e.status_code}）：{getattr(e, 'message', e)}") from e
        try:
            data = json.loads(_extract_json_text(raw))
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            logger.warning("LLM 输出校验失败（第 %d 次）：%s", attempt + 1, e)
            convo = convo + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "你上面的输出不符合要求的 JSON Schema，解析/校验错误如下：\n"
                        f"{str(e)[:2000]}\n"
                        "请重新输出完整、合法的 JSON（不要输出任何解释文字）。"
                    ),
                },
            ]
    raise LLMError(f"模型连续 3 次未能输出合法 JSON：{str(last_err)[:500]}")


def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "\n\n你必须只输出一个 JSON 对象（不要 Markdown、不要解释文字），严格符合以下 JSON Schema：\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )


def chat_json(system: str, user: str, schema: type[BaseModel], model: str | None = None) -> BaseModel:
    """文本任务：返回通过 schema 校验的 pydantic 对象。"""
    messages = [
        {"role": "system", "content": system + _schema_instruction(schema)},
        {"role": "user", "content": user},
    ]
    return _chat_validated(messages, schema, model or settings.llm_text_model)


def chat_text(system: str, user: str, model: str | None = None) -> str:
    """文本任务：自由文本输出（如对比总结 Markdown）。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        return _chat_once(messages, model=model or settings.llm_text_model, json_mode=False)
    except APIStatusError as e:
        raise LLMError(f"LLM 接口错误（HTTP {e.status_code}）：{getattr(e, 'message', e)}") from e


def _image_content(images: list[bytes], mime: str = "image/png") -> list[dict]:
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(img).decode()}"},
        }
        for img in images
    ]


def vision_json(system: str, images: list[bytes], user: str, schema: type[BaseModel],
                mime: str = "image/png") -> BaseModel:
    """视觉任务：图片 + 指令 → 通过 schema 校验的结构化结果。"""
    messages = [
        {"role": "system", "content": system + _schema_instruction(schema)},
        {"role": "user", "content": _image_content(images, mime) + [{"type": "text", "text": user}]},
    ]
    return _chat_validated(messages, schema, settings.llm_vision_model)


def vision_text(system: str, images: list[bytes], user: str, mime: str = "image/png") -> str:
    """视觉任务：自由文本输出（如扫描页转写）。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _image_content(images, mime) + [{"type": "text", "text": user}]},
    ]
    try:
        return _chat_once(messages, model=settings.llm_vision_model, json_mode=False)
    except APIStatusError as e:
        raise LLMError(f"LLM 接口错误（HTTP {e.status_code}）：{getattr(e, 'message', e)}") from e

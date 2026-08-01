"""LLM 结构化输出契约 + API 请求体。字段 description 会随 JSON Schema 注入 prompt。"""
from pydantic import BaseModel, Field


# ---------- 条款分析 ----------

class KeyClauseItem(BaseModel):
    category: str = Field(description=(
        "条款类别，只能取以下值之一：waiting_period(等待期)/exclusions(免责条款)/pre_existing(既往症定义)/"
        "deductible_calc(免赔额计算)/hospital_scope(医院范围)/external_drugs(外购药)/"
        "outpatient_around_hosp(住院前后门急诊)/special_outpatient(特殊门诊)/renewal(续保条件)/"
        "claim_process(理赔流程)/health_disclosure(健康告知)/other(其他)"
    ))
    title: str = Field(description="条款要点标题，如「等待期30天」「不保证续保」")
    summary: str = Field(description="用大白话向普通家庭解释这条条款意味着什么，以及是否是坑")
    risk_level: str = Field(description="high(明显严苛/大坑)/medium(需注意)/low(略逊于市场好条款)/info(中性或优势)")
    quote: str | None = Field(default=None, description="条款原文摘录（尽量逐字，50-300字），没有则为 null")
    page_no: int | None = Field(default=None, description="原文所在页码（依据文中【第N页】标记），不确定则为 null")


class PolicyBasicInfo(BaseModel):
    name: str | None = Field(default=None, description="保险产品全名")
    company: str | None = Field(default=None, description="保险公司名称")
    coverage_amount: str | None = Field(default=None, description="保额说明，如「一般医疗300万/重疾医疗600万」")
    deductible: str | None = Field(default=None, description="免赔额说明，如「1万免赔，重疾0免赔」")
    guaranteed_renewal: str | None = Field(default=None, description="续保情况，如「保证续保20年」「不保证续保」")


class PolicyAnalysisResult(BaseModel):
    basic_info: PolicyBasicInfo
    pros: list[str] = Field(description="产品优势列表，每条一句话")
    cons: list[str] = Field(description="产品劣势/风险列表，每条一句话")
    key_clauses: list[KeyClauseItem] = Field(description=(
        "影响理赔的关键条款逐条列出。每个类别都必须覆盖到；如果条款中找不到某类别的内容，"
        "也要输出该类别并在 summary 中注明「条款中未找到相关内容」且 risk_level 给 medium"
    ))


class ChunkClauseHit(BaseModel):
    category: str = Field(description="同 KeyClauseItem.category 的取值范围")
    quote: str = Field(description="与该类别相关的条款原文摘录")
    page_no: int | None = Field(default=None, description="页码，依据【第N页】标记")
    note: str | None = Field(default=None, description="简短说明该摘录讲了什么")


class ChunkExtractResult(BaseModel):
    hits: list[ChunkClauseHit] = Field(description="本块中与关键条款清单相关的所有摘录；本块没有相关内容则为空列表")


# ---------- 健康材料抽取 ----------

class HealthFindingItem(BaseModel):
    kind: str = Field(description="abnormal_indicator(异常指标)/diagnosis(诊断)/history(疾病史)/surgery(手术史)/medication(用药)")
    name: str = Field(description="名称，如「甘油三酯」「甲状腺结节 TI-RADS 3类」")
    value: str | None = Field(default=None, description="数值（含单位），如「2.3 mmol/L」，无则 null")
    reference_range: str | None = Field(default=None, description="参考范围，如「0.4-1.7」，无则 null")
    flag: str | None = Field(default=None, description="high(偏高)/low(偏低)/positive(阳性/检出)/other")
    note: str | None = Field(default=None, description="补充说明，如医生建议")


class HealthExtractResult(BaseModel):
    record_type: str | None = Field(default=None, description="材料类型：体检报告/门诊病历/住院记录/检查单/其他")
    exam_date: str | None = Field(default=None, description="检查/就诊日期，格式 YYYY-MM-DD，材料中没有则 null")
    institution: str | None = Field(default=None, description="医院/体检机构名称")
    conclusion: str | None = Field(default=None, description="总检结论或诊断摘要，忠实于原文")
    findings: list[HealthFindingItem] = Field(description="逐条列出异常指标、诊断、病史；正常项不要列")


# ---------- 健康告知辅助 ----------

class DisclosureItem(BaseModel):
    question: str = Field(description="健康告知中的询问事项（概括原文）")
    matched_findings: list[str] = Field(description="该成员健康档案中可能命中此告知项的记录，没有则为空列表")
    should_disclose: str = Field(description="yes(建议告知)/maybe(建议咨询后决定)/no(大概率无需告知)")
    impact: str = Field(description="可能的核保影响提示：标准体承保/可能除外责任/可能加费/可能拒保/无法判断")
    advice: str = Field(description="给用户的具体建议，大白话")


class DisclosureResult(BaseModel):
    items: list[DisclosureItem] = Field(description="逐条告知事项核对结果")
    overall: str = Field(description="总体建议：这位成员投保该产品在健康告知上的整体情况与注意事项")


# ---------- API 请求体 ----------

class MemberIn(BaseModel):
    name: str
    relation: str = "other"
    birth_year: int | None = None
    gender: str | None = None
    notes: str | None = None


class FindingIn(BaseModel):
    kind: str = "abnormal_indicator"
    name: str
    value: str | None = None
    reference_range: str | None = None
    flag: str | None = None
    note: str | None = None
    finding_date: str | None = None  # YYYY-MM-DD


class RecordMetaIn(BaseModel):
    record_type: str | None = None
    exam_date: str | None = None
    institution: str | None = None
    conclusion: str | None = None


class DisclosureIn(BaseModel):
    member_id: int
    product_id: int


class CompareSummaryIn(BaseModel):
    product_ids: list[int]

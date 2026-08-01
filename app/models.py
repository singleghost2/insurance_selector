from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def now() -> datetime:
    return datetime.now()


class FamilyMember(Base):
    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    relation: Mapped[str] = mapped_column(String(20), default="other")  # self/spouse/father/mother/other
    birth_year: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String(10))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    records: Mapped[list["HealthRecord"]] = relationship(back_populates="member", cascade="all, delete-orphan")
    findings: Mapped[list["HealthFinding"]] = relationship(back_populates="member", cascade="all, delete-orphan")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    __table_args__ = (UniqueConstraint("sha256", "purpose", name="uq_file_sha_purpose"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))  # 相对 data/uploads 的文件名
    media_type: Mapped[str] = mapped_column(String(10))  # pdf / image
    purpose: Mapped[str] = mapped_column(String(10))  # policy / health
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer)
    is_scanned: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class InsuranceProduct(Base):
    __tablename__ = "insurance_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/analyzing/done/failed
    name: Mapped[str | None] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))
    coverage_amount: Mapped[str | None] = mapped_column(String(500))
    deductible: Mapped[str | None] = mapped_column(String(500))
    guaranteed_renewal: Mapped[str | None] = mapped_column(String(500))
    pros_json: Mapped[str] = mapped_column(Text, default="[]")
    cons_json: Mapped[str] = mapped_column(Text, default="[]")
    analysis_json: Mapped[str | None] = mapped_column(Text)
    is_shortlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    shortlisted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    documents: Mapped[list["ProductDocument"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ProductDocument.sort_order"
    )
    clauses: Mapped[list["KeyClause"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="KeyClause.sort_order"
    )

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.documents:
            return self.documents[0].file.original_name
        return f"产品 {self.id}"


class ProductDocument(Base):
    """一款产品包含的条款文件（主险条款、附加险条款、费率表等）。"""
    __tablename__ = "product_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("insurance_products.id", ondelete="CASCADE"))
    file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    product: Mapped[InsuranceProduct] = relationship(back_populates="documents")
    file: Mapped[UploadedFile] = relationship()


# 关键条款类别（顺序即展示/对比顺序）
CLAUSE_CATEGORIES: dict[str, str] = {
    "waiting_period": "等待期",
    "exclusions": "免责条款",
    "pre_existing": "既往症定义",
    "deductible_calc": "免赔额计算",
    "hospital_scope": "医院范围",
    "external_drugs": "外购药报销",
    "outpatient_around_hosp": "住院前后门急诊",
    "special_outpatient": "特殊门诊",
    "renewal": "续保条件",
    "claim_process": "理赔流程",
    "health_disclosure": "健康告知",
    "other": "其他",
}


class KeyClause(Base):
    __tablename__ = "key_clauses"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("insurance_products.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(40), default="other")
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(10), default="info")  # high/medium/low/info
    quote: Mapped[str | None] = mapped_column(Text)
    quote_verified: Mapped[bool | None] = mapped_column(Boolean)  # 原文核实结果
    source_doc: Mapped[str | None] = mapped_column(String(255))  # 出自哪份条款文件
    page_no: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped[InsuranceProduct] = relationship(back_populates="clauses")


class HealthRecord(Base):
    __tablename__ = "health_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("family_members.id", ondelete="CASCADE"))
    file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    record_type: Mapped[str | None] = mapped_column(String(50))  # 体检报告/门诊病历/住院记录/检查单/其他
    exam_date: Mapped[date | None] = mapped_column(Date)
    institution: Mapped[str | None] = mapped_column(String(200))
    conclusion: Mapped[str | None] = mapped_column(Text)
    raw_extract_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    member: Mapped[FamilyMember] = relationship(back_populates="records")
    file: Mapped[UploadedFile] = relationship()
    findings: Mapped[list["HealthFinding"]] = relationship(back_populates="record", cascade="all, delete-orphan")


class HealthFinding(Base):
    __tablename__ = "health_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int | None] = mapped_column(ForeignKey("health_records.id", ondelete="CASCADE"))
    member_id: Mapped[int] = mapped_column(ForeignKey("family_members.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30), default="abnormal_indicator")
    # abnormal_indicator / diagnosis / history / surgery / medication
    name: Mapped[str] = mapped_column(String(200))
    value: Mapped[str | None] = mapped_column(String(100))
    reference_range: Mapped[str | None] = mapped_column(String(100))
    flag: Mapped[str | None] = mapped_column(String(20))  # high/low/positive/other
    note: Mapped[str | None] = mapped_column(Text)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    finding_date: Mapped[date | None] = mapped_column(Date)

    record: Mapped[HealthRecord | None] = relationship(back_populates="findings")
    member: Mapped[FamilyMember] = relationship(back_populates="findings")


FINDING_KINDS: dict[str, str] = {
    "abnormal_indicator": "异常指标",
    "diagnosis": "诊断",
    "history": "疾病史",
    "surgery": "手术史",
    "medication": "用药",
}


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_type: Mapped[str] = mapped_column(String(30))
    # policy_analysis / health_extract / disclosure / compare_summary
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/success/failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_msg: Mapped[str] = mapped_column(String(500), default="")
    target_id: Mapped[int | None] = mapped_column(Integer)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class DisclosureCheck(Base):
    __tablename__ = "disclosure_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("family_members.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("insurance_products.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    result_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    member: Mapped[FamilyMember] = relationship()
    product: Mapped[InsuranceProduct] = relationship()

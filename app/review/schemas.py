from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from app.schemas.contracts import StrictModel
from app.schemas.output_contract import OUTPUT_SCHEMA_VERSION


class HumanReviewRequest(StrictModel):
    reviewer_id: str = Field(min_length=1, max_length=100)
    # REDECLARED=供应商重新申报已受理（问题较小分支的后续动作，仍进入人工复核队列）
    review_result: Literal["APPROVED", "REJECTED", "NEED_MORE_EVIDENCE", "REDECLARED"]
    final_action: Literal["observe", "alert", "rectify"] | None = None
    review_comment: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_approved_action(self):
        if self.review_result == "APPROVED" and self.final_action is None:
            raise ValueError("APPROVED review requires final_action")
        if self.review_result == "REDECLARED" and not self.review_comment:
            raise ValueError("REDECLARED review requires review_comment describing the redeclaration")
        return self


class SupplierRedeclareRequest(StrictModel):
    """供应商侧「重新申报」补充材料提交（智能体只做受理与登记，不做判定）。

    门禁（是否允许重新申报）由确定性处置门禁在分析与人工复核阶段完成，
    本请求仅承载供应商补充说明材料，供后端与人工复核队列使用。
    """

    supplier_id: str = Field(min_length=1, max_length=100)
    redeclare_reason: str = Field(min_length=1, max_length=1000)
    attachment_refs: list[str] = Field(default_factory=list, max_length=20)
    submitted_by: str = Field(default="", max_length=100)


class HumanReviewRecord(HumanReviewRequest):
    schema_version: Literal["C-DRAFT-V0.2"] = OUTPUT_SCHEMA_VERSION
    risk_level: Literal["RED", "YELLOW", "GREEN"] | None = None
    # 处置状态（与报告 disposition 对齐）：正常复核 / 证据不足 / 待供应商重新申报
    disposition_state: Literal[
        "PENDING_HUMAN_REVIEW", "EVIDENCE_INSUFFICIENT", "AWAITING_SUPPLIER_REDECLARE"
    ] | None = None
    review_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    supplier_id: str
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

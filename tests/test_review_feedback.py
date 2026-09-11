from pathlib import Path

import pytest

from app.review.schemas import HumanReviewRequest
from app.review.service import ReviewFeedbackService


def test_review_feedback_is_validated_and_persisted(tmp_path: Path) -> None:
    service = ReviewFeedbackService(tmp_path / "reviews.jsonl")
    request = HumanReviewRequest(
        reviewer_id="reviewer-test",
        review_result="APPROVED",
        final_action="rectify",
        review_comment="模拟审核",
    )

    record = service.record(run_id="run-1", supplier_id="supplier-1", request=request)

    assert record.schema_version == "C-DRAFT-V0.2"
    assert service.list_records()[0].final_action == "rectify"
    with pytest.raises(ValueError, match="Review already exists"):
        service.record(run_id="run-1", supplier_id="supplier-1", request=request)


def test_approved_review_requires_final_action() -> None:
    with pytest.raises(ValueError, match="requires final_action"):
        HumanReviewRequest(reviewer_id="reviewer-test", review_result="APPROVED")

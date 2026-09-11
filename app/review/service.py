from __future__ import annotations

import json
from pathlib import Path

from app.review.schemas import HumanReviewRecord, HumanReviewRequest


class ReviewFeedbackService:
    """Local prototype store; member B should replace it with a transactional database adapter."""

    def __init__(self, path: Path = Path("artifacts/reviews/review_feedback.jsonl")) -> None:
        self.path = path

    def list_records(self) -> list[HumanReviewRecord]:
        if not self.path.exists():
            return []
        return [
            HumanReviewRecord.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def record(
        self,
        *,
        run_id: str,
        supplier_id: str,
        request: HumanReviewRequest,
        risk_level: str | None = None,
        disposition_state: str | None = None,
    ) -> HumanReviewRecord:
        if any(record.run_id == run_id for record in self.list_records()):
            raise ValueError(f"Review already exists for run_id={run_id}")
        record = HumanReviewRecord(
            run_id=run_id,
            supplier_id=supplier_id,
            risk_level=risk_level,
            disposition_state=disposition_state,
            **request.model_dump(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(record.model_dump_json() + "\n")
        return record

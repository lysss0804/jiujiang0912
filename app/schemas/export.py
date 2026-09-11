import json
from pathlib import Path

from app.review.schemas import HumanReviewRecord, HumanReviewRequest
from app.schemas.output_contract import AssessmentOutput, OUTPUT_SCHEMA_VERSION
from app.schemas.report import RiskReport, REPORT_SCHEMA_VERSION


def export_contract_schemas(output_dir: Path = Path("docs/contracts")) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    contracts = {
        f"assessment_output.{OUTPUT_SCHEMA_VERSION}.schema.json": AssessmentOutput,
        f"risk_report.{REPORT_SCHEMA_VERSION}.schema.json": RiskReport,
        f"human_review_request.{OUTPUT_SCHEMA_VERSION}.schema.json": HumanReviewRequest,
        f"human_review_record.{OUTPUT_SCHEMA_VERSION}.schema.json": HumanReviewRecord,
    }
    paths = []
    for filename, model in contracts.items():
        path = output_dir / filename
        path.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths.append(path)
    return paths

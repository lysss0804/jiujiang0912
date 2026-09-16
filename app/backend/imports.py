"""CSV import parsing, validation and local-file metadata helpers."""

from __future__ import annotations

import csv
import hashlib
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.backend.repository import DIMENSIONS, IMPORTANCE, RiskRepository, normalize_supplier_id


SUPPLIER_HEADERS = {
    "供应商编号": "supplier_id", "supplier_id": "supplier_id",
    "供应商名称": "supplier_name", "supplier_name": "supplier_name",
    "统一社会信用代码": "credit_code", "credit_code": "credit_code",
    "重要程度": "importance", "importance": "importance",
    "负责部门": "responsible_department", "responsible_department": "responsible_department",
    "状态": "status", "status": "status",
}
EVENT_HEADERS = {
    "供应商编号": "supplier_id", "supplier_id": "supplier_id",
    "风险维度": "risk_dimension", "risk_dimension": "risk_dimension",
    "风险类型": "risk_type", "risk_type": "risk_type",
    "风险描述": "risk_description", "risk_description": "risk_description",
    "严重程度": "severity", "severity": "severity",
    "发生日期": "event_date", "event_date": "event_date",
    "事件周次": "event_week", "event_week": "event_week",
    "来源类型": "source_type", "source_type": "source_type",
    "来源记录编号": "source_record_id", "source_record_id": "source_record_id",
    "证据编号": "evidence_id", "evidence_id": "evidence_id",
    "证据标题": "evidence_title", "evidence_title": "evidence_title",
    "证据摘要": "evidence_content", "evidence_content": "evidence_content",
    "证据链接": "source_url", "source_url": "source_url",
    "涉及金额": "amount", "amount": "amount",
}


def _decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("CSV must use UTF-8 or GB18030 encoding")


def _read_rows(content: bytes, aliases: dict[str, str]) -> list[dict[str, str]]:
    reader = csv.DictReader(StringIO(_decode_csv(content)))
    if not reader.fieldnames:
        raise ValueError("CSV header is required")
    missing = [header for header in reader.fieldnames if header.strip() not in aliases]
    if missing:
        raise ValueError(f"unsupported CSV columns: {', '.join(missing)}")
    rows: list[dict[str, str]] = []
    for raw in reader:
        row = {aliases[key.strip()]: (value or "").strip() for key, value in raw.items() if key is not None}
        if any(row.values()):
            rows.append(row)
    if not rows:
        raise ValueError("CSV contains no data rows")
    return rows


def validate_supplier_csv(content: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row_number, row in enumerate(_read_rows(content, SUPPLIER_HEADERS), start=2):
        importance = IMPORTANCE.get(row.get("importance", "").upper()) or IMPORTANCE.get(row.get("importance", ""))
        row["supplier_id"] = normalize_supplier_id(row.get("supplier_id", ""))
        if not all((row.get("supplier_id"), row.get("supplier_name"), row.get("credit_code"), importance)):
            errors.append({"row": row_number, "message": "供应商编号、供应商名称、统一社会信用代码、重要程度为必填项"})
            continue
        row["importance"] = importance
        valid.append(row)
    return valid, errors


def validate_event_csv(content: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row_number, row in enumerate(_read_rows(content, EVENT_HEADERS), start=2):
        row["supplier_id"] = normalize_supplier_id(row.get("supplier_id", ""))
        try:
            if not row.get("event_week") and row.get("event_date"):
                row["event_week"] = date.fromisoformat(row["event_date"]).isocalendar().week
            row["event_week"] = int(row["event_week"])
            row["severity"] = int(row["severity"])
            if row.get("amount"):
                row["amount"] = float(row["amount"])
        except (ValueError, KeyError):
            errors.append({"row": row_number, "message": "严重程度须为 0-5；发生日期须为 YYYY-MM-DD，或填写事件周次"})
            continue
        if (not row.get("supplier_id") or row.get("risk_dimension") not in DIMENSIONS or not row.get("risk_type")
                or not row.get("risk_description") or row["severity"] not in range(6) or not 1 <= row["event_week"] <= 52):
            errors.append({"row": row_number, "message": "供应商编号、六维风险、风险类型、描述、严重程度(0-5)、事件周次(1-52)不合法或缺失"})
            continue
        valid.append(row)
    return valid, errors


def save_upload(repository: RiskRepository, content: bytes, filename: str, content_type: str | None, purpose: str, upload_dir: Path) -> str:
    safe_name = Path(filename or "upload").name
    target_dir = upload_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = target_dir / f"{uuid4()}_{safe_name}"
    stored.write_bytes(content)
    return repository.create_file_object(
        original_filename=safe_name, stored_path=str(stored), content_type=content_type,
        file_size=len(content), sha256=hashlib.sha256(content).hexdigest(), purpose=purpose,
    )

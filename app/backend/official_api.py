"""Generic, authorised public-data API synchronisation.

The connector is configuration driven because each authorised provider exposes
different paths and payloads.  It does not scrape web pages or store tokens.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

from app.backend.repository import RiskRepository


def _lookup(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for key in path.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and key.isdigit():
            current = current[int(key)] if int(key) < len(current) else None
        else:
            return None
    return current


def _render(value: Any, supplier: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render(item, supplier) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, supplier) for item in value]
    if isinstance(value, str):
        for field, field_value in supplier.items():
            value = value.replace("{{supplier." + field + "}}", str(field_value or ""))
    return value


def sync_source(repository: RiskRepository, source_code: str, supplier_id: str, *, timeout_seconds: float = 20) -> dict[str, Any]:
    source = repository.get_source(source_code)
    if not source["enabled"]:
        raise ValueError(f"source is disabled: {source_code}")
    if source["access_mode"] != "API":
        raise ValueError(f"source access_mode={source['access_mode']} does not permit HTTP sync")
    if not source["live_http_enabled"]:
        raise ValueError(f"live HTTP is disabled for source: {source_code}")
    token = os.getenv(source["auth_env_var"])
    if not token:
        raise ValueError(f"missing credential environment variable: {source['auth_env_var']}")
    supplier = repository.supplier_for_source(supplier_id)
    response = httpx.get(
        source["base_url"] + source["endpoint_path"],
        params=_render(source["query_template"], supplier),
        headers={source["auth_header"]: token, "Accept": "application/json"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    raw_record_id = repository.save_raw_source_payload(source_code, supplier_id, payload)
    records = _lookup(payload, source.get("records_path")) or []
    if not isinstance(records, list):
        raise ValueError("configured records_path must point to an array")
    mapping = source["field_mapping"]
    normalized: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        event = {target: _lookup(item, path) for target, path in mapping.items()}
        event["supplier_id"] = supplier["supplier_id"]
        event["source_type"] = source_code.upper()
        event["source_record_id"] = str(event.get("source_record_id") or raw_record_id)
        if not event.get("event_week"):
            event["event_week"] = datetime.now().isocalendar().week
        normalized.append(event)
    imported = repository.import_risk_events(normalized, source_type=source_code.upper()) if normalized else {"imported_count": 0}
    return {"source_code": source_code.upper(), "supplier_id": supplier["supplier_id"], "raw_record_id": raw_record_id, "received_count": len(records), **imported}

import pytest

from app.agents.decision import _align_identifiers, _validate_grounding
from app.schemas.contracts import AdvisoryResult


def _advisory(**overrides) -> AdvisoryResult:
    payload = {
        "risk_summary": "测试",
        "key_factors": ["测试因素"],
        "recommendation": "建议人工复核",
        "rationale": "测试",
        "evidence_ids": [],
        "policy_chunk_ids": [],
        "candidate_actions": [
            {
                "suggest_type": "observe",
                "suggest_content": "人工复核",
                "suggest_priority": "LOW",
                "execution_status": "PENDING_HUMAN_REVIEW",
            }
        ],
    }
    payload.update(overrides)
    return AdvisoryResult.model_validate(payload)


def test_llm_advisory_rejects_invented_evidence_or_policy_ids() -> None:
    advisory = _advisory(evidence_ids=["E-INVENTED"], policy_chunk_ids=["P-INVENTED"])
    state = {
        "evidence_result": {"evidence_items": [{"evidence_id": "E-REAL"}]},
        "policy_context": [{"chunk_id": "P-REAL"}],
    }

    with pytest.raises(ValueError, match="outside supplied context"):
        _validate_grounding(advisory, state)


def test_visually_equivalent_hyphen_is_restored_to_source_id() -> None:
    advisory = _advisory(evidence_ids=["E-S-01"], policy_chunk_ids=["P-01"])
    state = {
        "evidence_result": {"evidence_items": [{"evidence_id": "E‑S‑01"}]},
        "policy_context": [{"chunk_id": "P-01"}],
    }

    _align_identifiers(advisory, state)
    _validate_grounding(advisory, state)
    assert advisory.evidence_ids == ["E‑S‑01"]


def test_grounded_advisory_passes_validation() -> None:
    advisory = _advisory(evidence_ids=["E-REAL"], policy_chunk_ids=["P-REAL"])
    state = {
        "evidence_result": {"evidence_items": [{"evidence_id": "E-REAL"}]},
        "policy_context": [{"chunk_id": "P-REAL"}],
    }
    _align_identifiers(advisory, state)
    _validate_grounding(advisory, state)
    assert advisory.evidence_ids == ["E-REAL"]

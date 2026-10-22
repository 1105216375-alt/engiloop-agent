"""Formal governance for report, recomputed, BCI and mandatory-grade tracks."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Optional


SCORE_GOVERNANCE_VERSION = "bridge_score_governance.v1"
REVIEW_STATUSES = {"review_required", "severe_conflict"}


def _optional_float(value: Any) -> Optional[float]:
    try:
        return round(float(value), 2) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _compact_approval(approval: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    approval = dict(approval or {})
    return {
        "approval_id": approval.get("approval_id") or "",
        "status": approval.get("status") or "not_required",
        "risk_level": approval.get("risk_level") or "",
        "requested_at": approval.get("requested_at"),
        "decided_at": approval.get("decided_at"),
        "decided_by": approval.get("decided_by") or "",
        "decision_note": approval.get("decision_note") or "",
    }


def build_score_governance(
    audit: Dict[str, Any],
    *,
    bci: Any = None,
    bci_grade: str = "",
    mandatory_grade: str = "",
    approval: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Bind score conflict policy to a human-review state without changing scores."""
    audit = dict(audit or {})
    reported = _optional_float(audit.get("reported_dr"))
    computed = _optional_float(audit.get("computed_dr"))
    status = str(audit.get("status") or "not_comparable")
    requires_review = status in REVIEW_STATUSES
    approval_view = _compact_approval(approval)
    approval_status = approval_view["status"]

    if reported is None and computed is not None:
        publication_status = "system_fallback_provisional"
        decision_authority = "system_recomputed_fallback"
    elif requires_review and approval_status == "approved":
        publication_status = "dual_track_reviewed"
        decision_authority = "report_published_with_signed_conflict_review"
    elif requires_review and approval_status == "rejected":
        publication_status = "blocked_for_more_evidence"
        decision_authority = "report_published_conflict_unresolved"
    elif requires_review:
        publication_status = "dual_track_pending_review"
        decision_authority = "report_published_pending_conflict_review"
    elif reported is not None:
        publication_status = "dual_track_audited"
        decision_authority = "report_published"
    else:
        publication_status = "score_evidence_missing"
        decision_authority = "none"

    return {
        "schema_version": SCORE_GOVERNANCE_VERSION,
        "publication_status": publication_status,
        "decision_authority": decision_authority,
        "requires_human_review": requires_review,
        "approval_level": (
            "L2_total_engineer" if status == "severe_conflict"
            else "L1_engineer" if status == "review_required"
            else "none"
        ),
        "approval": approval_view,
        "score_snapshot": {
            "reported_dr": reported,
            "computed_dr": computed,
            "gap_computed_minus_reported": _optional_float(
                audit.get("gap_computed_minus_reported", audit.get("gap"))
            ),
            "bci": _optional_float(bci),
            "bci_grade": str(bci_grade or ""),
            "mandatory_grade": str(mandatory_grade or ""),
        },
        "priority_policy": [
            {
                "order": 1,
                "track": "report_published_dr",
                "role": "signed_delivery_record",
                "rule": "报告正式Dr作为已发布主值，系统不得静默覆盖。",
            },
            {
                "order": 2,
                "track": "system_recomputed_dr",
                "role": "independent_audit_or_missing_score_fallback",
                "rule": "系统重算Dr用于复核；仅在报告无Dr时作为带来源的暂定补算值。",
            },
            {
                "order": 3,
                "track": "bci",
                "role": "separate_methodology",
                "rule": "BCI独立展示，不与Dr互相代换。",
            },
            {
                "order": 4,
                "track": "mandatory_grade_control",
                "role": "standard_specific_grade_gate",
                "rule": "强制类别仅控制对应规范等级，不反写Dr数值。",
            },
        ],
        "threshold_policy": {
            "aligned": "|差值| <= 1分",
            "minor_gap": "1分 < |差值| <= 3分",
            "review_required": "3分 < |差值| <= 8分，工程师复核",
            "severe_conflict": "|差值| > 8分，总工签字复核",
            "scope": "差值审计优先级，不是桥梁技术状况等级",
        },
        "approval_scope": "conflict_review_only",
        "approval_does_not_change_score": True,
        "automatic_overwrite_allowed": False,
        "manual_calibration_allowed": bool(audit.get("calibration_allowed")),
        "calibration_note": (
            "冲突审批仅确认双轨处置；除非原因、输入、公式和校准审批四项闸门全部通过，"
            "否则不构成数值校准授权。"
        ),
    }


def score_conflict_subject_id(
    bridge_name: str,
    year: Any,
    report_ids: Iterable[Any],
    audit: Dict[str, Any],
) -> str:
    """Return a stable identity for one exact score-conflict snapshot."""
    payload = {
        "version": SCORE_GOVERNANCE_VERSION,
        "bridge_name": str(bridge_name or "").strip(),
        "year": str(year or "").strip(),
        "report_ids": sorted(str(value) for value in (report_ids or []) if value),
        "audit_version": str(
            (audit or {}).get("audit_version") or (audit or {}).get("schema_version") or ""
        ),
        "status": str((audit or {}).get("status") or ""),
        "reported_dr": _optional_float((audit or {}).get("reported_dr")),
        "computed_dr": _optional_float((audit or {}).get("computed_dr")),
        "gap": _optional_float(
            (audit or {}).get("gap_computed_minus_reported", (audit or {}).get("gap"))
        ),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    return f"score_conflict_{digest}"


def build_score_approval_payload(
    bridge_name: str,
    year: Any,
    report_ids: Iterable[Any],
    audit: Dict[str, Any],
) -> Dict[str, Any]:
    status = str((audit or {}).get("status") or "not_comparable")
    gap = _optional_float(
        (audit or {}).get("gap_computed_minus_reported", (audit or {}).get("gap"))
    )
    return {
        "schema_version": SCORE_GOVERNANCE_VERSION,
        "bridge_name": str(bridge_name or "未命名桥梁"),
        "year": str(year or ""),
        "report_ids": sorted(str(value) for value in (report_ids or []) if value),
        "reported_dr": _optional_float((audit or {}).get("reported_dr")),
        "computed_dr": _optional_float((audit or {}).get("computed_dr")),
        "gap_computed_minus_reported": gap,
        "audit_status": status,
        "reason": f"报告Dr与系统重算Dr差值{gap if gap is not None else '-'}分，需人工确认双轨处置。",
        "cause_codes": [
            str(row.get("code") or "")
            for row in ((audit or {}).get("causes") or [])
            if row.get("code")
        ],
        "required_actions": list((audit or {}).get("required_actions") or []),
        "decision_scope": "确认差值证据与双轨发布状态，不授权改写任何评分。",
        "automatic_overwrite_allowed": False,
    }

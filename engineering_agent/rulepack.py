"""Small, explicit rule-pack adapter for reproducible synthetic demonstrations.

This is new public integration code, not a standards database or rule extractor.
The caller supplies a reviewed scale-to-deduction mapping. A digest binds a
snapshot to the entire supplied pack; it establishes identity, not authority.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any

from engineering_agent.scoring import evaluate_scoring_snapshot


def content_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def validate_rulepack(pack: dict) -> dict:
    """Validate an explicit synthetic pack, never execute code from it."""
    if (type(pack) is not dict or type(pack.get("schema")) is not int
            or pack["schema"] != 1 or pack.get("synthetic") is not True):
        raise ValueError("Only schema-1, explicitly synthetic rule packs are supported")
    for field in ("id", "version", "source"):
        if type(pack.get(field)) is not str or not pack[field].strip():
            raise ValueError(f"Rule pack requires {field}")
    rules = pack.get("rules")
    if type(rules) is not list or not rules or len(rules) > 128:
        raise ValueError("Rule pack requires 1 to 128 rules")
    seen = set()
    for rule in rules:
        if type(rule) is not dict:
            raise ValueError("Rule must be a mapping")
        for field in ("component", "disease", "clause"):
            if type(rule.get(field)) is not str or not rule[field].strip():
                raise ValueError(f"Rule requires {field}")
        scale, dp = rule.get("scale"), rule.get("deduction")
        if type(scale) is not int or not 1 <= scale <= 5:
            raise ValueError("Scale must be an integer from 1 to 5")
        if type(dp) not in (int, float) or not 0 <= dp <= 100 or not math.isfinite(dp):
            raise ValueError("Deduction must be a finite number from 0 to 100")
        key = (rule["component"], rule["disease"], scale)
        if key in seen:
            raise ValueError("Ambiguous duplicate rule")
        seen.add(key)
    return deepcopy(pack)


def evaluate_rulepack(pack: dict, observations: list[dict], *, selected_parts: list[str],
                      structure: dict, part_applicability: dict) -> dict:
    """Map explicit scales to deductions and invoke the existing scoring engine.

    A changed pack or changed input creates a different computation context.
    An unsupported observation blocks the snapshot; no nearest-rule fallback.
    """
    checked = validate_rulepack(pack)
    if type(observations) is not list or not observations:
        raise ValueError("Explicit observations are required")
    context = {"observations": observations, "selected_parts": selected_parts,
               "structure": structure, "part_applicability": part_applicability}
    binding = {"rulepack_id": checked["id"], "version": checked["version"],
               "rulepack_sha256": content_digest(checked),
               "input_sha256": content_digest(context)}
    records, applied = [], []
    for row in observations:
        if type(row) is not dict:
            raise ValueError("Observation must be a mapping")
        if type(row.get("标度")) is not int:
            raise ValueError("Observation requires an explicit integer scale")
        matched = [r for r in checked["rules"] if (
            r["component"], r["disease"], r["scale"]
        ) == (row.get("构件类别"), row.get("病害"), row["标度"])]
        if len(matched) != 1:
            return {"status": "rule_not_applicable", "binding": binding,
                    "snapshot": None, "applied_rules": [], "publishable": False}
        rule = matched[0]
        records.append({**deepcopy(row), "DP值": rule["deduction"]})
        applied.append({"component": row.get("构件"), "scale": row["标度"],
                        "deduction": rule["deduction"], "clause": rule["clause"],
                        "rule_source": checked["source"],
                        "observation_source": row.get("source")})
    snapshot = evaluate_scoring_snapshot(
        records, selected_parts=selected_parts, structure=structure,
        part_applicability=part_applicability, synthetic=True,
    )
    return {"status": snapshot["status"], "binding": binding,
            "snapshot": snapshot, "applied_rules": applied, "publishable": False}


def snapshot_reuse_status(result: dict, pack: dict, *, context: dict) -> dict:
    """Compare identities before reuse; no claim of tamper-proof authenticity.

    This check is explicit, not a background watcher or a production cache.
    The caller must not use a stale result until it has been recomputed.
    """
    checked = validate_rulepack(pack)
    binding = result.get("binding", {})
    reasons = []
    if result.get("status") != "computed_snapshot":
        reasons.append("no_computed_snapshot")
    if binding.get("rulepack_sha256") != content_digest(checked):
        reasons.append("rulepack_changed")
    if binding.get("input_sha256") != content_digest(context):
        reasons.append("input_changed")
    return {"reusable": not reasons, "requires_recomputation": bool(reasons),
            "reasons": reasons, "authenticity_verified": False}

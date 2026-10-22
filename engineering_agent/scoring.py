"""Fail-closed snapshot adapter over the extracted BridgeScoringAgent.

This is a NEW public adapter, not a rewritten score formula or a certified
whole-bridge grading service. It never loads a private DP database and never
publishes the legacy engine's empty-input grade or unobserved-system defaults.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any

from legacy_v7.core.scoring_agent import (
    BridgeScoringAgent, COMPONENT_TO_PART, WEIGHTS_CONFIG, SYSTEM_WEIGHTS,
)

_SYSTEM_NAMES = {"deck": "桥面系", "upper": "上部结构", "lower": "下部结构"}
# Deliberately bounded supported subset: every selected part needs a concrete
# count contract. The special cap/column pier aggregation is not asserted safe
# for arbitrary snapshots and therefore requires a separate reviewed adapter.
_COUNT_KEYS = {
    "upper/main": "总主梁数", "upper/bear": "总支座数",
    "deck/joints": "伸缩缝数", "deck/pavement": "跨数",
    "deck/railing": "跨数", "deck/drain": "跨数", "deck/sidewalk": "跨数",
    "lower/abut": "桥台数", "lower/cone": "总锥坡护坡数",
}
_INSTANCE = re.compile(r"\d{1,3}(?:-\d{1,3}){0,2}[号#]?[^\d\s~～〜–—,，;；]{1,30}\Z")


class _NoDatabaseScoringAgent(BridgeScoringAgent):
    """Only disable external data loading; all scoring methods are inherited."""

    def _load_disease_db(self):
        return {}


def _valid_number(value: Any) -> bool:
    return type(value) in (int, float) and 0 <= value <= 100 and math.isfinite(value)


def _nonempty_text(value: Any) -> bool:
    return type(value) is str and bool(value.strip()) and len(value) <= 500


def evaluate_scoring_snapshot(
    records: list[dict[str, Any]],
    *,
    selected_parts: list[str],
    structure: dict[str, Any] | None,
    part_applicability: dict[str, Any] | None,
    reported_score: int | float | None = None,
    reported_source: str = "",
    reported_parts: list[str] | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """Calculate only an explicitly covered component/part snapshot.

    Inputs retain the real diagnostic fields: 构件 / 构件类别 / 病害 / DP值.
    Each record also needs a nonempty ``source`` locator. The locator is an
    audit claim, not independently authenticated evidence. No output here is a
    formal engineering grade, even if all structural checks pass.
    """
    output = {
        "adapter": "new_public_selected_scope_scoring_v1",
        "engine": "BridgeScoringAgent.evaluate_bridge",
        "input_kind": "synthetic" if synthetic else "caller_supplied",
        "status": "rejected", "issues": [], "calculated": None,
        "formal_grade": None, "publishable": False,
        "reported": {"value": reported_score if _valid_number(reported_score) else None, "source": reported_source,
                     "selected_parts": deepcopy(reported_parts), "verified": False},
        "comparison": {"status": "not_comparable", "computed_minus_reported": None},
        "automatic_overwrite_allowed": False, "calibration_applied": False,
        "boundary": "仅为指定部位快照规则演算，不是全桥实测Dr、正式等级或规范有效性证明。",
    }

    def issue(code: str, detail: str, *, severity: str = "rejected") -> None:
        output["issues"].append({"code": code, "detail": detail, "severity": severity})

    if type(records) is not list or not records:
        issue("no_observations", "没有病害记录不能推断现场完好，更不能返回100分。")
        return output
    if len(records) > 256:
        issue("record_limit", "公开快照每次最多256条记录。")
        return output
    if (type(selected_parts) is not list or not selected_parts
            or len(selected_parts) > len(_COUNT_KEYS)
            or any(type(part) is not str for part in selected_parts)
            or len(set(selected_parts)) != len(selected_parts)):
        issue("invalid_scope", "需要非空、无重复的显式部位列表。")
        return output
    parts = set(selected_parts)
    if not parts <= _COUNT_KEYS.keys():
        issue("unsupported_scope", "所选部位缺少本适配器的数量覆盖契约，需专门复核适配。", severity="review")
    structure = deepcopy(structure) if type(structure) is dict else {}
    applicability = deepcopy(part_applicability) if type(part_applicability) is dict else {}
    if reported_score is not None:
        if not _valid_number(reported_score):
            issue("invalid_reported_score", "报告原值必须是0至100之间的有限实数。")
        if not _nonempty_text(reported_source):
            issue("missing_report_source", "报告原值缺少来源定位，不能与重算值合并为同一事实。", severity="review")
    if reported_parts is not None and (type(reported_parts) is not list
            or any(type(part) is not str for part in reported_parts)
            or len(set(reported_parts)) != len(reported_parts)):
        issue("invalid_report_scope", "报告原值范围必须为无重复的显式部位列表。")

    agent = _NoDatabaseScoringAgent()
    observed: dict[str, set[str]] = {part: set() for part in parts}
    prepared = []
    sources = []
    for index, row in enumerate(records, start=1):
        if type(row) is not dict:
            issue("invalid_record", f"第{index}条不是结构化记录。")
            continue
        category, component, disease = row.get("构件类别"), row.get("构件"), row.get("病害")
        if type(category) is not str or category not in COMPONENT_TO_PART:
            issue("unknown_component", f"第{index}条缺少已知的明确构件类别。")
            continue
        if not _nonempty_text(component) or not _INSTANCE.fullmatch(component):
            issue("ambiguous_instance", f"第{index}条需要单个明确编号实例，不能输入范围、无编号或合并构件。")
            continue
        if category not in component:
            issue("component_identity_conflict", f"第{index}条类别与实例名不一致，需核对。", severity="review")
        if not _nonempty_text(disease):
            issue("missing_disease", f"第{index}条没有病害描述。")
        if not _valid_number(row.get("DP值")):
            issue("invalid_dp", f"第{index}条DP必须显式提供0至100之间的有限实数，不能默认补零。")
            continue
        part = "/".join(COMPONENT_TO_PART[category])
        if part not in parts:
            issue("outside_selected_scope", f"第{index}条不属于明确选中的部位。")
            continue
        if not _nonempty_text(row.get("source")):
            issue("missing_record_source", f"第{index}条DP输入缺少来源定位。", severity="review")
        if row.get("评分待复核") or row.get("engineering_verified") is False:
            issue("unverified_record", f"第{index}条已标记待复核，不能直接进入本次快照评分。", severity="review")
        if "标度" in row and (type(row["标度"]) is not int or not 1 <= row["标度"] <= 5):
            issue("invalid_scale", f"第{index}条标度非法。")
        observed[part].add(agent._extract_instance_id(component))
        safe_row = {"构件": component, "构件类别": category,
                    "病害": disease, "DP值": float(row["DP值"])}
        if "标度" in row:
            safe_row["标度"] = row["标度"]
        prepared.append(safe_row)
        sources.append({"record": index, "source": row.get("source"),
                        "component": component, "dp": row["DP值"]})

    validated_structure = {}
    validated_applicability = {}
    for part in sorted(parts):
        if part not in _COUNT_KEYS:
            continue
        system, key = part.split("/")
        count_key = _COUNT_KEYS[part]
        count = structure.get(count_key)
        if type(count) is not int or not 1 <= count <= 128:
            issue("missing_or_invalid_structure", f"{part}缺少1至128之间的明确整数数量：{count_key}。", severity="review")
        else:
            validated_structure[count_key] = count
            if len(observed[part]) != count:
                issue("incomplete_instance_coverage", f"{part}记录实例数与声明数量不同；不把未记录实例填成100分。", severity="review")
        entry = applicability.get(system, {})
        entry = entry.get(key) if type(entry) is dict else None
        if (type(entry) is not dict or type(entry.get("applicable")) is not bool
                or not _nonempty_text(entry.get("source"))):
            issue("missing_applicability", f"{part}需要带来源的明确适用性。", severity="review")
        elif entry["applicable"] is not True:
            issue("applicability_conflict", f"{part}被选择且有记录，却被标为不适用。", severity="review")
        else:
            validated_applicability.setdefault(system, {})[key] = {
                "applicable": True, "source": entry["source"]}
    output["scope"] = {"selected_parts": sorted(parts), "structure": validated_structure,
                       "applicability": validated_applicability}
    output["input_provenance"] = sources
    if output["issues"]:
        output["status"] = "rejected" if any(row["severity"] == "rejected" for row in output["issues"]) else "review_required"
        return output

    # Real inherited data flow and formulas; only external DB loading is disabled.
    try:
        result = agent.evaluate_bridge(prepared, validated_structure, validated_applicability)
    except (ValueError, TypeError, ArithmeticError, KeyError):
        issue("engine_rejected_input", "原评分引擎未能完成本次快照，请人工核对输入。", severity="review")
        output["status"] = "review_required"
        return output
    diagnostics = result.get("评分归类诊断", {})
    if (result.get("未分类病害") or diagnostics.get("structure_adjustments")
            or result.get("评分可靠性", {}).get("provisional")):
        issue("engine_requires_review", "原评分引擎发现结构调整、未分类或暂定条件；不公开数值。", severity="review")
        output["status"] = "review_required"
        return output

    selected_part_scores: dict[str, dict[str, float]] = {}
    for part in sorted(parts):
        system, key = part.split("/")
        score = result.get("部位得分", {}).get(_SYSTEM_NAMES[system], {}).get(key)
        if not _valid_number(score):
            issue("missing_engine_score", f"{part}没有有效计算值。", severity="review")
            output["status"] = "review_required"
            return output
        selected_part_scores.setdefault(system, {})[key] = score
    selected_system_scores = {
        system: round(agent.calculate_system_weighted(scores, WEIGHTS_CONFIG[system]), 2)
        for system, scores in selected_part_scores.items()
    }
    selected_scope_score = round(agent.calculate_system_weighted(selected_system_scores, SYSTEM_WEIGHTS), 2)
    digest_payload = {"records": prepared, "scope": output["scope"]}
    digest = hashlib.sha256(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    output["status"] = "computed_snapshot"
    output["calculated"] = {
        "selected_scope_score": selected_scope_score,
        "component_scores": {key: value for key, value in result["构件得分"].items()
                             if "/".join(key.split("/")[:2]) in parts},
        "part_scores": selected_part_scores,
        "selected_system_scores": selected_system_scores,
        "source": "extracted_BridgeScoringAgent_with_selected_scope_projection",
        "input_sha256": digest,
        "rule_basis_declared_by_legacy_engine": result.get("计算依据"),
        "rule_validity_independently_verified": False,
        "weight_policy": "仅在显式选中的部位/系统内按原引擎权重归一，不补未检查系统100分。",
    }
    if reported_score is not None and reported_parts is not None and set(reported_parts) == parts:
        output["comparison"] = {
            "status": "same_declared_scope_for_review",
            "computed_minus_reported": round(selected_scope_score - reported_score, 2),
            "meaning": "仅为同声明范围的数值差，不证明输入、规范版本或测量时点一致。",
        }
    return output

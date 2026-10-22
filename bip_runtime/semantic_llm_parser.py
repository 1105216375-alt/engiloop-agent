"""Constrained neural candidate parser for bridge-language semantics.

The model never emits an executable engineering fact.  It proposes typed
semantic fields, and every proposal must quote an exact span from the user's
input before the symbolic arbiter can consider it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional


NEURAL_CANDIDATE_VERSION = "bridge.semantic.neural-candidate.v1"

_INTENTS = {
    "compare", "summary", "statistics", "repair_priority", "risk",
    "treatment", "cause", "environment", "cost", "life",
    "repair_status", "archive_search", "knowledge", "report_generation",
}
_CONSTRAINTS = {
    "exclude_repaired", "generate_report", "include_components",
    "exclude_components", "exclusive_intent",
}
_EXISTENCE = {"present", "absent", "unknown"}
_EPISTEMIC = {
    "confirmed", "suspected", "queried", "reported", "not_reported",
    "hypothesized", "unknown",
}
_EVENT_TYPES = {"inspection", "repair", "recurrence", "observation"}
_REFERENCE_TYPES = {"bridge", "component", "defect", "event", "year", "unknown"}


def _json_object(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.0


def _exact_span(value: Any, text: str) -> str:
    span = str(value or "").strip()
    return span if span and span in text else ""


def _clean_rows(
    rows: Any,
    text: str,
    *,
    allowed_names: Optional[Iterable[str]] = None,
    minimum_confidence: float = 0.45,
) -> List[Dict[str, Any]]:
    allowed = set(allowed_names or [])
    cleaned: List[Dict[str, Any]] = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        span = _exact_span(raw.get("source_span"), text)
        confidence = _confidence(raw.get("confidence"))
        if not span or confidence < minimum_confidence:
            continue
        if allowed and name not in allowed:
            continue
        row = dict(raw)
        row["name"] = name
        row["source_span"] = span
        row["confidence"] = confidence
        cleaned.append(row)
    return cleaned


def _clean_entities(raw_entities: Any, text: str) -> Dict[str, List[Dict[str, Any]]]:
    entities = raw_entities if isinstance(raw_entities, dict) else {}
    result: Dict[str, List[Dict[str, Any]]] = {
        "bridge_mentions": [],
        "years": [],
        "components": [],
        "defects": [],
    }
    for key in result:
        for raw in entities.get(key) if isinstance(entities.get(key), list) else []:
            if not isinstance(raw, dict):
                continue
            span = _exact_span(raw.get("source_span") or raw.get("surface"), text)
            confidence = _confidence(raw.get("confidence"))
            if not span or confidence < 0.45:
                continue
            row = dict(raw)
            row["source_span"] = span
            row["confidence"] = confidence
            if key == "years":
                try:
                    value = int(row.get("value"))
                except (TypeError, ValueError):
                    continue
                if value < 1900 or value > 2200:
                    continue
                row["value"] = value
            result[key].append(row)
    return result


def _clean_propositions(rows: Any, text: str) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        span = _exact_span(raw.get("source_span"), text)
        confidence = _confidence(raw.get("confidence"))
        existence = str(raw.get("existence") or "unknown").strip()
        epistemic = str(raw.get("epistemic_status") or "unknown").strip()
        predicate = str(raw.get("predicate") or "").strip()
        if not span or confidence < 0.45 or predicate not in {
            "defect_exists", "cause_of", "repair_status",
        }:
            continue
        if existence not in _EXISTENCE or epistemic not in _EPISTEMIC:
            continue
        cleaned.append({
            "subject": str(raw.get("subject") or "").strip(),
            "predicate": predicate,
            "object": str(raw.get("object") or "").strip(),
            "existence": existence,
            "epistemic_status": epistemic,
            "source_span": span,
            "confidence": confidence,
        })
    return cleaned


def _clean_events(rows: Any, text: str) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        span = _exact_span(raw.get("source_span"), text)
        confidence = _confidence(raw.get("confidence"))
        event_type = str(raw.get("type") or "").strip()
        if not span or confidence < 0.45 or event_type not in _EVENT_TYPES:
            continue
        row = {
            "type": event_type,
            "subject": str(raw.get("subject") or "").strip(),
            "object": str(raw.get("object") or "").strip(),
            "time": raw.get("time"),
            "status": str(raw.get("status") or "").strip(),
            "source_span": span,
            "confidence": confidence,
        }
        cleaned.append(row)
    return cleaned


def _clean_references(rows: Any, text: str) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        span = _exact_span(raw.get("source_span") or raw.get("surface"), text)
        confidence = _confidence(raw.get("confidence"))
        ref_type = str(raw.get("type") or "unknown").strip()
        if not span or confidence < 0.45 or ref_type not in _REFERENCE_TYPES:
            continue
        cleaned.append({
            "surface": span,
            "type": ref_type,
            "resolved_to": str(raw.get("resolved_to") or "").strip(),
            "resolution_basis": str(raw.get("resolution_basis") or "").strip(),
            "source_span": span,
            "confidence": confidence,
        })
    return cleaned


def validate_neural_candidate(payload: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Whitelist fields and discard every item without exact source evidence."""
    candidate = {
        "schema_version": NEURAL_CANDIDATE_VERSION,
        "intents": _clean_rows(payload.get("intents"), text, allowed_names=_INTENTS),
        "constraints": _clean_rows(
            payload.get("constraints"), text, allowed_names=_CONSTRAINTS,
        ),
        "entities": _clean_entities(payload.get("entities"), text),
        "propositions": _clean_propositions(payload.get("propositions"), text),
        "events": _clean_events(payload.get("events"), text),
        "references": _clean_references(payload.get("references"), text),
    }
    candidate["accepted_item_count"] = (
        len(candidate["intents"])
        + len(candidate["constraints"])
        + sum(len(rows) for rows in candidate["entities"].values())
        + len(candidate["propositions"])
        + len(candidate["events"])
        + len(candidate["references"])
    )
    return candidate


def parse_neural_semantic_candidate(
    text: str,
    *,
    rule_frame: Dict[str, Any],
    session_context: Optional[Dict[str, Any]],
    llm_call: Callable[[str, str], Any],
) -> Dict[str, Any]:
    """Ask an LLM for a typed, evidence-bound semantic candidate."""
    context = dict(session_context or {})
    compact_context = {
        "bridge_name": context.get("bridge_name") or "",
        "archive_years": list(context.get("archive_years") or [])[:8],
        "components": list(context.get("components") or [])[:12],
        "current_date": context.get("current_date") or "",
    }
    compact_rule = {
        "entities": rule_frame.get("entities") or {},
        "intents": rule_frame.get("intents") or [],
        "constraints": rule_frame.get("constraints") or {},
        "defect_mentions": [
            {
                "component": row.get("component"),
                "disease": row.get("disease"),
                "polarity": row.get("polarity"),
                "status": row.get("status"),
                "certainty": row.get("certainty"),
                "source_text": row.get("source_text"),
            }
            for row in (rule_frame.get("defect_mentions") or [])[:12]
        ],
    }
    system_prompt = (
        "你是桥梁领域神经符号系统中的受约束语义解析器，不是回答问题的助手。"
        "只把用户原话转换成JSON语义候选，禁止输出工程结论、评分、处治意见或原文没有的信息。"
        "每个候选项必须给出source_span，且source_span必须是用户原话中的连续原文；没有原文依据就不要输出。"
        "桥名简称只能保留原始mention，除非会话上下文明确给出唯一完整桥名。"
        "严格区分：没有=absent；没发现=reported absent；没说有/未记录=unknown+not_reported；"
        "可能= suspected；是否=queried。还要区分病害事实与成因假设的修饰作用域。"
        "只输出一个JSON对象，不要Markdown。允许的结构为："
        "{intents:[{name,source_span,confidence}],"
        "constraints:[{name,value,source_span,confidence}],"
        "entities:{bridge_mentions:[],years:[],components:[],defects:[]},"
        "propositions:[{subject,predicate,object,existence,epistemic_status,source_span,confidence}],"
        "events:[{type,subject,object,time,status,source_span,confidence}],"
        "references:[{surface,type,resolved_to,resolution_basis,source_span,confidence}]}。"
        "intent仅可为compare/summary/statistics/repair_priority/risk/treatment/cause/environment/cost/"
        "life/repair_status/archive_search/knowledge/report_generation。"
        "constraint仅可为exclude_repaired/generate_report/include_components/exclude_components/exclusive_intent。"
        "predicate仅可为defect_exists/cause_of/repair_status。"
    )
    user_payload = json.dumps({
        "input": text,
        "session_context": compact_context,
        "deterministic_candidate": compact_rule,
    }, ensure_ascii=False)
    try:
        raw = llm_call(system_prompt, user_payload)
    except Exception as exc:
        return {
            "status": "error",
            "error": f"neural_parser_exception:{str(exc)[:180]}",
            "candidate": validate_neural_candidate({}, text),
        }
    raw_text = str(raw or "")
    if any(marker in raw_text for marker in ("未配置", "调用失败", "API Key", "接口地址")):
        return {
            "status": "unavailable",
            "error": raw_text[:180],
            "candidate": validate_neural_candidate({}, text),
        }
    parsed = _json_object(raw)
    if parsed is None:
        return {
            "status": "invalid_json",
            "error": "模型未返回有效JSON对象",
            "candidate": validate_neural_candidate({}, text),
        }
    candidate = validate_neural_candidate(parsed, text)
    return {
        "status": "used" if candidate["accepted_item_count"] else "empty",
        "error": "",
        "candidate": candidate,
    }

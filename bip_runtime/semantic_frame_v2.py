"""Hybrid neuro-symbolic semantic compiler for bridge-domain questions.

Version 2 preserves the version-1 frame contract while adding event semantics,
field provenance, neural candidate arbitration, and conservative conflict
handling.  Neural output may narrow or block an unsafe interpretation, but it
can never create an engineering fact eligible for diagnosis by itself.
"""
from __future__ import annotations

import copy
import re
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .semantic_llm_parser import parse_neural_semantic_candidate


SEMANTIC_FRAME_V2_VERSION = "bridge.semantic.v2"

_INTENT_LABELS = {
    "diagnosis": "病害诊断",
    "observation_update": "状态更新",
    "compare": "对比分析",
    "summary": "综合汇总",
    "statistics": "统计分析",
    "repair_priority": "维修优先级",
    "risk": "风险评估",
    "treatment": "处治建议",
    "cause": "原因分析",
    "environment": "环境影响",
    "cost": "费用估算",
    "life": "寿命预测",
    "repair_status": "维修闭环",
    "archive_search": "档案检索",
    "knowledge": "规范问答",
    "report_generation": "报告生成",
}

_ANALYSIS_INTENTS = {
    "compare", "summary", "statistics", "repair_priority", "risk",
    "treatment", "cause", "environment", "cost", "life",
    "repair_status", "archive_search", "report_generation",
}

_NEURAL_TRIGGERS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("complex_negation", re.compile(
        r"没说有|没有说有|未说明|未提到|没提到|未记录|没有记录|"
        r"不是说|并非|未必|不见得|不一定|应该不是|不能说明|不代表"
    )),
    ("relative_time", re.compile(
        r"今年|去年|前年|上次|前次|最近一次|修前|修后|维修后|复检后|那次"
    )),
    ("reference_resolution", re.compile(
        r"这座桥|那座桥|这桥|那个桥|它|这个|那个|那条|这条|"
        r"三号那个|另一个|前者|后者|上述|刚才"
    )),
    ("implicit_intent", re.compile(
        r"最要紧|最该修|先搞谁|先弄谁|先处理谁|哪个.*先|还能扛|还能撑|"
        r"好在哪|变好|变差|恶化|改善|别出\s*(?:Word|word)|别算"
    )),
    ("multi_proposition", re.compile(r"但|不过|而是|只是|后来|之后|然后|同时")),
)

_NOT_REPORTED = re.compile(
    r"没说有|没有说有|未说明(?:存在|有)?|未提到|没提到|"
    r"未记录(?:存在|有)?|没有记录(?:存在|有)?|无相关记录|"
    r"(?:报告|记录|档案)?(?:里|中)?(?:没说|没有说|未说明|未提到|没提到|未记录|没有记录)"
    r"[^，,；;。]{0,24}(?:有|存在)"
)
_EXCLUDE_REPAIRED = re.compile(
    r"排除(?:已经|已)?(?:闭环|修复|维修|处理|处治|整治)(?:的|项|项目|记录)?|"
    r"(?:别|不要|不)(?:把)?(?:已经)?(?:修过|修好|修复|处理|处治|整治)"
    r"(?:的|那些|项目|项)?(?:算|统计|计入|列入|纳入|看)|"
    r"(?:修过|修好|已修|已处理|已处治|修完)(?:的|那些|项目|项)?(?:先)?(?:别|不要|不)"
    r"(?:算|统计|计入|列入|纳入|看)|"
    r"(?:exclude|omit|ignore)\s+(?:already\s+)?repaired(?:\s+items?)?"
, re.IGNORECASE
)
_NO_REPORT = re.compile(
    r"(?:别|不要|不用|无需|不)(?:再)?(?:出|生成|导出|形成)\s*(?:Word|word|报告)|"
    r"(?:Word|word|报告)(?:别|不要|不用|无需|不)(?:出|生成|导出)|"
    r"别(?:给我)?写(?:Word|word|文件|报告)|别整\s*(?:Word|word|报告)|"
    r"只(?:在页面|口头)(?:说|回答|展示)|"
    r"(?:no|without)\s+(?:word|report)|do\s+not\s+(?:create|generate|export)\s+(?:a\s+)?(?:word|report)"
, re.IGNORECASE
)
_EVIDENCE_ONLY = re.compile(
    r"只(?:用|依据|引用)(?:报告原文|原文|规范|档案证据)|"
    r"(?:别|不要)(?:拿|用)(?:模型结论|模型推断|猜测)|"
    r"只说(?:报告|规范|档案)(?:能证明|有证据)(?:的|内容)?|"
    r"(?:evidence|source)[ -]?only|only\s+(?:use|cite)\s+(?:the\s+)?(?:report|standard|evidence)",
    re.IGNORECASE,
)
_NO_TREATMENT = re.compile(
    r"(?:先)?(?:别给|不给)(?:我)?(?:修复|处治|维修)?方案|"
    r"(?:修复|处治|维修)?方案(?:先)?(?:别|不要|不用)讲|"
    r"先不(?:说|讲|谈)(?:怎么修|处治|维修|方案)"
)
_REPAIR_PRIORITY = re.compile(
    r"最要紧|最该(?:先)?修|先(?:搞|弄|处理|修)谁|"
    r"(?:哪个|哪些|谁)(?:最)?(?:该|要|需要)?先(?:修|处理|搞|弄)|"
    r"先修(?:哪个|哪些|谁)|先(?:修|搞|整|弄)(?:啥|什么|这个|哪个)"
)
_LIFE_INTENT = re.compile(r"还能(?:扛|撑|顶)(?:多少|几)?年|还能(?:扛|撑|顶)多久")
_COMPARE_INTENT = re.compile(r"好在哪|好点(?:了|没有)?|变好(?:了吗|没有)?|变差(?:了吗|没有)?|改善多少|恶化多少")
_EXCLUSIVE_INTENT_NEGATION = {
    "risk": re.compile(r"(?:不要|别|不需要|无需)(?:再)?(?:讲|说|分析|看)?风险"),
    "cause": re.compile(r"(?:不要|别|不需要|无需)(?:再)?(?:讲|说|分析|解释)?(?:原因|成因)"),
    "treatment": re.compile(r"(?:不要|别|不需要|无需)(?:再)?(?:讲|说|给|分析)?(?:处治|维修|修复|方案|建议)"),
    "environment": re.compile(r"(?:不要|别|不需要|无需)(?:再)?(?:讲|说|分析|看)?环境"),
}


def _ordered_unique(values: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "是", "需要"}:
        return True
    if text in {"false", "0", "no", "否", "不要", "不需要"}:
        return False
    return None


def _today(context: Dict[str, Any], override: Optional[date]) -> date:
    if override is not None:
        return override
    raw = context.get("current_date")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    return date.today()


def _source_entry(field: str, value: Any, span: str, source: str, confidence: float = 1.0) -> Dict[str, Any]:
    return {
        "field": field,
        "value": value,
        "source_span": span,
        "source": source,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
    }


def _ensure_intent(frame: Dict[str, Any], intent: str) -> bool:
    intents = list(frame.get("intents") or [])
    if intent in intents:
        return False
    exclusive = (frame.get("constraints") or {}).get("exclusive_intent")
    if exclusive and intent != exclusive:
        return False
    intents.append(intent)
    frame["intents"] = intents
    return True


def _exclude_intent(frame: Dict[str, Any], intent: str) -> None:
    frame["intents"] = [value for value in (frame.get("intents") or []) if value != intent]
    frame["excluded_intents"] = _ordered_unique([
        *(frame.get("excluded_intents") or []), intent,
    ])


def _resolve_relative_years(
    text: str,
    context: Dict[str, Any],
    current_date: date,
) -> List[Dict[str, Any]]:
    resolutions: List[Dict[str, Any]] = []
    direct = {
        "今年": current_date.year,
        "去年": current_date.year - 1,
        "前年": current_date.year - 2,
    }
    for surface, value in direct.items():
        if surface in text:
            resolutions.append({
                "surface": surface,
                "value": value,
                "resolution_basis": f"message_date:{current_date.isoformat()}",
                "source_span": surface,
                "confidence": 1.0,
            })

    archive_years: List[int] = []
    for raw in context.get("archive_years") or []:
        try:
            archive_years.append(int(raw))
        except (TypeError, ValueError):
            continue
    archive_years = sorted(set(archive_years))
    archive_terms = (
        ("最近一次", archive_years[-1] if archive_years else None),
        ("上次", archive_years[-1] if archive_years else None),
        ("前次", archive_years[-2] if len(archive_years) >= 2 else None),
    )
    for surface, value in archive_terms:
        if surface in text and value is not None:
            resolutions.append({
                "surface": surface,
                "value": value,
                "resolution_basis": "selected_bridge_archive_years",
                "source_span": surface,
                "confidence": 0.96,
            })
    return resolutions


def _deterministic_enrichment(
    text: str,
    frame: Dict[str, Any],
    context: Dict[str, Any],
    current_date: date,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    accepted: List[Dict[str, Any]] = []
    constraints = frame.setdefault("constraints", {})
    exclusive_intent = str(constraints.get("exclusive_intent") or "")
    negative = _EXCLUSIVE_INTENT_NEGATION.get(exclusive_intent)
    if negative:
        match = negative.search(text)
        if match:
            frame.setdefault("ambiguities", []).append({
                "type": "intent_conflict",
                "impact": f"同一句话同时要求仅执行和禁止执行{exclusive_intent}",
                "intent": exclusive_intent,
                "source_span": match.group(0),
            })

    match = _REPAIR_PRIORITY.search(text)
    if match and _ensure_intent(frame, "repair_priority"):
        accepted.append(_source_entry(
            "intents.repair_priority", True, match.group(0), "symbolic_v2",
        ))
    match = _LIFE_INTENT.search(text)
    if match and _ensure_intent(frame, "life"):
        accepted.append(_source_entry("intents.life", True, match.group(0), "symbolic_v2"))
    match = _COMPARE_INTENT.search(text)
    if match and _ensure_intent(frame, "compare"):
        accepted.append(_source_entry("intents.compare", True, match.group(0), "symbolic_v2"))

    match = _EXCLUDE_REPAIRED.search(text)
    if match:
        constraints["exclude_repaired"] = True
        _ensure_intent(frame, "repair_status")
        accepted.append(_source_entry(
            "constraints.exclude_repaired", True, match.group(0), "symbolic_v2",
        ))
    match = _NO_REPORT.search(text)
    if match:
        constraints["generate_report"] = False
        _exclude_intent(frame, "report_generation")
        accepted.append(_source_entry(
            "constraints.generate_report", False, match.group(0), "symbolic_v2",
        ))
    match = _EVIDENCE_ONLY.search(text)
    if match:
        constraints["evidence_only"] = True
        _ensure_intent(frame, "knowledge")
        accepted.append(_source_entry(
            "constraints.evidence_only", True, match.group(0), "symbolic_v2",
        ))
    match = _NO_TREATMENT.search(text)
    if match:
        _exclude_intent(frame, "treatment")
        accepted.append(_source_entry(
            "excluded_intents.treatment", True, match.group(0), "symbolic_v2",
        ))

    temporal = _resolve_relative_years(text, context, current_date)
    if temporal:
        entities = frame.setdefault("entities", {})
        entities["years"] = sorted(_ordered_unique([
            *(entities.get("years") or []),
            *(item["value"] for item in temporal),
        ]))
        accepted.extend(
            _source_entry("entities.years", item["value"], item["source_span"], "symbolic_v2")
            for item in temporal
        )

    not_reported_mentions: List[str] = []
    for mention in frame.get("defect_mentions") or []:
        clause = str(mention.get("source_text") or text)
        marker = _NOT_REPORTED.search(clause)
        if not marker:
            continue
        mention["polarity"] = "unknown"
        mention["status"] = "unknown"
        mention["certainty"] = "unknown"
        mention["evidence_status"] = "not_reported"
        mention["active_for_diagnosis"] = False
        mention["blocked_reason"] = "原文仅表示未报告，不能作为病害存在或不存在的证据"
        mention["rules"] = _ordered_unique([
            *(mention.get("rules") or []), "absence_of_evidence_not_evidence_of_absence",
        ])
        not_reported_mentions.append(str(mention.get("mention_id") or ""))
        accepted.append(_source_entry(
            f"defect_mentions.{mention.get('mention_id')}.epistemic_status",
            "not_reported", marker.group(0), "symbolic_v2",
        ))
    if not_reported_mentions:
        frame.setdefault("ambiguities", []).append({
            "type": "evidence_not_reported",
            "impact": "未报告不等于病害不存在，不能进入评分",
            "mentions": not_reported_mentions,
        })
    return accepted, temporal


def assess_neural_need(text: str, frame: Dict[str, Any]) -> List[str]:
    reasons = [name for name, pattern in _NEURAL_TRIGGERS if pattern.search(text)]
    if frame.get("ambiguities"):
        reasons.append("symbolic_ambiguity")
    if not (frame.get("intents") or []) and not (frame.get("defect_mentions") or []):
        reasons.append("rule_frame_incomplete")
    return _ordered_unique(reasons)


def _candidate_component_values(value: Any) -> List[str]:
    if isinstance(value, list):
        return _ordered_unique(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    return [text] if text else []


def _mention_matches_proposition(mention: Dict[str, Any], proposition: Dict[str, Any]) -> bool:
    subject = str(proposition.get("subject") or "")
    obj = str(proposition.get("object") or "")
    component = str(mention.get("component") or "")
    disease = str(mention.get("disease") or "")
    component_match = not subject or subject in component or component in subject
    disease_match = not obj or obj in disease or disease in obj
    return component_match and disease_match


def _merge_neural_candidate(
    text: str,
    frame: Dict[str, Any],
    candidate: Dict[str, Any],
    context: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    constraints = frame.setdefault("constraints", {})

    for row in candidate.get("intents") or []:
        intent = row.get("name")
        if _ensure_intent(frame, intent):
            accepted.append(_source_entry(
                f"intents.{intent}", True, row["source_span"], "neural_candidate", row["confidence"],
            ))

    known_components = set(context.get("components") or [])
    known_components.update((frame.get("entities") or {}).get("components") or [])
    for row in candidate.get("constraints") or []:
        name = row.get("name")
        value = row.get("value")
        if name == "exclude_repaired" and _as_bool(value) is True:
            constraints["exclude_repaired"] = True
            _ensure_intent(frame, "repair_status")
        elif name == "generate_report":
            parsed = _as_bool(value)
            if parsed is False:
                constraints["generate_report"] = False
                _exclude_intent(frame, "report_generation")
            elif parsed is True and constraints.get("generate_report") is not False:
                constraints["generate_report"] = True
                _ensure_intent(frame, "report_generation")
            else:
                rejected.append({"field": name, "reason": "invalid_or_weaker_constraint"})
                continue
        elif name in {"include_components", "exclude_components"}:
            values = _candidate_component_values(value)
            safe_values = [
                component for component in values
                if component in known_components or component in text
            ]
            if not safe_values:
                rejected.append({"field": name, "reason": "component_without_text_or_context_anchor"})
                continue
            constraints[name] = _ordered_unique([
                *(constraints.get(name) or []), *safe_values,
            ])
        elif name == "exclusive_intent" and str(value) in _INTENT_LABELS:
            exclusive = str(value)
            constraints["exclusive_intent"] = exclusive
            frame["intents"] = [exclusive]
            frame["excluded_intents"] = _ordered_unique([
                *(frame.get("excluded_intents") or []),
                *(_ANALYSIS_INTENTS - {exclusive}),
            ])
        else:
            rejected.append({"field": name, "reason": "unsupported_constraint_value"})
            continue
        accepted.append(_source_entry(
            f"constraints.{name}", value, row["source_span"], "neural_candidate", row["confidence"],
        ))

    entities = frame.setdefault("entities", {})
    for row in (candidate.get("entities") or {}).get("years") or []:
        year = row.get("value")
        entities["years"] = sorted(_ordered_unique([*(entities.get("years") or []), year]))
        accepted.append(_source_entry(
            "entities.years", year, row["source_span"], "neural_candidate", row["confidence"],
        ))

    known_bridge = str(context.get("bridge_name") or entities.get("bridge_name") or "").strip()
    for row in (candidate.get("entities") or {}).get("bridge_mentions") or []:
        canonical = str(row.get("canonical") or row.get("value") or "").strip()
        if canonical and known_bridge and canonical == known_bridge:
            entities["bridge_name"] = known_bridge
            accepted.append(_source_entry(
                "entities.bridge_name", known_bridge, row["source_span"], "neural_candidate", row["confidence"],
            ))
        elif canonical and canonical != known_bridge:
            rejected.append({
                "field": "entities.bridge_name",
                "value": canonical,
                "reason": "canonical_bridge_not_confirmed_by_archive_or_context",
            })

    for proposition in candidate.get("propositions") or []:
        if proposition.get("predicate") != "defect_exists":
            continue
        matches = [
            mention for mention in (frame.get("defect_mentions") or [])
            if _mention_matches_proposition(mention, proposition)
        ]
        if not matches:
            rejected.append({
                "field": "proposition.defect_exists",
                "reason": "no_deterministic_defect_anchor",
                "source_span": proposition.get("source_span"),
            })
            continue
        for mention in matches:
            rule_active = bool(mention.get("active_for_diagnosis"))
            existence = proposition.get("existence")
            epistemic = proposition.get("epistemic_status")
            neural_active = existence == "present" and epistemic in {"confirmed", "reported"}
            if rule_active == neural_active:
                accepted.append(_source_entry(
                    f"defect_mentions.{mention.get('mention_id')}.neural_agreement",
                    True, proposition["source_span"], "neural_candidate", proposition["confidence"],
                ))
                continue
            conflict = {
                "type": "defect_assertion_conflict",
                "severity": "high",
                "mention_id": mention.get("mention_id"),
                "component": mention.get("component"),
                "disease": mention.get("disease"),
                "rule_active": rule_active,
                "neural_existence": existence,
                "neural_epistemic_status": epistemic,
                "source_span": proposition.get("source_span"),
                "resolution": "block_and_clarify",
            }
            conflicts.append(conflict)
            # A neural candidate may block a potentially unsafe fact, but never
            # promote a fact rejected by deterministic rules.
            mention["active_for_diagnosis"] = False
            mention["blocked_reason"] = "规则与开放语义解析对病害事实存在冲突，已暂停评分"
            mention["evidence_status"] = "semantic_conflict"
            mention["rules"] = _ordered_unique([
                *(mention.get("rules") or []), "neural_symbolic_conflict_gate",
            ])
    return accepted, rejected, conflicts


def _rebuild_frame_summary(frame: Dict[str, Any], conflicts: Sequence[Dict[str, Any]]) -> None:
    mentions = list(frame.get("defect_mentions") or [])
    active = [row for row in mentions if row.get("active_for_diagnosis")]
    blocked = [row for row in mentions if not row.get("active_for_diagnosis")]
    intents = list(frame.get("intents") or [])
    if active:
        if "diagnosis" not in intents:
            intents.append("diagnosis")
    else:
        intents = [value for value in intents if value != "diagnosis"]
        if mentions and "observation_update" not in intents:
            intents.append("observation_update")
    frame["intents"] = _ordered_unique(intents)
    frame["intent_labels"] = [
        _INTENT_LABELS.get(value, value) for value in frame["intents"]
    ]

    high_conflict = any(row.get("severity") == "high" for row in conflicts)
    intent_conflict = next(
        (
            row for row in (frame.get("ambiguities") or [])
            if row.get("type") == "intent_conflict"
        ),
        None,
    )
    analysis_requested = bool(set(frame["intents"]) & _ANALYSIS_INTENTS)
    if intent_conflict:
        frame["requires_clarification"] = True
        frame["clarification_question"] = (
            "这句话同时要求执行和禁止同一项分析。请明确最终要保留的任务，"
            "系统不会自行猜测优先级。"
        )
    elif high_conflict:
        frame["requires_clarification"] = True
        frame["clarification_question"] = (
            "规则解析与开放语义解析对病害是否成立存在冲突。请明确该病害是已确认存在、"
            "仅为疑似，还是报告中没有记录；确认后再进入评分。"
        )
    elif any(row.get("evidence_status") == "not_reported" for row in blocked):
        frame["requires_clarification"] = bool(analysis_requested)
        if frame["requires_clarification"]:
            frame["clarification_question"] = (
                "当前原文只说明报告没有记录该病害，不能判断其存在或不存在。"
                "如需风险或评分分析，请补充现场确认或报告证据。"
            )
    frame["partial_clarification"] = bool(
        frame.get("partial_clarification")
        or ((frame.get("requires_clarification") or conflicts) and active)
    )
    verification = frame.setdefault("verification", {})
    verification.update({
        "passed": not bool(frame.get("requires_clarification")),
        "diagnostic_safe": True,
        "active_mentions": len(active),
        "blocked_mentions": len(blocked),
        "semantic_conflicts": len(conflicts),
    })
    frame["summary"] = {
        "intents": frame["intent_labels"],
        "active": [
            f"{row.get('component')}·{row.get('disease')}" for row in active
        ],
        "blocked": [
            f"{row.get('component')}·{row.get('disease')}({row.get('blocked_reason') or '语义闸门拦截'})"
            for row in blocked
        ],
        "years": (frame.get("entities") or {}).get("years") or [],
        "scope": (frame.get("constraints") or {}).get("scope_mode") or "all",
    }


def _deterministic_references(text: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    references: List[Dict[str, Any]] = []
    known_bridge = str(context.get("bridge_name") or "").strip()
    components = list(context.get("components") or [])
    for surface in ("这座桥", "那座桥", "这桥", "那个桥", "该桥"):
        if surface in text:
            references.append({
                "surface": surface,
                "type": "bridge",
                "resolved_to": known_bridge,
                "resolution_basis": "session_bridge" if known_bridge else "unresolved",
                "source_span": surface,
                "confidence": 1.0 if known_bridge else 0.0,
            })
    for surface in ("它", "这个", "那个", "这条", "那条"):
        if surface in text:
            resolved = components[0] if len(components) == 1 else ""
            references.append({
                "surface": surface,
                "type": "component" if resolved else "unknown",
                "resolved_to": resolved,
                "resolution_basis": "single_context_component" if resolved else "ambiguous",
                "source_span": surface,
                "confidence": 0.92 if resolved else 0.0,
            })
    return references


def _propositions_from_mentions(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    propositions: List[Dict[str, Any]] = []
    for mention in frame.get("defect_mentions") or []:
        polarity = mention.get("polarity")
        evidence_status = mention.get("evidence_status")
        certainty = mention.get("certainty")
        existence = "absent" if polarity == "negative" else (
            "present" if polarity == "affirmed" else "unknown"
        )
        if evidence_status == "not_reported":
            epistemic = "not_reported"
            existence = "unknown"
        elif evidence_status == "semantic_conflict":
            epistemic = "unknown"
            existence = "unknown"
        elif polarity == "queried":
            epistemic = "queried"
        elif certainty == "suspected":
            epistemic = "suspected"
        else:
            epistemic = "confirmed"
        propositions.append({
            "proposition_id": f"P-{mention.get('mention_id')}",
            "subject": mention.get("component") or "",
            "predicate": "defect_exists",
            "object": mention.get("disease") or "",
            "existence": existence,
            "epistemic_status": epistemic,
            "eligible_for_diagnosis": bool(mention.get("active_for_diagnosis")),
            "source_span": mention.get("source_text") or frame.get("input") or "",
            "provenance": evidence_status or "user_asserted",
        })
    return propositions


def _events_from_mentions(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for mention in frame.get("defect_mentions") or []:
        status = mention.get("status")
        if status not in {"repaired", "unrepaired", "recurrence"}:
            continue
        events.append({
            "event_id": f"E-{mention.get('mention_id')}",
            "type": "recurrence" if status == "recurrence" else "repair",
            "subject": mention.get("component") or "",
            "object": mention.get("disease") or "",
            "status": status,
            "source_span": mention.get("source_text") or frame.get("input") or "",
            "eligible_for_engineering_fact": bool(mention.get("active_for_diagnosis")),
        })
    return events


def build_semantic_frame_v2(
    text: str,
    rule_frame: Dict[str, Any],
    *,
    session_context: Optional[Dict[str, Any]] = None,
    llm_call: Optional[Callable[[str, str], Any]] = None,
    current_date: Optional[date] = None,
    force_neural: bool = False,
) -> Dict[str, Any]:
    """Compile and arbitrate a V2 frame while preserving V1 consumer fields."""
    raw_text = str(text or "").strip()
    context = dict(session_context or {})
    frame = copy.deepcopy(rule_frame or {})
    source_schema = frame.get("schema_version") or "bridge.semantic.v1"
    now = _today(context, current_date)

    symbolic_accepts, temporal = _deterministic_enrichment(
        raw_text, frame, context, now,
    )
    trigger_reasons = assess_neural_need(raw_text, frame)
    neural_requested = bool(force_neural or trigger_reasons)
    neural_result: Dict[str, Any] = {
        "status": "not_requested" if not neural_requested else "unavailable",
        "error": "",
        "candidate": {
            "intents": [], "constraints": [],
            "entities": {"bridge_mentions": [], "years": [], "components": [], "defects": []},
            "propositions": [], "events": [], "references": [],
            "accepted_item_count": 0,
        },
    }
    if neural_requested and llm_call is not None:
        neural_result = parse_neural_semantic_candidate(
            raw_text,
            rule_frame=frame,
            session_context=context,
            llm_call=llm_call,
        )

    neural_accepts: List[Dict[str, Any]] = []
    neural_rejects: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    candidate = neural_result.get("candidate") or {}
    if neural_result.get("status") in {"used", "empty"}:
        neural_accepts, neural_rejects, conflicts = _merge_neural_candidate(
            raw_text, frame, candidate, context,
        )

    _rebuild_frame_summary(frame, conflicts)
    deterministic_refs = _deterministic_references(raw_text, context)
    references = [*deterministic_refs, *(candidate.get("references") or [])]
    propositions = _propositions_from_mentions(frame)
    neural_events = [
        {
            **row,
            "provenance": "neural_candidate",
            "eligible_for_engineering_fact": False,
        }
        for row in (candidate.get("events") or [])
    ]
    events = [*_events_from_mentions(frame), *neural_events]

    if neural_result.get("status") == "used":
        parser_mode = "hybrid_neuro_symbolic"
    elif neural_requested:
        parser_mode = "deterministic_v2_neural_fallback"
    else:
        parser_mode = "deterministic_v2_fast_path"

    frame["schema_version"] = SEMANTIC_FRAME_V2_VERSION
    frame["parser"] = "neuro_symbolic_semantic_compiler"
    frame["parser_mode"] = parser_mode
    frame["semantic_v2"] = {
        "schema_version": SEMANTIC_FRAME_V2_VERSION,
        "compatibility_schema": source_schema,
        "task": {
            "intents": list(frame.get("intents") or []),
            "excluded_intents": list(frame.get("excluded_intents") or []),
        },
        "propositions": propositions,
        "events": events,
        "references": references,
        "temporal_resolution": temporal,
        "provenance": [*symbolic_accepts, *neural_accepts],
        "arbitration": {
            "mode": parser_mode,
            "neural_triggered": neural_requested,
            "trigger_reasons": trigger_reasons,
            "neural_status": neural_result.get("status") or "unknown",
            "neural_error": neural_result.get("error") or "",
            "candidate_items": int(candidate.get("accepted_item_count") or 0),
            "accepted_fields": len(symbolic_accepts) + len(neural_accepts),
            "rejected_fields": neural_rejects,
            "conflicts": conflicts,
            "hard_gate": "no_neural_fact_promotion",
        },
    }
    frame.setdefault("verification", {})["semantic_v2"] = {
        "passed": not bool(frame.get("requires_clarification")),
        "provenance_bound": True,
        "neural_fact_promotion_allowed": False,
        "conflict_count": len(conflicts),
    }
    return frame

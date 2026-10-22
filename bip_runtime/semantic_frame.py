"""Structured semantic frame and input-side semantic verification.

The legacy NLU extracts bridge component/disease pairs well, but a pair alone
does not say whether the user affirmed, denied, repaired, suspected, included,
or excluded the defect.  This module adds that missing layer without changing
the legacy diagnosis schema.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .archive_scope import extract_requested_years, strip_requested_years


SEMANTIC_FRAME_VERSION = "bridge.semantic.v1"

_UNCERTAIN_MARKERS = (
    "可能", "疑似", "似乎", "好像", "也许", "或许", "怀疑", "不确定",
    "待确认", "待核实", "看着像", "像是",
)
_QUERY_MARKERS = (
    "有没有", "有无", "是否有", "是不是有", "是否存在", "是不是存在",
    "算不算", "是不是", "吗",
)
_HYPOTHETICAL_MARKERS = (
    "假装", "假设", "假定", "虚构", "编造", "捏造", "随便说", "随便编",
    "当作存在", "当成存在", "假装存在",
)
_RECURRENCE_MARKERS = (
    "复发", "再次出现", "重新出现", "又出现", "再度出现", "修复后又",
    "处治后又", "修补后又", "修过又", "维修后又", "处理后又",
    "复检又", "同一位置又", "又开了", "又裂了", "又脱空", "又空了", "又悬空",
    "又裂缝", "再次脱空", "重新脱空", "重新开裂",
)
_REPAIRED_MARKERS = (
    "已经修复", "已修复", "修好了", "已修好", "已经处治", "已处治",
    "已经处理", "已处理", "已经整治", "已整治", "完成修复", "完成修补",
    "维修完成", "处治完成", "已经修过", "修过",
)
_UNREPAIRED_MARKERS = (
    "尚未修复", "未修复", "没有修复", "没修复", "尚未处治", "未处治",
    "没有处理", "没处理", "待修复", "待处治",
)
_NEGATION_PATTERNS = (
    re.compile(r"(?:没有|没发现|未发现|未见|不存在|并无|也没)(?!有?修复|有?处治|有?处理)"),
    re.compile(r"(?:^|[，,；;。\s])无(?:明显|相关|该|此|新发|新增)?(?:病害|裂|缝|脱空|锈|破损|渗水|露筋|偏位|坑槽|沉降)"),
)

_INTENT_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("compare", ("对比", "比较", "差异", "变化", "趋势", "跨年", "哪个更")),
    ("summary", ("汇总", "总结", "综合分析", "综合评估", "整体情况", "年度汇总", "项目汇总")),
    ("statistics", ("统计", "多少条", "几条", "占比", "分布", "总共", "一共")),
    ("repair_priority", (
        "优先维修", "维修优先", "优先级", "先修", "先处理", "维修顺序", "处治顺序", "优先",
    )),
    ("risk", (
        "风险", "严重", "危险", "安全", "大修", "值不值得",
        "封桥", "限行", "限载", "交通管制", "还能不能过车", "能不能过车",
    )),
    ("treatment", ("怎么修", "如何修", "修复方案", "处治建议", "处治方案", "加固", "维修建议", "措施")),
    ("cause", ("原因", "成因", "为什么", "为啥", "机理")),
    ("environment", ("环境", "沿海", "氯盐", "盐雾", "冻融", "潮湿", "腐蚀环境")),
    ("cost", ("预算", "费用", "成本", "多少钱", "造价", "全寿命成本")),
    ("life", ("寿命", "剩余使用", "还能用", "服役年限")),
    ("repair_status", (
        "已修复", "修好了", "修过", "维修过", "处治过", "维修记录",
        "修复情况", "处治闭环", "复发",
    )),
    ("archive_search", ("档案", "报告里", "报告中", "记录里", "文件里", "查找", "搜索", "调取")),
    ("knowledge", (
        "规范", "标准", "条文", "定义", "怎么算", "如何评定", "什么是",
        "凭什么", "依据", "证据边界", "报告Dr", "重算Dr", "BCI", "评分冲突",
    )),
    ("report_generation", ("生成报告", "出报告", "导出报告", "形成报告", "生成汇总报告")),
)

_EXCLUDED_INTENT_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "treatment": (
        "不要给修复方案", "不需要修复方案", "无需修复方案", "不要处治建议",
        "不需要处治建议", "不要维修建议", "不谈修复", "先不说怎么修",
        "不要方案", "不需要方案", "无需方案", "只要原因", "只讲原因",
    ),
    "cause": ("不要解释原因", "不分析原因", "无需原因分析", "不谈成因"),
    "report_generation": (
        "不要生成报告", "不生成报告", "无需生成报告", "不要出报告",
        "不用出报告", "不导出报告",
    ),
    "cost": ("不要估算费用", "不算预算", "不分析成本", "无需造价"),
    "life": ("不要预测寿命", "不估算寿命", "无需寿命预测"),
}

_EXCLUSIVE_INTENT_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "risk": ("只说风险", "只讲风险", "只看风险", "只要风险", "仅说风险", "仅讲风险"),
    "cause": ("只解释原因", "只讲原因", "只说原因", "只要原因", "只谈成因"),
    "treatment": ("只给处治建议", "只给维修建议", "只说怎么修", "只讲怎么修"),
    "environment": ("只讲环境", "只说环境", "只看环境影响"),
}

_ANALYSIS_INTENTS = {
    "compare", "summary", "statistics", "repair_priority", "risk", "treatment", "cause",
    "environment", "cost", "life", "repair_status", "report_generation",
}

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


def _ordered_unique(values: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


def _extract_years(text: str) -> List[int]:
    return extract_requested_years(text)


def _normalize_component_mentions(
    component_mentions: Optional[Sequence[Any]],
    text: str,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for value in component_mentions or []:
        if isinstance(value, dict):
            canonical = str(value.get("canonical") or value.get("component") or "").strip()
            alias = str(value.get("alias") or canonical).strip()
            start = value.get("start")
            end = value.get("end")
        else:
            canonical = str(value or "").strip()
            alias = canonical
            start = text.find(alias) if alias else -1
            end = start + len(alias) if start >= 0 else -1
        if not canonical:
            continue
        normalized.append({
            "canonical": canonical,
            "alias": alias,
            "start": int(start) if isinstance(start, int) else -1,
            "end": int(end) if isinstance(end, int) else -1,
        })
    return normalized


def _component_scope(
    text: str,
    mentions: Sequence[Dict[str, Any]],
) -> Tuple[List[str], List[str], str]:
    included: List[str] = []
    excluded: List[str] = []
    only_signal = bool(re.search(r"(?:只|仅)(?:看|分析|比较|统计|关注|保留|检查)", text))

    for mention in mentions:
        canonical = mention["canonical"]
        start = mention.get("start", -1)
        end = mention.get("end", -1)
        if start >= 0:
            before = text[max(0, start - 12):start]
            after = text[end:min(len(text), end + 8)]
        else:
            before = text
            after = ""

        include_hit = bool(re.search(
            r"(?:只|仅|重点)(?:看|分析|比较|统计|关注|保留|检查)?[^，,；;。]{0,5}$",
            before,
        ))
        exclude_hit = bool(re.search(
            r"(?:不要|不看|不分析|排除|剔除|忽略|去掉|除去)[^，,；;。]{0,5}$",
            before,
        ))
        # “除了支座，主梁和桥面系都看”中的“除了”就是排除信号。
        # 旧逻辑要求后文再出现“只/排除”，会把这类自然表达误读成全选。
        if not exclude_hit and before.endswith("除了"):
            exclude_hit = True
        # “除支座外都看”是同一语义的紧凑写法，排除词位于构件前、
        # “外”位于构件后，需要跨实体边界联合判断。
        if not exclude_hit and before.endswith("除") and re.match(r"\s*外", after):
            exclude_hit = True

        if exclude_hit:
            excluded.append(canonical)
        elif include_hit:
            included.append(canonical)

    if only_signal and not included:
        # A short "只看支座" may arrive without reliable character offsets.
        for mention in mentions:
            canonical = mention["canonical"]
            alias = mention.get("alias") or canonical
            if re.search(rf"(?:只|仅)(?:看|分析|比较|统计|关注|保留|检查)?[^，,；;。]{{0,5}}{re.escape(alias)}", text):
                included.append(canonical)

    # “除了 X，Y 和 Z 都看/分析”同时表达了一个负范围和一组正范围。
    # 只在存在明确“都看”谓词时补全正范围，避免把普通“除了”句式过度解释。
    if excluded and re.search(r"(?:都|也都)(?:要)?(?:看|分析|比较|统计|关注|保留|检查)", text):
        for mention in mentions:
            canonical = mention["canonical"]
            if canonical not in excluded:
                included.append(canonical)

    included = _ordered_unique(included)
    excluded = [value for value in _ordered_unique(excluded) if value not in included]
    mode = "only" if included else ("exclude" if excluded else "all")
    return included, excluded, mode


def _classify_mention(clause: str) -> Tuple[str, str, str, List[str]]:
    rules: List[str] = []
    queried = _contains_any(clause, _QUERY_MARKERS)
    uncertain = _contains_any(clause, _UNCERTAIN_MARKERS)
    recurrence = _contains_any(clause, _RECURRENCE_MARKERS)
    unrepaired = _contains_any(clause, _UNREPAIRED_MARKERS)
    repaired = _contains_any(clause, _REPAIRED_MARKERS) and not recurrence
    negative = any(pattern.search(clause) for pattern in _NEGATION_PATTERNS)
    hypothetical = _contains_any(clause, _HYPOTHETICAL_MARKERS)

    correction_matches = list(re.finditer(
        r"等等|不对|更正|改口|我看错了|刚才说错了|前面说错了",
        clause,
    ))
    correction_tail = clause[correction_matches[-1].end():] if correction_matches else ""
    late_negative = bool(correction_tail) and any(
        pattern.search(correction_tail) for pattern in _NEGATION_PATTERNS
    )
    late_affirmed = bool(correction_tail) and bool(re.search(
        r"(?:确认|确实|实际|查明|发现|复核确认|有|存在).{0,18}"
        r"(?:裂缝|开裂|脱空|悬空|锈蚀|露筋|破损|渗水|泛碱|坑槽|偏位|堵塞)",
        correction_tail,
    )) and not late_negative

    # 复发是更具体的活动事实。“不是没有裂缝，是修过又开了”虽然包含
    # “没有”，但最终断言是裂缝重新出现，不能被嵌套否定覆盖。
    if recurrence:
        polarity = "affirmed"
        rules.append("recurrence_overrides_embedded_negation")
    elif hypothetical:
        polarity = "hypothetical"
        rules.append("hypothetical_fact_gate")
    elif late_affirmed:
        polarity = "affirmed"
        rules.append("late_affirmation_overrides_prior_negation")
    elif queried:
        polarity = "queried"
        rules.append("question_scope")
    elif negative:
        polarity = "negative"
        rules.append("negation_scope")
    else:
        polarity = "affirmed"

    if recurrence:
        status = "recurrence"
        rules.append("repair_recurrence")
    elif repaired:
        status = "repaired"
        rules.append("repair_completed")
    elif unrepaired:
        status = "unrepaired"
        rules.append("repair_pending")
    else:
        status = "active" if polarity == "affirmed" else "unknown"

    certainty = "suspected" if uncertain else "confirmed"
    if uncertain:
        rules.append("uncertainty_scope")
    return polarity, status, certainty, rules


def _detect_intents(text: str) -> Tuple[List[str], List[str], Optional[bool], Optional[str]]:
    excluded: List[str] = []
    for intent, patterns in _EXCLUDED_INTENT_PATTERNS.items():
        if _contains_any(text, patterns):
            excluded.append(intent)

    intents: List[str] = []
    for intent, keywords in _INTENT_KEYWORDS:
        if intent in excluded:
            continue
        if _contains_any(text, keywords):
            intents.append(intent)

    exclusive_intent = next((
        intent
        for intent, patterns in _EXCLUSIVE_INTENT_PATTERNS.items()
        if _contains_any(text, patterns)
    ), None)
    multi_target_scope = bool(
        re.search(
            r"(?:只|仅)(?:说|讲|看|要|输出|给)?.{0,18}(?:和|与|、|以及|及).{1,18}",
            text,
        )
        and len(set(intents) & _ANALYSIS_INTENTS) >= 2
    )
    if multi_target_scope:
        exclusive_intent = None
    if exclusive_intent:
        intents = [exclusive_intent]
        excluded.extend(sorted(_ANALYSIS_INTENTS - {exclusive_intent}))

    generate_report: Optional[bool] = None
    if "report_generation" in excluded:
        generate_report = False
    elif "report_generation" in intents:
        generate_report = True
    return _ordered_unique(intents), _ordered_unique(excluded), generate_report, exclusive_intent


def _extract_bridge_name(text: str, session_context: Dict[str, Any]) -> str:
    known = str(session_context.get("bridge_name") or "").strip()
    source = strip_requested_years(text)
    matches = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,28}(?:特大桥|大桥|中桥|小桥)", source)
    if not matches:
        return known
    candidate = matches[-1]
    candidate = re.sub(
        r"^(?:(?:请|麻烦|劳驾)?(?:帮我|给我)?(?:对|把)?"
        r"(?:详细)?(?:分析一下|分析|对比|比较|查找|搜索|看看|查看|调取|打开|评估|评价)?"
        r"(?:一下)?)",
        "",
        candidate,
    )
    return candidate or known


def build_semantic_frame(
    text: str,
    parsed_items: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    session_context: Optional[Dict[str, Any]] = None,
    component_mentions: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """Build and validate a JSON-serializable semantic frame.

    ``parsed_items`` is the legacy component/disease extraction.  Source-clause
    metadata is optional, so existing callers remain compatible.
    """
    raw_text = str(text or "").strip()
    context = dict(session_context or {})
    items = [dict(item) for item in (parsed_items or [])]
    mentions = _normalize_component_mentions(component_mentions, raw_text)
    included, excluded, scope_mode = _component_scope(raw_text, mentions)
    intents, excluded_intents, generate_report, exclusive_intent = _detect_intents(raw_text)
    years = _extract_years(raw_text)

    defect_mentions: List[Dict[str, Any]] = []
    applied_rules: List[str] = []
    for index, item in enumerate(items):
        clause = str(item.get("_source_clause") or raw_text)
        source_index = int(item.get("_source_index", index))
        polarity, status, certainty, rules = _classify_mention(clause)
        component = str(item.get("comp") or "")
        active = (
            polarity == "affirmed"
            and certainty == "confirmed"
            and status in {"active", "unrepaired", "recurrence"}
            and (not included or component in included)
            and component not in excluded
        )
        blocked_reason = ""
        if polarity == "negative":
            blocked_reason = "用户明确否定该病害"
        elif polarity == "queried":
            blocked_reason = "用户在询问是否存在，不能当作已存在事实"
        elif certainty == "suspected":
            blocked_reason = "病害仅为疑似，需确认后评级"
        elif polarity == "hypothetical":
            blocked_reason = "用户要求假设或虚构病害，不能作为观测事实"
        elif status == "repaired":
            blocked_reason = "用户说明病害已修复，仅记录维修状态"
        elif included and component not in included:
            blocked_reason = "超出用户指定的仅分析范围"
        elif component in excluded:
            blocked_reason = "用户明确排除该构件"

        mention = {
            "mention_id": f"M{index + 1}",
            "source_index": source_index,
            "source_text": clause,
            "component": component,
            "disease": str(item.get("disease") or ""),
            "scale": item.get("scale") if item.get("_scale_explicit") else None,
            "scale_explicit": bool(item.get("_scale_explicit")),
            "scale_source": "user_explicit" if item.get("_scale_explicit") else "not_provided",
            "polarity": polarity,
            "status": status,
            "certainty": certainty,
            "evidence_status": "user_asserted",
            "active_for_diagnosis": active,
            "blocked_reason": blocked_reason,
            "rules": rules,
        }
        defect_mentions.append(mention)
        applied_rules.extend(rules)

    active_count = sum(1 for mention in defect_mentions if mention["active_for_diagnosis"])
    if active_count:
        intents.append("diagnosis")
    elif defect_mentions:
        intents.append("observation_update")
    if len(years) >= 2 and "compare" not in excluded_intents:
        intents.append("compare")

    exclude_repaired = _contains_any(raw_text, (
        "排除已修复", "剔除已修复", "不看已修复", "只看未修复", "仅看未修复",
        "不含已修复", "已修复项除外",
    ))
    if exclude_repaired:
        applied_rules.append("exclude_repaired_scope")

    unresolved_mentions = [
        mention for mention in defect_mentions
        if mention["polarity"] == "queried" or mention["certainty"] == "suspected"
    ]
    # 混合确定性输入只拦截疑似项，已确认项继续诊断；只有本轮没有任何
    # 可诊断事实时才让澄清阻塞整轮。
    requires_clarification = bool(
        unresolved_mentions
        and active_count == 0
        and "archive_search" not in intents
    )
    ambiguities: List[Dict[str, Any]] = []
    if unresolved_mentions:
        ambiguities.append({
            "type": "defect_existence_or_certainty",
            "impact": "不能据此生成标度、风险或处治结论",
            "mentions": [mention["mention_id"] for mention in unresolved_mentions],
        })

    prior_components = list(context.get("components") or [])
    has_singular_reference = _contains_any(raw_text, ("它", "这个", "那条", "该病害", "这条"))
    if has_singular_reference and not mentions and len(prior_components) > 1:
        ambiguities.append({
            "type": "ambiguous_reference",
            "impact": "单数指代可能对应多个既有构件",
            "candidates": prior_components[:8],
        })

    active_labels = [
        f"{m['component']}·{m['disease']}" for m in defect_mentions
        if m["active_for_diagnosis"]
    ]
    blocked_labels = [
        f"{m['component']}·{m['disease']}({m['blocked_reason']})" for m in defect_mentions
        if not m["active_for_diagnosis"]
    ]
    intent_labels = [_INTENT_LABELS.get(name, name) for name in _ordered_unique(intents)]

    frame = {
        "schema_version": SEMANTIC_FRAME_VERSION,
        "input": raw_text,
        "parser": "deterministic_semantic_gate",
        "entities": {
            "bridge_name": _extract_bridge_name(raw_text, context),
            "years": years,
            "components": _ordered_unique(m["canonical"] for m in mentions),
        },
        "intents": _ordered_unique(intents),
        "intent_labels": intent_labels,
        "excluded_intents": excluded_intents,
        "defect_mentions": defect_mentions,
        "constraints": {
            "scope_mode": scope_mode,
            "include_components": included,
            "exclude_components": excluded,
            "exclude_repaired": exclude_repaired,
            "generate_report": generate_report,
            "exclusive_intent": exclusive_intent,
        },
        "ambiguities": ambiguities,
        "requires_clarification": requires_clarification,
        "partial_clarification": bool(unresolved_mentions and active_count > 0),
        "clarification_question": (
            "请确认这些病害是现场已经确认存在，还是仅为疑似/待查询；确认后我再进行标度和风险分析。"
            if requires_clarification else ""
        ),
        "verification": {
            "passed": not requires_clarification,
            "diagnostic_safe": True,
            "active_mentions": active_count,
            "blocked_mentions": len(defect_mentions) - active_count,
            "rules_applied": _ordered_unique(applied_rules),
        },
        "summary": {
            "intents": intent_labels,
            "active": active_labels,
            "blocked": blocked_labels,
            "years": years,
            "scope": scope_mode,
        },
    }
    return frame


def filter_diagnostic_items(
    parsed_items: Sequence[Dict[str, Any]],
    frame: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (safe items, blocked items) according to a semantic frame."""
    mention_by_source: Dict[int, Dict[str, Any]] = {
        int(mention.get("source_index", index)): mention
        for index, mention in enumerate(frame.get("defect_mentions") or [])
    }
    safe: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for index, raw in enumerate(parsed_items or []):
        item = dict(raw)
        source_index = int(item.get("_source_index", index))
        mention = mention_by_source.get(source_index)
        if mention and mention.get("active_for_diagnosis"):
            item["semantic"] = {
                "mention_id": mention.get("mention_id"),
                "polarity": mention.get("polarity"),
                "status": mention.get("status"),
                "certainty": mention.get("certainty"),
            }
            safe.append(item)
        else:
            item["semantic"] = dict(mention or {})
            blocked.append(item)
    return safe, blocked


def build_semantic_acknowledgement(frame: Dict[str, Any]) -> str:
    """Create a non-diagnostic response for negated/repaired/uncertain input."""
    mentions = list(frame.get("defect_mentions") or [])
    if frame.get("requires_clarification"):
        return str(frame.get("clarification_question") or "请先确认病害是否真实存在。")

    not_reported = [m for m in mentions if m.get("evidence_status") == "not_reported"]
    negated = [
        m for m in mentions
        if m.get("polarity") == "negative" and m not in not_reported
    ]
    repaired = [m for m in mentions if m.get("status") == "repaired"]
    hypothetical = [m for m in mentions if m.get("polarity") == "hypothetical"]
    conflicts = [m for m in mentions if m.get("evidence_status") == "semantic_conflict"]
    excluded = [
        m for m in mentions
        if not m.get("active_for_diagnosis")
        and m not in negated
        and m not in repaired
        and m not in not_reported
        and m not in conflicts
        and m not in hypothetical
    ]
    parts: List[str] = []
    if negated:
        labels = "、".join(f"{m.get('component')}·{m.get('disease')}" for m in negated)
        parts.append(f"已识别为“未发现病害”：{labels}，本轮不会据此生成病害评级或风险分数。")
    if repaired:
        labels = "、".join(f"{m.get('component')}·{m.get('disease')}" for m in repaired)
        parts.append(f"已识别为“已修复”：{labels}，仅记录维修状态，不把它重新计入现状病害。")
    if not_reported:
        labels = "、".join(f"{m.get('component')}·{m.get('disease')}" for m in not_reported)
        parts.append(
            f"已识别为“报告未提供该事实”：{labels}。未报告不等于不存在，"
            "本轮不会把它作为现状病害或反向证明。"
        )
    if conflicts:
        labels = "、".join(f"{m.get('component')}·{m.get('disease')}" for m in conflicts)
        parts.append(
            f"开放语义解析与确定性规则对以下事实存在冲突：{labels}，"
            "系统已暂停评级，等待用户确认。"
        )
    if hypothetical:
        labels = "、".join(f"{m.get('component')}·{m.get('disease')}" for m in hypothetical)
        parts.append(
            f"已识别为假设或虚构指令：{labels}。该内容不属于现场观测事实，"
            "本轮不会据此评级、计算风险或生成处治结论。"
        )
    if excluded:
        labels = "、".join(f"{m.get('component')}·{m.get('disease')}" for m in excluded)
        parts.append(f"已按本轮语义约束排除：{labels}。")
    if not parts:
        parts.append("本轮没有可直接进入诊断的已确认病害，系统未生成评级或处治结论。")
    parts.append("语义闸门已完成极性、修复状态和范围校验。")
    return "\n\n".join(parts)


def compile_residual_plan(frame: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Compile a SemanticFrame into explicit residual goals and obligations."""
    frame = dict(frame or {})
    intents = set(frame.get("intents") or [])
    entities = frame.get("entities") or {}
    constraints = frame.get("constraints") or {}
    bridge_name = entities.get("bridge_name") or "当前桥梁"
    years = list(entities.get("years") or [])

    goal_parts = [_INTENT_LABELS.get(name, name) for name in frame.get("intents") or []]
    goal = f"完成{bridge_name}的{'、'.join(goal_parts) or '桥梁分析'}"
    if years:
        goal += f"（{'、'.join(str(y) + '年' for y in years)}）"

    unresolved: List[str] = []
    if "archive_search" in intents:
        unresolved.append("定位桥名、年份与对应报告证据")
    if "compare" in intents:
        unresolved.extend(("建立同桥同口径的年度可比集合", "核对新增、持续、已修复与复发病害"))
    if "summary" in intents or "statistics" in intents:
        unresolved.append("按项目、年份、桥梁和构件汇总已验证病害")
    if "repair_priority" in intents:
        unresolved.append("按构件评分、病害严重度与证据位置形成维修优先顺序")
    if "risk" in intents:
        unresolved.append("基于现役病害、Dr/BCI与承载证据形成条件性风险结论")
    if "treatment" in intents:
        unresolved.append("将已确认病害映射到可追溯处治措施")
    if "cause" in intents:
        unresolved.append("区分报告证据、环境影响与工程机理解释")
    if "environment" in intents:
        unresolved.append("核对桥位环境并计算环境修正影响")
    if "cost" in intents:
        unresolved.append("核对工程量、单价口径、预算约束与全寿命成本")
    if "life" in intents:
        unresolved.append("核对设计寿命、服役年限与劣化序列")
    if "repair_status" in intents or constraints.get("exclude_repaired"):
        unresolved.append("核对维修闭环、同位置复检与复发状态")
    if "report_generation" in intents and constraints.get("generate_report") is not False:
        unresolved.append("仅以验证通过的事实生成报告")
    if not unresolved:
        unresolved.append("基于已有证据完成用户指定分析")

    compiled_constraints = ["只能把肯定、已确认且未被排除的病害送入诊断"]
    if constraints.get("include_components"):
        compiled_constraints.append("仅分析构件：" + "、".join(constraints["include_components"]))
    if constraints.get("exclude_components"):
        compiled_constraints.append("排除构件：" + "、".join(constraints["exclude_components"]))
    if constraints.get("exclude_repaired"):
        compiled_constraints.append("现状统计排除已修复项，复发项除外")
    if constraints.get("generate_report") is False:
        compiled_constraints.append("本轮不得生成或导出报告")
    if frame.get("excluded_intents"):
        compiled_constraints.append("不得扩展到：" + "、".join(
            _INTENT_LABELS.get(name, name) for name in frame["excluded_intents"]
        ))
    if years:
        compiled_constraints.append("时间范围：" + "、".join(str(y) + "年" for y in years))

    unknowns = []
    for ambiguity in frame.get("ambiguities") or []:
        unknowns.append({
            "key": ambiguity.get("type") or "semantic_ambiguity",
            "description": ambiguity.get("impact") or "输入语义仍有歧义",
            "impact": "相关结论只能暂停或输出条件性结果",
        })

    return {
        "goal": goal,
        "unresolved": _ordered_unique(unresolved),
        "constraints": _ordered_unique(compiled_constraints),
        "unknowns": unknowns,
        "semantic_frame_version": frame.get("schema_version") or "",
    }

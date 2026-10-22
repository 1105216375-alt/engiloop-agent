"""Conservative, deterministic evidence-presence gates.

These booleans describe documented evidence, not whether a bridge is safe and
not whether a generated number follows from that evidence. Ambiguous or
contradictory statements fail closed. This module never promotes a proposed
inspection/repair to a completed one.
"""
from __future__ import annotations

import re
from typing import Any


_MISSING = re.compile(
    r"缺少|缺乏|缺失|暂无|尚无|尚未|未提供|未检测|未检验|未开展|未评估|未进行|"
    r"未实施|未完成|未形成|未取得|未修复|未维修|未更换|待维修|待检测|待检|待评估|待核实|待复核|未知|不明|"
    r"无(?:相关)?(?:维修记录|修复记录|检测报告|荷载试验|承载能力资料)|"
    r"资料不足|证据不足|无法确定|不能确定|无法评估|不能评估|没有.{0,8}(?:记录|证据|报告|资料)|"
    r"\b(?:unknown|missing|unavailable|unverified|pending|insufficient|"
    r"not\s+(?:available|provided|performed|tested|assessed|completed|evaluated|documented)|"
    r"no\s+(?:(?:repair|maintenance|test|inspection)\s+)?(?:data|evidence|records?|report)|"
    r"to\s+be\s+(?:assessed|tested|verified))\b",
    re.I,
)
_PROPOSED = re.compile(
    r"建议|拟(?:开展|进行|实施|完成|修复|维修|更换|检测|评估)|计划(?:开展|进行|实施)|"
    r"(?:应|需|需要)(?:开展|进行|实施|检测|评估|修复|维修)|"
    r"\b(?:recommend(?:ed)?|proposed|planned|should|to\s+be\s+performed)\b", re.I,
)
_PLACEHOLDERS = {"", "无", "暂无", "不详", "未知", "未提供", "待定", "-", "--",
                 "n/a", "na", "none", "null", "tbd", "tbc", "unknown", "false"}
_ADVICE_KEY = re.compile(r"建议|处治措施|recommendation|suggestion", re.I)
_PLAN_KEY = re.compile(r"处治方案|维修方案|养护方案|treatment.plan|repair.plan", re.I)
_TOPICS = {
    "load": re.compile(r"承载能力|荷载试验|静载试验|动载试验|结构验算|承载评定|load.capacity|load.test|structural.check", re.I),
    "repair": re.compile(r"维修|修复|处治|更换|加固|repair|rehabilitat|replacement", re.I),
    "life": re.compile(r"剩余(?:使用|服役)?寿命|剩余服役年限|remaining.(?:service.)?life", re.I),
    "deterioration": re.compile(r"劣化|退化|疲劳|腐蚀|碳化|氯离子|deterioration|fatigue|corrosion|carbonation", re.I),
    "cost": re.compile(r"工程量|单价|预算|造价|定额|费用|cost|quantity|quantities|unit.price|budget", re.I),
    "schedule": re.compile(r"检查周期|复评周期|处治期限|计划完成日期|批准期限|inspection.interval|deadline|completion.date", re.I),
}
_LOAD_RESULT = re.compile(
    r"满足|符合|合格|无明显异常|未见明显异常|未发现明显异常|(?:检算|评定|安全)系数\s*[=:：]?\s*\d|"
    r"承载能力.{0,8}(?:不足|降低|正常)|\b(?:pass(?:ed)?|fail(?:ed)?|satisf(?:y|ies|ied)|"
    r"adequate|inadequate|no\s+significant\s+abnormalit(?:y|ies))\b", re.I,
)
_COMPLETED_REPAIR = re.compile(
    r"(?:已|完成|竣工|验收).{0,12}(?:维修|修复|处治|更换|加固)|"
    r"(?:维修|修复|处治|更换|加固).{0,12}(?:完成|竣工|验收|闭环)|"
    r"\b(?:repair|replacement|rehabilitation).{0,20}(?:completed|accepted|closed)|"
    r"\b(?:completed|accepted).{0,20}(?:repair|replacement|rehabilitation)\b", re.I,
)
_LIFE_VALUE = re.compile(
    r"(?:剩余(?:使用|服役)?寿命|剩余服役年限).{0,20}\d+(?:\.\d+)?\s*"
    r"(?:(?:-|—|~|～|至)\s*\d+(?:\.\d+)?)?\s*年|"
    r"remaining.{0,20}life.{0,20}\d+(?:\.\d+)?\s*(?:years?|yrs?)", re.I,
)
_LIFE_METHOD = re.compile(
    r"(?:劣化|退化|疲劳|腐蚀|碳化|氯离子|可靠度).{0,18}(?:模型|计算|分析|评估|实测)|"
    r"(?:模型|实测|检测).{0,18}(?:劣化|退化|疲劳|腐蚀|碳化|氯离子)|"
    r"(?:deterioration|fatigue|corrosion|carbonation|reliability).{0,24}(?:model|assessment|analysis)", re.I,
)
_GRADE_ONLY = re.compile(
    r"(?:仅|只|直接|单凭|单纯).{0,16}(?:Dr|BCI|评分|类别|技术状况)|"
    r"(?:Dr|BCI|评分|类别|技术状况).{0,16}(?:直接换算|直接推算|经验换算)|"
    r"(?:solely|only).{0,20}(?:grade|score|Dr|BCI)", re.I,
)
_SOURCE_KEYS = ("来源定位", "报告ID", "报告编号", "证据编号", "evidence_id", "report_id",
                "source_ref", "来源", "source")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_MONEY = re.compile(r"\d+(?:\.\d+)?\s*(?:万元|元|人民币|CNY|RMB|USD)", re.I)
_TIME = re.compile(r"\d+\s*(?:天|日|个月|月|年)|20\d{2}[-年/]\d{1,2}(?:[-月/]\d{1,2})?|"
                   r"每(?:月|年|季度)|\d+\s*(?:days?|months?|years?)", re.I)


def _valid(text: str) -> bool:
    return text.strip().lower() not in _PLACEHOLDERS and not _MISSING.search(text)


def _units(row: dict[str, Any]) -> list[tuple[str, str]]:
    """Keep field names with clauses; do not join unrelated evidence fields."""
    result = []
    for key, value in row.items():
        if isinstance(value, dict):
            result.extend((f"{key}.{subkey}", text) for subkey, text in _units(value))
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            for text in re.split(r"[。；;\n，,]", str(value)):
                if text.strip():
                    result.append((str(key), text.strip()))
    return result


def _scoped(units: list[tuple[str, str]], topic: str) -> list[tuple[str, str]]:
    pattern = _TOPICS[topic]
    selected = []
    previous_topic_key = None
    for key, text in units:
        if _ADVICE_KEY.search(key) or _PLAN_KEY.search(key):
            continue
        direct = bool(pattern.search(key) or pattern.search(text))
        inherited = key == previous_topic_key and bool(re.match(r"(?:检测)?(?:结论|结果|评定)(?:为|是|[：:])", text))
        if direct or inherited:
            selected.append((key, text))
            previous_topic_key = key
        elif any(p.search(text) for p in _TOPICS.values()):
            previous_topic_key = None
    return selected


def _usable_scoped(units: list[tuple[str, str]], topic: str) -> list[tuple[str, str]]:
    rows = _scoped(units, topic)
    # Explicit absence and a positive assertion about the same capability in
    # one fact are a contradiction, not grounds to silently choose the positive.
    if any(not _valid(text) for _, text in rows):
        return []
    return [(key, text) for key, text in rows if not _PROPOSED.search(text)]


def _source_present(row: dict[str, Any], contract: dict[str, Any]) -> bool:
    if any(_valid(str(row.get(key, ""))) for key in _SOURCE_KEYS):
        return True
    return any(isinstance(report, dict) and any(
        _valid(str(report.get(key, ""))) for key in (*_SOURCE_KEYS, "id", "path", "filename")
    ) for report in contract.get("reports", []) or [])


def evidence_contract_capabilities(contract: dict) -> dict[str, bool]:
    """Return evidence-presence flags, conservatively and without side effects.

    ``life_basis`` requires a traceable explicit remaining-life assessment AND
    a non-grade-based assessment method. Design minus age, or a load-test
    conclusion alone, never enables it. ``repair_records`` means completed
    repair evidence, not a date, a plan or a nonempty record field.
    """
    flags = dict.fromkeys(("load_capacity", "repair_records", "cost_basis", "life_basis",
                           "schedule_basis", "deterioration_or_capacity", "design_life",
                           "service_years"), False)
    if not isinstance(contract, dict):
        return flags
    for row in contract.get("facts", []) or []:
        if not isinstance(row, dict):
            continue
        units = _units(row)
        factual = [(key, text) for key, text in units
                   if not _ADVICE_KEY.search(key) and not _PLAN_KEY.search(key)
                   and _valid(text) and not _PROPOSED.search(text)]
        traceable = _source_present(row, contract)
        load = _usable_scoped(units, "load")
        flags["load_capacity"] |= any(_LOAD_RESULT.search(text) is not None for _, text in load)
        repair = _usable_scoped(units, "repair")
        flags["repair_records"] |= any(
            bool(_COMPLETED_REPAIR.search(f"{key}：{text}")) or
            (bool(re.search(r"修复状态|维修状态|repair.status", key, re.I)) and
             text.lower() in {"已修复", "已完成", "完成", "completed", "repaired", "closed"})
            for key, text in repair
        )
        life = _usable_scoped(units, "life")
        full_factual = "；".join(f"{key}：{text}" for key, text in factual)
        flags["life_basis"] |= bool(
            traceable and life and any(_LIFE_VALUE.search(f"{key}：{text}") for key, text in life)
            and _LIFE_METHOD.search(full_factual) and not _GRADE_ONLY.search(full_factual)
        )
        deterioration = _usable_scoped(units, "deterioration")
        flags["deterioration_or_capacity"] |= bool(any(
            re.search(r"(?:实测|检测|评估|计算|分析).{0,16}(?:结果|结论|\d)|"
                      r"(?:碳化深度|氯离子含量|腐蚀速率|疲劳损伤).{0,8}\d|"
                      r"(?:result|measured|assessed).{0,16}\d", f"{key}：{text}", re.I)
            for key, text in deterioration
        ))
        for flag, topic in (("design_life", r"设计(?:使用)?寿命|design.life"),
                            ("service_years", r"已服役|服役年限|已使用年限|service.years")):
            candidates = [(key, text) for key, text in units if re.search(topic, key + "：" + text, re.I)]
            flags[flag] |= bool(candidates and all(_valid(text) for _, text in candidates) and any(
                not _PROPOSED.search(text) and _NUMBER.search(text) for _, text in candidates
            ))
        cost = _usable_scoped(units, "cost")
        quantity = any(re.search(r"工程量|quantit", key + text, re.I) and _NUMBER.search(text)
                       for key, text in cost)
        unit_price = any(re.search(r"单价|unit.price", key + text, re.I) and _NUMBER.search(text)
                        for key, text in cost)
        treatment = any(re.search(r"处治方案|维修方案|treatment.plan", key, re.I)
                        and not _ADVICE_KEY.search(key) and _valid(text) and not _PROPOSED.search(text)
                        and re.search(r"更换|修复|加固|灌注|封闭|更换|replace|repair", text, re.I)
                        for key, text in units)
        budget = any(re.search(r"预算(?:金额|审定)|审定(?:造价|金额)|批准预算|approved.budget", key + text, re.I)
                     and _MONEY.search(text) for key, text in cost)
        flags["cost_basis"] |= bool(traceable and cost and (budget or (quantity and unit_price and treatment)))
        schedule = _usable_scoped(units, "schedule")
        flags["schedule_basis"] |= bool(traceable and any(
            _TIME.search(text) and (re.search(r"检查周期|复评周期|处治期限|计划完成日期|批准期限|"
                                            r"inspection.interval|deadline|completion.date", key, re.I)
                                   or re.search(r"明确|规定|批准|审定|确定|approved|specified", text, re.I))
            for key, text in schedule
        ))
    flags["deterioration_or_capacity"] |= flags["load_capacity"]
    return {key: bool(value) for key, value in flags.items()}

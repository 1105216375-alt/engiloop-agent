"""Semantic output projection for direct diagnosis replies."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _mentions_by_key(frame: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = {}
    for mention in frame.get("defect_mentions") or []:
        key = f"{mention.get('component') or ''}|{mention.get('disease') or ''}"
        rows[key] = mention
    return rows


def _row(result: Dict[str, Any], mentions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    component = str(result.get("构件") or result.get("component") or "未知构件")
    disease = str(result.get("病害") or result.get("disease") or "未知病害")
    mention = mentions.get(f"{component}|{disease}") or {}
    return {
        "component": component,
        "disease": disease,
        "scale": int(_float(result.get("标度") or result.get("评定标度") or result.get("scale"))),
        "risk": _float(result.get("风险评分") or result.get("risk")),
        "urgency": str(result.get("紧急程度") or "待复核"),
        "critical": bool(result.get("严控区")),
        "recurrence": mention.get("status") == "recurrence",
    }


def _fmt_number(value: float) -> str:
    return f"{value:g}"


def build_diagnosis_scope_answer(
    results: Iterable[Dict[str, Any]],
    semantic_frame: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a narrow answer when the user did not request treatment detail."""
    intents = set(semantic_frame.get("intents") or [])
    constraints = semantic_frame.get("constraints") or {}
    if "treatment" in intents:
        return {"active": False, "mode": "treatment_requested", "answer": ""}

    mode = ""
    if "repair_priority" in intents:
        mode = "repair_priority"
    elif "risk" in intents:
        mode = "risk_only"
    if not mode:
        return {"active": False, "mode": "no_scope_projection", "answer": ""}

    mentions = _mentions_by_key(semantic_frame)
    rows: List[Dict[str, Any]] = [_row(result, mentions) for result in results]
    rows.sort(
        key=lambda row: (
            row["critical"], row["recurrence"], row["risk"], row["scale"],
        ),
        reverse=True,
    )
    if not rows:
        return {"active": False, "mode": "no_results", "answer": ""}

    if mode == "repair_priority":
        lead = "维修优先级结论：建议按以下顺序复核并安排处置。"
    else:
        lead = "风险结论：以下排序仅基于本轮已验证病害事实。"
    details = []
    for index, row in enumerate(rows, 1):
        flags = []
        if row["recurrence"]:
            flags.append("用户表述为复发")
        if row["critical"]:
            flags.append("严控区")
        suffix = "；" + "、".join(flags) if flags else ""
        details.append(
            f"{index}. {row['component']}·{row['disease']}："
            f"{row['scale']}类，风险{_fmt_number(row['risk'])}分，"
            f"紧急程度{row['urgency']}{suffix}。"
        )

    lines = [lead, "\n".join(details)]
    if mode == "repair_priority":
        lines.append(
            "排序依据：本轮仅按技术标度、风险评分、严控区和复发表述排序；"
            "未请求处治方案，因此不展开工法、材料、压力参数或预算。"
        )
    else:
        lines.append("输出边界：本轮只说明风险，不扩展处治工法、材料或预算。")
    if constraints.get("generate_report") is False:
        lines.append("输出约束：本轮不生成或导出 Word 报告。")
    return {
        "active": True,
        "mode": mode,
        "answer": "\n\n".join(lines),
        "rows": len(rows),
    }

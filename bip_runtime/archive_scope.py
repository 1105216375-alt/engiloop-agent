"""Deterministic query-scope projection for archive evidence.

The archive LLM may rewrite a verified draft, but it must not broaden a user-
named component or year range.  This module projects the evidence contract to
that requested scope and provides a usable recovery answer when verification
rejects the rewrite.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional


_EXCLUDE_REPAIRED = re.compile(
    r"排除(?:已经|已)?(?:闭环|修复|维修|处理|处治|整治)|"
    r"(?:修过|修好|已修|已处理|已处治|修完).{0,8}(?:别|不要|不)(?:算|统计|计入|纳入)|"
    r"(?:别|不要|不).{0,8}(?:修过|修好|已修复|已处理|已处治).{0,6}(?:算|统计|计入|纳入)"
)
_NO_REPORT = re.compile(
    r"(?:别|不要|不用|无需|不)(?:再)?(?:出|生成|导出|形成)\s*(?:Word|word|报告)|"
    r"别(?:给我)?写(?:文件|报告)|别整\s*(?:Word|word|报告)|只(?:在页面|口头)(?:说|回答|展示)"
)
_NOT_REPORTED_BOUNDARY = re.compile(
    r"(?:别|不要|不能).{0,8}(?:把)?(?:没记录|未记录|没提到|未提到).{0,8}(?:当|算|视为)(?:没有|不存在)|"
    r"(?:没记录|未记录|没提到|未提到).{0,4}(?:不等于|不能说明)(?:没有|不存在)"
)
_REPAIRED_MARKERS = ("已修复", "已维修", "已处治", "已处理", "已整治", "已闭环", "修复完成", "处治完成")
_UNREPAIRED_MARKERS = ("未修复", "未维修", "未处治", "未处理", "未闭环", "待处治", "待维修")
_STATUS_KEYS = ("修复状态", "维修状态", "处治状态", "维修记录", "repair_status")


def _component_family(row: Dict[str, Any]) -> str:
    component = str(
        row.get("构件") or row.get("构件类型") or row.get("component") or "其他"
    )
    for token, family in (
        ("湿接缝", "湿接缝"), ("横隔板", "横隔板"), ("伸缩缝", "伸缩缝"),
        ("支座", "支座"), ("盖梁", "盖梁"), ("桥墩", "桥墩"),
        ("墩", "桥墩"), ("桥台", "桥台"), ("护栏", "护栏"),
        ("排水", "排水设施"), ("泄水", "排水设施"),
    ):
        if token in component:
            return family
    if "梁" in component:
        return "主梁"
    return component[:18] or "其他"


def stratified_fact_sample(rows: Iterable[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Keep year and component-family coverage when an evidence cap is needed."""
    indexed = [(index, dict(row or {})) for index, row in enumerate(rows)]
    if limit <= 0 or len(indexed) <= limit:
        return [row for _, row in indexed]

    by_year: Dict[str, List[tuple[int, Dict[str, Any]]]] = defaultdict(list)
    for item in indexed:
        row = item[1]
        year = str(row.get("年份") or row.get("year") or "未标年")[:4]
        by_year[year].append(item)

    selected: List[tuple[int, Dict[str, Any]]] = []
    selected_ids = set()
    years = sorted(by_year)
    per_year = max(1, limit // max(1, len(years)))
    for year in years:
        families: Dict[str, List[tuple[int, Dict[str, Any]]]] = defaultdict(list)
        for item in by_year[year]:
            families[_component_family(item[1])].append(item)
        family_names = sorted(families)
        year_selected = 0
        while family_names and year_selected < per_year and len(selected) < limit:
            progressed = False
            for family in list(family_names):
                if year_selected >= per_year or len(selected) >= limit:
                    break
                bucket = families[family]
                if bucket:
                    item = bucket.pop(0)
                    selected.append(item)
                    selected_ids.add(item[0])
                    year_selected += 1
                    progressed = True
                if not bucket:
                    family_names.remove(family)
            if not progressed or len(selected) >= limit:
                break

    if len(selected) < limit:
        for item in indexed:
            if item[0] in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(item[0])
            if len(selected) >= limit:
                break
    selected.sort(key=lambda item: item[0])
    return [row for _, row in selected[:limit]]


_DURATION_YEAR_MARKERS = (
    "寿命", "剩余", "服役", "使用", "运营", "延长", "还能用", "年限",
)
_REPAIR_EVENT_MARKERS = (
    "修过", "修复", "维修", "处治", "处理", "整治", "闭环",
)
_SPOKEN_ARCHIVE_YEARS = {
    "二零二二": 2022, "二〇二二": 2022, "二二": 2022,
    "二零二三": 2023, "二〇二三": 2023, "二三": 2023,
    "二零二四": 2024, "二〇二四": 2024, "二四": 2024,
    "二零二五": 2025, "二〇二五": 2025, "二五": 2025,
}


def _requested_year_mentions(
    text: str,
    current_year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    source = str(text or "")
    mentions: List[Dict[str, Any]] = []
    occupied: set[int] = set()
    reference_year = int(current_year or date.today().year)

    def add_mention(
        years: Iterable[int],
        start: int,
        end: int,
        surface: str,
    ) -> None:
        normalized = sorted({
            int(year) for year in years if 2000 <= int(year) <= 2099
        })
        if not normalized:
            return
        mentions.append({
            "year": normalized[0],
            "years": normalized,
            "start": start,
            "end": end,
            "surface": surface,
        })
        occupied.update(range(start, end))

    def normalize_year(value: str) -> int:
        number = int(value)
        return number if number >= 2000 else 2000 + number

    for surface, year in sorted(
        _SPOKEN_ARCHIVE_YEARS.items(), key=lambda item: len(item[0]), reverse=True
    ):
        for match in re.finditer(re.escape(surface) + r"年(?:度)?", source):
            if any(index in occupied for index in range(match.start(), match.end())):
                continue
            prefix = source[max(0, match.start() - 12):match.start()]
            suffix = source[match.end():match.end() + 12]
            if len(surface) == 2 and any(
                marker in prefix or marker in suffix
                for marker in _DURATION_YEAR_MARKERS
            ):
                continue
            add_mention((year,), match.start(), match.end(), match.group(0))

    list_pattern = re.compile(
        r"(?<!\d)((?:20\d{2}|[2-3]\d)"
        r"(?:\s*(?:/|、|,|，|和|与|及)\s*(?:20\d{2}|[2-3]\d)){2,})"
        r"年(?:度)?(?!\d)"
    )
    for match in list_pattern.finditer(source):
        values = [
            normalize_year(value)
            for value in re.findall(r"20\d{2}|[2-3]\d", match.group(1))
        ]
        add_mention(values, match.start(), match.end(), match.group(0))

    # Parse paired/ranged forms first so a compact expression remains one
    # removable unit: 23和25年, 22-25年, 2022-2025, 2023到25年.
    pair_pattern = re.compile(
        r"(?<!\d)(20\d{2}|[2-3]\d)(?:年(?:度)?)?"
        r"\s*(和|与|及|、|/|-|—|–|~|～|至|到)\s*"
        r"(20\d{2}|[2-3]\d)(年(?:度)?)?(?!\d)"
    )
    for match in pair_pattern.finditer(source):
        left_raw, separator, right_raw, right_suffix = match.groups()
        if not right_suffix and not (len(left_raw) == 4 and len(right_raw) == 4):
            continue
        left = normalize_year(left_raw)
        right = normalize_year(right_raw)
        if separator in {"-", "—", "–", "~", "～", "至", "到"} and abs(right - left) <= 30:
            years = range(min(left, right), max(left, right) + 1)
        else:
            years = (left, right)
        add_mention(years, match.start(), match.end(), match.group(0))

    for match in re.finditer(r"(?<!\d)(20\d{2})(?:年(?:度)?)?(?!\d)", source):
        if any(index in occupied for index in range(match.start(), match.end())):
            continue
        add_mention(
            (int(match.group(1)),),
            match.start(),
            match.end(),
            match.group(0),
        )
    for match in re.finditer(r"(?<!\d)([2-3]\d)年(?:度)?", source):
        if any(index in occupied for index in range(match.start(), match.end())):
            continue
        prefix = source[max(0, match.start() - 12):match.start()]
        suffix = source[match.end():match.end() + 12]
        if any(
            marker in prefix or marker in suffix
            for marker in _DURATION_YEAR_MARKERS
        ):
            continue
        add_mention(
            (2000 + int(match.group(1)),),
            match.start(),
            match.end(),
            match.group(0),
        )

    for surface, year in (
        ("今年", reference_year),
        ("本年度", reference_year),
        ("本年", reference_year),
        ("去年", reference_year - 1),
        ("上年度", reference_year - 1),
        ("前年", reference_year - 2),
    ):
        for match in re.finditer(re.escape(surface), source):
            if any(index in occupied for index in range(match.start(), match.end())):
                continue
            suffix = source[match.end():match.end() + 8]
            if any(marker in suffix for marker in _REPAIR_EVENT_MARKERS):
                continue
            add_mention((year,), match.start(), match.end(), match.group(0))
    return sorted(mentions, key=lambda row: (row["start"], row["end"]))


def extract_requested_years(
    text: str,
    current_year: Optional[int] = None,
) -> List[int]:
    return sorted({
        int(year)
        for row in _requested_year_mentions(text, current_year=current_year)
        for year in row.get("years", (row["year"],))
        if 2000 <= int(year) <= 2099
    })


def strip_requested_years(
    text: str,
    current_year: Optional[int] = None,
) -> str:
    source = str(text or "")
    mentions = _requested_year_mentions(source, current_year=current_year)
    for row in reversed(mentions):
        source = source[:row["start"]] + " " + source[row["end"]:]
    return re.sub(r"\s+", " ", source).strip()


def extract_requested_sides(text: str) -> List[str]:
    source = str(text or "")
    compact = re.sub(r"[\s，,。.!！?？]+", "", source)
    sides: List[str] = []
    if any(marker in source for marker in (
        "左右幅", "左、右幅", "左/右幅", "两幅",
        "左右线", "左、右线", "左/右线", "两线",
        "两边", "两侧",
    )):
        return ["左幅", "右幅"]
    english = source.lower()
    if re.search(r"\b(?:both|left\s*(?:and|/)\s*right)\s+sides?\b", english):
        return ["左幅", "右幅"]
    if re.search(r"\bleft\s+(?:side|carriageway|deck)\b", english):
        sides.append("左幅")
    if re.search(r"\bright\s+(?:side|carriageway|deck)\b", english):
        sides.append("右幅")
    for markers, canonical in (
        (("拼宽左幅", "左幅", "左线"), "左幅"),
        (("拼宽右幅", "右幅", "右线"), "右幅"),
        (("上行",), "上行"),
        (("下行",), "下行"),
    ):
        if any(marker in source for marker in markers):
            sides.append(canonical)
    if not sides:
        # “左边的 / 那右边呢”只在句尾作为报告选择口语处理，避免把
        # “桥左边支座”这类构件位置描述误解成左右幅。
        if re.search(
            r"(?:左边|左侧)(?:那一?幅|这一?幅|一幅|"
            r"那份(?:报告)?|这份(?:报告)?|这个(?:报告)?|报告|的|呢|吧)?$",
            compact,
        ):
            sides.append("左幅")
        if re.search(
            r"(?:右边|右侧)(?:那一?幅|这一?幅|一幅|"
            r"那份(?:报告)?|这份(?:报告)?|这个(?:报告)?|报告|的|呢|吧)?$",
            compact,
        ):
            sides.append("右幅")
    return list(dict.fromkeys(sides))


def _canonical_report_side(value: Any) -> str:
    text = str(value or "")
    if "左幅" in text or "左线" in text:
        return "左幅"
    if "右幅" in text or "右线" in text:
        return "右幅"
    if "上行" in text:
        return "上行"
    if "下行" in text:
        return "下行"
    return text.strip()


def build_report_year_scope(
    reports: Iterable[Dict[str, Any]],
    query: str,
) -> Dict[str, Any]:
    all_reports = [dict(row or {}) for row in reports]
    requested_years = [str(year) for year in extract_requested_years(query)]
    requested_sides = extract_requested_sides(query)
    available_years = sorted({
        str(row.get("year") or "")
        for row in all_reports
        if row.get("year")
    })
    available_sides = sorted({
        _canonical_report_side(row.get("side"))
        for row in all_reports
        if _canonical_report_side(row.get("side"))
    })
    scoped_reports = [
        row for row in all_reports
        if (not requested_years or str(row.get("year") or "") in requested_years)
        and (
            not requested_sides
            or _canonical_report_side(row.get("side")) in requested_sides
        )
    ]
    year_counts = Counter(str(row.get("year") or "unknown") for row in scoped_reports)
    return {
        "requested_years": requested_years,
        "requested_sides": requested_sides,
        "available_years": available_years,
        "available_sides": available_sides,
        "missing_years": [
            year for year in requested_years if year not in available_years
        ],
        "missing_sides": [
            side for side in requested_sides if side not in available_sides
        ],
        "reports": scoped_reports,
        "reports_matched": len(scoped_reports),
        "reports_total_for_bridge": len(all_reports),
        "years": sorted(year_counts),
        "year_counts": dict(sorted(year_counts.items())),
    }


def _fact_count(row: Dict[str, Any]) -> int:
    raw = row.get("__archive_count")
    if raw in (None, ""):
        raw = row.get("数量") or row.get("count") or 1
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return 1


def _float(row: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        try:
            value = row.get(key)
            if value not in (None, ""):
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _repair_state(row: Dict[str, Any]) -> str:
    blob = " ".join(str(row.get(key) or "") for key in _STATUS_KEYS)
    if any(marker in blob for marker in _UNREPAIRED_MARKERS):
        return "unrepaired"
    if any(marker in blob for marker in _REPAIRED_MARKERS):
        return "repaired"
    return "unknown"


def _default_component_match(component: str, target: str) -> bool:
    component = str(component or "").strip()
    target = str(target or "").strip()
    if not component or not target:
        return False
    if target in component or component in target:
        return True
    if target == "主梁":
        return "梁" in component and not any(
            token in component for token in ("盖梁", "横隔板", "系梁", "垫梁")
        )
    if target == "桥墩":
        return "墩" in component and "支座" not in component
    if target == "桥台":
        return "台" in component and "台帽" not in component
    return False


def _component_matches(
    component: str,
    targets: Iterable[str],
    component_extractor: Optional[Callable[[str], List[str]]],
) -> bool:
    extracted: List[str] = []
    if component_extractor is not None:
        try:
            extracted = list(component_extractor(component) or [])
        except Exception:
            extracted = []
    for target in targets:
        if target in extracted or _default_component_match(component, target):
            return True
    return False


def _targets(
    query: str,
    component_extractor: Optional[Callable[[str], List[str]]],
) -> List[str]:
    if component_extractor is None:
        return []
    try:
        return list(dict.fromkeys(component_extractor(query) or []))
    except Exception:
        return []


def _priority_rows(rows: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    grouped: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "max_scale": 0.0, "max_risk": 0.0}
    )
    for row in rows:
        disease = str(row.get("病害") or row.get("disease") or "未分类病害").strip()
        item = grouped[disease]
        item["count"] += _fact_count(row)
        item["max_scale"] = max(item["max_scale"], _float(row, "标度", "评定标度", "scale"))
        item["max_risk"] = max(item["max_risk"], _float(row, "风险评分", "risk"))
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            item[1]["max_risk"], item[1]["max_scale"], item[1]["count"], item[0],
        ),
        reverse=True,
    )[:limit]
    result = []
    for index, (disease, values) in enumerate(ranked, 1):
        severity = "高优先" if values["max_scale"] >= 3 or values["max_risk"] >= 60 else "常规复核"
        detail = f"{index}. {disease}：{values['count']}条/处，{severity}"
        if values["max_scale"]:
            detail += f"，最高标度{values['max_scale']:g}"
        if values["max_risk"]:
            detail += f"，最高风险{values['max_risk']:g}分"
        result.append(detail + "。")
    return result


def build_scoped_archive_answer(
    query: str,
    bridge_name: str,
    facts: Iterable[Dict[str, Any]],
    *,
    component_extractor: Optional[Callable[[str], List[str]]] = None,
) -> Dict[str, Any]:
    """Project archive facts to user-named component/year constraints.

    Returns ``active=False`` when the query does not name a component; callers
    can then keep the existing whole-bridge formatter.
    """
    targets = _targets(query, component_extractor)
    years = extract_requested_years(query)
    if not targets:
        return {
            "active": False,
            "answer": "",
            "targets": [],
            "years": years,
            "facts": [],
        }

    requested_years = set(years)
    scoped: List[Dict[str, Any]] = []
    repaired_skipped = 0
    unknown_repair = 0
    exclude_repaired = bool(_EXCLUDE_REPAIRED.search(query or ""))
    for raw in facts:
        row = dict(raw or {})
        try:
            year = int(str(row.get("年份") or row.get("year") or "")[:4])
        except (TypeError, ValueError):
            year = 0
        if requested_years and year not in requested_years:
            continue
        component = row.get("构件") or row.get("构件类型") or row.get("component") or ""
        if not _component_matches(str(component), targets, component_extractor):
            continue
        repair_state = _repair_state(row)
        if exclude_repaired and repair_state == "repaired":
            repaired_skipped += _fact_count(row)
            continue
        if exclude_repaired and repair_state == "unknown":
            unknown_repair += _fact_count(row)
        scoped.append(row)

    bridge = str(bridge_name or "当前桥梁").strip()
    target_text = "、".join(targets)
    year_text = "、".join(f"{year}年" for year in years) if years else "全部已解析年度"
    lines = [
        f"{bridge}｜{target_text}专项核验",
        f"范围：仅统计{year_text}的{target_text}档案事实；未把其他构件写入本轮结论。",
    ]
    if not scoped:
        lines.append("核验结果：当前证据合同内没有命中该范围的可用病害事实，系统不以全桥统计代替构件结论。")
    else:
        by_year: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in scoped:
            year = str(row.get("年份") or row.get("year") or "未标年")[:4]
            by_year[year].append(row)
        for year in sorted(by_year):
            rows = by_year[year]
            total = sum(_fact_count(row) for row in rows)
            high = sum(
                _fact_count(row)
                for row in rows
                if _float(row, "标度", "评定标度", "scale") >= 3
                or _float(row, "风险评分", "risk") >= 60
            )
            diseases = Counter()
            for row in rows:
                disease = str(row.get("病害") or row.get("disease") or "未分类病害")
                diseases[disease] += _fact_count(row)
            top = "、".join(f"{name}{count}条/处" for name, count in diseases.most_common(4)) or "-"
            lines.append(f"{year}年：{total}条/处，高优先级{high}条/处；主要病害为{top}。")
        priority = _priority_rows(scoped)
        if priority:
            lines.append("风险与优先级：\n" + "\n".join(priority))

    if exclude_repaired:
        if repaired_skipped:
            lines.append(f"维修闭环边界：已按档案中的明确状态排除{repaired_skipped}条/处已修复事实。")
        if unknown_repair:
            lines.append(
                f"维修闭环边界：另有{unknown_repair}条/处没有可核验维修状态，"
                "未把“状态未知”冒充“已修复”，需凭维修记录继续核销。"
            )
        if not repaired_skipped and not unknown_repair:
            lines.append("维修闭环边界：未检出可用于执行排除的维修状态字段，不声称已完成维修项核销。")
    if _NO_REPORT.search(query or ""):
        lines.append("输出约束：本轮仅返回对话结果，不生成或导出 Word 报告。")
    if _NOT_REPORTED_BOUNDARY.search(query or ""):
        lines.append("证据语义边界：报告未记录不等于现场不存在；未命中的病害不作“没有”结论。")

    return {
        "active": True,
        "answer": "\n\n".join(lines),
        "targets": targets,
        "years": years,
        "facts": scoped,
        "exclude_repaired": exclude_repaired,
        "repaired_skipped": repaired_skipped,
        "unknown_repair": unknown_repair,
    }

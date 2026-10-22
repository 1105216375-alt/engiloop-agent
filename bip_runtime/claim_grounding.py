"""Object-bound reverse checking of diagnostic numeric claims.

Evidence rows, never model-supplied declarations, define allowed values. The
scope is component + disease (and instance text where supplied). Ambiguous
unaggregated assertions are not certified by a global bag of numbers.
"""
from __future__ import annotations

import math
import re
from typing import Any

from legacy_v7.core import ontology
from nskernel.verification.numeric_claims import (
    extract_numeric_claims, expected_values, numeric_value_matches,
)


_FIELDS = {"risk": "风险评分", "level": "标度", "dp": "DP值"}
_COUNT_UNITS = ("条", "处", "项", "个")
_COUNT_TRIGGERS = ("共", "合计", "总计", "累计", "发现", "出现", "存在", "有")


def _hits(text: str, pairs: list[tuple[str, str]]) -> list[tuple[int, int, str]]:
    hits = []
    for alias, canonical in sorted(set(pairs), key=lambda row: (-len(row[0]), row)):
        if len(alias) < 2:
            continue
        for match in re.finditer(re.escape(alias), text):
            start, end = match.span()
            if not any(left <= start and end <= right for left, right, _ in hits):
                hits.append((start, end, canonical))
    return sorted(hits)


def _canonical_component(value: str) -> str:
    return ontology.canonicalize_component(value) or ontology.fuzzy_canonicalize_component(value) or value


def _row_count(row: dict) -> int:
    for key in ("处数", "数量", "count", "__archive_count"):
        value = row.get(key)
        try:
            if value not in (None, ""):
                return max(1, int(value))
        except (TypeError, ValueError):
            continue
    return 1


def _component_pairs(rows: list[dict]) -> list[tuple[str, str]]:
    return ontology.build_comp_keywords_list() + [
        (str(row.get("构件") or ""), str(row.get("构件") or "")) for row in rows
    ]


def _preceding_heading(text: str, rows: list[dict]) -> str:
    # A heading survives intervening metric lines, but ordinary prose such as
    # “主要构件为主梁1、支座1。” must not narrow the next aggregate sentence.
    lines = text.split("\n")[:-1]
    for line in reversed(lines):
        line = line.strip()
        if not line:
            break
        if len(line) > 80 or re.search(r"[。；;]", line):
            break
        if extract_numeric_claims(line):
            continue
        hits = _hits(line, _component_pairs(rows))
        if not hits:
            continue
        rest = line
        for start, end, _ in reversed(hits):
            rest = rest[:start] + rest[end:]
        for start, end, _ in reversed(_hits(rest, ontology.build_disease_keywords_list())):
            rest = rest[:start] + rest[end:]
        rest = re.sub(r"病害|分析|评估|评定|概况|构件|以及|和|与|及", "", rest)
        if re.fullmatch(r"[\s\d#*📋·:：、（）()\[\]【】.\-]*", rest):
            return line
        break
    return ""


def _scope(prefix: str, fallback: str, rows: list[dict]) -> tuple[list[dict], str]:
    pairs = _component_pairs(rows)
    # Preserve specific identifiers such as 1号支座 when the source contains
    # them, while also recognizing canonical component aliases.
    hits = _hits(prefix, pairs)
    if not hits:
        hits = _hits(fallback, pairs)
        prefix = fallback
    scoped = rows
    scope_label = "当前结果"
    disease_prefix = prefix
    if hits:
        selected = [hits[-1]]
        # A coordinated subject (主梁和支座) is not just the final component.
        for previous in reversed(hits[:-1]):
            if re.fullmatch(r"\s*(?:和|与|及|、|以及)\s*", prefix[previous[1]:selected[0][0]]):
                selected.insert(0, previous)
            else:
                break
        names = {hit[2] for hit in selected}
        canonical_names = {_canonical_component(name) for name in names}
        exact_instance = {name for name in names if re.search(r"\d", name)}
        scoped = [row for row in rows if (
            str(row.get("构件") or "") in exact_instance if exact_instance else
            _canonical_component(str(row.get("构件") or "")) in canonical_names
        )]
        scope_label = "/".join(sorted(names))
        disease_prefix = prefix[selected[-1][1]:]
    disease_hits = _hits(disease_prefix, ontology.build_disease_keywords_list())
    if disease_hits:
        disease = disease_hits[-1][2]
        scoped = [row for row in scoped if (
            ontology.canonicalize_disease(str(row.get("病害") or "")) or str(row.get("病害") or "")
        ) == disease or ontology.get_disease_root(str(row.get("病害") or "")) == disease]
        scope_label += "·" + disease
    return scoped, scope_label


def check_diagnostic_numbers(text: str, rows: list[dict], *, original_query: str = "") -> list[dict[str, Any]]:
    """Return hard mismatches or ambiguous bindings for recognized metrics."""
    issues = []
    for claim in extract_numeric_claims(text):
        # The prefix is local to this sentence. Carry a preceding heading's
        # component only when the current sentence has no explicit subject.
        fragments = re.split(r"[。；;\n]", text[:claim.start])
        prefix = fragments[-1]
        heading = _preceding_heading(text[:claim.start], rows)
        query_components = {_canonical_component(hit[2]) for hit in _hits(original_query, _component_pairs(rows))}
        # A multi-object question is not evidence that an unqualified scalar
        # belongs to the final component mentioned in that question.
        fallback = heading or (original_query if len(query_components) == 1 else "")
        scoped, label = _scope(prefix, fallback, rows)
        values = []
        for row in scoped:
            raw = row.get(_FIELDS[claim.metric])
            try:
                value = float(raw)
                if math.isfinite(value):
                    values.append(value)
            except (TypeError, ValueError):
                continue
        expected = expected_values(values, claim.aggregation)
        ambiguous = not claim.aggregation and len(set(values)) > 1
        if not expected or ambiguous or not numeric_value_matches(claim, expected):
            issues.append({
                "metric": claim.metric, "scope": label, "asserted": claim.value,
                "expected": sorted(set(expected)),
                "start": claim.start, "end": claim.end,
                "reason": "ambiguous_numeric_subject" if ambiguous else "numeric_claim_evidence_mismatch",
            })
    return issues


def _count_claim_candidates(text: str) -> list[dict[str, Any]]:
    """Extract only occurrence claims, not risk/step/list numbers."""
    candidates: list[dict[str, Any]] = []
    prefix_pattern = re.compile(
        r"(?P<trigger>共|合计|总计|累计|发现|出现|存在|有)\s*"
        r"(?P<value>\d+)\s*(?P<unit>条|处|项|个)"
        r"(?P<tail>[\u4e00-\u9fa5A-Za-z]{0,12})"
    )
    suffix_pattern = re.compile(
        r"(?P<value>\d+)\s*(?P<unit>条|处|项|个)"
        r"(?P<tail>[\u4e00-\u9fa5A-Za-z]{1,12})"
    )
    for match in prefix_pattern.finditer(text):
        candidates.append({
            "start": match.start(),
            "end": match.end(),
            "value": int(match.group("value")),
            "unit": match.group("unit"),
            "tail": match.group("tail") or "",
        })
    for match in suffix_pattern.finditer(text):
        # A prefix claim such as “共3条裂缝” also contains “3条裂缝”.
        # Keep the longer, trigger-bound claim only once.
        if any(
            existing["start"] <= match.start() < existing["end"]
            or match.start() <= existing["start"] < match.end()
            for existing in candidates
        ):
            continue
        candidates.append({
            "start": match.start(),
            "end": match.end(),
            "value": int(match.group("value")),
            "unit": match.group("unit"),
            "tail": match.group("tail") or "",
        })
    return sorted(candidates, key=lambda item: item["start"])


def _count_scope(prefix: str, tail: str, rows: list[dict]) -> tuple[list[dict], str, bool]:
    """Bind a count claim to component/disease rows, or to all rows."""
    scope_text = f"{prefix}{tail}"
    component_hits = _hits(scope_text, _component_pairs(rows))
    disease_hits = _hits(tail or scope_text, ontology.build_disease_keywords_list())
    scoped = list(rows)
    labels = []

    if component_hits:
        selected = [component_hits[-1]]
        for previous in reversed(component_hits[:-1]):
            if re.fullmatch(r"\s*(?:和|与|及|、|以及)\s*", scope_text[previous[1]:selected[0][0]]):
                selected.insert(0, previous)
            else:
                break
        names = {hit[2] for hit in selected}
        exact_instance = {name for name in names if re.search(r"\d", name)}
        canonical_names = {_canonical_component(name) for name in names}
        scoped = [row for row in scoped if (
            str(row.get("构件") or "") in exact_instance
            if exact_instance else
            _canonical_component(str(row.get("构件") or "")) in canonical_names
        )]
        labels.extend(sorted(names))

    if disease_hits:
        disease = disease_hits[-1][2]
        scoped = [row for row in scoped if (
            ontology.canonicalize_disease(str(row.get("病害") or "")) or str(row.get("病害") or "")
        ) == disease or ontology.get_disease_root(str(row.get("病害") or "")) == disease]
        labels.append(disease)

    explicit_scope = bool(component_hits or disease_hits)
    return scoped, "·".join(labels) or "当前结果", explicit_scope


def check_diagnostic_counts(text: str, rows: list[dict], *, original_query: str = "") -> list[dict[str, Any]]:
    """Check occurrence counts against object-bound evidence rows.

    This is deliberately separate from scalar checks: “高风险3条” is not a
    risk score, and numbered treatment steps are not disease counts. A count is
    checked only when it is an explicit occurrence phrase or names a disease.
    """
    issues = []
    rows = list(rows or [])
    if not rows:
        return issues

    for claim in _count_claim_candidates(text):
        prefix = re.split(r"[。；;\n]", text[:claim["start"]])[-1]
        tail = claim["tail"]
        context = f"{prefix}{tail}"
        # Do not interpret risk/zone/step/list counts as disease counts here.
        if any(marker in prefix[-24:] for marker in ("高风险", "严控区", "步骤", "条款", "编号", "第")):
            continue
        disease_tail = bool(_hits(tail, ontology.build_disease_keywords_list()))
        generic_tail = any(word in tail for word in ("病害", "记录", "项目"))
        component_context = bool(_hits(context, _component_pairs(rows)))
        trigger_context = any(prefix.rstrip().endswith(trigger) for trigger in _COUNT_TRIGGERS)
        if not (disease_tail or generic_tail or component_context or trigger_context):
            continue

        scoped, scope_label, explicit_scope = _count_scope(prefix, tail, rows)
        if not scoped:
            continue
        expected = sum(_row_count(row) for row in scoped)
        # A bare “共N条病害” is a global aggregate; object-bound text is scoped
        # to the rows selected above. Both use the same evidence sum.
        if claim["value"] != expected:
            issues.append({
                "metric": "数量",
                "scope": scope_label,
                "asserted": claim["value"],
                "expected": [expected],
                "start": claim["start"],
                "end": claim["end"],
                "reason": "count_claim_evidence_mismatch",
            })
    return issues

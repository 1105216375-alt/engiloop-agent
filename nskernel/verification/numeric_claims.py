"""Extract bounded scalar assertions; this is not a general entailment prover.

Keep offsets so domain verifiers can bind each value to its own subject rather
than accepting a value merely because it occurs somewhere in the evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class NumericClaim:
    metric: str
    value: float
    aggregation: str
    start: int
    end: int


_PATTERNS = (
    ("risk", re.compile(
        r"(?P<agg>平均|最高|最低|最大|最小)?风险(?:评分|得分|分数|分)?"
        r"\s*(?:为|是|约|=|：|:)?\s*(?P<value>-?\d+(?:\.\d+)?)(?:\s*分)?"
    )),
    ("level", re.compile(
        r"(?P<agg>最高|最低|最大|最小)?(?:评定|评级|标度)(?:等级)?"
        r"\s*(?:为|是|=|：|:)?\s*(?P<value>-?\d+(?:\.\d+)?)(?![\d.])(?:\s*[类级])?"
    )),
    ("dp", re.compile(
        r"(?P<agg>平均|最高|最低|最大|最小)?DP\s*值?"
        r"\s*(?:为|是|=|：|:)?\s*(?P<value>-?\d+(?:\.\d+)?)", re.I
    )),
)
_AGGREGATIONS = {"平均": "mean", "最高": "max", "最大": "max", "最低": "min", "最小": "min"}


def extract_numeric_claims(text: str) -> list[NumericClaim]:
    claims = []
    for metric, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            # Counts such as “高风险12条” / “定量标度27条待补” are not
            # scalar scores or levels. Read the complete numeric token first;
            # never truncate 27 to level 2. Counts belong to the count verifier.
            if metric in {"risk", "level"} and re.match(r"\s*(?:条|处|项|个)", text[match.end():]):
                continue
            claims.append(NumericClaim(
                metric, float(match.group("value")),
                _AGGREGATIONS.get(match.group("agg"), ""),
                match.start(), match.end(),
            ))
    return sorted(claims, key=lambda claim: claim.start)


def expected_values(values: Iterable[float], aggregation: str) -> list[float]:
    values = list(values)
    if not values:
        return []
    if aggregation == "mean":
        return [sum(values) / len(values)]
    if aggregation == "max":
        return [max(values)]
    if aggregation == "min":
        return [min(values)]
    return values


def numeric_value_matches(claim: NumericClaim, values: Iterable[float]) -> bool:
    # Risk is rendered to one decimal in the application; classifications and
    # deductions are exact. Do not reuse the old ±0.6 risk tolerance.
    tolerance = 0.051 if claim.metric == "risk" else 1e-9
    return any(abs(claim.value - value) <= tolerance for value in values)


def _subject_hint(text: str, start: int) -> str:
    prefix = re.split(r"[。；;\n]", text[:start])[-1]
    prefix = re.sub(r"【[^】]*】", "", prefix)
    first_claim = extract_numeric_claims(prefix)
    if first_claim:
        prefix = prefix[:first_claim[0].start]
    fragments = [part.strip(" ：:，,()（）") for part in re.split(r"[，,]", prefix)]
    fragments = [part for part in fragments if part and not re.fullmatch(r"\d+年", part)]
    subject = fragments[-1] if fragments else ""
    subject = re.split(r"出现|发生|存在|·", subject)[0]
    return re.sub(r"\s+", "", subject).strip(" ：:()（）")


def check_cited_numeric_claims(statement: str, evidence: Iterable[str]) -> list[dict]:
    """Check recognizable assertions against cited facts, not arbitrary state.

    Domains can supply a stronger object/ontology-aware checker. Unrecognized
    prose is deliberately outside this bounded numeric contract.
    """
    sources = []
    for text in evidence:
        for claim in extract_numeric_claims(text):
            sources.append((claim, _subject_hint(text, claim.start)))
    issues = []
    for claim in extract_numeric_claims(statement):
        subject = _subject_hint(statement, claim.start)
        comparable = [
            source for source, scope in sources
            if source.metric == claim.metric and scope == subject
        ]
        # Exact aggregate assertions may be quoted; otherwise compute the
        # explicitly requested aggregate from individual cited values.
        exact = [source.value for source in comparable if source.aggregation == claim.aggregation]
        values = exact or expected_values(
            [source.value for source in comparable if not source.aggregation], claim.aggregation,
        )
        if not numeric_value_matches(claim, values):
            issues.append({
                "metric": claim.metric, "subject": subject, "asserted": claim.value,
                "expected": values, "reason": "numeric_claim_not_supported_by_cited_facts",
            })
    return issues

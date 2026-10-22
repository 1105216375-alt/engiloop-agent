from __future__ import annotations

import re
from typing import Any


KNOWN_REGULATIONS = frozenset({
    "JTG 5120-2021",
    "JTG 5220-2020",
    "JTG/T J21-2011",
    "JTG/T J21-01-2015",
    "JTG/T J22-2008",
    "JTG/T 5214-2022",
    "JTG/T H21-2011",
    "JTG/T 5431-2025",
    "JTG/T 5531-2025",
    "JTG/T 52XX-2026",
})

_REGULATION_PATTERN = re.compile(
    r"(?<![A-Z0-9])JTG\s*(?:/\s*T)?\s*[A-Z]?\s*[A-Z0-9]+(?:\s*[-\u2013\u2014]\s*\d+)?"
    r"\s*[-\u2013\u2014]\s*\d{4}(?!\d)",
    re.IGNORECASE,
)


def normalize_regulation_code(value: str) -> str:
    text = str(value or "").upper()
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\s*/\s*T", "/T", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^JTG/T\s*", "JTG/T ", text)
    text = re.sub(r"^JTG(?!/T)\s*", "JTG ", text)
    return text


def find_regulation_references(text: str) -> list[str]:
    found: list[str] = []
    for match in _REGULATION_PATTERN.finditer(str(text or "")):
        code = normalize_regulation_code(match.group(0))
        if code and code not in found:
            found.append(code)
    return found


def validate_regulation_references(text: str) -> dict[str, Any]:
    references = find_regulation_references(text)
    known_keys = {normalize_regulation_code(code).replace(" ", "") for code in KNOWN_REGULATIONS}
    unsupported = [
        code for code in references
        if code.replace(" ", "") not in known_keys
    ]
    return {
        "passed": not unsupported,
        "references": references,
        "unsupported": unsupported,
        "known_count": len(KNOWN_REGULATIONS),
    }


def build_regulation_guard_reply(unsupported: list[str]) -> str:
    quoted = "、".join(f"“{code}”" for code in unsupported)
    return (
        f"规范依据校验未通过：当前本地规范库未收录{quoted}，不能把它作为有效依据，"
        "也不能据此得出立即拆桥、封桥或大修等工程结论。请先核对规范编号；"
        "如需继续诊断，可直接描述构件、病害、标度和现场证据，系统会改用已核验规则处理。"
    )

from __future__ import annotations

import re
from typing import Any


_BRIDGE_SUFFIX = re.compile(r"(?:特大桥|大桥|中桥|小桥|桥)$")


def archive_bridge_name_stem(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"(?:拼宽)?(?:左幅|右幅|左线|右线|上行|下行)$", "", text)
    text = re.sub(r"[\s，,。.!！?？、·_\-]+", "", text)
    return _BRIDGE_SUFFIX.sub("", text).strip()


def explicit_archive_bridge_names(
    query: str,
    bridge_names: list[str],
    *,
    normalizer=None,
) -> list[str]:
    normalize = normalizer or (
        lambda value: re.sub(r"[\s，,。.!！?？、·_\-]+", "", str(value or "").lower())
    )

    def identity_key(value: str) -> str:
        normalized = str(normalize(value) or "")
        normalized = re.sub(r"(?<=\d)号", "#", normalized)
        return normalized

    normalized_query = identity_key(query)
    located: list[tuple[int, int, str]] = []
    for bridge_name in bridge_names:
        full = identity_key(bridge_name)
        aliases: list[tuple[str, int]] = []
        for alias, strength in (
            (full, 3),
            (full.replace("互通", ""), 2),
        ):
            if alias and (alias, strength) not in aliases:
                aliases.append((alias, strength))

        stem = identity_key(archive_bridge_name_stem(bridge_name))
        if stem and stem != full:
            # A generic “桥” may uniquely expand to 大桥/特大桥, but a
            # conflicting concrete suffix must never be swallowed.
            for alias in (stem + "桥", stem.replace("互通", "") + "桥"):
                if alias and (alias, 1) not in aliases:
                    aliases.append((alias, 1))
            # People sometimes omit the final bridge-name suffix in a query.
            # Accept a two-character-or-longer stem only through the existing
            # uniqueness gate; multiple catalog matches remain ambiguous.
            for alias in (stem, stem.replace("互通", "")):
                if len(alias) >= 2 and (alias, 0) not in aliases:
                    aliases.append((alias, 0))
        elif full and not full.endswith("桥"):
            for alias in (full + "桥", full.replace("互通", "") + "桥"):
                if alias and (alias, 1) not in aliases:
                    aliases.append((alias, 1))

        positions = []
        for alias, strength in aliases:
            position = normalized_query.find(alias)
            if position >= 0 and strength == 0:
                suffix_tail = normalized_query[position + len(alias):]
                if re.match(r"(?:特大桥|大桥|中桥|小桥|桥|挢)", suffix_tail):
                    position = -1
            if position >= 0:
                positions.append((position, strength))
        if positions:
            position, strength = min(positions, key=lambda item: (item[0], -item[1]))
            located.append((position, -strength, bridge_name))
    result: list[str] = []
    for _, _, bridge_name in sorted(located):
        if bridge_name not in result:
            result.append(bridge_name)
    return result


def verify_archive_request_binding(
    query: str,
    resolved_bridge: str,
    bridge_names: list[str],
    *,
    normalizer=None,
) -> dict[str, Any]:
    requested = explicit_archive_bridge_names(
        query,
        bridge_names,
        normalizer=normalizer,
    )
    resolved = str(resolved_bridge or "").strip()
    passed = not requested or (len(requested) == 1 and requested[0] == resolved)
    return {
        "enabled": True,
        "passed": passed,
        "requested_bridges": requested,
        "resolved_bridge": resolved,
        "reason": (
            "request_and_archive_entity_match"
            if passed else
            "multiple_explicit_bridges"
            if len(requested) > 1 else
            "resolved_bridge_differs_from_request"
        ),
    }


def build_fuzzy_archive_confirmation(
    search_tool: dict[str, Any],
    group: dict[str, Any],
) -> dict[str, Any] | None:
    match_type = str(search_tool.get("match_type") or "").lower()
    bridge_name = str(group.get("bridge_core") or "").strip()
    if "fuzzy" not in match_type or not bridge_name:
        return None

    years = [str(value) for value in (group.get("years") or [])]
    year_text = "、".join(f"{year}年" for year in years) or "未标年份"
    tool_call = dict(search_tool)
    tool_call.update({
        "status": "confirmation_required",
        "candidate_bridge": bridge_name,
        "reason": "fuzzy_bridge_name",
    })
    answer = (
        f"BridgeArchiveSearch 仅得到一个模糊候选：{bridge_name}。"
        f"档案共{group.get('count', 0)}份，覆盖{year_text}。"
        "输入桥名可能有错字；为避免串桥，本轮不继续生成评分、大修或寿命结论。"
        f"请回复完整桥名“{bridge_name}”或重新输入正确桥名，确认后再分析。"
    )
    return {
        "answer": answer,
        "tool_call": tool_call,
        "candidate_bridge": bridge_name,
    }

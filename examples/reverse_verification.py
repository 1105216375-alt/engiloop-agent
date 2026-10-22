"""Synthetic reverse-verification example; JTG 0000-2099 is NOT a real norm."""
from __future__ import annotations

import json

from engineering_agent.evidence import InMemoryEvidence, graph_from_relations


def synthetic_evidence() -> InMemoryEvidence:
    clauses = {
        "斜向裂缝": [{"reg": "JTG 0000-2099", "clause": "第4.1条",
                    "title": "合成主梁裂缝复核条目（非真实规范）",
                    "content": "合成示例：主梁斜向裂缝记录需核对来源。不得用于工程评定。"}],
        "支座脱空": [{"reg": "JTG 0000-2099", "clause": "第4.2条",
                    "title": "合成支座复核条目（非真实规范）",
                    "content": "合成示例：支座脱空记录需核对来源。不得用于工程评定。"}],
    }
    relations = [
        {"component": "主梁", "disease": "斜向裂缝", "reg": "JTG 0000-2099",
         "clause": "第4.1条", "weight": 20},
        {"component": "支座", "disease": "支座脱空", "reg": "JTG 0000-2099",
         "clause": "第4.2条", "weight": 20},
    ]
    return InMemoryEvidence(clause_index=clauses,
                            graph=graph_from_relations(clauses, relations))


def synthetic_results() -> list[dict]:
    return [
        {"构件": "主梁", "病害": "斜向裂缝", "风险评分": 80, "标度": 3, "DP值": 25},
        {"构件": "支座", "病害": "支座脱空", "风险评分": 30, "标度": 2, "DP值": 10},
    ]


def run_demo() -> dict:
    evidence = synthetic_evidence()
    rows = synthetic_results()
    cases = {
        "correct_numeric_binding": evidence.deliver("主梁的风险评分为80。", rows),
        "wrong_numeric_binding": evidence.deliver("主梁的风险评分为30。", rows),
        "mixed_draft_cleanup": evidence.deliver("主梁的风险评分为30。主梁的风险评分为80。", rows),
        "absent_clause": evidence.deliver("依据 JTG 0000-2099 第9.9条，应复核记录。", rows[:1]),
        "unrelated_clause": evidence.deliver("依据 JTG 0000-2099 第4.2条，应复核记录。", rows[:1]),
        "related_clause": evidence.deliver("依据 JTG 0000-2099 第4.1条，应复核记录。", rows[:1]),
    }
    assert cases["correct_numeric_binding"]["released"]
    assert not cases["wrong_numeric_binding"]["released"]
    assert not cases["absent_clause"]["released"]
    assert cases["unrelated_clause"]["status"] == "review_required"
    assert cases["related_clause"]["status"] == "checked"
    return {
        "notice": "全部记录、分值与条款为合成；JTG 0000-2099非真实规范。",
        "retrieved": evidence.retrieve("主梁", "斜向裂缝"),
        "cases": cases,
        "boundary": "检索命中和检查通过不等于整段语义被证明；自由文本仍需专业复核。",
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))

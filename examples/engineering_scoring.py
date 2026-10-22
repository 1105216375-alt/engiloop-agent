"""Synthetic DP sensitivity through the real scoring engine; no final grade."""
from copy import deepcopy
import json

from engineering_agent.scoring import evaluate_scoring_snapshot


def synthetic_scoring_inputs():
    return {
        "records": [
            {"构件": "1号主梁", "构件类别": "主梁", "病害": "合成裂缝",
             "DP值": 20, "source": "synthetic://diagnosis/main-1"},
            {"构件": "1号支座", "构件类别": "支座", "病害": "合成支座脱空",
             "DP值": 10, "source": "synthetic://diagnosis/bearing-1"},
        ],
        "selected_parts": ["upper/main", "upper/bear"],
        "structure": {"总主梁数": 1, "总支座数": 1},
        "part_applicability": {"upper": {
            "main": {"applicable": True, "source": "synthetic://scope/main"},
            "bear": {"applicable": True, "source": "synthetic://scope/bearing"},
        }},
        "reported_score": 82.0,
        "reported_source": "synthetic://report/selected-snapshot/table-1",
        "reported_parts": ["upper/main", "upper/bear"],
        "synthetic": True,
    }


def run_scoring_demo():
    inputs = synthetic_scoring_inputs()
    before = evaluate_scoring_snapshot(**inputs)
    changed = deepcopy(inputs)
    changed["records"][0]["DP值"] = 60
    after = evaluate_scoring_snapshot(**changed)
    absent = deepcopy(inputs)
    absent["records"] = []
    missing = deepcopy(inputs)
    missing["structure"].pop("总支座数")
    return {"dp20": before, "dp60": after,
            "empty_input": evaluate_scoring_snapshot(**absent),
            "missing_structure": evaluate_scoring_snapshot(**missing)}


def main():
    print("合成演算：只考察主梁+支座指定快照；不是全桥实测或规范有效性证明。")
    print(json.dumps(run_scoring_demo(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

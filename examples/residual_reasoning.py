"""Actual residual runtime on synthetic evidence-withdrawal cases, fully offline."""
from __future__ import annotations

import json

from engineering_agent.bridge_fixture import run_bridge_case, unknown_keys, accepted_action_types


def run_residual_demo() -> dict:
    cases = {
        "complete_score_pair": run_bridge_case("整体风险如何？"),
        "missing_dr": run_bridge_case("整体风险如何？", dr=None),
        "missing_bci": run_bridge_case("整体风险如何？", bci=None),
        "missing_capacity_and_repair": run_bridge_case("值不值得大修？", extra={
            "荷载试验结论": "尚未开展荷载试验，缺少承载能力和结构验算资料。",
            "维修记录": "暂无维修记录，是否修复未知。",
        }),
        "missing_calibrated_life_model": run_bridge_case(
            "剩余使用寿命还有多少年？",
            extra={"荷载试验结论": "荷载试验结论：承载能力满足原设计要求。"},
            analysis_options={"service_years": 20, "design_life": 100},
        ),
    }
    assert "技术状况风险筛查:低风险" in cases["complete_score_pair"]["reply"]
    assert "不等于承载能力或通行安全结论" in cases["complete_score_pair"]["reply"]
    for key, unknown in (("missing_dr", "bridge_score"), ("missing_bci", "bci_score")):
        assert unknown in unknown_keys(cases[key])
        assert "不能形成Dr/BCI交叉风险等级判断" in cases[key]["reply"]
        assert "技术状况风险筛查:" not in cases[key]["reply"]
    assert {"load_capacity", "repair_records"} <= unknown_keys(cases["missing_capacity_and_repair"])
    assert "residual_life_model" in unknown_keys(cases["missing_calibrated_life_model"])
    assert "没有可审计、经校准的剩余寿命模型" in cases["missing_calibrated_life_model"]["reply"]
    return cases


def main() -> None:
    results = run_residual_demo()
    print(json.dumps({
        "synthetic": True,
        "notice": "分值是测试输入，不是本示例的规范评分成果；未调用模型或读取真实档案。",
        "cases": {
            key: {
                "reply": result["reply"],
                "unknowns": sorted(unknown_keys(result)),
                "accepted_actions": accepted_action_types(result),
                "completion_mode": result["residual"]["final"]["metadata"].get("completion_mode"),
                "status": result["final_state_status"],
            }
            for key, result in results.items()
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

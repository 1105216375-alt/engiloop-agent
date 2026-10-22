"""Evidence withdrawal must change the real final reply, not just metadata."""
import re

import pytest

from domain.reasoning_bridge import _valid_score
from engineering_agent.bridge_fixture import run_bridge_case, unknown_keys, create_synthetic_profile
from examples.residual_reasoning import run_residual_demo


def test_minimal_profile_contract_is_valid():
    assert create_synthetic_profile().validate() == []


@pytest.mark.parametrize("value", [None, "", "未提供", -1, 101, float("nan"), float("inf"), True, False])
def test_invalid_score_is_unknown_not_zero(value):
    assert _valid_score(value) is None


@pytest.mark.parametrize("metric", ["dr", "bci"])
@pytest.mark.parametrize("value", [None, -1, 101, float("nan"), True])
def test_withdrawn_score_limits_reply_and_retains_obligation(metric, value):
    result = run_bridge_case("整体风险如何？", **{metric: value})
    assert "不能形成Dr/BCI交叉风险等级判断" in result["reply"]
    assert "技术状况风险筛查:" not in result["reply"]
    assert "Dr=0" not in result["reply"] and "BCI=0" not in result["reply"]
    assert ("bridge_score" if metric == "dr" else "bci_score") in unknown_keys(result)
    assert result["residual"]["final"]["metadata"]["completion_mode"] == "conditional"


def test_true_zero_is_present_and_not_treated_as_missing():
    result = run_bridge_case("整体风险如何？", dr=0, bci=0)
    assert "Dr=0, BCI=0" in result["reply"]
    assert "技术状况风险筛查:高风险" in result["reply"]
    assert not {"bridge_score", "bci_score"} & unknown_keys(result)


def test_complete_scores_do_not_become_a_safety_assurance():
    text = run_bridge_case("整体风险如何？")["reply"]
    assert "技术状况风险筛查:低风险" in text
    assert "不等于承载能力或通行安全结论" in text
    assert "结构本体健康" not in text


def test_negated_capacity_and_missing_repairs_stay_unknown():
    result = run_bridge_case("值不值得大修？", extra={
        "荷载试验结论": "尚未开展荷载试验，缺少承载能力和结构验算资料。",
        "维修记录": "暂无维修记录，是否修复未知。",
    })
    assert {"load_capacity", "repair_records"} <= unknown_keys(result)
    assert "需补齐或核对承载能力/荷载试验或结构验算、同位置维修闭环记录" in result["reply"]


def test_positive_records_remove_only_relevant_unknowns_not_decision_boundary():
    result = run_bridge_case("值不值得大修？", extra={
        "荷载试验结论": "荷载试验结论：承载能力满足原设计要求。",
        "维修记录": "合成记录：1号主梁裂缝已修复并验收合格。",
    })
    assert not {"load_capacity", "repair_records"} & unknown_keys(result)
    assert "现有证据不足以直接决定是否实施全桥大修" in result["reply"]


@pytest.mark.parametrize("options", [{}, {"service_years": 20, "design_life": 100}])
def test_dates_and_capacity_cannot_invent_residual_life(options):
    result = run_bridge_case(
        "剩余使用寿命还有多少年？", analysis_options=options,
        extra={"荷载试验结论": "荷载试验结论：承载能力满足原设计要求。"},
    )
    assert "residual_life_model" in unknown_keys(result)
    assert "没有可审计、经校准的剩余寿命模型" in result["reply"]
    assert not re.search(r"剩余使用寿命约\s*\d", result["reply"])
    assert "常规养护即可" not in result["reply"]


def test_all_offline_demo_cases_run_to_bounded_answers():
    cases = run_residual_demo()
    assert len(cases) == 5
    assert all(result["final_state_status"] == "solved" for result in cases.values())
    assert all(result["llm_refined"] is False for result in cases.values())

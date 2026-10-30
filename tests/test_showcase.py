"""Exercise final outcomes and failure paths, not promotional labels."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from engineering_agent.rulepack import (
    evaluate_rulepack, snapshot_reuse_status, validate_rulepack,
)
from examples.showcase import report_demo, residual_demo, rules_demo, run_showcase
from legacy_v7.core.scoring_agent import BridgeScoringAgent


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Showcase must remain offline")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def test_report_checks_real_docx_and_independent_evidence():
    demo = report_demo()
    assert demo["source"]["records"]
    assert demo["source"]["records"][0]["source"]["row"] == 2
    assert len(demo["source"]["sha256"]) == 64
    assert demo["blocked_delivery"]["released"] is False
    assert {i["category"] for i in demo["blocked_delivery"]["issues"]} >= {"number", "clause"}
    assert demo["wrong_object_delivery"]["released"] is False
    assert demo["checked_delivery"]["text"] == "主梁的风险评分为80。"
    assert demo["checked_delivery"]["engineering_verified"] is False


def test_report_does_not_hardcode_the_corrected_value(monkeypatch):
    from examples import reverse_verification
    original = reverse_verification.synthetic_results
    def changed():
        rows = original()
        rows[0]["风险评分"] = 75
        return rows
    monkeypatch.setattr(reverse_verification, "synthetic_results", changed)
    assert report_demo()["checked_delivery"]["text"] == "主梁的风险评分为75。"


def test_residual_changes_answer_not_only_status():
    demo = residual_demo()
    stages = demo["stages"]
    assert [s["remaining_requirements"] for s in stages] == [
        ["bci_score", "bridge_score"], ["bci_score"], [],
    ]
    assert [s["cross_indicator_screening_present"] for s in stages] == [False, False, True]
    assert "不能形成Dr/BCI交叉风险等级判断" in stages[0]["reply"]
    assert "不等于承载能力或通行安全结论" in stages[2]["reply"]
    assert all(s["engineering_signoff"] is False for s in stages)
    intake = demo["separate_intake_example"]
    assert [len(t["result"]["missing_fields"]) for t in intake] == [4, 2, 0]
    assert "length_m" not in intake[1]["result"]["symbol"]
    assert intake[2]["result"]["eligible_for_scoring"] is False
    fault = demo["separate_fault_injection"]
    assert fault["reject_before_commit"]["state_unchanged"] is True
    assert fault["rollback_after_injected_invalid_update"]["old_state_preserved"] is True


def rule_context():
    return {
        "observations": [{"构件": "1号主梁", "构件类别": "主梁", "病害": "合成裂缝",
                          "标度": 3, "source": "synthetic://observation/1"}],
        "selected_parts": ["upper/main"], "structure": {"总主梁数": 1},
        "part_applicability": {"upper": {"main": {"applicable": True, "source": "synthetic://scope/1"}}},
    }


def rulepack():
    path = Path(__file__).parents[1] / "examples" / "fixtures" / "rules_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_rule_changes_reach_the_extracted_engine(monkeypatch):
    calls = []
    original = BridgeScoringAgent.evaluate_bridge
    def observe(self, records, *args, **kwargs):
        calls.append(deepcopy(records))
        return original(self, records, *args, **kwargs)
    monkeypatch.setattr(BridgeScoringAgent, "evaluate_bridge", observe)
    demo = rules_demo()
    assert len(calls) == 2
    assert [rows[0]["DP值"] for rows in calls] == [20, 40]
    assert demo["before"]["snapshot"]["calculated"]["component_scores"]["upper/main/1"] == 80
    assert demo["after"]["snapshot"]["calculated"]["component_scores"]["upper/main/1"] == 60
    assert demo["old_result_under_new_rules"]["reusable"] is False
    assert demo["recomputed_context"]["reusable"] is True
    assert demo["after"]["snapshot"]["formal_grade"] is None


def test_same_version_label_does_not_hide_changed_rules():
    pack, context = rulepack(), rule_context()
    before = evaluate_rulepack(pack, **context)
    changed = deepcopy(pack)
    changed["rules"][0]["deduction"] = 35
    assert snapshot_reuse_status(before, changed, context=context)["reasons"] == ["rulepack_changed"]
    assert evaluate_rulepack(changed, **context)["snapshot"]["calculated"]["selected_scope_score"] == 65


def test_input_change_also_invalidates_reuse_and_input_is_not_mutated():
    pack, context = rulepack(), rule_context()
    initial = deepcopy((pack, context))
    before = evaluate_rulepack(pack, **context)
    assert (pack, context) == initial
    context["observations"][0]["source"] = "synthetic://new-observation"
    assert snapshot_reuse_status(before, pack, context=context)["reasons"] == ["input_changed"]


def test_nonmatching_rule_does_not_fallback_or_compute():
    context = rule_context()
    context["observations"][0]["标度"] = 5
    result = evaluate_rulepack(rulepack(), **context)
    assert result["status"] == "rule_not_applicable"
    assert result["snapshot"] is None
    assert not snapshot_reuse_status(result, rulepack(), context=context)["reusable"]


@pytest.mark.parametrize("value", [True, -1, 101, float("nan"), float("inf"), "20"])
def test_bad_rule_deductions_are_rejected(value):
    pack = rulepack()
    pack["rules"][0]["deduction"] = value
    with pytest.raises(ValueError):
        validate_rulepack(pack)


def test_duplicate_rule_is_ambiguous_even_with_same_value():
    pack = rulepack()
    pack["rules"].append(deepcopy(pack["rules"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_rulepack(pack)


def test_real_standards_are_not_silently_accepted_as_demo_rules():
    pack = rulepack()
    pack["synthetic"] = False
    with pytest.raises(ValueError):
        validate_rulepack(pack)


def test_missing_source_retains_scoring_review_boundary():
    context = rule_context()
    context["observations"][0]["source"] = ""
    result = evaluate_rulepack(rulepack(), **context)
    assert result["snapshot"]["calculated"] is None
    assert not snapshot_reuse_status(result, rulepack(), context=context)["reusable"]


def test_json_cli_is_machine_readable_and_runs_all_stories():
    # This subprocess has no network adapter; in-process tests enforce network denial.
    result = subprocess.run([sys.executable, "-m", "examples.showcase", "--json"],
                            capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    assert [d["id"] for d in output["demos"]] == ["report", "residual", "rules"]
    assert output["model_calls"] == 0


def test_unknown_showcase_name_is_rejected():
    with pytest.raises(ValueError):
        run_showcase("not_a_demo")

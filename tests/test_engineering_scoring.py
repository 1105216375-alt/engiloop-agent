"""Synthetic score flow and public boundary checks; no policy/data files."""
from copy import deepcopy
import socket

import pytest

from engineering_agent.scoring import evaluate_scoring_snapshot
from examples.engineering_scoring import run_scoring_demo, synthetic_scoring_inputs
from legacy_v7.core.scoring_agent import BridgeScoringAgent


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Synthetic scoring must not use the network")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def test_dp_changes_reach_real_component_and_selected_total(monkeypatch):
    calls = []
    original = BridgeScoringAgent.evaluate_bridge
    def observed(self, records, structure=None, part_applicability=None):
        calls.append(deepcopy(records))
        return original(self, records, structure, part_applicability)
    monkeypatch.setattr(BridgeScoringAgent, "evaluate_bridge", observed)
    demo = run_scoring_demo()
    assert len(calls) == 2
    before, after = demo["dp20"], demo["dp60"]
    assert before["calculated"]["component_scores"]["upper/main/1"] == 80
    assert after["calculated"]["component_scores"]["upper/main/1"] == 40
    assert before["calculated"]["selected_scope_score"] == pytest.approx(81.46)
    assert after["calculated"]["selected_scope_score"] == pytest.approx(47.32)
    assert calls[0][0]["DP值"] == 20 and calls[1][0]["DP值"] == 60
    assert before["calculated"]["input_sha256"] != after["calculated"]["input_sha256"]
    assert before["formal_grade"] is None and after["formal_grade"] is None
    assert before["publishable"] is False and after["publishable"] is False


def test_reported_and_recomputed_numbers_remain_independent():
    demo = run_scoring_demo()
    assert demo["dp20"]["reported"]["value"] == 82
    assert demo["dp60"]["reported"]["value"] == 82
    assert demo["dp60"]["comparison"]["computed_minus_reported"] == pytest.approx(-34.68)
    assert demo["dp60"]["calibration_applied"] is False
    assert demo["dp60"]["automatic_overwrite_allowed"] is False
    assert demo["dp20"]["reported"]["source"].startswith("synthetic://report/")
    assert demo["dp20"]["input_provenance"][0]["source"].startswith("synthetic://diagnosis/")


def test_missing_records_do_not_call_legacy_engine_or_produce_100(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Empty input must be stopped before the legacy engine")
    monkeypatch.setattr(BridgeScoringAgent, "evaluate_bridge", forbidden)
    inputs = synthetic_scoring_inputs()
    inputs["records"] = []
    result = evaluate_scoring_snapshot(**inputs)
    assert result["status"] == "rejected"
    assert result["calculated"] is None
    assert result["formal_grade"] is None
    assert result["issues"][0]["code"] == "no_observations"


@pytest.mark.parametrize("dp", [None, -1, 100.01, float("nan"), float("inf"), -float("inf"), True, "20", 10**1000])
def test_invalid_dp_is_not_zero_or_a_silent_score(dp):
    inputs = synthetic_scoring_inputs()
    inputs["records"][0]["DP值"] = dp
    result = evaluate_scoring_snapshot(**inputs)
    assert result["status"] == "rejected"
    assert result["calculated"] is None
    assert any(item["code"] == "invalid_dp" for item in result["issues"])


def test_missing_dp_is_not_default_zero():
    inputs = synthetic_scoring_inputs()
    del inputs["records"][0]["DP值"]
    assert evaluate_scoring_snapshot(**inputs)["calculated"] is None


@pytest.mark.parametrize("dp, expected_component", [(0, 100), (100, 0)])
def test_explicit_zero_and_hundred_dp_are_valid_algorithm_inputs(dp, expected_component):
    inputs = synthetic_scoring_inputs()
    inputs["records"][0]["DP值"] = dp
    result = evaluate_scoring_snapshot(**inputs)
    assert result["calculated"]["component_scores"]["upper/main/1"] == expected_component
    assert result["publishable"] is False


@pytest.mark.parametrize("category", ["未知构件", "", None])
def test_unknown_component_is_not_discarded_into_better_score(category):
    inputs = synthetic_scoring_inputs()
    inputs["records"][0]["构件类别"] = category
    result = evaluate_scoring_snapshot(**inputs)
    assert result["status"] == "rejected"
    assert result["calculated"] is None
    assert any(item["code"] == "unknown_component" for item in result["issues"])


@pytest.mark.parametrize("component", ["主梁", "1~20号主梁", "1号主梁、2号主梁", "1号桥墩"])
def test_ambiguous_or_conflicting_instance_needs_review(component):
    inputs = synthetic_scoring_inputs()
    inputs["records"][0]["构件"] = component
    result = evaluate_scoring_snapshot(**inputs)
    assert result["calculated"] is None
    assert result["publishable"] is False


@pytest.mark.parametrize("count", [None, 0, -1, 1.5, True, float("inf"), 129, 2])
def test_missing_illegal_or_uncovered_structure_does_not_fill_healthy_instances(count):
    inputs = synthetic_scoring_inputs()
    inputs["structure"]["总支座数"] = count
    result = evaluate_scoring_snapshot(**inputs)
    assert result["status"] == "review_required"
    assert result["calculated"] is None
    assert result["formal_grade"] is None


@pytest.mark.parametrize("entry", [None, True, {"applicable": True},
    {"applicable": "true", "source": "synthetic"},
    {"applicable": False, "source": "synthetic"}])
def test_missing_or_conflicting_applicability_never_grades(entry):
    inputs = synthetic_scoring_inputs()
    inputs["part_applicability"]["upper"]["bear"] = entry
    result = evaluate_scoring_snapshot(**inputs)
    assert result["status"] == "review_required"
    assert result["calculated"] is None
    assert result["formal_grade"] is None


def test_snapshot_does_not_expose_unchecked_lower_deck_defaults_or_dr():
    result = evaluate_scoring_snapshot(**synthetic_scoring_inputs())
    assert set(result["calculated"]["selected_system_scores"]) == {"upper"}
    assert set(result["calculated"]["part_scores"]["upper"]) == {"main", "bear"}
    assert "全桥评分" not in result and "技术状况等级" not in result
    assert "Dr" not in result["calculated"]


def test_different_report_scope_cannot_produce_a_numeric_gap():
    inputs = synthetic_scoring_inputs()
    inputs["reported_parts"] = ["whole_bridge"]
    result = evaluate_scoring_snapshot(**inputs)
    assert result["reported"]["value"] == 82
    assert result["calculated"]["selected_scope_score"] == pytest.approx(81.46)
    assert result["comparison"]["status"] == "not_comparable"
    assert result["comparison"]["computed_minus_reported"] is None


@pytest.mark.parametrize("field,value", [("评分待复核", True), ("engineering_verified", False), ("source", "")])
def test_declared_review_or_missing_source_blocks_snapshot(field, value):
    inputs = synthetic_scoring_inputs()
    inputs["records"][0][field] = value
    result = evaluate_scoring_snapshot(**inputs)
    assert result["status"] == "review_required"
    assert result["calculated"] is None


def test_no_external_database_is_loaded(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Do not read the original configured DP database")
    monkeypatch.setattr(BridgeScoringAgent, "_load_disease_db", forbidden)
    assert evaluate_scoring_snapshot(**synthetic_scoring_inputs())["status"] == "computed_snapshot"


def test_multiple_defects_use_the_actual_recursive_component_formula():
    inputs = synthetic_scoring_inputs()
    inputs["records"].append({**inputs["records"][0], "DP值": 10,
                               "病害": "另一条合成病害", "source": "synthetic://diagnosis/main-1-second"})
    result = evaluate_scoring_snapshot(**inputs)
    expected = round(BridgeScoringAgent.calculate_component_score([20, 10]), 2)
    assert result["calculated"]["component_scores"]["upper/main/1"] == expected


def test_adapter_does_not_mutate_caller_inputs():
    inputs = synthetic_scoring_inputs()
    before = deepcopy(inputs)
    evaluate_scoring_snapshot(**inputs)
    assert inputs == before


def test_engine_inferred_count_adjustment_requires_review_not_hidden_completion():
    inputs = synthetic_scoring_inputs()
    inputs["records"][0]["构件"] = "2号主梁"
    inputs["records"].append({**inputs["records"][0], "构件": "3号主梁",
                               "source": "synthetic://diagnosis/main-3"})
    inputs["structure"]["总主梁数"] = 2
    result = evaluate_scoring_snapshot(**inputs)
    assert result["status"] == "review_required"
    assert result["calculated"] is None
    assert result["issues"][0]["code"] == "engine_requires_review"


def test_report_without_provenance_is_preserved_but_not_blended_into_score():
    inputs = synthetic_scoring_inputs()
    inputs["reported_source"] = ""
    result = evaluate_scoring_snapshot(**inputs)
    assert result["reported"]["value"] == 82
    assert result["calculated"] is None
    assert result["status"] == "review_required"

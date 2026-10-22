"""Reviewed reuse must affect execution order without transferring old facts."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from bip_runtime.euler_memory import EulerMemoryExtractor, EulerMemoryMatcher
from domain.reasoning_bridge import EulerMemoryGuidedHeuristicProposer
from engineering_agent.bridge_fixture import analyze_fixture, build_bridge_fixture
from examples.reviewed_memory import run_reviewed_memory_demo
from legacy_v7.core.state_store import SQLiteStateStore
from nskernel.dialogue.reasoning import CandidateStep, ProposerSource, ResidualState
from nskernel.reasoning import HeuristicRule


@pytest.fixture(scope="module")
def demonstrated_memory():
    return run_reviewed_memory_demo()


def test_pending_memory_cannot_affect_route(demonstrated_memory):
    assert demonstrated_memory["pending_candidate"]["review_status"] == "pending"
    assert demonstrated_memory["before_review"]["euler_memory"]["retrieved"] == 0
    assert demonstrated_memory["before_review"]["euler_memory"]["applied"] is False


def test_reviewed_memory_changes_actual_accepted_order(demonstrated_memory):
    assert demonstrated_memory["before_actions"][0] == "risk_assess"
    assert demonstrated_memory["after_actions"][0] == "priority"
    memory = demonstrated_memory["after_review"]["euler_memory"]
    assert memory["applied"] and memory["guided_steps"] >= 1
    assert memory["must_reverify"]
    assert "risk_assess" in demonstrated_memory["after_actions"]


def test_route_does_not_copy_old_bridge_facts(demonstrated_memory):
    result = demonstrated_memory["after_review"]
    for text in [result["reply"], "\n".join(result["verified_facts"])]:
        assert "合成来源桥" not in text
        assert "锈蚀" not in text
        assert "风险77" not in text
    assert "合成目标桥" in result["reply"]
    assert "Dr=60, BCI=66" in result["reply"]
    assert "44.0分" in result["reply"]
    policy = demonstrated_memory["pending_candidate"]["payload"]["reuse_policy"]
    assert policy["may_copy_final_answer"] is False
    assert policy["may_copy_bridge_facts"] is False


def test_approval_does_not_override_missing_current_evidence(demonstrated_memory):
    memory = demonstrated_memory["missing_required_evidence"]["euler_memory"]
    assert memory["retrieved"] >= 1
    assert memory["applicable"] == 0
    assert memory["applied"] is False
    assert {"load_capacity", "repair_records"} <= set(memory["matches"][0]["missing_evidence"])


def test_conflicting_repair_scope_blocks_approved_pattern(tmp_path):
    store = SQLiteStateStore(Path(tmp_path) / "synthetic.sqlite3")
    try:
        source = analyze_fixture(build_bridge_fixture(state_store=store), "哪些构件优先维修？")
        candidate = store.get_memory_candidate(source["memory_candidate"]["candidate_id"])
        payload = deepcopy(candidate["payload"])
        from bip_runtime.euler_memory import build_context_phase
        payload["context_phase"] = build_context_phase(
            task_type="repair_priority", goal_template="哪些构件优先维修？",
            semantic_frame={"constraints": {"exclude_repaired": True}},
            session_context={"components": ["主梁"]},
            evidence_profile=EulerMemoryExtractor._required_evidence("repair_priority"),
        )
        # Explicit synthetic review fixture: the scope is part of what is approved.
        candidate["payload"] = payload
        store.upsert_memory_candidate(candidate)
        store.review_memory_candidate(candidate["candidate_id"], "approved", "synthetic-reviewer")
        matched = EulerMemoryMatcher(store).match(
            question="哪些构件优先维修？", bridge_name="另一座合成桥",
            available_evidence=EulerMemoryExtractor._required_evidence("repair_priority"),
            semantic_frame={"constraints": {"exclude_repaired": False}},
            session_context={"components": ["主梁"]},
        )
        assert matched and matched[0]["can_apply"] is False
        assert "repair_scope" in matched[0]["phase_match"]["hard_mismatches"]
    finally:
        store.close()


def test_route_falls_back_when_no_current_rule_matches():
    state = ResidualState()
    state.register_goal("合成风险任务")
    state.add_unresolved("风险")

    def build(state, context):
        return CandidateStep(
            source=ProposerSource.HEURISTIC,
            action={"type": "risk_assess"},
            produced_items=[{"type": "fact", "statement": "合成当前事实"}],
        )

    proposer = EulerMemoryGuidedHeuristicProposer([HeuristicRule("risk", ["风险"], build)])
    candidates = proposer.propose(state, {
        "question": "风险如何？",
        "approved_memory_patterns": [{
            "memory_id": "synthetic-other-route",
            "derivation_steps": [{"step_index": 0, "action_types": ["cost_estimate"]}],
        }],
    })
    assert [row.action["type"] for row in candidates] == ["risk_assess"]
    assert "memory_guided" not in candidates[0].action


def _synthetic_step(*, decisions, candidate_id="synthetic-candidate"):
    return SimpleNamespace(
        step_index=0,
        winning_proposer="synthetic-proposer",
        candidates=[{"candidate_id": candidate_id, "action": {"type": "priority"}}],
        decisions=decisions,
        candidates_accepted=1,  # Counters alone must not establish acceptance.
        candidates_rejected=0,
    )


@pytest.mark.parametrize("decisions", [
    [],
    [{"action": "accept"}],
    [{"candidate_id": "other-candidate", "action": "accept"}],
    [{"candidate_id": "synthetic-candidate", "action": "reject"}],
    [
        {"candidate_id": "synthetic-candidate", "action": "accept"},
        {"candidate_id": "synthetic-candidate", "action": "reject"},
    ],
    [
        {"candidate_id": "synthetic-candidate", "action": "accept"},
        {"rollback": True},
    ],
])
def test_unverified_or_rolled_back_attempts_are_not_reusable(decisions, tmp_path):
    summary = SimpleNamespace(run_id="synthetic-invalid-history", steps=[
        _synthetic_step(decisions=decisions),
    ])
    assert EulerMemoryExtractor._step_templates(summary) == []
    store = SQLiteStateStore(Path(tmp_path) / "synthetic.sqlite3")
    try:
        final = SimpleNamespace(is_solved=lambda: True)
        extracted = EulerMemoryExtractor(store).extract_pending(
            question="哪些构件优先维修？", final_state=final,
            run_summary=summary, verifier_names=["SyntheticChecker"],
        )
        assert extracted is None
        assert store.list_memory_candidates() == []
    finally:
        store.close()


def test_candidate_identity_not_list_position_controls_memory_extraction():
    step = _synthetic_step(decisions=[
        {"candidate_id": "other-candidate", "action": "reject"},
        {"candidate_id": "synthetic-candidate", "action": "accept"},
    ])
    route = EulerMemoryExtractor._step_templates(SimpleNamespace(steps=[step]))
    assert route[0]["action_types"] == ["priority"]
    assert route[0]["accepted_candidates"] == 1


def test_legacy_candidates_without_identity_are_audit_only():
    step = _synthetic_step(candidate_id="", decisions=[{"action": "accept"}])
    assert EulerMemoryExtractor._step_templates(SimpleNamespace(steps=[step])) == []

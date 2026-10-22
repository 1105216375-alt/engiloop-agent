"""Offline, synthetic demonstrations of the extracted runtime's actual boundaries.

Run from the repository root: python -m examples.checkpoints_and_rollback
No model, network, customer archive, or persistent application database is used.
The only database is inside TemporaryDirectory and is removed on exit.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

from bip_runtime.jobs import DurableJobWorker
from legacy_v7.core.state_store import SQLiteStateStore
from nskernel.dialogue.reasoning import (
    CandidateStep,
    LoopManager,
    MainStateUpdater,
    ProposerSource,
    ResidualState,
    ResidualStatus,
    VerifierGate,
)
from nskernel.reasoning import BudgetManager, RuntimeController


class SyntheticMeasurementProposer:
    """A known synthetic value, not a language-model or field measurement."""

    name = "synthetic_measurement"
    priority = 10

    def propose(self, residual_state, context):
        return [CandidateStep(
            source=ProposerSource.TOOL,
            input_refs=["synthetic:ruler-001"],
            action={"type": "normalize_synthetic_measurement"},
            produced_items=[{
                "type": "fact",
                "statement": "合成示例：长度 80 cm，归一为 0.80 m。",
            }],
            expected_effect={
                "type": "resolve_unresolved",
                "target_unresolved_item": "U_length",
            },
            confidence=1.0,
            justification="纯合成输入的确定性单位换算，不代表现场事实已核实。",
        )]


class RejectSyntheticChecker:
    def check(self, candidate, residual_state, context=None):
        return False, ["synthetic_evidence_rejected"], {"synthetic": True}


class InjectInconsistentState(MainStateUpdater):
    """Fault injection only: mark SOLVED while an obligation remains open."""

    def apply(self, candidate, decision, residual_state):
        new_state, transition = super().apply(candidate, decision, residual_state)
        new_state.add_unresolved("合成故障：定位证据仍未核实", item_id="U_injected")
        new_state.status = ResidualStatus.SOLVED
        return new_state, transition


def make_synthetic_state() -> ResidualState:
    state = ResidualState(residual_id="R_synthetic_initial")
    state.register_goal("整理一条合成量测记录", goal_id="G_record")
    state.add_unresolved("需要长度量测", item_id="U_length")
    state.metadata["synthetic"] = True
    return state


def run_runtime_demo() -> dict:
    """Use the real gate, loop, controller and rollback handler, not mock results."""
    initial = make_synthetic_state()
    original = deepcopy(initial.to_dict())

    rejected_loop = LoopManager(
        [SyntheticMeasurementProposer()],
        VerifierGate(hard_checkers=[RejectSyntheticChecker()]),
    )
    rejected, reject_log = RuntimeController(
        rejected_loop, budget_manager=BudgetManager(max_steps=1),
    ).run(initial)
    assert rejected.to_dict() == original
    assert reject_log.steps[0].candidates_rejected == 1
    assert reject_log.rollbacks == 0
    assert not reject_log.transitions

    faulty_loop = LoopManager(
        [SyntheticMeasurementProposer()],
        VerifierGate(),
        main_state_updater=InjectInconsistentState(),
    )
    restored, rollback_log = RuntimeController(
        faulty_loop, budget_manager=BudgetManager(max_steps=1),
    ).run(initial)
    assert rollback_log.steps[0].candidates_accepted == 1
    assert rollback_log.rollbacks == 1
    assert {"rollback": True} in rollback_log.steps[0].decisions
    assert restored.to_dict() == original
    assert restored is not initial
    assert initial.to_dict() == original

    return {
        "synthetic": True,
        "reject_before_commit": {
            "rejected_candidates": reject_log.steps[0].candidates_rejected,
            "rollbacks": reject_log.rollbacks,
            "state_unchanged": rejected.to_dict() == original,
        },
        "rollback_after_injected_invalid_update": {
            "gate_accepted_candidates": rollback_log.steps[0].candidates_accepted,
            "rollbacks": rollback_log.rollbacks,
            "old_state_preserved": restored.to_dict() == original,
            "final_status": restored.status.value,
            "stop_reason": rollback_log.stop_reason,
            "decisions": rollback_log.steps[0].decisions,
            "audit_note": "transitions 保留尝试记录；不能脱离 rollback 事件当作已提交结果。",
        },
    }


def run_persistence_demo() -> dict:
    """Real SQLite persistence and worker retry; no precise resume is claimed."""
    with TemporaryDirectory(prefix="engineering-agent-checkpoints-") as directory:
        path = Path(directory) / "synthetic.sqlite3"
        store = SQLiteStateStore(path)
        try:
            payload = {"synthetic": True, "status": "review_required", "value": 7}
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode("utf-8"),
            ).hexdigest()
            store.save_checkpoint("synthetic-run", 1, "review_checkpoint", digest, payload)
            store.close()
            store = SQLiteStateStore(path)
            checkpoint = store.latest_checkpoint("synthetic-run")
            assert checkpoint["payload"] == payload
            assert checkpoint["state_hash"] == digest

            job = store.enqueue_job(
                "synthetic_double", "synthetic-idempotency-key", {"value": 7},
                waiting_approval=True,
            )
            duplicate = store.enqueue_job(
                "synthetic_double", "synthetic-idempotency-key", {"value": 99},
                waiting_approval=True,
            )
            assert duplicate["job_id"] == job["job_id"]
            worker = DurableJobWorker(store, {
                "synthetic_double": lambda data: {"result": data["value"] * 2},
            })
            assert worker.run_once() is False
            approval = store.create_approval_request(
                "job", job["job_id"], "L2", {"reason": "synthetic demonstration"},
            )
            store.decide_approval(approval["approval_id"], "approved", "synthetic-reviewer", "demo")
            store.release_job(job["job_id"])
            assert worker.run_once() is True
            completed = store.get_job(job["job_id"])
            assert completed["result"] == {"result": 14}

            calls = []

            def flaky_handler(data):
                calls.append(data["_job_context"]["attempt"])
                if len(calls) == 1:
                    raise RuntimeError("synthetic transient failure")
                return {"attempts_seen": list(calls), "result": 21}

            retry_job = store.enqueue_job("synthetic_retry", "synthetic-retry-key", {"value": 7})
            retry_worker = DurableJobWorker(store, {"synthetic_retry": flaky_handler})
            assert retry_worker.run_once() is True
            assert store.get_job(retry_job["job_id"])["status"] == "retry"
            future_time = time.time() + 5
            # Advance only the demo clock; no sleeping or application DB mutation.
            with patch("legacy_v7.core.state_store.time.time", return_value=future_time):
                assert retry_worker.run_once() is True
            assert calls == [1, 2]  # Handler entry is reached again: whole-job retry.
            assert store.get_job(retry_job["job_id"])["status"] == "completed"

            lease_job = store.enqueue_job("synthetic_lease", "synthetic-lease-key", {})
            first_claim = store.claim_job("synthetic-dead-worker", lease_seconds=0)
            replacement = store.claim_job("synthetic-replacement-worker", lease_seconds=60)
            assert first_claim["job_id"] == lease_job["job_id"] == replacement["job_id"]
            assert replacement["attempt_count"] == 2
            # The queue API deliberately only cancels not-yet-running jobs.
            assert store.cancel_job(lease_job["job_id"])["status"] == "running"

            return {
                "synthetic": True,
                "checkpoint_survives_store_reopen": True,
                "checkpoint_read_is_not_execution_resume": True,
                "stored_hash_is_not_automatically_verified": True,
                "same_key_returns_existing_job": True,
                "approval_blocked_execution_until_release": True,
                "completed_result": completed["result"],
                "whole_job_retry_handler_attempts": calls,
                "expired_lease_reclaimed_at_attempt": replacement["attempt_count"],
                "running_queue_job_not_cancelled": True,
                "temporary_database_removed_on_exit": True,
            }
        finally:
            store.close()


def main() -> None:
    result = {"runtime": run_runtime_demo(), "persistence": run_persistence_demo()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

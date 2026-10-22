"""Regression tests use only synthetic state and temporary SQLite files."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from examples.checkpoints_and_rollback import (
    make_synthetic_state,
    run_persistence_demo,
    run_runtime_demo,
)
from nskernel.dialogue.reasoning import ResidualStatus
from nskernel.reasoning import RollbackHandler


class RollbackTests(unittest.TestCase):
    def test_enum_solved_with_open_obligation_is_invalid(self):
        state = make_synthetic_state()
        state.status = ResidualStatus.SOLVED
        self.assertTrue(RollbackHandler._is_invalid_state(state))

    def test_plain_string_status_and_enum_share_semantics(self):
        for status in ("solved", ResidualStatus.SOLVED):
            with self.subTest(status=status):
                state = SimpleNamespace(status=status, unresolved_items=["synthetic"])
                self.assertTrue(RollbackHandler._is_invalid_state(state))

    def test_status_suffix_is_not_a_solved_state(self):
        # The old suffix check also over-matched unrelated strings, e.g. unsolved.
        for status in ("unsolved", "not_solved", "active"):
            with self.subTest(status=status):
                state = SimpleNamespace(status=status, unresolved_items=["synthetic"])
                self.assertFalse(RollbackHandler._is_invalid_state(state))

    def test_valid_solved_state_without_obligations_is_kept(self):
        state = make_synthetic_state()
        state.unresolved_items.clear()
        state.status = ResidualStatus.SOLVED
        self.assertFalse(RollbackHandler._is_invalid_state(state))

    def test_missing_state_or_identifier_rolls_back_to_independent_copy(self):
        original = make_synthetic_state()
        corrupted = deepcopy(original)
        corrupted.residual_id = ""
        for invalid in (None, corrupted):
            with self.subTest(invalid=invalid):
                restored, rolled_back = RollbackHandler().safe_apply(original, invalid)
                self.assertTrue(rolled_back)
                self.assertEqual(restored.to_dict(), original.to_dict())
                self.assertIsNot(restored, original)
                restored.metadata["changed"] = True
                self.assertNotIn("changed", original.metadata)

    def test_runtime_records_rollback_and_preserves_previous_state(self):
        result = run_runtime_demo()
        self.assertEqual(result["reject_before_commit"]["rollbacks"], 0)
        self.assertTrue(result["reject_before_commit"]["state_unchanged"])
        actual = result["rollback_after_injected_invalid_update"]
        self.assertEqual(actual["rollbacks"], 1)
        self.assertTrue(actual["old_state_preserved"])
        self.assertEqual(actual["final_status"], "active")
        self.assertEqual(actual["stop_reason"], "budget_exceeded")


class PersistenceBoundaryTests(unittest.TestCase):
    def test_persistent_audit_and_whole_job_retry_are_distinct(self):
        result = run_persistence_demo()
        self.assertTrue(result["checkpoint_survives_store_reopen"])
        self.assertTrue(result["checkpoint_read_is_not_execution_resume"])
        self.assertTrue(result["same_key_returns_existing_job"])
        self.assertTrue(result["approval_blocked_execution_until_release"])
        self.assertEqual(result["completed_result"], {"result": 14})
        self.assertEqual(result["whole_job_retry_handler_attempts"], [1, 2])
        self.assertEqual(result["expired_lease_reclaimed_at_attempt"], 2)
        self.assertTrue(result["running_queue_job_not_cancelled"])


if __name__ == "__main__":
    unittest.main()

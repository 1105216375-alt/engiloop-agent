"""Public-release isolation and actual output-boundary regression checks."""
import builtins
from contextlib import ExitStack
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bip_runtime import native_tracing
from domain.reasoning_bridge import _rule_cost_analysis
from engineering_agent.bridge_fixture import run_bridge_case, unknown_keys
from examples.reverse_verification import synthetic_evidence, synthetic_results
from legacy_v7.core.agents import RegulationRAGAgent
from legacy_v7.core.component_scope import extract_mentioned_components
from legacy_v7.core.knowledge_graph import BridgeKnowledgeGraph
from legacy_v7.core.repair_report_audit import REPORT_TEXT_WITHHELD
from legacy_v7.core import state_store
from nskernel.dialogue.reasoning import ResidualState


class ReleaseIsolationTests(unittest.TestCase):
    def test_rag_constructor_never_autoloads_indexes_graph_or_client(self):
        with ExitStack() as stack:
            for obj, name in ((builtins, "open"), (Path, "read_text"),
                              (Path, "read_bytes"), (socket, "create_connection"),
                              (socket.socket, "connect"),
                              (RegulationRAGAgent, "_load_index"),
                              (BridgeKnowledgeGraph, "build")):
                stack.enter_context(patch.object(
                    obj, name, side_effect=AssertionError("Implicit I/O is forbidden")))
            rag = RegulationRAGAgent()
            self.assertEqual(rag.retrieve("斜向裂缝", component="主梁"), [])
        self.assertIsNone(rag._ai)
        self.assertIsNone(rag._kg)
        self.assertEqual(rag._clause_index, {})

    def test_explicit_index_loader_requires_public_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            rag = RegulationRAGAgent()
            with patch.object(builtins, "open", side_effect=AssertionError("Unexpected read")):
                with self.assertRaises(ValueError):
                    rag._load_index()

    def test_production_state_variable_is_ignored_and_default_does_not_write(self):
        environment = {"BIP_STATE_DB": "/synthetic-production-location/state.db"}
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(state_store.sqlite3, "connect", side_effect=AssertionError("Unexpected DB")):
                with patch.object(Path, "mkdir", side_effect=AssertionError("Unexpected write")):
                    with self.assertRaises(ValueError):
                        state_store.default_state_db_path()
                    with self.assertRaises(ValueError):
                        state_store.SQLiteStateStore()
                    with patch.object(state_store, "_STORE", None):
                        with self.assertRaises(ValueError):
                            state_store.get_state_store()

    def test_public_state_variable_is_explicitly_accepted(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "public-test.db"
            with patch.dict(os.environ, {"ENGINEERING_AGENT_STATE_DB": str(target)}, clear=True):
                self.assertEqual(state_store.default_state_db_path(), target.resolve())
                store = state_store.SQLiteStateStore()
                try:
                    self.assertTrue(target.is_file())
                    self.assertEqual(store.path, target.resolve())
                finally:
                    store.close()

    def test_explicit_state_path_wins_over_environment(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "explicit.db"
            with patch.dict(os.environ, {"ENGINEERING_AGENT_STATE_DB": "/unused-public-location/other.db"}, clear=True):
                store = state_store.SQLiteStateStore(target)
                try:
                    self.assertEqual(store.path, target)
                    self.assertTrue(target.is_file())
                finally:
                    store.close()

    def test_production_trace_variable_is_not_inherited(self):
        legacy_path = "/synthetic-production-location/traces"
        with patch.dict(os.environ, {"BIP_TRACE_DIR": legacy_path}, clear=True):
            self.assertEqual(native_tracing._trace_directory(), native_tracing.DEFAULT_TRACE_DIR)
            self.assertNotEqual(str(native_tracing._trace_directory()), legacy_path)

    def test_public_trace_variable_requires_its_own_name(self):
        with TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"ENGINEERING_AGENT_TRACE_DIR": temporary}, clear=True):
                self.assertEqual(native_tracing._trace_directory(), Path(temporary).resolve())

    def test_isolated_component_import_is_callable(self):
        self.assertIn("主梁", extract_mentioned_components("只分析主梁病害"))
        self.assertIn("支座", extract_mentioned_components("支座存在脱空"))

    def test_component_mismatch_changes_the_final_delivered_text(self):
        evidence = synthetic_evidence()
        original = "支座存在脱空。"
        final = evidence.deliver(original, synthetic_results(), original_query="只分析主梁病害。")
        self.assertFalse(final["released"])
        self.assertEqual(final["text"], REPORT_TEXT_WITHHELD)
        self.assertNotEqual(final["text"], original)
        self.assertTrue(any(issue["category"] == "component_mismatch"
                            and issue["severity"] == "hard" for issue in final["issues"]))

    def test_component_match_keeps_the_requested_object(self):
        evidence = synthetic_evidence()
        original = "主梁存在斜向裂缝。"
        final = evidence.deliver(original, synthetic_results(), original_query="只分析主梁病害。")
        self.assertTrue(final["released"])
        self.assertEqual(final["text"], original)

    def test_cost_basis_cannot_import_an_unpublished_estimator_or_invent_amounts(self):
        state = ResidualState()
        state.add_unresolved("预估大修成本")
        real_import = builtins.__import__

        def forbid_estimator(name, *args, **kwargs):
            if name == "domain.cost_estimator":
                raise AssertionError("Unpublished cost module must not be imported")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=forbid_estimator):
            candidate = _rule_cost_analysis(state, {"v7_data": {
                "cost_basis": {"synthetic": True, "quantity": 5, "unit_price": 100},
                "structure": {}, "stats": {"总数": 1},
            }})
        self.assertEqual(candidate.action["type"], "cost_model_missing")
        statement = candidate.produced_items[0]["statement"]
        self.assertIn("没有已校准、可审计的造价适配器", statement)
        self.assertNotRegex(statement, r"\d+(?:\.\d+)?\s*(?:万元|元|万)")
        self.assertNotIn("bridge_length", candidate.action)

    def test_cost_request_without_basis_preserves_both_residual_gaps(self):
        result = run_bridge_case("维修费用是多少？")
        self.assertTrue({"cost_basis", "cost_model"} <= unknown_keys(result))
        self.assertIn("不能输出可审计的维修或大修金额", result["reply"])
        self.assertEqual(result["residual"]["final"]["metadata"]["completion_mode"], "conditional")
        self.assertNotRegex(result["reply"], r"\d+(?:\.\d+)?\s*(?:万元|元|万)")

    def test_traceable_budget_does_not_turn_into_a_new_system_cost_estimate(self):
        result = run_bridge_case("请估算维修造价。", extra={
            "批准预算": "合成批准预算15万元。", "来源定位": "synthetic:budget:1",
        })
        self.assertIn("cost_model", unknown_keys(result))
        self.assertNotIn("cost_basis", unknown_keys(result))
        self.assertIn("已提供部分造价依据", result["reply"])
        self.assertIn("没有已校准、可审计的造价适配器", result["reply"])
        self.assertNotRegex(result["reply"], r"\d+(?:\.\d+)?\s*(?:万元|元|万)")
        self.assertEqual(result["residual"]["final"]["metadata"]["completion_mode"], "conditional")


if __name__ == "__main__":
    unittest.main()

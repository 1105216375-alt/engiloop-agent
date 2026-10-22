"""Tests use only explicit synthetic memory inputs, never production data."""
import builtins
from contextlib import ExitStack
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engineering_agent.evidence import InMemoryEvidence, graph_from_relations, safe_delivery
from examples.reverse_verification import synthetic_evidence, synthetic_results, run_demo
from legacy_v7.core.knowledge_graph import BridgeKnowledgeGraph
from legacy_v7.core.repair_report_audit import REPORT_TEXT_WITHHELD


class ReverseVerificationTests(unittest.TestCase):
    def setUp(self):
        self.evidence = synthetic_evidence()
        self.rows = synthetic_results()

    def test_graph_retrieval_uses_original_traversal(self):
        graph = self.evidence.graph
        with patch.object(graph, "find_clauses", wraps=graph.find_clauses) as traverse:
            result = self.evidence.retrieve("主梁", "斜向裂缝")
        traverse.assert_called_once()
        self.assertEqual(result[0]["_source"], "graph")
        self.assertEqual(result[0]["clause"], "第4.1条")

    def test_wrong_object_number_is_not_rescued_by_global_value(self):
        self.assertEqual(self.rows[1]["风险评分"], 30)
        delivery = self.evidence.deliver("主梁的风险评分为30。", self.rows)
        self.assertFalse(delivery["released"])
        self.assertEqual(delivery["text"], REPORT_TEXT_WITHHELD)
        self.assertTrue(any(x["category"] == "number" and x["severity"] == "hard"
                            for x in delivery["issues"]))

    def test_correct_object_number_survives(self):
        delivery = self.evidence.deliver("主梁的风险评分为80。", self.rows)
        self.assertEqual(delivery["status"], "checked")
        self.assertEqual(delivery["text"], "主梁的风险评分为80。")
        self.assertFalse(delivery["engineering_verified"])

    def test_partial_cleanup_never_reintroduces_rejected_sentence(self):
        text = "主梁的风险评分为30。主梁的风险评分为80。"
        delivery = self.evidence.deliver(text, self.rows)
        self.assertTrue(delivery["released"])
        self.assertEqual(delivery["text"], "主梁的风险评分为80。")
        self.assertEqual(delivery["deleted_count"], 1)
        self.assertTrue(delivery["needs_review"])

    def test_absent_clause_is_hard_rejected(self):
        delivery = self.evidence.deliver("依据 JTG 0000-2099 第9.9条，应复核记录。", self.rows[:1])
        self.assertFalse(delivery["released"])
        self.assertTrue(any(x["category"] == "clause" and x["severity"] == "hard"
                            for x in delivery["issues"]))

    def test_existing_but_unrelated_clause_keeps_warning(self):
        delivery = self.evidence.deliver("依据 JTG 0000-2099 第4.2条，应复核记录。", self.rows[:1])
        self.assertTrue(delivery["released"])
        self.assertEqual(delivery["status"], "review_required")
        self.assertTrue(delivery["needs_review"])
        self.assertIn("⚠️[clause]", delivery["text"])

    def test_related_clause_pass_is_only_mechanical(self):
        delivery = self.evidence.deliver("依据 JTG 0000-2099 第4.1条，应复核记录。", self.rows[:1])
        self.assertEqual(delivery["status"], "checked")
        self.assertTrue(delivery["mechanical_checks_only"])
        self.assertFalse(delivery["engineering_verified"])

    def test_free_text_is_not_certified_by_a_retrieval_hit(self):
        # The existing checker does not decide every arbitrary causal claim.
        self.assertTrue(self.evidence.retrieve("主梁", "斜向裂缝"))
        delivery = self.evidence.deliver("该现象仅由材料老化导致。", self.rows[:1])
        self.assertTrue(delivery["released"])
        self.assertFalse(delivery["engineering_verified"])
        self.assertTrue(delivery["mechanical_checks_only"])

    def test_verifier_exception_withholds_and_does_not_echo_error_text(self):
        class Broken:
            def verify(self, *args):
                raise RuntimeError("private draft should never be returned")
        delivery = safe_delivery("original draft", self.rows, verifier=Broken())
        self.assertEqual(delivery["text"], REPORT_TEXT_WITHHELD)
        self.assertEqual(delivery["status"], "verification_failed")
        self.assertNotIn("private draft", str(delivery))
        self.assertNotIn("original draft", str(delivery))

    def test_malformed_verifier_response_withholds(self):
        class Malformed:
            def verify(self, *args):
                return SimpleNamespace(cleaned="unchecked model text")
        delivery = safe_delivery("draft", self.rows, verifier=Malformed())
        self.assertFalse(delivery["released"])
        self.assertEqual(delivery["text"], REPORT_TEXT_WITHHELD)

    def test_stale_verifier_response_cannot_release_unrelated_text(self):
        class Stale:
            def verify(self, *args):
                return SimpleNamespace(original="old draft", cleaned="old output",
                                       deleted_count=0, flagged_count=0, issues=[])
        delivery = safe_delivery("new draft", self.rows, verifier=Stale())
        self.assertFalse(delivery["released"])
        self.assertEqual(delivery["status"], "verification_failed")
        self.assertNotIn("old output", str(delivery))

    def test_empty_evidence_cannot_silently_use_norm_whitelist(self):
        with self.assertRaises(ValueError):
            InMemoryEvidence(clause_index={}, graph=BridgeKnowledgeGraph())

    def test_absent_graph_and_dangling_relations_are_rejected(self):
        index = {"裂缝": [{"reg": "JTG 0000-2099", "clause": "第1条"}]}
        with self.assertRaises(TypeError):
            InMemoryEvidence(clause_index=index, graph=None)
        with self.assertRaises(ValueError):
            graph_from_relations(index, [{"component": "主梁", "disease": "裂缝",
                                          "reg": "JTG 0000-2099", "clause": "第2条"}])

    def test_demo_does_not_read_files_build_private_graph_or_use_network(self):
        denied = AssertionError("Offline example attempted I/O")
        with ExitStack() as stack:
            for obj, name in ((builtins, "open"), (Path, "open"), (Path, "read_text"),
                              (Path, "read_bytes"), (socket, "create_connection"),
                              (socket.socket, "connect"), (BridgeKnowledgeGraph, "build")):
                stack.enter_context(patch.object(obj, name, side_effect=denied))
            result = run_demo()
        self.assertIn("合成", result["notice"])


if __name__ == "__main__":
    unittest.main()

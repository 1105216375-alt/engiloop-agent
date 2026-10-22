"""Public, in-memory adapters around the extracted bridge retrieval/verifier.

The adapter is new; retrieval, graph traversal and claim checking use the
extracted implementations. No constructor calls graph.build(), scans a data
directory, reads application configuration or instantiates a model client.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Iterable, Mapping

from legacy_v7.core.agents import RegulationRAGAgent
from legacy_v7.core.claim_verifier import ClaimVerifier
from legacy_v7.core.knowledge_graph import BridgeKnowledgeGraph
from legacy_v7.core import ontology
from legacy_v7.core.repair_report_audit import (
    REPORT_TEXT_WITHHELD, verified_report_text,
)


def _copy_index(clause_index: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict:
    if not isinstance(clause_index, Mapping):
        raise TypeError("clause_index must be an explicitly supplied mapping")
    copied = {}
    for disease, bucket in clause_index.items():
        if not isinstance(disease, str) or not disease.strip():
            raise ValueError("Each clause bucket needs a nonempty disease key")
        if not isinstance(bucket, (list, tuple)):
            raise TypeError("Each clause bucket must be a list or tuple")
        copied[disease] = []
        for clause in bucket:
            if not isinstance(clause, Mapping):
                raise TypeError("Each clause must be a mapping")
            record = deepcopy(dict(clause))
            for key in ("reg", "clause"):
                if not isinstance(record.get(key), str) or not record[key].strip():
                    raise ValueError(f"A clause needs a nonempty {key} identifier")
            copied[disease].append(record)
    if not any(copied.values()):
        raise ValueError("An empty index cannot claim complete clause verification")
    return copied


def graph_from_relations(
    clause_index: Mapping[str, Iterable[Mapping[str, Any]]],
    relations: Iterable[Mapping[str, Any]],
) -> BridgeKnowledgeGraph:
    """Build only the caller's explicit graph; never infer validity from text.

    Relations contain component, disease, reg, clause, and optional weight.
    Weight is a supplied retrieval ranking value, not a confidence probability.
    This helper neither loads nor verifies the factual authority of the data.
    """
    index = _copy_index(clause_index)
    clauses = {}
    for bucket in index.values():
        for record in bucket:
            key = (record["reg"], record["clause"])
            if key in clauses and clauses[key] != record:
                raise ValueError("Conflicting records share a clause identifier")
            clauses[key] = record
    graph = BridgeKnowledgeGraph()
    for (reg, number), record in clauses.items():
        graph.add_node("Clause", (reg, number), reg=reg, clause_num=number,
                       title=record.get("title", ""),
                       content=record.get("content", record.get("summary", "")))
    for relation in relations:
        component = str(relation.get("component") or "").strip()
        disease = str(relation.get("disease") or "").strip()
        if not component or not disease:
            raise ValueError("A relation needs both component and disease")
        component = ontology.canonicalize_component(component) or component
        disease = ontology.canonicalize_disease(disease) or disease
        key = (relation.get("reg"), relation.get("clause"))
        clause = graph.get_node("Clause", key)
        if clause is None:
            raise ValueError("A graph relation references an absent clause")
        weight = relation.get("weight", 10)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight):
            raise ValueError("Relation weight must be a finite numeric ranking value")
        comp_node = graph.add_node("Component", component)
        dis_node = graph.add_node("Disease", disease)
        graph.add_edge(comp_node, "HAS_DISEASE", dis_node)
        graph.add_edge(comp_node, "GOVERNED_BY", clause, disease=disease, weight=weight)
    return graph


class InMemoryEvidence:
    """Explicit evidence context using the actual bridge RAG and verifier."""

    def __init__(self, *, clause_index: Mapping, graph: BridgeKnowledgeGraph):
        if not isinstance(graph, BridgeKnowledgeGraph):
            raise TypeError("An explicit BridgeKnowledgeGraph instance is required")
        index = _copy_index(clause_index)
        self.graph = graph
        self.rag = RegulationRAGAgent(clause_index=index, graph=graph)
        self.verifier = ClaimVerifier(rag_agent=self.rag)

    def retrieve(self, component: str, disease: str, *, top_k: int = 5) -> list[dict]:
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        component = ontology.canonicalize_component(component) or component
        disease = ontology.canonicalize_disease(disease) or disease
        return self.rag.retrieve(disease, component=component, top_k=top_k)

    def deliver(self, draft: str, results: list[dict], *, original_query: str = "") -> dict:
        return safe_delivery(draft, results, verifier=self.verifier,
                             original_query=original_query)


def safe_delivery(
    draft: str, results: list[dict], *, verifier: Any, original_query: str = "",
) -> dict[str, Any]:
    """Deliver only the production verifier's validated cleaned text.

    This new adapter calls the original verified_report_text contract. A
    failure or malformed response withholds the draft; it never falls back to
    model text. Exception messages are deliberately not echoed because they
    may contain input data or secrets. A soft issue remains visible for review.
    """
    try:
        if not isinstance(draft, str) or not isinstance(results, list):
            raise TypeError("draft/results have invalid types")
        report = verifier.verify(draft, results, original_query)
        if getattr(report, "original", None) != draft:
            raise ValueError("Verifier response is not bound to the current draft")
        delivered = verified_report_text(report)
        withheld = not report.cleaned.strip()
        issues = [{"severity": issue.severity, "category": issue.category,
                   "detail": issue.detail} for issue in report.issues]
        status = ("withheld" if withheld else "review_required" if report.flagged_count
                  else "cleaned" if report.deleted_count else "checked")
        return {
            "text": delivered, "status": status, "released": not withheld,
            "needs_review": bool(withheld or report.flagged_count or report.deleted_count),
            "deleted_count": report.deleted_count, "flagged_count": report.flagged_count,
            "issues": issues, "mechanical_checks_only": True,
            "engineering_verified": False,
        }
    except Exception as error:
        return {
            "text": REPORT_TEXT_WITHHELD, "status": "verification_failed",
            "released": False, "needs_review": True,
            "error_kind": type(error).__name__, "issues": [],
            "mechanical_checks_only": True, "engineering_verified": False,
        }

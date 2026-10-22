"""Euler-style memory: abstract verified derivations, review, then reuse."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, Iterable, List, Optional


_TAU = math.tau
_PHASE_AXIS_WEIGHTS = {
    "task": 0.28,
    "intent": 0.20,
    "temporal_scope": 0.12,
    "component_scope": 0.12,
    "repair_scope": 0.12,
    "evidence_profile": 0.10,
    "goal_pattern": 0.06,
}


def _ordered_text(values: Iterable[Any]) -> List[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _phase_coordinate(axis: str, token: str, *, hard: bool = False) -> Dict[str, Any]:
    """Map one bounded context token to a deterministic Euler coordinate."""
    digest = hashlib.sha256(f"{axis}:{token}".encode("utf-8")).hexdigest()
    theta = (int(digest[:16], 16) / float(0xFFFFFFFFFFFFFFFF)) * _TAU
    radius = float(_PHASE_AXIS_WEIGHTS.get(axis, 0.05))
    return {
        "axis": axis,
        "theta": round(theta, 9),
        "radius": radius,
        "real": round(radius * math.cos(theta), 9),
        "imag": round(radius * math.sin(theta), 9),
        "digest": digest[:16],
        "hard_gate": bool(hard),
    }


def _build_context_phase(
    *,
    task_type: str,
    goal_template: str,
    semantic_frame: Optional[Dict[str, Any]] = None,
    session_context: Optional[Dict[str, Any]] = None,
    evidence_profile: Iterable[str] = (),
) -> Dict[str, Any]:
    """Encode reusable context dimensions as z_k = r_k * exp(i*theta_k)."""
    frame = dict(semantic_frame or {})
    context = dict(session_context or {})
    entities = dict(frame.get("entities") or {})
    constraints = dict(frame.get("constraints") or {})

    intents = _ordered_text(frame.get("intents") or [task_type])
    years = _ordered_text(entities.get("years") or context.get("years") or [])
    components = _ordered_text(
        (constraints.get("include_components") or [])
        or entities.get("components")
        or context.get("components")
        or []
    )
    statuses = _ordered_text(
        mention.get("status")
        for mention in (frame.get("defect_mentions") or [])
        if isinstance(mention, dict) and mention.get("status")
    )
    repair_scope = "exclude_repaired" if constraints.get("exclude_repaired") else \
        ("+".join(statuses) if statuses else "current_state")
    temporal_scope = \
        ("none" if not years else (f"single:{years[0]}" if len(years) == 1 else "multi:" + ",".join(years)))
    component_scope = "+".join(components) if components else "all_components"
    tokens = {
        "task": task_type,
        "intent": "+".join(intents) if intents else task_type,
        "temporal_scope": temporal_scope,
        "component_scope": component_scope,
        "repair_scope": repair_scope,
        "evidence_profile": "+".join(_ordered_text(evidence_profile)) or "no_evidence_profile",
        "goal_pattern": goal_template or task_type,
    }
    axes = [
        _phase_coordinate(
            axis,
            token,
            hard=(axis == "task" or (axis == "repair_scope" and token != "current_state")),
        )
        for axis, token in tokens.items()
    ]
    signature = hashlib.sha256(
        json.dumps(
            {axis: coordinate["digest"] for axis, coordinate in zip(tokens, axes)},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "euler-context.v2",
        "formula": "z_k = r_k * e^(i*theta_k)",
        "coordinate_system": "deterministic_multi_axis_context",
        "axes": axes,
        "axis_count": len(axes),
        "signature": signature[:24],
        "privacy": "generalized_context_only",
    }


def build_context_phase(
    *,
    task_type: str,
    goal_template: str,
    semantic_frame: Optional[Dict[str, Any]] = None,
    session_context: Optional[Dict[str, Any]] = None,
    evidence_profile: Iterable[str] = (),
) -> Dict[str, Any]:
    """Public encoder for session context that does not create reusable memory."""
    return _build_context_phase(
        task_type=task_type,
        goal_template=goal_template,
        semantic_frame=semantic_frame,
        session_context=session_context,
        evidence_profile=evidence_profile,
    )


def _phase_alignment(stored: Dict[str, Any], query: Dict[str, Any]) -> Dict[str, Any]:
    stored_axes = {
        str(row.get("axis")): row for row in (stored.get("axes") or []) if isinstance(row, dict)
    }
    query_axes = {
        str(row.get("axis")): row for row in (query.get("axes") or []) if isinstance(row, dict)
    }
    total_weight = 0.0
    weighted_score = 0.0
    matched: List[str] = []
    mismatched: List[str] = []
    hard_mismatches: List[str] = []
    axis_scores: Dict[str, float] = {}
    for axis, stored_axis in stored_axes.items():
        query_axis = query_axes.get(axis)
        if not query_axis:
            continue
        weight = float(_PHASE_AXIS_WEIGHTS.get(axis, stored_axis.get("radius") or 0.05))
        total_weight += weight
        exact = stored_axis.get("digest") == query_axis.get("digest")
        if exact:
            score = 1.0
            matched.append(axis)
        else:
            distance = abs(float(stored_axis.get("theta") or 0) - float(query_axis.get("theta") or 0))
            distance = min(distance, _TAU - distance)
            # A non-identical phase can only break a tie; it cannot pass a hard gate.
            score = max(0.0, (math.cos(distance) + 1.0) / 2.0) * 0.08
            mismatched.append(axis)
            if stored_axis.get("hard_gate"):
                hard_mismatches.append(axis)
        axis_scores[axis] = round(score, 4)
        weighted_score += weight * score
    alignment = weighted_score / total_weight if total_weight else 0.0
    return {
        "alignment": round(alignment, 4),
        "matched_axes": matched,
        "mismatched_axes": mismatched,
        "hard_mismatches": hard_mismatches,
        "axis_scores": axis_scores,
        "matched_axis_count": len(matched),
        "axis_count": len(stored_axes),
    }


class EulerMemoryExtractor:
    """
    Extract a reusable reasoning pattern without storing a bridge conclusion.

    The design mirrors FormulaExtractor:
      verified derivation -> generalized slots -> applicability conditions
      -> pending review -> approved pattern -> re-verification on reuse.
    """

    def __init__(self, state_store: Any) -> None:
        self.state_store = state_store

    def extract_pending(
        self,
        *,
        question: str,
        final_state: Any,
        run_summary: Any,
        verifier_names: Iterable[str],
        bridge_name: str = "",
        semantic_frame: Optional[Dict[str, Any]] = None,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not final_state or not getattr(final_state, "is_solved", lambda: False)():
            return None

        task_type = self.classify_task(question)
        generalized_goal = self._generalize_text(question, bridge_name)
        step_templates = self._step_templates(run_summary)
        if not step_templates:
            return None

        unknown_keys = sorted({
            str(row.get("key") or "unknown")
            for row in (getattr(final_state, "unknowns", None) or [])
            if isinstance(row, dict)
        })
        constraint_templates = sorted({
            self._generalize_text(str(getattr(row, "description", "")), bridge_name)
            for row in (getattr(final_state, "constraints", None) or [])
            if getattr(row, "description", "")
        })
        evidence_refs = self._evidence_refs(final_state)
        required_evidence = self._required_evidence(task_type)
        context_phase = _build_context_phase(
            task_type=task_type,
            goal_template=generalized_goal,
            semantic_frame=semantic_frame,
            session_context=session_context,
            evidence_profile=required_evidence,
        )
        payload = {
            "schema_version": "euler-memory.v2",
            "name": f"{task_type} verified reasoning pattern",
            "memory_type": "reasoning_pattern",
            "task_type": task_type,
            "goal_template": generalized_goal,
            "slot_schema": self._slot_schema(task_type),
            "applicable_conditions": {
                "required_evidence": required_evidence,
                "hard_constraints": constraint_templates,
                "preserved_unknown_types": unknown_keys,
                "entity_policy": "facts_same_bridge_only",
                "cross_bridge_policy": "approved_pattern_only",
            },
            "derivation_steps": step_templates,
            "verified_by": sorted(set(verifier_names)),
            "source_run_id": str(getattr(run_summary, "run_id", "")),
            "source_evidence_refs": evidence_refs,
            "context_phase": context_phase,
            "review_status": "pending",
            "reuse_policy": {
                "instantiate_slots": True,
                "must_reverify": True,
                "may_copy_final_answer": False,
                "may_copy_bridge_facts": False,
            },
        }
        signature_core = {
            "schema_version": payload["schema_version"],
            "task_type": task_type,
            "goal_template": generalized_goal,
            "required_evidence": required_evidence,
            "derivation_steps": step_templates,
            "verified_by": payload["verified_by"],
            "context_phase_signature": context_phase["signature"],
        }
        pattern_hash = hashlib.sha256(
            json.dumps(signature_core, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        confidence = self._confidence(final_state, step_templates, evidence_refs)
        candidate = {
            "pattern_hash": pattern_hash,
            "memory_type": "reasoning_pattern",
            "task_type": task_type,
            "scope_type": "pattern",
            "scope_key": "bridge_maintenance",
            "confidence": confidence,
            "payload": payload,
        }
        return self.state_store.upsert_memory_candidate(candidate)

    @staticmethod
    def classify_task(question: str) -> str:
        text = (question or "").lower()
        if any(word in text for word in ("费用", "成本", "预算", "多少钱", "造价")):
            return "repair_cost"
        if any(word in text for word in ("寿命", "还能用", "服役")):
            return "remaining_life"
        if any(word in text for word in ("大修", "值不值得", "值不值", "是否需要大修")):
            return "major_repair_decision"
        if any(word in text for word in ("优先", "顺序", "先修", "重点")):
            return "repair_priority"
        if any(word in text for word in ("风险", "安全", "危险", "严重")):
            return "risk_assessment"
        return "bridge_analysis"

    @staticmethod
    def _generalize_text(text: str, bridge_name: str) -> str:
        result = text or ""
        if bridge_name:
            result = result.replace(bridge_name, "{bridge_name}")
        result = re.sub(r"20\d{2}", "{year}", result)
        result = re.sub(r"(?<![A-Za-z])\d+(?:\.\d+)?", "{number}", result)
        return re.sub(r"\s+", " ", result).strip()

    @staticmethod
    def _slot_schema(task_type: str) -> List[Dict[str, Any]]:
        slots = [
            {"name": "bridge_name", "type": "entity", "required": True},
            {"name": "evidence_contract", "type": "bridge_evidence.v1", "required": True},
        ]
        if task_type == "remaining_life":
            slots.extend([
                {"name": "design_life", "type": "number", "required": True},
                {"name": "service_years", "type": "number", "required": True},
            ])
        if task_type == "repair_cost":
            slots.append({"name": "cost_basis", "type": "cost_evidence", "required": True})
        return slots

    @staticmethod
    def _required_evidence(task_type: str) -> List[str]:
        common = ["bridge_identity", "report_provenance", "defect_facts"]
        extras = {
            "major_repair_decision": ["dr_or_bci", "load_capacity", "repair_records"],
            "remaining_life": ["design_life", "service_years", "deterioration_or_capacity"],
            "repair_cost": ["quantities", "treatment_plan", "regional_rates"],
            "repair_priority": ["severity", "component", "location"],
            "risk_assessment": ["dr_or_bci", "critical_defects"],
        }
        return common + extras.get(task_type, [])

    @staticmethod
    def _step_templates(run_summary: Any) -> List[Dict[str, Any]]:
        templates: List[Dict[str, Any]] = []
        for step in (getattr(run_summary, "steps", None) or []):
            candidates = getattr(step, "candidates", None) or []
            decisions = getattr(step, "decisions", None) or []
            # Public extraction fix: ACCEPT is not the final commit result.
            # RuntimeController appends rollback after a faulty state update.
            if any(isinstance(row, dict) and row.get("rollback") for row in decisions):
                continue
            accepted_actions = []
            accepted_count = 0
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                candidate_id = candidate.get("candidate_id")
                if not candidate_id:
                    # Old summaries without identity-bound decisions remain audit
                    # data, not evidence that a reusable step was accepted.
                    continue
                aligned = [
                    row for row in decisions
                    if isinstance(row, dict)
                    and row.get("candidate_id") == candidate_id
                ]
                if len(aligned) != 1:
                    continue
                decision = aligned[0]
                if (decision.get("decision") or decision.get("action")) != "accept":
                    continue
                action = candidate.get("action") or {}
                action_type = action.get("type") or action.get("rule_id")
                if action_type:
                    accepted_actions.append(str(action_type))
                    accepted_count += 1
            if not accepted_actions:
                continue
            templates.append({
                "step_index": int(getattr(step, "step_index", len(templates) + 1)),
                "proposer": str(getattr(step, "winning_proposer", "")),
                "action_types": sorted(set(accepted_actions)),
                "accepted_candidates": accepted_count,
                "rejected_candidates": int(getattr(step, "candidates_rejected", 0) or 0),
            })
        return templates

    @staticmethod
    def _evidence_refs(final_state: Any) -> List[str]:
        refs = []
        for fact in (getattr(final_state, "verified_facts", None) or []):
            for ref in (getattr(fact, "evidence", None) or []):
                ref = str(ref)
                if ref and ref not in refs:
                    refs.append(ref)
                if len(refs) >= 64:
                    return refs
        return refs

    @staticmethod
    def _confidence(final_state: Any, steps: List[Dict[str, Any]], refs: List[str]) -> float:
        verified_count = len(getattr(final_state, "verified_facts", None) or [])
        score = 0.55
        score += min(verified_count, 20) * 0.01
        score += min(len(refs), 10) * 0.01
        score += min(len(steps), 5) * 0.02
        if getattr(final_state, "unknowns", None):
            score -= 0.05
        return round(min(max(score, 0.5), 0.95), 3)


class EulerMemoryMatcher:
    """Retrieve approved patterns with hard scope gates before soft scoring."""

    def __init__(self, state_store: Any) -> None:
        self.state_store = state_store

    def match(
        self,
        *,
        question: str,
        bridge_name: str,
        available_evidence: Iterable[str],
        semantic_frame: Optional[Dict[str, Any]] = None,
        session_context: Optional[Dict[str, Any]] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        task_type = EulerMemoryExtractor.classify_task(question)
        available = set(available_evidence)
        generalized_goal = EulerMemoryExtractor._generalize_text(question, bridge_name)
        query_phase = _build_context_phase(
            task_type=task_type,
            goal_template=generalized_goal,
            semantic_frame=semantic_frame,
            session_context=session_context,
            evidence_profile=available,
        )
        rows = self.state_store.list_approved_memories(
            task_type=task_type,
            scope_type="pattern",
            scope_key="bridge_maintenance",
            limit=50,
        )
        scored = []
        for row in rows:
            payload = row.get("payload") or {}
            policy = payload.get("reuse_policy") or {}
            if not policy.get("must_reverify") or policy.get("may_copy_final_answer"):
                continue
            conditions = payload.get("applicable_conditions") or {}
            required = set(conditions.get("required_evidence") or [])
            coverage = len(required & available) / max(len(required), 1)
            stored_phase = payload.get("context_phase") or {}
            if stored_phase:
                phase_match = _phase_alignment(stored_phase, query_phase)
                phase_alignment = float(phase_match["alignment"])
                precision_gate = not phase_match["hard_mismatches"] and phase_alignment >= 0.58
                score = 0.45 * coverage + 0.55 * phase_alignment
                phase_status = "matched" if precision_gate else "context_mismatch"
            else:
                # Approved v1 memories remain readable but are clearly marked unphased.
                phase_match = {
                    "alignment": 0.0,
                    "matched_axes": [],
                    "mismatched_axes": [],
                    "hard_mismatches": [],
                    "axis_scores": {},
                    "matched_axis_count": 0,
                    "axis_count": 0,
                }
                precision_gate = True
                score = 0.55 + 0.45 * coverage
                phase_status = "legacy_unphased"
            scored.append({
                **row,
                "match_score": round(score, 4),
                "bridge_name": bridge_name,
                "missing_evidence": sorted(required - available),
                "can_apply": required.issubset(available) and precision_gate,
                "must_reverify": True,
                "phase_status": phase_status,
                "phase_match": phase_match,
                "query_phase_signature": query_phase["signature"],
            })
        scored.sort(key=lambda item: (item["can_apply"], item["match_score"]), reverse=True)
        return scored[:max(1, min(limit, 20))]

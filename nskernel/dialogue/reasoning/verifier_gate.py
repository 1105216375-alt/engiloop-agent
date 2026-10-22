"""
VerifierGate / MainStateUpdater / ResidualRebuilder · 推理闭环的三大件
======================================================================

VerifierGate:
  聚合多个 checker 的结果,产生 VerifierResult,并综合判断 Decision(四级动作)

MainStateUpdater:
  candidate 经过 VerifierGate.accept 后,把 candidate 的产出写入
  verified_facts,生成 StateTransition 记录

ResidualRebuilder:
  基于 StateTransition 重建下一个 ResidualState
  (当前版本是"evolve-based",未来可以替换为更复杂的重建逻辑)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol
import uuid

from .residual_state import (
    ResidualState, ResidualStatus, VerifiedFact, UnresolvedItem,
)
from .candidate_and_transition import (
    CandidateStep, StateTransition,
)
from .decision import (
    ReasoningDecision, VerifierResult, DecisionAction, ReasonCode,
)


# ══════════════════════════════════════════════════════════════
# Checker 协议(鸭子类型)
# ══════════════════════════════════════════════════════════════
class CheckerProtocol(Protocol):
    """
    领域或通用 checker 实现此协议
    接受:问题上下文 + 候选 → 返回 (ok, reason_codes, details)
    """
    def check(
        self,
        candidate: CandidateStep,
        residual_state: ResidualState,
        context: Optional[Dict[str, Any]] = None,
    ) -> "tuple[bool, List[str], Dict[str, Any]]":
        ...


# ══════════════════════════════════════════════════════════════
# 内置的通用 Checker(精简版,领域可以自己加更多)
# ══════════════════════════════════════════════════════════════
class ResidualReductionChecker:
    """
    ★ 核心 checker - 判断候选是否真的缩小 residual
    """
    def check(self, candidate: CandidateStep, residual_state: ResidualState,
              context=None):
        effect = candidate.expected_effect or {}
        if not effect.get("type"):
            return False, [ReasonCode.MISSING_EXPECTED_EFFECT], {}

        # 接受的 effect type(可扩展)
        accepted_types = {
            "resolve_unresolved",      # 消除某未解决项
            "verify_fact",             # 新增已验证事实
            "solve_goal",              # 解决某 goal
            "narrow_search_scope",     # 缩小搜索空间(全局)
            "reduce_symbolic_burden",  # 减轻符号负担
            "reconstruct_goal_view",   # 重构目标视图
        }
        if effect["type"] not in accepted_types:
            return False, [ReasonCode.RESIDUAL_NOT_REDUCED], {
                "effect_type": effect["type"]
            }

        effect_type = effect["type"]
        produced_facts = [
            item for item in (candidate.produced_items or [])
            if isinstance(item, dict)
            and item.get("type") == "fact"
            and str(item.get("statement") or "").strip()
        ]
        if effect_type == "resolve_unresolved":
            target = effect.get("target_unresolved_item")
            known = {item.item_id for item in residual_state.unresolved_items}
            if not target or target not in known:
                return False, [ReasonCode.RESIDUAL_NOT_REDUCED], {
                    "reason": "target_unresolved_item_not_found",
                    "target": target,
                }
            if not produced_facts:
                return False, [ReasonCode.RESIDUAL_NOT_REDUCED], {
                    "reason": "resolved_without_verified_output",
                    "target": target,
                }
        elif effect_type == "verify_fact" and not produced_facts:
            return False, [ReasonCode.RESIDUAL_NOT_REDUCED], {
                "reason": "verify_fact_without_fact",
            }
        elif effect_type == "solve_goal":
            target = effect.get("target_goal")
            known = {goal.goal_id for goal in residual_state.goals if not goal.solved}
            if not target or target not in known:
                return False, [ReasonCode.RESIDUAL_NOT_REDUCED], {
                    "reason": "target_goal_not_found_or_already_solved",
                    "target": target,
                }
            if residual_state.unresolved_count() > 0:
                return False, [ReasonCode.RESIDUAL_NOT_REDUCED], {
                    "reason": "goal_solved_with_open_obligations",
                    "remaining_unresolved": residual_state.unresolved_count(),
                }
            if not produced_facts:
                return False, [ReasonCode.RESIDUAL_NOT_REDUCED], {
                    "reason": "goal_solved_without_conclusion",
                }
        return True, [], {}


class SafetyChecker:
    """通用安全检查(调用危险函数/越权/注入残留)"""
    DANGEROUS_KEYWORDS = ["__import__", "eval(", "exec(", "os.system", "subprocess"]

    def check(self, candidate, residual_state, context=None):
        action_str = str(candidate.action)
        hits = [k for k in self.DANGEROUS_KEYWORDS if k in action_str]
        if hits:
            return False, [ReasonCode.UNSAFE_CANDIDATE], {"hits": hits}
        return True, [], {}


class TraceabilityChecker:
    """candidate 必须有 justification 或 provenance"""
    def check(self, candidate, residual_state, context=None):
        if not candidate.justification and not candidate.provenance:
            return False, [ReasonCode.NOT_TRACEABLE], {}
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# VerifierGate
# ══════════════════════════════════════════════════════════════
class VerifierGate:
    """
    总裁决闸门

    使用:
      gate = VerifierGate([checker1, checker2, ...])
      result, decision = gate.verify(candidate, residual_state)
    """

    # checker 分类,决定严重程度
    # hard: 失败 → 可能 interrupt
    # soft: 失败 → defer 或 reject
    # trace: 失败 → defer

    def __init__(
        self,
        hard_checkers: Optional[List[CheckerProtocol]] = None,
        soft_checkers: Optional[List[CheckerProtocol]] = None,
    ):
        self._hard = list(hard_checkers or [SafetyChecker()])
        self._soft = list(soft_checkers or [
            ResidualReductionChecker(),
            TraceabilityChecker(),
        ])

    def add_hard_checker(self, c: CheckerProtocol) -> "VerifierGate":
        self._hard.append(c)
        return self

    def add_soft_checker(self, c: CheckerProtocol) -> "VerifierGate":
        self._soft.append(c)
        return self

    def verify(
        self,
        candidate: CandidateStep,
        residual_state: ResidualState,
        context: Optional[Dict[str, Any]] = None,
    ) -> "tuple[VerifierResult, ReasoningDecision]":
        """
        串起所有 checker,产出 VerifierResult + Decision
        """
        result = VerifierResult(candidate_id=candidate.candidate_id)
        all_codes: List[str] = []

        # 跑所有 hard checker
        hard_fail = False
        for ck in self._hard:
            try:
                ok, codes, det = ck.check(candidate, residual_state, context)
            except Exception as e:
                ok, codes, det = False, [f"checker_exception:{type(ck).__name__}"], {"err": str(e)}
            if not ok:
                hard_fail = True
                result.details[type(ck).__name__] = det
            all_codes.extend(codes)
            # 把 hard 失败反映到 result 字段
            if not ok:
                name = type(ck).__name__.lower()
                if "safety" in name: result.safe = False
                if "consistency" in name: result.consistency_ok = False
                if "library" in name: result.library_compliance_ok = False
                if "operation" in name: result.operation_rule_ok = False
                if "symbolic" in name: result.symbolic_equivalence_ok = False
                if "applicab" in name: result.applicable = False
                if "solution" in name: result.solution_ok = False
                if "soundness" in name: result.soundness_ok = False

        # 跑 soft checker
        soft_fail = False
        for ck in self._soft:
            try:
                ok, codes, det = ck.check(candidate, residual_state, context)
            except Exception as e:
                ok, codes, det = False, [f"checker_exception:{type(ck).__name__}"], {"err": str(e)}
            if not ok:
                soft_fail = True
                result.details[type(ck).__name__] = det
            all_codes.extend(codes)
            if not ok:
                name = type(ck).__name__.lower()
                if "residual" in name: result.residual_reduction_ok = False
                if "traceab" in name: result.traceable = False
                if "execut" in name: result.executable = False
                if "numerical" in name: result.numerical_sampling_ok = False
                if "dedup" in name: result.deduplication_ok = False

        result.reason_codes = list(set(all_codes))

        # 综合决策
        decision = self._make_decision(candidate, result, hard_fail, soft_fail)
        return result, decision

    # -----------------------------------------------------------------
    # 决策逻辑(对齐 Reasoner 的 _make_decision)
    # -----------------------------------------------------------------
    def _make_decision(
        self, candidate: CandidateStep, result: VerifierResult,
        hard_fail: bool, soft_fail: bool,
    ) -> ReasoningDecision:
        codes = result.reason_codes

        # 1) 不安全 → 强打断
        if not result.safe:
            return ReasoningDecision(
                candidate_id=candidate.candidate_id,
                action=DecisionAction.INTERRUPT,
                reason_codes=codes,
                message="候选存在安全风险,打断",
                follow_up={"suggestion": "halt_current_chain"},
            )

        # 2) 库内合法性失败 → interrupt
        if not result.library_compliance_ok:
            return ReasoningDecision(
                candidate_id=candidate.candidate_id,
                action=DecisionAction.INTERRUPT,
                reason_codes=codes,
                message="候选违反库内合法性",
                follow_up={"suggestion": "switch_or_restrict_proposer"},
            )

        # 3) 符号等价失败 → reject(可重试)
        if not result.symbolic_equivalence_ok:
            return ReasoningDecision(
                candidate_id=candidate.candidate_id,
                action=DecisionAction.REJECT,
                reason_codes=codes,
                message="候选的变换非等价",
                follow_up={"suggestion": "propose_correct_rewrite"},
            )

        # 4) 其他 hard 失败 → reject
        if hard_fail:
            return ReasoningDecision(
                candidate_id=candidate.candidate_id,
                action=DecisionAction.REJECT,
                reason_codes=codes,
                message="候选未通过硬检查",
            )

        # 5) 全部硬检查通过 + 软检查也通过 → accept
        if not soft_fail:
            return ReasoningDecision(
                candidate_id=candidate.candidate_id,
                action=DecisionAction.ACCEPT,
                reason_codes=codes,
                message="候选通过所有检查",
            )

        # 6) 软检查失败(可追溯性/执行性不足) → defer
        if not result.traceable or not result.executable:
            return ReasoningDecision(
                candidate_id=candidate.candidate_id,
                action=DecisionAction.DEFER,
                reason_codes=codes,
                message="候选暂缓,等更多依据",
                follow_up={"suggestion": "request_more_support"},
            )

        # 7) 其他软失败 → reject
        return ReasoningDecision(
            candidate_id=candidate.candidate_id,
            action=DecisionAction.REJECT,
            reason_codes=codes,
            message="候选未通过软检查",
        )


# ══════════════════════════════════════════════════════════════
# MainStateUpdater
# ══════════════════════════════════════════════════════════════
class MainStateUpdater:
    """
    把通过的 candidate 的效果写入 residual state,产出 StateTransition
    """

    def apply(
        self,
        candidate: CandidateStep,
        decision: ReasoningDecision,
        residual_state: ResidualState,
    ) -> "tuple[ResidualState, StateTransition]":
        """
        Args:
          candidate: 已通过的候选(decision.action==ACCEPT)
          decision:  对应的决策
          residual_state: 当前残差

        Returns:
          (new_residual_state, transition)
        """
        if decision.action != DecisionAction.ACCEPT:
            # 不是 accept 不应该走到这里
            raise ValueError(f"只能 apply accept,收到 {decision.action}")

        # 从 candidate 抽取应当添加的 facts
        new_facts: List[VerifiedFact] = []
        for prod in candidate.produced_items:
            if prod.get("type") == "fact":
                new_facts.append(VerifiedFact(
                    fact_id=f"F_{uuid.uuid4().hex[:6]}",
                    statement=prod.get("statement", ""),
                    source=candidate.source.value,
                    evidence=candidate.input_refs,
                    confidence=candidate.confidence,
                ))

        # 解决掉的 unresolved_items(来自 expected_effect)
        removed_ids = []
        effect = candidate.expected_effect or {}
        if effect.get("type") == "resolve_unresolved":
            target = effect.get("target_unresolved_item")
            if target:
                removed_ids.append(target)

        # 解决掉的 goals
        solved_goals = []
        if effect.get("type") == "solve_goal":
            target_goal = effect.get("target_goal")
            if target_goal:
                solved_goals.append(target_goal)

        # evolve
        new_state = residual_state.evolve(
            added_facts=new_facts,
            removed_unresolved_ids=removed_ids,
            solved_goals=solved_goals,
        )

        # 生成 transition
        transition = StateTransition(
            from_residual_id=residual_state.residual_id,
            to_residual_id=new_state.residual_id,
            candidate_id=candidate.candidate_id,
            decision_action=decision.action.value,
            added_verified_facts=[{"fact_id": f.fact_id,
                                    "statement": f.statement}
                                   for f in new_facts],
            removed_unresolved_items=removed_ids,
            solved_goals=solved_goals,
            summary=candidate.justification or "",
        )

        return new_state, transition


# ══════════════════════════════════════════════════════════════
# ResidualRebuilder
# ══════════════════════════════════════════════════════════════
class ResidualRebuilder:
    """
    在 StateTransition 之后,做"残差重建"的后处理

    目前:MVP 直接透传 MainStateUpdater 的结果
    未来:可以在这里加"推导新 unresolved_items 的传播"
    """

    def rebuild(
        self,
        residual_state: ResidualState,
        transition: StateTransition,
    ) -> ResidualState:
        # 检查是否全部 goals 解决，并保留“还有未知证据”的条件性完成状态。
        if residual_state.goals and all(g.solved for g in residual_state.goals):
            residual_state.status = ResidualStatus.SOLVED
            residual_state.metadata["completion_mode"] = (
                "conditional" if residual_state.unknowns else "complete"
            )
            residual_state.metadata["remaining_unknowns"] = len(residual_state.unknowns)
        return residual_state

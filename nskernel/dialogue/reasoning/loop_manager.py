"""
LoopManager · 推理主循环管理器
================================

驱动 propose → verify → update → rebuild 的核心闭环

每一步:
  1. ProposalScheduler 生成 candidate 列表(从多 proposer)
  2. 对每个 candidate 依次跑 VerifierGate
  3. 遇到第一个 ACCEPT 的 → MainStateUpdater.apply → ResidualRebuilder.rebuild
  4. 都 reject/defer/interrupt 的 → 根据策略决定下一步
  5. 返回 (new_residual, new_working, step_summary)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol
import uuid

from .residual_state import ResidualState, ResidualStatus
from .candidate_and_transition import CandidateStep, StateTransition
from .decision import ReasoningDecision, DecisionAction
from .verifier_gate import (
    VerifierGate, MainStateUpdater, ResidualRebuilder,
)


class ProposerProtocol(Protocol):
    """Proposer 协议"""
    name: str
    priority: int

    def propose(
        self,
        residual_state: ResidualState,
        context: Dict[str, Any],
    ) -> List[CandidateStep]:
        ...


@dataclass
class StepSummary:
    """单步推理的摘要"""
    step_index: int
    from_residual_id: str
    to_residual_id: str

    # 候选统计
    candidates_proposed: int = 0
    candidates_accepted: int = 0
    candidates_rejected: int = 0
    candidates_deferred: int = 0
    candidates_interrupted: int = 0

    # 这一步使用的 proposer(第一个产生 accept 的)
    winning_proposer: Optional[str] = None

    # 是否有推进(删项/验证事实/解决目标)
    is_progressive: bool = False

    # 主事件(可选记录所有候选和决策)
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)

    # 如果被 interrupt,记录建议的升级动作
    escalation_follow_up: Optional[Dict[str, Any]] = None


@dataclass
class LoopStats:
    """主循环的累计统计(给 EscalationPolicy 用)"""
    total_steps: int = 0
    consecutive_empty_candidates: int = 0
    consecutive_rejects: int = 0
    consecutive_interrupts: int = 0
    no_progress_rounds: int = 0

    def tick_step(self, step: StepSummary) -> None:
        self.total_steps += 1
        if step.candidates_proposed == 0:
            self.consecutive_empty_candidates += 1
        else:
            self.consecutive_empty_candidates = 0
        if step.candidates_accepted > 0:
            self.consecutive_rejects = 0
            self.consecutive_interrupts = 0
        elif step.candidates_interrupted > 0:
            self.consecutive_interrupts += 1
        else:
            self.consecutive_rejects += 1
        if step.is_progressive:
            self.no_progress_rounds = 0
        else:
            self.no_progress_rounds += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_steps": self.total_steps,
            "consecutive_empty_candidates": self.consecutive_empty_candidates,
            "consecutive_rejects": self.consecutive_rejects,
            "consecutive_interrupts": self.consecutive_interrupts,
            "no_progress_rounds": self.no_progress_rounds,
        }


class LoopManager:
    """推理主循环"""

    def __init__(
        self,
        proposers: List[ProposerProtocol],
        verifier_gate: VerifierGate,
        main_state_updater: Optional[MainStateUpdater] = None,
        residual_rebuilder: Optional[ResidualRebuilder] = None,
    ):
        # 按优先级排序(小的在前)
        self.proposers = sorted(proposers, key=lambda p: p.priority)
        self.verifier_gate = verifier_gate
        self.main_state_updater = main_state_updater or MainStateUpdater()
        self.residual_rebuilder = residual_rebuilder or ResidualRebuilder()
        self.stats = LoopStats()

    def run_one_step(
        self,
        step_index: int,
        residual_state: ResidualState,
        context: Optional[Dict[str, Any]] = None,
    ) -> "tuple[ResidualState, StepSummary, Optional[StateTransition]]":
        """
        跑一步闭环

        Returns:
          (new_residual_state, step_summary, transition_or_none)
        """
        context = context or {}
        step = StepSummary(
            step_index=step_index,
            from_residual_id=residual_state.residual_id,
            to_residual_id=residual_state.residual_id,  # 默认不变
        )

        # 1) 按 proposer 优先级依次尝试
        all_candidates: List["tuple[CandidateStep, str]"] = []
        for proposer in self.proposers:
            try:
                cands = proposer.propose(residual_state, context)
            except Exception as e:
                cands = []
                step.decisions.append({
                    "proposer_error": f"{proposer.name}: {e}",
                })
            for c in cands or []:
                all_candidates.append((c, proposer.name))

        step.candidates_proposed = len(all_candidates)

        # 2) 对每个候选过 verifier,取第一个 accept
        transition = None
        new_residual = residual_state
        for cand, proposer_name in all_candidates:
            result, decision = self.verifier_gate.verify(
                cand, residual_state, context=context,
            )
            step.candidates.append(cand.to_dict())
            step.decisions.append(decision.to_dict())

            if decision.is_accept():
                # 应用 + 重建
                step.candidates_accepted += 1
                step.winning_proposer = proposer_name
                new_residual, transition = self.main_state_updater.apply(
                    cand, decision, residual_state,
                )
                new_residual = self.residual_rebuilder.rebuild(new_residual, transition)
                step.to_residual_id = new_residual.residual_id
                step.is_progressive = transition.is_progressive()
                break
            elif decision.is_interrupt():
                step.candidates_interrupted += 1
                step.escalation_follow_up = decision.follow_up
                # 不再尝试后续 candidate
                break
            elif decision.is_defer():
                step.candidates_deferred += 1
                # 继续试下一个
            else:  # reject
                step.candidates_rejected += 1
                # 继续试下一个

        # 3) 更新 stats
        self.stats.tick_step(step)

        return new_residual, step, transition

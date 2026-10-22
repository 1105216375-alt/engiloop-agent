"""
RuntimeController · 总控
=========================

聚合 LoopManager + StopPolicy + EscalationPolicy + BudgetManager
驱动完整的多步推理会话:

  while not stop:
      loop_manager.run_one_step(...)
      stop_policy.should_stop(...)
      escalation_policy.suggest(...)
      (optional) rollback_handler.safe_apply(...)
  return run_summary
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from nskernel.dialogue.reasoning import (
    ResidualState, LoopManager, StepSummary, StateTransition,
)
from .controllers import (
    EscalationPolicy, RollbackHandler, BudgetManager, StopPolicy,
)


@dataclass
class RunSummary:
    """一次完整推理运行的摘要"""
    run_id: str = ""
    initial_residual_id: str = ""
    final_residual_id: str = ""
    final_status: str = ""

    step_count: int = 0
    stop_reason: str = ""

    # 记录每步
    steps: List[StepSummary] = field(default_factory=list)

    # 累积的状态转移
    transitions: List[StateTransition] = field(default_factory=list)

    # 升级建议(如触发)
    escalations: List[Dict[str, Any]] = field(default_factory=list)

    # 回滚次数
    rollbacks: int = 0

    # 预算报告
    budget_report: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "initial_residual_id": self.initial_residual_id,
            "final_residual_id": self.final_residual_id,
            "final_status": self.final_status,
            "step_count": self.step_count,
            "stop_reason": self.stop_reason,
            "rollbacks": self.rollbacks,
            "escalations": self.escalations,
            "budget_report": self.budget_report,
            "steps_count_summary": [
                {"step": s.step_index, "accepted": s.candidates_accepted,
                 "progressive": s.is_progressive}
                for s in self.steps
            ],
        }


class RuntimeController:
    """
    总控 - 驱动多步推理会话直到停机
    """

    def __init__(
        self,
        loop_manager: LoopManager,
        stop_policy: Optional[StopPolicy] = None,
        escalation_policy: Optional[EscalationPolicy] = None,
        budget_manager: Optional[BudgetManager] = None,
        rollback_handler: Optional[RollbackHandler] = None,
    ):
        self.loop_manager = loop_manager
        self.stop_policy = stop_policy or StopPolicy()
        self.escalation_policy = escalation_policy or EscalationPolicy()
        self.budget_manager = budget_manager or BudgetManager()
        self.rollback_handler = rollback_handler or RollbackHandler()

    def run(
        self,
        initial_residual: ResidualState,
        context: Optional[Dict[str, Any]] = None,
    ) -> "tuple[ResidualState, RunSummary]":
        """
        跑完整会话,直到停机

        Returns: (最终 residual state, 完整 run summary)
        """
        import uuid

        summary = RunSummary(
            run_id=f"RUN_{uuid.uuid4().hex[:8]}",
            initial_residual_id=initial_residual.residual_id,
        )

        self.budget_manager.reset()
        # 每次 run 开始:重置 LoopManager 的累计统计,避免跨次污染
        # (之前的连续失败计数不应影响本次新推理)
        from nskernel.dialogue.reasoning import LoopStats
        self.loop_manager.stats = LoopStats()

        current_state = initial_residual

        while True:
            # 检查是否该停
            should_stop, reason = self.stop_policy.should_stop(
                current_state, self.budget_manager, self.loop_manager.stats,
            )
            if should_stop:
                summary.stop_reason = reason
                break

            # 跑一步
            self.budget_manager.step()
            snapshot_before = current_state

            new_state, step, transition = self.loop_manager.run_one_step(
                step_index=summary.step_count,
                residual_state=current_state,
                context=context or {},
            )

            # 回滚检查(只在真发生变化时)
            if new_state is not current_state:
                final_state, was_rolled_back = self.rollback_handler.safe_apply(
                    snapshot_before, new_state,
                )
                if was_rolled_back:
                    summary.rollbacks += 1
                    # 别忘了把回滚事件记上
                    step.decisions.append({"rollback": True})
                current_state = final_state
            else:
                current_state = new_state

            summary.steps.append(step)
            summary.step_count += 1
            if transition:
                summary.transitions.append(transition)

            # 检查是否需要升级
            esc = self.escalation_policy.suggest(self.loop_manager.stats)
            if esc.get("action") != "none":
                summary.escalations.append({
                    "step": step.step_index,
                    **esc,
                })

        # 收尾
        summary.final_residual_id = current_state.residual_id
        summary.final_status = current_state.status.value
        summary.budget_report = self.budget_manager.report()

        return current_state, summary

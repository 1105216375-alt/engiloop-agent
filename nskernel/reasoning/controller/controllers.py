"""
Controller 控制层 · 升级/回滚/预算/停机
==========================================

四个关键控制器:

  EscalationPolicy  发现连续失败时建议升级策略
  RollbackHandler   坏状态自动回滚到上个快照
  BudgetManager     步数/工具调用/token 预算
  StopPolicy        什么时候应该停止(solved/budget/stall)
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from nskernel.dialogue.reasoning import ResidualState, LoopStats


# ══════════════════════════════════════════════════════════════
# EscalationPolicy · 升级策略
# ══════════════════════════════════════════════════════════════
class EscalationPolicy:
    """
    根据 LoopStats 建议升级动作

    规则(来自 Reasoner):
      - 连续 2 次 interrupt → escalate_model(换更强 LLM)
      - 连续 4 次 reject   → switch_proposer(换 proposer)
      - 连续 4 轮无进展    → request_human_review(人工介入)
    """

    def __init__(
        self,
        escalate_on_interrupts: int = 2,
        switch_on_rejects: int = 4,
        human_review_on_no_progress: int = 4,
    ):
        self.escalate_on_interrupts = escalate_on_interrupts
        self.switch_on_rejects = switch_on_rejects
        self.human_review_on_no_progress = human_review_on_no_progress

    def suggest(self, stats: "LoopStats") -> Dict[str, Any]:
        """
        基于当前统计给出建议

        返回: {"action": "...", "reason": "..."}
          action: escalate_model / switch_proposer / request_human_review / none
        """
        if stats.consecutive_interrupts >= self.escalate_on_interrupts:
            return {"action": "escalate_model",
                    "reason": "too_many_interrupts"}

        if stats.consecutive_rejects >= self.switch_on_rejects:
            return {"action": "switch_proposer",
                    "reason": "too_many_rejects"}

        if stats.no_progress_rounds >= self.human_review_on_no_progress:
            return {"action": "request_human_review",
                    "reason": "no_progress_too_long"}

        return {"action": "none", "reason": "stable"}


# ══════════════════════════════════════════════════════════════
# RollbackHandler · 回滚处理
# ══════════════════════════════════════════════════════════════
class RollbackHandler:
    """
    状态更新出错时,回到上个稳定快照

    使用策略:
      每次 apply 前,存一份 snapshot
      apply 后,_is_invalid 判定 → 回滚 + 记录
    """

    def rollback(self, snapshot) -> Any:
        """直接回滚到快照(深拷贝)"""
        return deepcopy(snapshot)

    def safe_apply(
        self,
        snapshot: Any,
        new_state: Any,
    ) -> "tuple[Any, bool]":
        """
        安全应用:如果 new_state 检测为损坏,回滚到 snapshot

        Returns: (最终 state, 是否回滚)
        """
        if self._is_invalid_state(new_state):
            return self.rollback(snapshot), True
        return deepcopy(new_state), False

    @staticmethod
    def _is_invalid_state(state: Any) -> bool:
        """检测状态是否损坏(MVP)"""
        if state is None:
            return True
        if hasattr(state, "residual_id") and not state.residual_id:
            return True
        if hasattr(state, "status") and hasattr(state, "unresolved_items"):
            # Public extraction fix: str(EnumMember) is not the enum value.
            # Exact comparison also avoids treating "unsolved" as solved.
            status_value = getattr(state.status, "value", state.status)
            if status_value == "solved" and state.unresolved_items:
                # 标为 solved 但还有未解决项 → 不一致
                return True
        return False


# ══════════════════════════════════════════════════════════════
# BudgetManager · 预算管理
# ══════════════════════════════════════════════════════════════
@dataclass
class BudgetManager:
    """
    预算控制器 — 防止失控消耗

    支持的预算维度:
      max_steps:       最大推理步数
      max_tool_calls:  最大工具调用次数
      max_tokens:      最大 token 数(可选,需要 LLM provider 支持)
      max_wall_seconds: 最大总时长(秒)
    """
    max_steps: int = 20
    max_tool_calls: int = 10
    max_tokens: Optional[int] = None
    max_wall_seconds: Optional[float] = None

    current_steps: int = 0
    current_tool_calls: int = 0
    current_tokens: int = 0
    _start_time: Optional[float] = None

    def reset(self) -> None:
        import time
        self.current_steps = 0
        self.current_tool_calls = 0
        self.current_tokens = 0
        self._start_time = time.time()

    def step(self) -> None:
        self.current_steps += 1

    def tool_call(self) -> None:
        self.current_tool_calls += 1

    def add_tokens(self, n: int) -> None:
        self.current_tokens += n

    def step_budget_exceeded(self) -> bool:
        return self.current_steps >= self.max_steps

    def tool_budget_exceeded(self) -> bool:
        return self.current_tool_calls >= self.max_tool_calls

    def token_budget_exceeded(self) -> bool:
        return (self.max_tokens is not None
                and self.current_tokens >= self.max_tokens)

    def wall_time_exceeded(self) -> bool:
        if self.max_wall_seconds is None or self._start_time is None:
            return False
        import time
        return (time.time() - self._start_time) >= self.max_wall_seconds

    def any_budget_exceeded(self) -> bool:
        return (self.step_budget_exceeded()
                or self.tool_budget_exceeded()
                or self.token_budget_exceeded()
                or self.wall_time_exceeded())

    def report(self) -> Dict[str, Any]:
        return {
            "steps": f"{self.current_steps}/{self.max_steps}",
            "tool_calls": f"{self.current_tool_calls}/{self.max_tool_calls}",
            "tokens": f"{self.current_tokens}/{self.max_tokens}" if self.max_tokens else "n/a",
            "wall_time": (f"{self.max_wall_seconds}s 上限"
                          if self.max_wall_seconds else "no limit"),
        }


# ══════════════════════════════════════════════════════════════
# StopPolicy · 停机策略
# ══════════════════════════════════════════════════════════════
class StopPolicy:
    """
    决定 LoopManager 什么时候该停

    停机原因(reason_code):
      solved                目标全部达成
      budget_exceeded       预算超限
      stalled_empty         连续 N 次无候选
      stalled_no_progress   连续 N 次无推进
      blocked               状态为 blocked
      failed                状态为 failed
    """

    def __init__(
        self,
        max_consecutive_empty: int = 3,
        max_no_progress: int = 5,
    ):
        self.max_consecutive_empty = max_consecutive_empty
        self.max_no_progress = max_no_progress

    def should_stop(
        self,
        residual_state,
        budget: BudgetManager,
        stats,
    ) -> "tuple[bool, str]":
        """
        Returns: (should_stop, reason_code)
        """
        if residual_state.is_solved():
            return True, "solved"
        if residual_state.is_failed():
            return True, "failed"
        if residual_state.is_blocked():
            return True, "blocked"
        if budget.any_budget_exceeded():
            return True, "budget_exceeded"
        if stats.consecutive_empty_candidates >= self.max_consecutive_empty:
            return True, "stalled_empty"
        if stats.no_progress_rounds >= self.max_no_progress:
            return True, "stalled_no_progress"
        return False, ""

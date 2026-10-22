"""
CandidateStep & StateTransition · 候选步骤与状态转移
=====================================================

候选步骤(CandidateStep):
  由某个 proposer(neural/heuristic/retrieval/tool/formula)生成,
  表示"下一步应该这么做"的建议。尚未验证,还不是事实。

状态转移(StateTransition):
  对某个 candidate 经过 verify + 决策 accept 后,产生的**正式变化记录**。
  描述 "从残差状态 R_k 转到 R_k+1 发生了什么"。

这两者是推理闭环的核心数据:
  propose →  CandidateStep
  verify  →  VerifierResult + Decision
  update  →  StateTransition
  rebuild →  next ResidualState
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
import time


# ══════════════════════════════════════════════════════════════
# CandidateStep
# ══════════════════════════════════════════════════════════════
class ProposerSource(str, Enum):
    """候选来源 — 影响 verifier 的严格程度和优先级"""
    NEURAL = "neural"           # LLM 提议
    HEURISTIC = "heuristic"     # 启发式规则
    RETRIEVAL = "retrieval"     # 从知识库检索
    TOOL = "tool"               # 工具调用
    FORMULA = "formula"         # 公式库直接匹配(最高优先级)
    HUMAN = "human"             # 人工介入


class StepType(str, Enum):
    """步骤类型 — 决定 update 时用哪种更新器"""
    INFERENCE = "inference"     # 推导得出新结论
    REWRITE = "rewrite"         # 改写形式(等价变换)
    CHECK = "check"             # 检查/验证
    STRATEGY = "strategy"       # 切换策略
    TOOL_CALL = "tool_call"     # 调工具
    ASK_USER = "ask_user"       # 反问用户
    CALCULATION = "calculation" # 符号计算


@dataclass
class CandidateStep:
    """
    一个候选步骤

    示例(法律域):
      CandidateStep(
          source=ProposerSource.FORMULA,
          step_type=StepType.CALCULATION,
          action={"formula_id": "law.statute_of_limitation",
                  "args": {"known_date": "2023-05-01"}},
          expected_effect={"type": "resolve_unresolved",
                           "target_unresolved_item": "U_xxx"},
          confidence=0.95,
      )
    """
    candidate_id: str = field(default_factory=lambda: f"CS_{uuid.uuid4().hex[:8]}")
    source: ProposerSource = ProposerSource.NEURAL
    step_type: StepType = StepType.INFERENCE

    # 依赖项(fact_id / item_id / rule_id 等)
    input_refs: List[str] = field(default_factory=list)

    # 前提条件
    preconditions: List[Dict[str, Any]] = field(default_factory=list)

    # 核心动作 — 具体执行什么(领域自定义 schema)
    # 例: {"type": "apply_formula", "formula_id": "...", "args": {...}}
    # 例: {"type": "add_fact", "statement": "..."}
    action: Dict[str, Any] = field(default_factory=dict)

    # 预期会产出什么(事实 / 规则 / 结论)
    produced_items: List[Dict[str, Any]] = field(default_factory=list)

    # 预期效果 — 用于 ResidualReductionChecker
    # {"type": "resolve_unresolved"/"narrow_search_scope"/..., "target_xxx": "..."}
    expected_effect: Dict[str, Any] = field(default_factory=dict)

    # proposer 自己给的置信度(0.0 - 1.0)
    confidence: float = 0.5

    # 人类可读的解释
    justification: Optional[str] = None

    # 来源追踪(哪个 llm 模型 / 哪条规则 / 哪个检索结果)
    provenance: Dict[str, Any] = field(default_factory=dict)

    # proposer 的原始输出(调试用)
    raw_output: Optional[str] = None

    # 时间戳
    created_at: float = field(default_factory=time.time)

    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source": self.source.value,
            "step_type": self.step_type.value,
            "input_refs": self.input_refs,
            "preconditions": self.preconditions,
            "action": self.action,
            "produced_items": self.produced_items,
            "expected_effect": self.expected_effect,
            "confidence": self.confidence,
            "justification": self.justification,
            "provenance": self.provenance,
            "raw_output": self.raw_output,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


# ══════════════════════════════════════════════════════════════
# StateTransition
# ══════════════════════════════════════════════════════════════
@dataclass
class StateTransition:
    """
    一次正式的状态转移

    从 from_residual_id 转到 to_residual_id 的**完整变化**:
      - 新增了哪些 verified_facts
      - 新增了哪些 unresolved_items
      - 消除了哪些 unresolved_items
      - 哪些 goals 被 solved
    """
    transition_id: str = field(default_factory=lambda: f"T_{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)

    # 上下文
    from_residual_id: str = ""
    to_residual_id: str = ""

    # 触发这次转移的候选
    candidate_id: Optional[str] = None

    # 决策动作(accept / reject / defer / interrupt)
    decision_action: Optional[str] = None

    # 实际发生的变化
    added_verified_facts: List[Dict[str, Any]] = field(default_factory=list)
    added_local_lemmas: List[Dict[str, Any]] = field(default_factory=list)

    removed_unresolved_items: List[str] = field(default_factory=list)
                                                # 被解决的 item_id 列表
    added_unresolved_items: List[Dict[str, Any]] = field(default_factory=list)

    solved_goals: List[str] = field(default_factory=list)

    # 人类可读的摘要
    summary: Optional[str] = None

    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_progressive(self) -> bool:
        """是否真的推进了(删项 or 验证了事实 or 解决了 goal)"""
        return bool(
            self.removed_unresolved_items
            or self.added_verified_facts
            or self.solved_goals
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "timestamp": self.timestamp,
            "from_residual_id": self.from_residual_id,
            "to_residual_id": self.to_residual_id,
            "candidate_id": self.candidate_id,
            "decision_action": self.decision_action,
            "added_verified_facts": self.added_verified_facts,
            "added_local_lemmas": self.added_local_lemmas,
            "removed_unresolved_items": self.removed_unresolved_items,
            "added_unresolved_items": self.added_unresolved_items,
            "solved_goals": self.solved_goals,
            "summary": self.summary,
            "metadata": self.metadata,
        }

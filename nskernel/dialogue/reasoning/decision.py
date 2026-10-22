"""
ReasoningDecision · 推理决策(四级动作)
========================================

扩展 nskernel 原有的 ClaimVerifier 的 HARD/SOFT/PASS 三级为推理场景的四级动作:

  ACCEPT     通过 → 写入主状态
  REJECT     拒绝 → 候选作废,尝试下一个
  DEFER      暂缓 → 证据不足,等更多信息或下轮再看
  INTERRUPT  打断 → 触发升级策略(换 proposer / 升级模型 / 人工)

相比原 ClaimVerifier:
  ClaimVerifier HARD-FAIL   → 对应 REJECT
  ClaimVerifier SOFT-WARN   → 对应 DEFER
  ClaimVerifier PASS        → 对应 ACCEPT
  (新增)                     → INTERRUPT(用于严重违规)

reason_codes 是日志/调试的关键,让我们知道"为什么 reject"
follow_up 是升级策略的指导,"建议怎么办"
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DecisionAction(str, Enum):
    ACCEPT = "accept"          # 通过
    REJECT = "reject"          # 拒绝(可再试其他候选)
    DEFER = "defer"            # 暂缓(等更多证据)
    INTERRUPT = "interrupt"    # 打断(触发升级)


@dataclass
class VerifierResult:
    """
    一个候选经过所有 checker 后的综合结果
    各 *_ok 字段对应一种 checker 的结论
    """
    candidate_id: str

    # 通用的校验维度(每个垂域 checker 产生 True/False)
    library_compliance_ok: bool = True    # 库内合法性
    consistency_ok: bool = True           # 一致性
    operation_rule_ok: bool = True        # 操作规则
    symbolic_equivalence_ok: bool = True  # 符号等价
    residual_reduction_ok: bool = True    # ★ 残差缩减(核心)
    safe: bool = True                     # 安全
    traceable: bool = True                # 可追溯
    executable: bool = True               # 可执行
    applicable: bool = True               # 适用性
    numerical_sampling_ok: bool = True    # 数值采样
    solution_ok: bool = True              # 解答正确
    soundness_ok: bool = True             # 可靠性
    deduplication_ok: bool = True         # 去重

    # 各 checker 留下的原因码(串起来给 Decision 用)
    reason_codes: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def passed_all_hard_checks(self) -> bool:
        """全部硬检查通过"""
        return (self.library_compliance_ok and self.consistency_ok
                and self.operation_rule_ok and self.symbolic_equivalence_ok
                and self.safe and self.applicable
                and self.solution_ok and self.soundness_ok)

    def passed_soft_checks(self) -> bool:
        """软检查(警告级)"""
        return (self.residual_reduction_ok and self.traceable
                and self.executable and self.numerical_sampling_ok
                and self.deduplication_ok)


@dataclass
class ReasoningDecision:
    """
    对一个候选的最终决定

    action 四选一:
      - accept:    candidate 可以写入主状态
      - reject:    candidate 作废,但不升级
      - defer:     暂缓(等更多信息)
      - interrupt: 触发升级策略
    """
    candidate_id: str
    action: DecisionAction

    reason_codes: List[str] = field(default_factory=list)
    message: Optional[str] = None

    # 建议的后续动作(升级策略消费)
    # {"suggestion": "switch_proposer"/"escalate_model"/"request_human_review"/"halt_current_chain"}
    follow_up: Dict[str, Any] = field(default_factory=dict)

    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_accept(self) -> bool:
        return self.action == DecisionAction.ACCEPT

    def is_reject(self) -> bool:
        return self.action == DecisionAction.REJECT

    def is_defer(self) -> bool:
        return self.action == DecisionAction.DEFER

    def is_interrupt(self) -> bool:
        return self.action == DecisionAction.INTERRUPT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "action": self.action.value,
            "reason_codes": self.reason_codes,
            "message": self.message,
            "follow_up": self.follow_up,
            "metadata": self.metadata,
        }


# ══════════════════════════════════════════════════════════════
# 默认的 reason codes(领域可以扩展)
# ══════════════════════════════════════════════════════════════
class ReasonCode:
    """常见 reason code 常量库(防止拼错)"""

    # Hard library compliance
    OUT_OF_LIBRARY = "out_of_library"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    FORBIDDEN_SHORTCUT = "forbidden_shortcut_used"

    # Hard consistency
    UNANCHORED_NEW_SYMBOL = "unanchored_new_symbol"
    OFF_MAIN_THREAD = "off_main_thread"
    POST_SOLUTION_DETOUR = "post_solution_detour"

    # Operation rule
    OPERATION_RULE_VIOLATION = "operation_rule_violation"

    # Symbolic
    SYMBOLIC_NOT_EQUIVALENT = "symbolic_not_equivalent"
    EQUATION_NOT_EQUIVALENT = "equation_not_equivalent"

    # Safety
    UNSAFE_CANDIDATE = "unsafe_candidate"

    # Residual reduction
    RESIDUAL_NOT_REDUCED = "residual_not_clearly_reduced"
    MISSING_EXPECTED_EFFECT = "missing_expected_effect"

    # Applicability
    NOT_APPLICABLE = "not_applicable"
    PRECONDITIONS_NOT_MET = "preconditions_not_met"

    # Traceability / Execution
    NOT_TRACEABLE = "not_traceable"
    NOT_EXECUTABLE = "not_executable"

    # Domain-level (reasoner 扩展)
    # 每个 domain 可继承这个类加自己的

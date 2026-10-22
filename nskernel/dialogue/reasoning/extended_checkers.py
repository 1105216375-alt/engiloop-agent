"""
扩展 Checker 矩阵(RRI-9)
=========================

在 verifier_gate.py 里已有的 3 种基础 checker 之外,补齐 12 种通用 checker。

这些 checker 是**领域无关**的通用检查器。
领域特定的 checker(如"法条号必须真实存在")仍由各 domain 实现 ClaimRule。

12 种扩展 checker:
  1. ApplicabilityChecker       前提条件是否满足
  2. ConsistencyChecker         与已有 verified_facts 不矛盾
  3. DeduplicationChecker       不是重复已有 fact
  4. ExecutionChecker           action 是否可执行
  5. LibraryComplianceChecker   只用允许的工具/公式
  6. NumericalSamplingChecker   数值结果通过采样验证
  7. OperationRuleChecker       操作纪律(例:两边同时加法)
  8. SolutionChecker            最终解满足题目
  9. SoundnessChecker           结论必须蕴含前提
 10. SymbolicEquivalenceChecker 重写不改变语义
 11. ConfidenceChecker          置信度阈值
 12. InputRefsChecker           input_refs 里的 fact_id 真实存在
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional

from nskernel.dialogue.reasoning.residual_state import ResidualState
from nskernel.dialogue.reasoning.candidate_and_transition import CandidateStep
from nskernel.dialogue.reasoning.decision import ReasonCode


# ══════════════════════════════════════════════════════════════
# 1. ApplicabilityChecker
# ══════════════════════════════════════════════════════════════
class ApplicabilityChecker:
    """前提条件是否满足"""
    def check(self, candidate: CandidateStep, residual_state: ResidualState,
              context=None):
        preconds = candidate.preconditions or []
        if not preconds:
            return True, [], {}

        # 检查每个 precondition 是否在 verified_facts 里得到支撑
        facts_statements = [f.statement for f in residual_state.verified_facts]
        unmet = []
        for pc in preconds:
            pc_desc = pc.get("description") or pc.get("statement") or str(pc)
            # 极简匹配:描述的关键词必须在某个 fact 里出现
            key_words = [w for w in pc_desc.split() if len(w) >= 2][:3]
            matched = any(
                all(kw in f for kw in key_words) for f in facts_statements
            ) if key_words else False
            if not matched:
                unmet.append(pc_desc)

        if unmet:
            return False, [ReasonCode.PRECONDITIONS_NOT_MET], {"unmet": unmet}
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 2. ConsistencyChecker
# ══════════════════════════════════════════════════════════════
class ConsistencyChecker:
    """候选产出 vs 已有事实的一致性(不能矛盾)"""

    # 常见的矛盾词对
    CONTRADICTION_PAIRS = [
        ("通过", "不通过"), ("有效", "无效"), ("合法", "非法"),
        ("已过", "未过"), ("充分", "不充分"), ("存在", "不存在"),
        ("可以", "不可以"), ("是", "不是"),
    ]

    def check(self, candidate, residual_state, context=None):
        produced = candidate.produced_items or []
        conflicts = []
        existing_statements = [f.statement for f in residual_state.verified_facts]

        for p in produced:
            p_stmt = p.get("statement", "") if isinstance(p, dict) else ""
            if not p_stmt:
                continue
            for ex in existing_statements:
                # 检测是否含矛盾词对
                for a, b in self.CONTRADICTION_PAIRS:
                    if a in p_stmt and b in ex:
                        # 粗略:如果句子主干相似(共享 >= 3 字)就认为在说同一件事
                        common = set(p_stmt) & set(ex)
                        if len(common) >= 3:
                            conflicts.append({"new": p_stmt[:40],
                                              "existing": ex[:40]})

        if conflicts:
            return False, ["inconsistent_with_existing"], {"conflicts": conflicts}
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 3. DeduplicationChecker
# ══════════════════════════════════════════════════════════════
class DeduplicationChecker:
    """防止重复生成同样的事实"""
    def check(self, candidate, residual_state, context=None):
        produced = candidate.produced_items or []
        existing = {f.statement for f in residual_state.verified_facts}

        duplicates = []
        for p in produced:
            stmt = p.get("statement", "") if isinstance(p, dict) else ""
            if stmt and stmt in existing:
                duplicates.append(stmt)

        if duplicates:
            return False, ["duplicate_fact"], {"duplicates": duplicates}
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 4. ExecutionChecker
# ══════════════════════════════════════════════════════════════
class ExecutionChecker:
    """action 必须可执行(有 type 字段)"""
    def check(self, candidate, residual_state, context=None):
        if not candidate.action or not candidate.action.get("type"):
            return False, [ReasonCode.NOT_EXECUTABLE], {}
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 5. LibraryComplianceChecker
# ══════════════════════════════════════════════════════════════
class LibraryComplianceChecker:
    """
    只允许调用在 allowed_libraries 里注册的公式/工具

    用法:
      ck = LibraryComplianceChecker(allowed={"law.statute_of_limitation",
                                              "law.unpaid_wage_damages"})
    """
    def __init__(self, allowed_libraries: Optional[set] = None):
        self._allowed = set(allowed_libraries or [])

    def check(self, candidate, residual_state, context=None):
        if not self._allowed:
            return True, [], {}  # 没设限就通过

        action_type = candidate.action.get("type", "")
        if action_type == "apply_formula":
            fid = candidate.action.get("formula_id", "")
            if fid and fid not in self._allowed:
                return False, [ReasonCode.OUT_OF_LIBRARY], {"formula_id": fid}
        if action_type == "tool_call":
            tool = candidate.action.get("tool_name", "")
            if tool and tool not in self._allowed:
                return False, [ReasonCode.METHOD_NOT_ALLOWED], {"tool": tool}
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 6. NumericalSamplingChecker(数学域专用,但抽象成通用)
# ══════════════════════════════════════════════════════════════
class NumericalSamplingChecker:
    """
    如果 candidate 声称是数学等价,在随机值上采样验证
    MVP:只在 candidate.action 有 'sample_check_expr' 字段时触发
    """
    def check(self, candidate, residual_state, context=None):
        expr = candidate.action.get("sample_check_expr")
        if not expr:
            return True, [], {}  # 不声明就不检

        # 领域可传入 sampler,这里只做最基本的结构校验
        # 真正的等价性校验需要 SymPy/Numeric library
        if not isinstance(expr, dict) or "left" not in expr or "right" not in expr:
            return False, ["sampling_malformed"], {"expr": expr}

        # 留给领域 sampler 做真正检查
        sampler = (context or {}).get("numerical_sampler")
        if sampler is None:
            return True, [], {}

        try:
            ok = sampler(expr["left"], expr["right"])
        except Exception as e:
            return False, ["sampling_error"], {"err": str(e)}

        if not ok:
            return False, ["numerical_not_equivalent"], {"expr": expr}
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 7. OperationRuleChecker
# ══════════════════════════════════════════════════════════════
class OperationRuleChecker:
    """
    操作纪律检查:不允许某些快捷操作
    例:两边同时除零、约分前未检查非零等

    MVP:从 action.metadata 里读 'violated_rules' 字段
    """
    def check(self, candidate, residual_state, context=None):
        violations = candidate.metadata.get("violated_rules", [])
        if violations:
            return False, [ReasonCode.OPERATION_RULE_VIOLATION], {
                "violations": violations,
            }
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 8. SolutionChecker(终局校验)
# ══════════════════════════════════════════════════════════════
class SolutionChecker:
    """
    如果 candidate.step_type 声称是"最终解",检查它是否真的解了所有 goals
    """
    def check(self, candidate, residual_state, context=None):
        effect = candidate.expected_effect or {}
        if effect.get("type") != "solve_goal":
            return True, [], {}

        target_goal = effect.get("target_goal")
        if not target_goal:
            return False, ["solution_missing_target_goal"], {}

        # 有必要确保其他 unresolved 都解决了
        if residual_state.unresolved_count() > 0:
            return False, ["solution_premature"], {
                "remaining_unresolved": residual_state.unresolved_count(),
            }
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 9. SoundnessChecker
# ══════════════════════════════════════════════════════════════
class SoundnessChecker:
    """
    Require references and check bounded numeric assertions against evidence.

    This is not a general logical entailment proof. A domain can supply an
    object-aware evidence checker; otherwise only cited verified facts support
    recognized numeric assertions. Task/goal IDs are not numeric evidence.
    """
    def __init__(self, evidence_checker: Optional[Callable] = None):
        self.evidence_checker = evidence_checker

    def check(self, candidate, residual_state, context=None):
        if not candidate.produced_items:
            return True, [], {}  # 没产出就不检
        if not candidate.input_refs:
            return False, ["no_evidence_for_conclusion"], {}
        from nskernel.verification.numeric_claims import check_cited_numeric_claims

        cited = [fact.statement for fact in residual_state.verified_facts
                 if fact.fact_id in candidate.input_refs]
        issues = []
        for item in candidate.produced_items:
            if not isinstance(item, dict) or item.get("type") != "fact":
                continue
            statement = str(item.get("statement") or "")
            if self.evidence_checker is not None:
                issues.extend(self.evidence_checker(statement, candidate, residual_state, context or {}))
            else:
                issues.extend(check_cited_numeric_claims(statement, cited))
        if issues:
            return False, ["conclusion_not_supported_by_evidence"], {"issues": issues}
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 10. SymbolicEquivalenceChecker
# ══════════════════════════════════════════════════════════════
class SymbolicEquivalenceChecker:
    """
    rewrite 步骤必须保持语义等价(需领域提供 symbolic_checker)
    MVP:如果 step_type == REWRITE,action 里必须声明 equivalence_verified
    """
    def check(self, candidate, residual_state, context=None):
        from nskernel.dialogue.reasoning.candidate_and_transition import StepType

        if candidate.step_type != StepType.REWRITE:
            return True, [], {}

        if not candidate.action.get("equivalence_verified"):
            return False, [ReasonCode.SYMBOLIC_NOT_EQUIVALENT], {}
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 11. ConfidenceChecker
# ══════════════════════════════════════════════════════════════
class ConfidenceChecker:
    """低置信度自动降级为 defer"""
    def __init__(self, min_confidence: float = 0.3):
        self.min_confidence = min_confidence

    def check(self, candidate, residual_state, context=None):
        if candidate.confidence < self.min_confidence:
            return False, ["confidence_too_low"], {
                "confidence": candidate.confidence,
                "min": self.min_confidence,
            }
        return True, [], {}


# ══════════════════════════════════════════════════════════════
# 12. InputRefsChecker
# ══════════════════════════════════════════════════════════════
class InputRefsChecker:
    """input_refs 里的 fact_id / item_id 必须真实存在于 residual_state"""
    def check(self, candidate, residual_state, context=None):
        refs = candidate.input_refs or []
        if not refs:
            return True, [], {}

        known_ids = set()
        known_ids.update(f.fact_id for f in residual_state.verified_facts)
        known_ids.update(u.item_id for u in residual_state.unresolved_items)
        known_ids.update(g.goal_id for g in residual_state.goals)

        invalid = [r for r in refs if r not in known_ids]
        if invalid:
            return False, ["invalid_input_refs"], {"invalid": invalid}
        return True, [], {}

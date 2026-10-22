"""
Proposers · 多源候选生成器
============================

5 种 Proposer 按优先级调度(小的数字 = 高优先级):

  10  FormulaProposer       公式库直接匹配 ★最高
  20  RetrievalProposer     从权威库检索
  30  HeuristicProposer     启发式规则
  40  ToolSuggestionProposer 工具调用建议
  50  NeuralProposer        LLM 兜底

这实现了"能用符号绝不走 LLM"的架构原则:
  - 公式库命中 → 直接符号计算,跳过 LLM
  - 库里检索到权威引用 → 用那条,跳过 LLM
  - 有启发式规则匹配 → 规则优先
  - 需要工具 → 建议 tool_call
  - 都没有 → 让 LLM 出主意
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from nskernel.dialogue.reasoning.residual_state import ResidualState
from nskernel.dialogue.reasoning.candidate_and_transition import (
    CandidateStep, ProposerSource, StepType,
)

if TYPE_CHECKING:
    from nskernel.contracts import ComputationSpec, AuthoritySchema
    from nskernel.computation import Calculator
    from nskernel.llm import LLMClientBase


# ══════════════════════════════════════════════════════════════
# ProposerBase 统一接口
# ══════════════════════════════════════════════════════════════
class ProposerBase(ABC):
    """所有 Proposer 的基类"""
    name: str = "base_proposer"
    priority: int = 100
    source: ProposerSource = ProposerSource.NEURAL

    @abstractmethod
    def propose(
        self,
        residual_state: ResidualState,
        context: Dict[str, Any],
    ) -> List[CandidateStep]:
        """
        基于当前残差状态 + 上下文,生成候选
        返回列表(可能为空)
        """
        ...

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "source": self.source.value,
        }


# ══════════════════════════════════════════════════════════════
# 1. FormulaProposer(最高优先级)
# ══════════════════════════════════════════════════════════════
class FormulaProposer(ProposerBase):
    """
    公式库直接匹配

    工作逻辑:
      1. 查看当前 unresolved_items 和 question,找能命中 ComputationSpec 的 formula
      2. 如果命中 → 返回 CandidateStep(source=FORMULA, type=CALCULATION)
      3. candidate 包含完整的 formula_id + args(若缺 args 则不返回,让其他 proposer 接力)
    """
    name = "formula_proposer"
    priority = 10
    source = ProposerSource.FORMULA

    def __init__(self, computation_spec: "ComputationSpec"):
        self._spec = computation_spec

    def propose(self, residual_state, context):
        question = context.get("question", "")
        if not question:
            return []

        # 检测意图
        formula_id = self._spec.detect_computation_intent(question)
        if not formula_id:
            return []

        formula = self._spec.get_formula(formula_id)
        if not formula:
            return []

        # 从 context/residual 里尝试抽取参数(MVP: 简单)
        args = context.get("extracted_args", {})

        # 返回 candidate(即使参数不全,让下游决定)
        missing = [k for k in formula.inputs if k not in args]
        if missing:
            # 参数不全就不提议(让 neural 接力问用户)
            return []

        return [CandidateStep(
            source=self.source,
            step_type=StepType.CALCULATION,
            action={
                "type": "apply_formula",
                "formula_id": formula_id,
                "args": args,
            },
            expected_effect={
                "type": "verify_fact",
                "about": formula.name,
            },
            produced_items=[{
                "type": "fact",
                "statement": f"{formula.name}(by {formula_id})",
            }],
            justification=f"公式库命中: {formula.name}",
            provenance={"formula_id": formula_id,
                         "authority_ref": formula.authority_ref},
            confidence=0.95,
        )]


# ══════════════════════════════════════════════════════════════
# 2. RetrievalProposer
# ══════════════════════════════════════════════════════════════
class RetrievalProposer(ProposerBase):
    """
    从 AuthoritySchema / 知识库检索相关权威条目
    """
    name = "retrieval_proposer"
    priority = 20
    source = ProposerSource.RETRIEVAL

    def __init__(self, authority_schema: "AuthoritySchema"):
        self._auth = authority_schema

    def propose(self, residual_state, context):
        from nskernel.contracts import RetrievalQuery

        question = context.get("question", "")
        if not question:
            return []

        try:
            result = self._auth.retrieve(RetrievalQuery(
                query_text=question,
                topic_keywords=context.get("topic_keywords", []),
                top_k=3,
            ))
        except Exception:
            return []

        if not result.refs:
            return []

        # 每条检索结果 = 一个候选(建议引用该权威)
        candidates = []
        for ref in result.refs:
            candidates.append(CandidateStep(
                source=self.source,
                step_type=StepType.INFERENCE,
                action={
                    "type": "cite_authority",
                    "ref_id": ref.ref_id,
                },
                expected_effect={
                    "type": "verify_fact",
                    "about": "authority_cited",
                },
                produced_items=[{
                    "type": "fact",
                    "statement": f"依据 {ref.to_citation()}: {ref.text[:80]}...",
                }],
                justification=f"权威库检索到: {ref.to_citation()}",
                provenance={"ref_id": ref.ref_id},
                confidence=0.85,
            ))
        return candidates


# ══════════════════════════════════════════════════════════════
# 3. HeuristicProposer
# ══════════════════════════════════════════════════════════════
@dataclass
class HeuristicRule:
    """一条启发式规则"""
    name: str
    trigger_keywords: List[str]
    action_builder: Callable[[ResidualState, Dict], CandidateStep]
    priority: int = 50


class HeuristicProposer(ProposerBase):
    """启发式规则匹配(领域可注册自己的规则)"""
    name = "heuristic_proposer"
    priority = 30
    source = ProposerSource.HEURISTIC

    def __init__(self, rules: Optional[List[HeuristicRule]] = None):
        self._rules = list(rules or [])

    def register_rule(self, rule: HeuristicRule) -> "HeuristicProposer":
        self._rules.append(rule)
        return self

    def propose(self, residual_state, context):
        question = context.get("question", "")
        # 把 residual 里所有 unresolved 描述也加入匹配池
        pool = question
        for u in residual_state.unresolved_items:
            pool += " " + u.description
        candidates = []
        for rule in self._rules:
            if any(kw in pool for kw in rule.trigger_keywords):
                try:
                    cand = rule.action_builder(residual_state, context)
                    if cand:
                        candidates.append(cand)
                except Exception:
                    continue
        return candidates


# ══════════════════════════════════════════════════════════════
# 4. ToolSuggestionProposer
# ══════════════════════════════════════════════════════════════
class ToolSuggestionProposer(ProposerBase):
    """建议调用外部工具(爬虫/OCR/DB查询/API 等)"""
    name = "tool_suggestion_proposer"
    priority = 40
    source = ProposerSource.TOOL

    def __init__(self, tool_registry: Optional[Dict[str, Any]] = None):
        self._tools = dict(tool_registry or {})

    def register_tool(self, name: str, tool: Any) -> "ToolSuggestionProposer":
        self._tools[name] = tool
        return self

    def propose(self, residual_state, context):
        # MVP:如果 context 里明确指明 need_tool,建议调用
        needed = context.get("need_tool")
        if needed and needed in self._tools:
            return [CandidateStep(
                source=self.source,
                step_type=StepType.TOOL_CALL,
                action={"type": "tool_call", "tool_name": needed,
                        "args": context.get("tool_args", {})},
                expected_effect={"type": "verify_fact",
                                  "about": f"tool_result:{needed}"},
                justification=f"需要调用工具: {needed}",
                confidence=0.9,
            )]
        return []


# ══════════════════════════════════════════════════════════════
# 5. NeuralProposer(兜底)
# ══════════════════════════════════════════════════════════════
class NeuralProposer(ProposerBase):
    """
    LLM 提议 — 最后兜底

    只在其他 proposer 都没结果时起作用
    """
    name = "neural_proposer"
    priority = 50
    source = ProposerSource.NEURAL

    def __init__(self, llm_client: "LLMClientBase",
                 sys_prompt_builder: Optional[Callable] = None):
        self._llm = llm_client
        self._build_sys = sys_prompt_builder or self._default_sys_prompt

    def _default_sys_prompt(self, residual_state, context) -> str:
        """默认的 sys prompt - 领域可以覆盖"""
        scope = context.get("scope_description", "本领域")
        return (
            f"你是 {scope} 的助手。基于当前状态和用户问题,给出一条专业建议或结论。\n"
            f"你不能自己计算数值(必须用符号引擎),只能用自然语言描述思路。\n"
            f"回复必须简洁、专业。"
        )

    def propose(self, residual_state, context):
        question = context.get("question", "")
        if not question:
            return []

        sys_prompt = self._build_sys(residual_state, context)

        # 附加 residual state 摘要
        user_msg = f"用户问题: {question}\n\n当前状态:\n{residual_state.summary()}"

        try:
            reply = self._llm.complete(
                sys_prompt=sys_prompt,
                user_content=user_msg,
                temperature=0.7,
            )
        except Exception as e:
            return []

        if not reply:
            return []

        return [CandidateStep(
            source=self.source,
            step_type=StepType.INFERENCE,
            action={"type": "natural_language_response", "text": reply},
            expected_effect={"type": "narrow_search_scope"},
            produced_items=[{"type": "fact", "statement": reply[:100]}],
            justification="LLM 自然语言分析",
            raw_output=reply,
            confidence=0.6,
        )]


# ══════════════════════════════════════════════════════════════
# ProposalScheduler 调度器
# ══════════════════════════════════════════════════════════════
class ProposalScheduler:
    """
    总调度器 - 按优先级串起所有 proposer
    """
    def __init__(self, proposers: Optional[List[ProposerBase]] = None):
        self._proposers = sorted(proposers or [], key=lambda p: p.priority)

    def register(self, proposer: ProposerBase) -> "ProposalScheduler":
        self._proposers.append(proposer)
        self._proposers.sort(key=lambda p: p.priority)
        return self

    def generate_candidates(
        self,
        residual_state: ResidualState,
        context: Dict[str, Any],
        stop_on_first_hit: bool = False,
    ) -> List["tuple[CandidateStep, str]"]:
        """
        按优先级依次调用 proposer

        Args:
          stop_on_first_hit: True 表示第一个有输出的 proposer 就停(纯级联)
                             False 表示所有 proposer 都跑一次,汇总输出
        """
        all_candidates: List["tuple[CandidateStep, str]"] = []
        for proposer in self._proposers:
            try:
                cands = proposer.propose(residual_state, context) or []
            except Exception as e:
                cands = []
            for c in cands:
                all_candidates.append((c, proposer.name))
            if cands and stop_on_first_hit:
                break
        return all_candidates

    def list_proposers(self) -> List[Dict[str, Any]]:
        return [p.describe() for p in self._proposers]

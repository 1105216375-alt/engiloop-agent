"""Bridge-domain adapter extracted from the original application.
Data is supplied by the caller; examples use synthetic observations.
Residual execution, evidence boundaries, reviewed route memory and strict rendering.
"""
from __future__ import annotations
import hashlib
import json
import math
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from nskernel.dialogue.reasoning import (
    ResidualState, CandidateStep, ProposerSource, StepType,
    VerifierGate,
    ConfidenceChecker, DeduplicationChecker, SafetyChecker,
    TraceabilityChecker, ResidualReductionChecker,
    ApplicabilityChecker, ExecutionChecker, InputRefsChecker,
    SolutionChecker, SoundnessChecker,
    StrictOrchestrator, FactRealizer, StyleRefinement,
    LoopManager,
)
from nskernel.reasoning import (
    HeuristicProposer, HeuristicRule,
    RuntimeController, BudgetManager, StopPolicy, EscalationPolicy,
    RollbackHandler,
)
from nskernel.knowledge.distillation import (
    DistillationPipeline, KnowledgeLibrary,
)

if TYPE_CHECKING:
    from . import BridgeDomain

from .divergence_controller import decorate_prompt, normalize_persona_level, sampling_kwargs, sanitize_regulations
from bip_runtime.euler_memory import EulerMemoryExtractor, EulerMemoryMatcher
from bip_runtime.semantic_frame import compile_residual_plan
from bip_runtime.claim_grounding import check_diagnostic_numbers
from bip_runtime.evidence_capabilities import evidence_contract_capabilities
from nskernel.verification.numeric_claims import check_cited_numeric_claims


def _verify_candidate_numbers(statement, candidate, state, context):
    rows = list(context.get("evidence_rows") or [])
    if rows:
        # These are the current request's bound evidence rows, not a candidate's
        # self-reported correctness flag or a remembered bridge's facts.
        return check_diagnostic_numbers(statement, rows)
    cited = [fact.statement for fact in state.verified_facts if fact.fact_id in candidate.input_refs]
    return check_cited_numeric_claims(statement, cited)


def _valid_score(value: Any) -> Optional[float]:
    """Keep a genuine zero distinct from an absent or invalid engineering score."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def _flatten_part_scores(parts: Any, prefix: str = "") -> List[tuple[float, str]]:
    """Accept scalar or nested part scores without sorting dictionaries as scores."""
    if not isinstance(parts, dict):
        return []
    rows = []
    for name, value in parts.items():
        label = f"{prefix}·{name}" if prefix else str(name)
        if isinstance(value, dict):
            rows.extend(_flatten_part_scores(value, label))
        else:
            score = _valid_score(value)
            if score is not None:
                rows.append((score, label))
    return sorted(rows, key=lambda row: (row[0], row[1]))


# ══════════════════════════════════════════════════════════════
# 桥梁专用 FactRealizer
# ══════════════════════════════════════════════════════════════
class BridgeFactRealizer(FactRealizer):
    """桥梁领域的 fact → 自然语言骨架"""

    def realize(self, verified_facts, goals_context=None):
        if not verified_facts:
            return "(暂无可用的桥梁诊断事实)"

        lines = []
        if goals_context:
            lines.append("【分析目标】")
            for g in goals_context:
                lines.append(f"  · {g}")
            lines.append("")

        lines.append("【硬数据 · 符号引擎验证】")
        for i, f in enumerate(verified_facts, 1):
            stmt = f.statement
            src_map = {
                "v7_pipeline": "v7 诊断",
                "v7_score": "v7 评分",
                "v7_structure": "v7 结构识别",
                "v7_stats": "v7 统计",
                "archive_report": "档案报告",
                "archive_aggregate": "档案聚合",
                "heuristic": "推理规则",
                "symbolic_calc": "符号计算",
            }
            src = src_map.get(f.source, f.source or "分析")
            lines.append(f"  {i}. [{src}] {stmt}")

        return "\n".join(lines)


class EulerMemoryGuidedHeuristicProposer(HeuristicProposer):
    """Use an approved route only to narrow locally generated candidates."""

    def propose(self, residual_state, context):
        candidates = super().propose(residual_state, context)
        for candidate in candidates:
            # Domain rules read the current evidence snapshot. Record those
            # dependencies as facts; an unresolved task alone is not evidence.
            candidate.input_refs = list(dict.fromkeys([
                *candidate.input_refs,
                *(fact.fact_id for fact in residual_state.verified_facts),
            ]))
        patterns = list(context.get("approved_memory_patterns") or [])
        if not candidates or not patterns:
            return candidates

        route = patterns[0]
        for route_step in route.get("derivation_steps") or []:
            expected = set(route_step.get("action_types") or [])
            if not expected:
                continue
            matched = [
                candidate for candidate in candidates
                if str((candidate.action or {}).get("type") or "") in expected
            ]
            if not matched:
                continue
            for candidate in matched:
                candidate.action = dict(candidate.action or {})
                candidate.action.update({
                    "memory_guided": True,
                    "memory_id": route.get("memory_id"),
                    "memory_route_step": route_step.get("step_index"),
                })
                candidate.provenance = dict(candidate.provenance or {})
                candidate.provenance.update({
                    "memory_policy": "route_only_reverify_current_facts",
                    "memory_id": route.get("memory_id"),
                })
            return matched
        return candidates


# ══════════════════════════════════════════════════════════════
# 桥梁推理的 5 个启发式规则
# ══════════════════════════════════════════════════════════════
def _rule_life_analysis(state: ResidualState, context: Dict) -> Optional[CandidateStep]:
    """Report the life-evidence boundary; condition grades are not a life model."""
    for item in state.unresolved_items:
        # “全寿命成本” belongs to the cost-evidence task, not the life model.
        # Resolving it with a life-boundary fact would leave the actual life task
        # permanently blocked by duplicate-fact rejection on the next iteration.
        is_cost_task = any(term in item.description for term in ("成本", "费用", "预算", "造价"))
        if not is_cost_task and any(term in item.description for term in ("寿命", "剩余", "服役")):
            # This runtime has no calibrated residual-life model. Even supplied
            # design/service years and load tests cannot justify a new life range.
            return CandidateStep(
                source=ProposerSource.HEURISTIC,
                step_type=StepType.CALCULATION,
                input_refs=[item.item_id],
                action={"type": "life_evidence_missing"},
                expected_effect={
                    "type": "resolve_unresolved",
                    "target_unresolved_item": item.item_id,
                },
                produced_items=[{
                    "type": "fact",
                    "statement": (
                        "【寿命证据边界】当前推理路径没有可审计、经校准的剩余寿命模型，"
                        "不能由Dr、技术状况类别或设计寿命减服役年限生成剩余使用寿命。"
                        "设计寿命、已服役年限和一般承载能力资料不能代替材料劣化参数、"
                        "现场复测与专项寿命评估；本轮不生成寿命年限或继续安全服役承诺。"
                        "如原报告已有专项寿命评估，应核对原文、适用范围和评估依据后引用，"
                        "不得将报告结论冒充本系统重新计算的结果。"
                    ),
                }],
                justification="当前没有经校准寿命模型，保留证据边界而非按类别套经验年限",
                confidence=1.0,
            )
    return None


def _rule_cost_analysis(state: ResidualState, context: Dict) -> Optional[CandidateStep]:
    """Public release: report the cost boundary, never invent a price model.

    The original cost-estimator dependency and price data are not distributed.
    Even supplied quantities or a budget cannot enable an absent, uncalibrated
    calculator. This boundary is an explicit public-adapter change.
    """
    for u in state.unresolved_items:
        if any(term in u.description for term in ("成本", "费用", "预算", "造价", "多少钱", "修费")):
            v7_data = context.get("v7_data", {})
            supplied_basis = bool(
                v7_data.get("cost_basis")
                or (context.get("evidence_capabilities") or {}).get("cost_basis")
            )
            evidence_note = (
                "已提供部分造价依据，仍须核对工程量、处治范围、地区单价、价格时点与交通组织条件。"
                if supplied_basis else
                "本轮缺少可核验的工程量清单、处治方案、地区单价和交通组织条件。"
            )
            return CandidateStep(
                source=ProposerSource.HEURISTIC,
                step_type=StepType.CALCULATION,
                input_refs=[u.item_id],
                action={"type": "cost_model_missing" if supplied_basis else "cost_evidence_missing"},
                expected_effect={"type": "resolve_unresolved",
                                 "target_unresolved_item": u.item_id},
                produced_items=[{
                    "type": "fact",
                    "statement": (
                        "【造价证据边界】当前公开推理路径没有已校准、可审计的造价适配器，"
                        "不能输出可审计的维修或大修金额。" + evidence_note +
                        "不根据默认跨数、默认跨长或经验单价生成金额；"
                        "如原报告已有审定预算，只能核对来源和适用范围后引用，"
                        "不得冒充本系统重新估算的结果。"
                    ),
                }],
                justification="未提供已校准公开造价适配器，保留输入与模型缺口，不生成默认金额",
                provenance={"method": "public_cost_boundary", "calibrated_cost_model": False},
                confidence=1.0,
            )
    return None


def _rule_risk_analysis(state: ResidualState, context: Dict) -> Optional[CandidateStep]:
    """风险评估 - 基于 v7 的 Dr/BCI + 严控区"""
    for u in state.unresolved_items:
        if "风险" in u.description or "安全" in u.description:
            v7_data = context.get("v7_data", {})
            bs = v7_data.get("bridge_score") or {}
            bci = v7_data.get("bridge_score_52xx") or {}
            stats = v7_data.get("stats") or {}

            dr = _valid_score(bs.get("全桥评分"))
            bci_val = _valid_score(bci.get("BCI"))
            critical = stats.get("严控区数", 0) or 0

            # Either missing metric blocks the two-metric rule. A genuine 0
            # remains a valid observed score; it must not mean "not provided".
            if dr is None or bci_val is None:
                missing = [name for name, value in (("Dr", dr), ("BCI", bci_val)) if value is None]
                available = [f"{name}={value:g}" for name, value in (("Dr", dr), ("BCI", bci_val)) if value is not None]
                return CandidateStep(
                    source=ProposerSource.HEURISTIC,
                    step_type=StepType.INFERENCE,
                    input_refs=[u.item_id],
                    action={"type":"risk_data_missing"},
                    expected_effect={"type":"resolve_unresolved",
                                     "target_unresolved_item": u.item_id},
                    produced_items=[{
                        "type":"fact",
                        "statement": (
                            f"【风险证据边界】缺少有效的{'、'.join(missing)}评分"
                            "（应为0至100之间的有限数值），不能形成Dr/BCI交叉风险等级判断。"
                            + (f"现有指标：{'，'.join(available)}。" if available else "当前没有可用的Dr/BCI评分。")
                            + "缺失值不按0分参与计算；请补充或复核对应评分证据。"
                        ),
                    }],
                    justification="评分数据缺失,降级提示",
                    # 这是证据边界事实，不是低置信度风险猜测。
                    confidence=1.0,
                )

            # 综合风险
            if dr >= 85 and bci_val >= 90 and critical == 0:
                level = "低风险"
            elif dr >= 75 and critical <= 3:
                level = "中风险"
            elif dr >= 60 or critical > 10:
                level = "较高风险"
            else:
                level = "高风险"

            return CandidateStep(
                source=ProposerSource.HEURISTIC,
                step_type=StepType.INFERENCE,
                input_refs=[u.item_id],
                action={"type": "risk_assess"},
                expected_effect={"type": "resolve_unresolved",
                                 "target_unresolved_item": u.item_id},
                produced_items=[{
                    "type": "fact",
                    "statement": f"技术状况风险筛查:{level}"
                                 f"(Dr={dr:g}, BCI={bci_val:g}, 严控区{critical}处)。"
                                 "这是当前评分的规则筛查，不等于承载能力或通行安全结论。",
                }],
                justification="基于现有Dr、BCI与严控数量的内部筛查规则，不替代专项安全评估",
                confidence=0.88,
            )
    return None


def _rule_priority_analysis(state: ResidualState, context: Dict) -> Optional[CandidateStep]:
    """构件维修优先级 - 优先用部位分，无部位分时回退到验证事实。"""
    for u in state.unresolved_items:
        if "优先" in u.description or "顺序" in u.description:
            v7_data = context.get("v7_data", {})
            bs = v7_data.get("bridge_score") or {}
            parts = bs.get("部位得分", {}) or bs.get("分项得分", {})

            # 按得分升序(分数低的优先修)
            flat = _flatten_part_scores(parts)

            basis = "按部位得分升序，分数低的优先"
            top_3 = flat[:3]
            desc = "; ".join(f"{name}({s:.1f}分)" for s, name in top_3)

            if not top_3:
                component_rows: Dict[str, Dict[str, Any]] = {}
                for row in context.get("evidence_rows") or []:
                    component = str(
                        row.get("component") or row.get("构件") or "未明确构件"
                    ).strip()
                    bucket = component_rows.setdefault(component, {
                        "risk": 0.0,
                        "scale": 0,
                        "critical": 0,
                        "count": 0,
                    })
                    try:
                        bucket["risk"] = max(
                            bucket["risk"],
                            float(row.get("risk") or row.get("风险评分") or 0),
                        )
                    except (TypeError, ValueError):
                        pass
                    try:
                        bucket["scale"] = max(
                            bucket["scale"],
                            int(row.get("scale") or row.get("标度") or 0),
                        )
                    except (TypeError, ValueError):
                        pass
                    bucket["critical"] += int(bool(row.get("critical") or row.get("严控区")))
                    try:
                        bucket["count"] += max(1, int(row.get("count") or row.get("数量") or 1))
                    except (TypeError, ValueError):
                        bucket["count"] += 1

                ranked = sorted(
                    component_rows.items(),
                    key=lambda item: (
                        -item[1]["critical"],
                        -item[1]["risk"],
                        -item[1]["scale"],
                        -item[1]["count"],
                        item[0],
                    ),
                )[:3]
                desc = "; ".join(
                    f"{component}(最高风险{values['risk']:.1f}/最高标度{values['scale']}"
                    f"/严控{values['critical']}条/病害{values['count']}条)"
                    for component, values in ranked
                )
                basis = "按已验证病害的严控状态、最高风险、最高标度和数量排序"

            if not desc:
                desc = "证据不足，暂不形成构件优先级"
                basis = "未发现可用于构件排序的评分或已验证病害"

            return CandidateStep(
                source=ProposerSource.HEURISTIC,
                step_type=StepType.INFERENCE,
                input_refs=[u.item_id],
                action={"type": "priority"},
                expected_effect={"type": "resolve_unresolved",
                                 "target_unresolved_item": u.item_id},
                produced_items=[{
                    "type": "fact",
                    "statement": f"【优先维修顺序】Top 3: {desc}",
                }],
                justification=basis,
                confidence=0.9,
            )
    return None


def _rule_conclude(state: ResidualState, context: Dict) -> Optional[CandidateStep]:
    """Conclude from accepted analysis facts, without a second unsupported verdict."""
    if state.unresolved_count() > 0 or state.verified_count() < 3:
        return None
    for goal in state.goals:
        if goal.solved:
            continue
        v7_data = context.get("v7_data") or {}
        score = v7_data.get("bridge_score") or {}
        stats = v7_data.get("stats") or {}
        question = str(context.get("question") or "").lower()
        grade = str(score.get("技术状况等级") or "")
        dr = _valid_score(score.get("全桥评分"))
        critical = stats.get("严控区数", 0) or 0
        capabilities = context.get("evidence_capabilities") or {}

        def latest_analysis(*markers):
            return next((
                fact for fact in reversed(state.verified_facts)
                if any(marker in fact.statement for marker in markers)
            ), None)

        references = [fact.fact_id for fact in state.verified_facts[-3:]]
        if any(term in question for term in ("大修", "值不值", "值得")):
            missing = []
            if dr is None:
                missing.append("有效Dr评分")
            if not grade:
                missing.append("技术状况等级")
            if not capabilities.get("load_capacity"):
                missing.append("承载能力/荷载试验或结构验算")
            if not capabilities.get("repair_records"):
                missing.append("同位置维修闭环记录")
            missing_text = "、".join(missing) or "专项方案的工程量、适用条件与影响评估"
            known = []
            if grade:
                known.append(f"报告技术状况为{grade}")
            if dr is not None:
                known.append(f"Dr={dr:g}")
            if critical > 0:
                known.append(f"已记录严控区{critical}处，需优先专项复核")
            else:
                known.append("当前记录未列出严控区病害，不等于排除结构安全风险")
            answer = (
                "【条件性结论】现有证据不足以直接决定是否实施全桥大修。"
                + "；".join(known) + "。"
                + f"需补齐或核对{missing_text}，再开展专项维修、局部加固与全桥大修方案比选；"
                "本结论不能解释为无需大修，也不替代现场安全决策。"
            )
        elif any(term in question for term in ("优先", "先修", "顺序", "先做", "重点", "最严重", "重要")):
            accepted = latest_analysis("【优先维修顺序】")
            if accepted:
                answer = accepted.statement
                references = [accepted.fact_id]
            else:
                answer = "【优先维修证据边界】缺少经验证的部位评分或病害排序结果，暂不形成维修顺序。"
            answer += (
                f"严控区{critical}处需优先复核；具体处治期限由现场评估与批准方案确定。"
                if critical > 0
                else "当前记录未列出严控区病害；本轮不编造具体处治期限。"
            )
        elif any(term in question for term in ("风险", "严重", "危险", "安全")):
            accepted = latest_analysis("【风险证据边界】", "技术状况风险筛查:")
            if accepted:
                answer = "【风险分析汇总】" + accepted.statement
                references = [accepted.fact_id]
            else:
                answer = "【风险证据边界】没有经验证的风险分析结果，不能据技术状况类别宣称结构健康或通行安全。"
        elif any(term in question for term in ("多少钱", "费用", "成本", "预算", "造价", "修费", "花钱")):
            accepted = latest_analysis("【预算估算】", "不能输出可审计的维修或大修金额")
            answer = "【造价分析汇总】" + accepted.statement if accepted else "【造价证据边界】缺少可审计的造价分析结果，本轮不输出金额。"
            if accepted:
                references = [accepted.fact_id]
        elif any(term in question for term in ("寿命", "还能用", "剩余", "服役")):
            accepted = latest_analysis("【寿命证据边界】")
            answer = "【寿命分析汇总】" + accepted.statement if accepted else "【寿命证据边界】缺少经校准的寿命模型与专项评估依据，本轮不输出剩余寿命年限。"
            if accepted:
                references = [accepted.fact_id]
        else:
            answer = (
                "【条件性结论】当前仅汇总已验证的检测事实与评分。"
                "处治范围、期限、承载能力和交通管制需专项证据及有权限人员复核，"
                "不能仅凭技术状况类别作出大修、封闭或继续安全服役承诺。"
            )
        return CandidateStep(
            source=ProposerSource.HEURISTIC,
            step_type=StepType.INFERENCE,
            input_refs=references,
            action={"type": "conclude_direct_answer"},
            expected_effect={"type": "solve_goal", "target_goal": goal.goal_id},
            produced_items=[{"type": "fact", "statement": answer}],
            justification="复用已接受的分析结果并保留证据边界，不另按等级生成安全或寿命结论",
            confidence=1.0,
        )
    return None


# ══════════════════════════════════════════════════════════════
# BridgeReasoningMode
# ══════════════════════════════════════════════════════════════
class BridgeReasoningMode:
    """桥梁多步推理模式 - 基于 v7 真实数据"""

    def __init__(self, bridge_domain: "BridgeDomain"):
        self.bridge = bridge_domain

        self.proposer = EulerMemoryGuidedHeuristicProposer([
            HeuristicRule("life", ["寿命", "剩余", "还能用", "服役"], _rule_life_analysis),
            HeuristicRule("cost", ["成本", "费用", "多少钱", "预算", "造价", "修费", "花钱"], _rule_cost_analysis),
            HeuristicRule("risk", ["风险", "严重", "安全"], _rule_risk_analysis),
            HeuristicRule("priority", ["优先", "顺序", "先修", "先做", "重点", "重要", "最严重"], _rule_priority_analysis),
            HeuristicRule("decide", [
                "值得", "大修", "综合", "整体", "顺序", "优先",
                "风险", "判断", "怎么样", "怎么办", "建议", "寿命",
                "费用", "成本", "预算", "多少钱", "造价", "修费", "花钱", "严重", "分析",
            ], _rule_conclude),
        ])

        self.verifier = VerifierGate(
            hard_checkers=[
                SafetyChecker(),
                ConfidenceChecker(min_confidence=0.5),
                InputRefsChecker(),
                ApplicabilityChecker(),
                ExecutionChecker(),
                SolutionChecker(),
                SoundnessChecker(evidence_checker=_verify_candidate_numbers),
            ],
            soft_checkers=[
                ResidualReductionChecker(),
                TraceabilityChecker(),
                DeduplicationChecker(),
            ],
        )

        self.loop = LoopManager(
            proposers=[self.proposer],
            verifier_gate=self.verifier,
        )

        self.controller = RuntimeController(
            loop_manager=self.loop,
            budget_manager=BudgetManager(max_steps=10),
            stop_policy=StopPolicy(max_no_progress=3),
            escalation_policy=EscalationPolicy(),
            rollback_handler=RollbackHandler(),
        )

        # 严格合成器
        self.strict = StrictOrchestrator(
            bridge_domain.profile,
            fact_realizer=BridgeFactRealizer(),
        )
        # StyleRefinement 不在 __init__ 里装,因为 LLM 可能沙箱失败
        # 具体调用时才按需使用

        # 知识蒸馏(默认要人工审核才入库)
        self.distill_library = KnowledgeLibrary(require_human_review=True)
        self.distill_pipeline = DistillationPipeline(library=self.distill_library)
        state_store = getattr(bridge_domain, "state_store", None)
        self.euler_memory = EulerMemoryExtractor(state_store) if state_store else None
        self.euler_memory_matcher = EulerMemoryMatcher(state_store) if state_store else None

    def analyze(
        self,
        question: str,
        session_id: str,
        enable_style_llm: bool = True,
        service_years: Optional[int] = None,
        design_life: Optional[int] = None,
        persona_level: Optional[str] = "senior",
        enable_euler_memory: bool = True,
    ) -> Dict[str, Any]:
        """
        基于 v7 真实数据做多步推理

        Args:
          enable_style_llm: True 时最终回复会用 LLM 做风格化
                            (但所有 facts 一条不改)
        """
        session = self.bridge.session_store.get_or_create(session_id)
        observations = session.get_all_observations()

        if not observations:
            return self._empty_response()

        # 1. 构造 ResidualState,灌入 v7 的所有硬数据
        state = ResidualState()
        semantic_frame = dict(session.metadata.get("semantic_frame") or {})
        semantic_plan = compile_residual_plan(semantic_frame)
        state.description = f"桥梁多步分析: {question}"
        goal_description = semantic_plan.get("goal") if semantic_frame else f"回答: {question}"
        g = state.register_goal(goal_description or f"回答: {question}")
        state.metadata["semantic_frame"] = semantic_frame
        state.metadata["semantic_plan"] = semantic_plan

        # v7 数据作为起点 verified_facts
        v7_bs = session.metadata.get("bridge_score") or {}
        v7_bci = session.metadata.get("bridge_score_52xx") or {}
        v7_struct = session.metadata.get("structure") or {}
        v7_stats = session.metadata.get("stats") or {}
        valid_dr = _valid_score(v7_bs.get("全桥评分"))
        valid_bci = _valid_score(v7_bci.get("BCI"))

        # 全桥状况
        if valid_dr is not None:
            state.add_verified_fact(
                statement=f"全桥 Dr 评分 = {valid_dr:g}, "
                          f"技术状况 {v7_bs.get('技术状况等级', '?')}",
                source="v7_score", confidence=1.0,
            )
        if valid_bci is not None:
            state.add_verified_fact(
                statement=f"BCI(输入的参考指标，须核对适用依据) = {valid_bci:g}, "
                          f"{v7_bci.get('grade_label', '?')}",
                source="v7_score", confidence=1.0,
            )
        # 结构
        if v7_struct:
            state.add_verified_fact(
                statement=f"结构: {v7_struct.get('桥名', '?')}, "
                          f"{v7_struct.get('跨数', '?')}跨, "
                          f"梁型 {v7_struct.get('梁型', '?')}, "
                          f"全桥主梁 {v7_struct.get('总主梁数', '?')}片",
                source="v7_structure", confidence=1.0,
            )
        # 统计
        if v7_stats:
            state.add_verified_fact(
                statement=f"累计病害 {v7_stats.get('总数', '?')}条, "
                          f"严控区 {v7_stats.get('严控区数', '?')}条, "
                          f"高风险 {v7_stats.get('高风险数', '?')}条, "
                          f"最高风险评分 {v7_stats.get('最高风险评分', '?')}分",
                source="v7_stats", confidence=1.0,
            )

        evidence_contract = session.metadata.get("evidence_contract") or {}
        bridge_name = (
            evidence_contract.get("bridge_name")
            or v7_struct.get("桥名")
            or next(
                (
                    getattr(obs, "entity_display_name", "")
                    or getattr(obs, "entity_key", "")
                    for obs in observations
                    if getattr(obs, "entity_display_name", "")
                    or getattr(obs, "entity_key", "")
                ),
                "",
            )
        )
        latest_score = ((evidence_contract.get("metrics") or {}).get("latest") or {})
        reported_dr = latest_score.get("reported_dr")
        computed_dr = latest_score.get("computed_dr")
        score_gap = latest_score.get("score_gap")
        if reported_dr is not None and computed_dr is not None:
            state.add_verified_fact(
                statement=(
                    f"评分口径交叉核验:报告发布Dr={reported_dr}, "
                    f"系统按抽取病害重算Dr={computed_dr}, 差值={score_gap}"
                ),
                source="score_cross_check",
                evidence=[
                    str(report.get("reported_score_source") or report.get("id") or "archive_report")
                    for report in (evidence_contract.get("reports") or [])
                    if report.get("reported_dr") is not None
                ][:6],
                confidence=1.0,
            )
            try:
                if score_gap is not None and abs(float(score_gap)) >= 2.0:
                    state.unknowns.append({
                        "key": "score_method_conflict",
                        "description": "报告发布Dr与系统病害重算Dr存在显著差异",
                        "impact": "大修判断需先复核原报告评分范围、构件数量与病害抽取口径",
                    })
            except Exception:
                pass
        evidence_rows = list(evidence_contract.get("facts") or [])
        if not evidence_rows:
            evidence_rows = [
                dict(getattr(obs, "raw_data", {}) or {})
                for obs in observations
                if getattr(obs, "raw_data", None)
            ]
        for evidence_row in evidence_rows[:24]:
            component = evidence_row.get("构件") or "全桥"
            disease = evidence_row.get("病害") or "病害事实"
            count = int(evidence_row.get("数量") or evidence_row.get("__archive_count") or 1)
            year = evidence_row.get("年份") or ""
            side = evidence_row.get("幅别") or ""
            location = evidence_row.get("位置") or ""
            level_value = evidence_row.get("标度")
            risk_value = evidence_row.get("风险评分")
            segments = []
            if year:
                segments.append(f"{year}年")
            if side:
                segments.append(str(side))
            segments.append(f"{component}出现{disease}")
            if location:
                segments.append(f"位置{location}")
            if count > 1:
                segments.append(f"数量{count}")
            if level_value not in (None, ""):
                segments.append(f"标度{level_value}")
            if risk_value not in (None, ""):
                segments.append(f"风险{risk_value}")
            source_ref = evidence_row.get("来源定位") or evidence_row.get("报告ID") or "v7_diagnosis"
            state.add_verified_fact(
                statement="，".join(segments),
                source=evidence_row.get("来源") or "v7_pipeline",
                evidence=[str(source_ref)],
                confidence=1.0,
                evidence_id=evidence_row.get("evidence_id") or "",
                report_id=evidence_row.get("报告ID") or "",
                year=str(year),
                bridge_name=evidence_row.get("桥名") or "",
            )

        state.add_constraint("所有工程结论只能引用当前ResidualState中的已验证事实", constraint_type="hard")
        state.add_constraint("缺失Dr/BCI、维修记录或承载能力证据时必须输出条件性结论", constraint_type="hard")
        for semantic_constraint in semantic_plan.get("constraints") or []:
            state.add_constraint(
                semantic_constraint,
                constraint_type="hard",
                source="SemanticFrame",
            )
        for semantic_unknown in semantic_plan.get("unknowns") or []:
            state.unknowns.append(dict(semantic_unknown))
        if valid_dr is None:
            state.unknowns.append({
                "key": "bridge_score",
                "description": "缺少有效Dr评分，需复核原始评分与技术状况等级",
                "impact": "大修、风险与寿命结论只能作条件性判断",
            })
        if valid_bci is None:
            state.unknowns.append({
                "key": "bci_score",
                "description": "缺少有效BCI评分",
                "impact": "不能形成双规范交叉判断",
            })

        # 部位得分(关键!)
        parts = v7_bs.get("部位得分", {}) or v7_bs.get("分项得分", {})
        for score_value, part_name in _flatten_part_scores(parts):
            state.add_verified_fact(
                statement=f"{part_name} 分项得分: {score_value:.1f}分",
                source="v7_score", confidence=1.0,
            )

        # 子任务(根据问题自动推断)。SemanticFrame 会先声明“定位档案”义务，
        # 但预检阶段若已经完成唯一档案绑定，该义务必须在进入推理闭环前结清；
        # 否则控制器会把已经完成的工具工作误判成未解决残差并最终 stalled_empty。
        subs = semantic_plan.get("unresolved") if semantic_frame else None
        subs = list(subs or self._infer_subtasks(question))
        archive_binding_fulfilled = bool(
            evidence_contract.get("facts")
            and evidence_contract.get("reports")
            and bridge_name
        )
        if archive_binding_fulfilled:
            archive_obligations = {
                "定位桥名、年份与对应报告证据",
            }
            subs = [sub for sub in subs if sub not in archive_obligations]
            report_refs = [
                str(report.get("id") or report.get("report_id") or "").strip()
                for report in (evidence_contract.get("reports") or [])
            ]
            report_refs = [ref for ref in report_refs if ref]
            state.add_verified_fact(
                statement=(
                    f"档案证据已定位并绑定：{bridge_name}，"
                    f"报告{len(evidence_contract.get('reports') or [])}份，"
                    f"已验证病害事实{len(evidence_contract.get('facts') or [])}条"
                ),
                source="evidence_contract",
                evidence=report_refs[:6],
                confidence=1.0,
            )
            state.metadata["fulfilled_preconditions"] = ["archive_binding"]
        for sub in subs:
            state.add_unresolved(
                sub,
                blocking_goals=[g.goal_id],
                source="SemanticFrame" if semantic_frame else "keyword_inference",
            )

        # session 也保存 service_years/design_life,下次问不用重传
        if service_years is not None:
            session.metadata["service_years"] = service_years
        if design_life is not None:
            session.metadata["design_life"] = design_life

        # 用 session 里存的兜底
        eff_service = service_years if service_years is not None else \
                      session.metadata.get("service_years")
        eff_design = design_life if design_life is not None else \
                     session.metadata.get("design_life")

        evidence_capabilities = evidence_contract_capabilities({
            **evidence_contract, "facts": evidence_rows,
        })
        available_memory_evidence = {"defect_facts"} if evidence_rows else set()
        if bridge_name:
            available_memory_evidence.add("bridge_identity")
        if evidence_contract.get("reports"):
            available_memory_evidence.add("report_provenance")
        if valid_dr is not None or valid_bci is not None:
            available_memory_evidence.add("dr_or_bci")
        if evidence_capabilities["load_capacity"]:
            available_memory_evidence.add("load_capacity")
        if evidence_capabilities["repair_records"]:
            available_memory_evidence.add("repair_records")
        if evidence_rows:
            available_memory_evidence.update({"severity", "component", "location"})
        if evidence_capabilities.get("design_life"):
            available_memory_evidence.add("design_life")
        if evidence_capabilities.get("service_years"):
            available_memory_evidence.add("service_years")

        memory_context = {
            "years": evidence_contract.get("years") or [],
            "components": sorted({
                str(row.get("构件") or "").strip()
                for row in evidence_rows
                if str(row.get("构件") or "").strip()
            })[:24],
        }
        memory_matches: List[Dict[str, Any]] = []
        if self.euler_memory_matcher and enable_euler_memory:
            try:
                memory_matches = self.euler_memory_matcher.match(
                    question=question,
                    bridge_name=bridge_name,
                    available_evidence=available_memory_evidence,
                    semantic_frame=semantic_frame,
                    session_context=memory_context,
                    limit=3,
                )
            except Exception:
                memory_matches = []
        applicable_memories = [row for row in memory_matches if row.get("can_apply")]
        state.metadata["euler_memory"] = {
            "formula": "z_k = r_k * e^(i*theta_k)",
            "enabled": bool(enable_euler_memory),
            "retrieved": len(memory_matches),
            "applicable": len(applicable_memories),
            "must_reverify": True,
            "top_phase_match": (
                (memory_matches[0].get("phase_match") or {}) if memory_matches else {}
            ),
        }
        if applicable_memories:
            state.add_constraint(
                "欧拉相位记忆只复用已审核推理步骤；不得复制桥梁事实或最终答案，且本轮必须重新验证",
                constraint_type="hard",
                source="EulerMemory",
            )
        if any(k in question for k in ("大修", "值不值", "值得")):
            if not evidence_capabilities["load_capacity"]:
                state.unknowns.append({
                    "key": "load_capacity",
                    "description": "缺少承载能力、荷载试验或结构验算证据",
                    "impact": "不能直接决定全桥大修范围",
                })
            if not evidence_capabilities["repair_records"]:
                state.unknowns.append({
                    "key": "repair_records",
                    "description": "缺少同位置维修闭环记录",
                    "impact": "不能确认既往病害是否已修复或复发",
                })
        if any(term in question for term in ("寿命", "剩余", "还能用", "服役")):
            state.unknowns.append({
                "key": "residual_life_model",
                "description": "当前推理路径没有可审计、经校准的剩余寿命模型",
                "impact": "不能把评分、设计寿命或服役年限换算成剩余使用寿命",
            })
        if any(term in question for term in ("成本", "费用", "花钱", "预算", "多少钱", "造价", "修费")):
            state.unknowns.append({
                "key": "cost_model",
                "description": "公开推理路径未提供已校准、可审计的造价适配器",
                "impact": "即使提供工程量或预算资料，也不能生成本系统重新估算的金额",
            })
            if not evidence_capabilities.get("cost_basis"):
                state.unknowns.append({
                    "key": "cost_basis",
                    "description": "缺少可追溯且适用的工程量、处治方案、地区单价或审定预算依据",
                    "impact": "不能用默认跨数、跨长或经验单价替代造价输入",
                })

        initial_residual_view = {
            "residual_id": state.residual_id,
            "status": state.status.value,
            "progress": state.compute_progress(),
            "goals": [goal.__dict__.copy() for goal in state.goals],
            "unresolved_items": [item.__dict__.copy() for item in state.unresolved_items],
            "unknowns": list(state.unknowns),
            "constraints": [constraint.__dict__.copy() for constraint in state.constraints],
            "verified_count": state.verified_count(),
            "metadata": dict(state.metadata),
        }

        # 2. 跑推理闭环
        final, summary = self.controller.run(
            initial_residual=state,
            context={
                "question": question,
                "observations": observations,
                "service_years": eff_service,
                "design_life": eff_design,
                "evidence_capabilities": evidence_capabilities,
                "approved_memory_patterns": [
                    {
                        "memory_id": row.get("memory_id"),
                        "derivation_steps": (row.get("payload") or {}).get("derivation_steps") or [],
                        "phase_match": row.get("phase_match") or {},
                        "must_reverify": True,
                    }
                    for row in applicable_memories
                ],
                "evidence_rows": evidence_rows,
                "v7_data": {
                    "bridge_score": v7_bs,
                    "bridge_score_52xx": v7_bci,
                    "structure": v7_struct,
                    "stats": v7_stats,
                    "service_years": eff_service,
                    "design_life": eff_design,
                },
            },
        )

        # 3. 合成回复
        # 骨架(纯符号)
        response = self.strict.synthesize(final, question=question)

        # 再过一遍 LLM 风格化(如果可用)
        # 三档发散系统只作用于"表达层",事实骨架仍由 StrictOrchestrator 生成。
        level = normalize_persona_level(persona_level)
        if enable_style_llm and self.bridge.llm:
            try:
                base_style_prompt = (
                    "下面是符号引擎给出的硬数据骨架(都是已验证的事实)。\n"
                    "请把它改写成一段连贯回答。\n"
                    "严禁添加新的事实/数据/规范引用,只做语言组织。\n"
                    "不要用 markdown,用普通段落。\n"
                    "\n"
                    "🔧 v0.7.3 关键约束:\n"
                    "1. 如果骨架里含【条件性结论】/【直接结论】/【整体风险】/【寿命估算】/【预算估算】/【优先维修顺序】"
                    "等明确标签,你的回答必须把这些标签的核心结论作为开头第一句直接给出。\n"
                    "2. 例如骨架里说'【直接结论】不需要大修。II类...',你的回答要以"
                    "'本桥不需要大修。'或'结论:这桥目前不需要大修,但...'开头。\n"
                    "3. 不能把直接结论隐藏在数据描述后面或干脆删除。"
                )
                style_prompt = decorate_prompt(base_style_prompt, level, task="reasoner_style_refinement")
                # 🔧 v0.6.3: 全链路传 temperature/top_p
                sampling = sampling_kwargs(level)
                temp = sampling.get('temperature', 0.5)
                top_p = sampling.get('top_p', 0.85)
                user_msg = (f"用户问题:{question}\n\n"
                            f"符号引擎的结论骨架:\n{response.skeleton}")
                refined = self.bridge.llm._call_zhipu(style_prompt, user_msg,
                                                       temperature=temp, top_p=top_p)
                # 检查:如果 LLM 调用失败(返回含"失败"的字符串),用骨架
                if refined and "调用失败" not in refined:
                    # 🔧 v0.6.6: 后处理过滤编造的规范号
                    cleaned, removed_specs = sanitize_regulations(refined)

                    # 🔧 v0.7.3: 强制保留"直接结论"标签
                    # 如果骨架里有【直接结论】/【整体风险】等句但 LLM 改写后丢了,
                    # 把骨架的结论句顶到 LLM 回复前面
                    direct_markers = ["【条件性结论】", "【直接结论】", "【整体风险】", "【寿命估算】",
                                       "【预算估算】", "【优先维修顺序】"]
                    skel = response.skeleton or ""
                    for marker in direct_markers:
                        if marker in skel and marker not in cleaned:
                            # 从骨架提取该标签的整句
                            idx = skel.find(marker)
                            line_end = skel.find("\n", idx)
                            if line_end < 0: line_end = len(skel)
                            direct_line = skel[idx:line_end].strip()
                            # 限制长度避免太长
                            if len(direct_line) > 250:
                                direct_line = direct_line[:250] + "..."
                            cleaned = direct_line + "\n\n" + cleaned
                            break  # 只补一个最关键的

                    response.reply = cleaned
                    response.llm_refined = True
                    if removed_specs:
                        response.metadata = getattr(response, 'metadata', {}) or {}
                        response.metadata['removed_fake_specs'] = removed_specs
            except Exception as e:
                pass  # 失败就用骨架

        # 4. 知识蒸馏(成功时)
        distill_info = None
        memory_candidate = None
        if final.is_solved():
            try:
                distill_info = self.distill_pipeline.process(
                    summary.transitions, final,
                )
            except Exception:
                pass
            if self.euler_memory:
                try:
                    verifier_names = [
                        type(checker).__name__
                        for checker in (list(self.verifier._hard) + list(self.verifier._soft))
                    ]
                    memory_candidate = self.euler_memory.extract_pending(
                        question=question,
                        final_state=final,
                        run_summary=summary,
                        verifier_names=verifier_names,
                        bridge_name=bridge_name,
                        semantic_frame=semantic_frame,
                        session_context=memory_context,
                    )
                except Exception:
                    memory_candidate = None

        step_records = []
        memory_guided_steps = 0
        applied_memory_ids = []
        for step in summary.steps:
            for candidate in list(step.candidates or []):
                action = candidate.get("action") or {}
                if action.get("memory_guided"):
                    memory_guided_steps += 1
                    memory_id = action.get("memory_id")
                    if memory_id and memory_id not in applied_memory_ids:
                        applied_memory_ids.append(memory_id)
            step_records.append({
                "step_index": step.step_index,
                "from_residual_id": step.from_residual_id,
                "to_residual_id": step.to_residual_id,
                "candidates_proposed": step.candidates_proposed,
                "candidates_accepted": step.candidates_accepted,
                "candidates_rejected": step.candidates_rejected,
                "candidates_deferred": step.candidates_deferred,
                "candidates_interrupted": step.candidates_interrupted,
                "winning_proposer": step.winning_proposer,
                "is_progressive": step.is_progressive,
                "candidates": list(step.candidates),
                "decisions": list(step.decisions),
                "escalation_follow_up": step.escalation_follow_up,
            })

        state_store = getattr(self.bridge, "state_store", None)
        if state_store:
            checkpoint_payload = {
                "schema_version": "residual-checkpoint.v1",
                "run_id": summary.run_id,
                "question": question,
                "final_status": final.status.value,
                "stop_reason": summary.stop_reason,
                "steps": step_records,
                "verified_fact_ids": [fact.fact_id for fact in final.verified_facts],
                "unknowns": list(final.unknowns),
                "constraints": [constraint.__dict__.copy() for constraint in final.constraints],
            }
            state_hash = hashlib.sha256(
                json.dumps(
                    checkpoint_payload, ensure_ascii=False, sort_keys=True, default=str,
                ).encode("utf-8")
            ).hexdigest()
            try:
                state_store.save_checkpoint(
                    summary.run_id,
                    summary.step_count,
                    "reasoning_completed",
                    state_hash,
                    checkpoint_payload,
                )
            except Exception:
                pass

        return {
            "reply": response.reply,
            "reasoning_mode": True,
            "engine": "residual_verified_runtime",
            "run_id": summary.run_id,
            "question": question,
            "final_state_status": final.status.value,
            "step_count": summary.step_count,
            "stop_reason": summary.stop_reason,
            "verified_facts": [f.statement for f in final.verified_facts],
            "verified_fact_records": [
                {
                    "fact_id": fact.fact_id,
                    "statement": fact.statement,
                    "source": fact.source,
                    "evidence": list(fact.evidence),
                    "confidence": fact.confidence,
                    "metadata": dict(fact.metadata),
                }
                for fact in final.verified_facts
            ],
            "steps": step_records,
            "transitions": [t.to_dict() for t in summary.transitions],
            "residual": {
                "initial": initial_residual_view,
                "final": {
                    "residual_id": final.residual_id,
                    "status": final.status.value,
                    "progress": final.compute_progress(),
                    "goals": [goal.__dict__.copy() for goal in final.goals],
                    "unresolved_items": [item.__dict__.copy() for item in final.unresolved_items],
                    "unknowns": list(final.unknowns),
                    "constraints": [constraint.__dict__.copy() for constraint in final.constraints],
                    "blocked_items": [item.__dict__.copy() for item in final.blocked_items],
                    "verified_count": final.verified_count(),
                    "metadata": dict(final.metadata),
                },
            },
            "semantic_plan": semantic_plan,
            "distilled": distill_info,
            "memory_candidate": {
                "candidate_id": memory_candidate.get("candidate_id"),
                "review_status": memory_candidate.get("review_status"),
                "task_type": memory_candidate.get("task_type"),
                "confidence": memory_candidate.get("confidence"),
            } if memory_candidate else None,
            "euler_memory": {
                "formula": "z_k = r_k * e^(i*theta_k)",
                "coordinate_system": "deterministic_multi_axis_context",
                "enabled": bool(enable_euler_memory),
                "retrieved": len(memory_matches),
                "applicable": len(applicable_memories),
                "applied": bool(memory_guided_steps),
                "guided_steps": memory_guided_steps,
                "applied_memory_ids": applied_memory_ids,
                "must_reverify": True,
                "matches": [
                    {
                        "memory_id": row.get("memory_id"),
                        "task_type": row.get("task_type"),
                        "match_score": row.get("match_score"),
                        "can_apply": row.get("can_apply"),
                        "phase_status": row.get("phase_status"),
                        "phase_match": row.get("phase_match") or {},
                        "missing_evidence": row.get("missing_evidence") or [],
                    }
                    for row in memory_matches
                ],
                "candidate": {
                    "candidate_id": memory_candidate.get("candidate_id"),
                    "review_status": memory_candidate.get("review_status"),
                    "schema_version": ((memory_candidate.get("payload") or {}).get("schema_version")),
                    "phase_axis_count": len(
                        (((memory_candidate.get("payload") or {}).get("context_phase") or {}).get("axes") or [])
                    ),
                } if memory_candidate else None,
            },
            "library_size": self.distill_library.size(),
            "strict_skeleton": response.skeleton,
            "llm_refined": response.llm_refined,
            "persona_level": level,
            "persona_label": self.bridge.divergence.label(level) if hasattr(self.bridge, "divergence") else level,
        }

    def _empty_response(self) -> Dict[str, Any]:
        return {
            "reply": ("🧠 深度推理需要先有桥梁数据才能工作。\n\n"
                      "请先在聊天框里描述病害(如:'主梁斜向裂缝0.3mm标度3位于泉州'),"
                      "或上传检测报告 .docx 文件。\n\n"
                      "等系统识别出 Dr/BCI 等硬数据后,再点 🧠 深度推理就能问"
                      "'值不值得大修''剩余寿命多少年'这类复杂问题。"),
            "reasoning_mode": True,
            "final_state_status": "empty",
            "step_count": 0,
            "stop_reason": "no_observations",
            "verified_facts": [],
            "transitions": [],
            "distilled": None,
            "library_size": 0,
            "strict_skeleton": "",
            "llm_refined": False,
            "needs_upload": True,
        }

    def _infer_subtasks(self, question: str) -> List[str]:
        """根据问题推断子任务"""
        subs = []
        # “值不值大修”是工程决策问题，不等于用户同时索要寿命和造价。
        if any(k in question for k in ["值不值", "值得大修", "要不要大修", "是否需要大修",
                                         "应不应该大修", "实施大修", "全桥大修"]):
            subs.append("整体风险评估")
        if any(k in question for k in ["寿命", "剩余", "还能用", "服役"]):
            if "预估剩余使用寿命" not in subs:
                subs.append("预估剩余使用寿命")
        if any(k in question for k in ["费用", "成本", "花钱", "预算", "多少钱", "造价", "修费"]):
            if "预估大修成本" not in subs:
                subs.append("预估大修成本")
        if any(k in question for k in ["优先", "顺序", "先修", "重点修", "重点修哪",
                                          "重要的", "先做", "最严重的几条", "最严重哪",
                                          "哪些要重点"]):
            subs.append("构件维修优先级")
        if any(k in question for k in ["风险", "严重", "安全", "危险"]):
            if "整体风险评估" not in subs:
                subs.append("整体风险评估")
        if not subs:
            subs.append("基于已有数据综合分析")
        return subs

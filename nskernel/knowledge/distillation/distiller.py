"""
KnowledgeDistiller · 知识蒸馏(反哺核心:系统越用越聪明)
=========================================================

从成功的推理会话里提炼可复用知识,经审核后入库。

设计(严格对齐 Reasoner 的设计):

  1. 每次 RuntimeController 成功 run 完(residual solved),
     触发 distill → 提炼 RuleDistiller/FormulaDistiller/AlgorithmDistiller
  2. 产生 DistilledCandidate
  3. 经 ReviewPipeline 4 项审核(trace/consistency/generalization/executability)
  4. 通过 → 写入 FormulaLibrary/RuleLibrary/AlgorithmLibrary
  5. 下次同类问题 → 直接查表,跳过 LLM

价值:
  - 每成功一次,knowledge 长一寸
  - 对垂域 AI 是资产积累机制
  - 和 FormulaProposer 联动:库里有的直接用,库里没有才走 LLM
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from nskernel.dialogue.reasoning import (
    ResidualState, StateTransition,
)


# ══════════════════════════════════════════════════════════════
# DistilledCandidate 蒸馏候选
# ══════════════════════════════════════════════════════════════
class KnowledgeType(str, Enum):
    RULE = "rule"             # 规则:if X then Y
    FORMULA = "formula"       # 公式:可复用的计算
    ALGORITHM = "algorithm"   # 算法:多步骤的解题套路
    PATTERN = "pattern"       # 模式:常见问题-解答对


@dataclass
class DistilledCandidate:
    """一个从推理会话里提炼出的知识候选"""
    candidate_id: str = field(default_factory=lambda: f"DC_{uuid.uuid4().hex[:8]}")
    knowledge_type: KnowledgeType = KnowledgeType.RULE

    # 知识本体(具体格式依 type)
    content: Dict[str, Any] = field(default_factory=dict)

    # 来源追踪
    source_transitions: List[str] = field(default_factory=list)  # transition_ids
    source_candidates: List[str] = field(default_factory=list)   # candidate_ids

    # 元信息
    confidence: float = 0.5
    usage_count: int = 0      # 未来再次被触发的次数
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# AdmissionDecision 准入决策
# ══════════════════════════════════════════════════════════════
class AdmissionAction(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"


@dataclass
class AdmissionDecision:
    """知识准入决策"""
    candidate_id: str
    action: AdmissionAction
    reason_codes: List[str] = field(default_factory=list)
    message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_approved(self) -> bool:
        return self.action == AdmissionAction.APPROVED


# ══════════════════════════════════════════════════════════════
# Distiller 基类 + 3 种具体
# ══════════════════════════════════════════════════════════════
class DistillerBase:
    """蒸馏器基类"""
    def distill(
        self,
        transitions: List[StateTransition],
        final_state: ResidualState,
    ) -> List[DistilledCandidate]:
        return []


class RuleDistiller(DistillerBase):
    """
    从 transitions 里提炼规则

    MVP 策略:
      如果某次 transition 的 candidate 用了 HEURISTIC 来源,且成功推进了 residual,
      就认为这是一个有价值的"规则",提炼它的 (触发条件,产出) 对
    """
    def distill(self, transitions, final_state):
        candidates = []
        import re as _re
        SPECIFIC_PATTERN = (
            r"\d|主梁|桥墩|桥台|盖梁|横隔板|支座|伸缩缝|护栏|"
            r"裂缝|破损|露筋|锈蚀|脱空|渗水|JTG|Dr|BCI|"
            r"年|万元|严控|风险"
        )

        for t in transitions:
            if not t.is_progressive():
                continue
            if not t.summary or not t.removed_unresolved_items:
                continue

            # 从 transition 添加的 facts 里抽含具体内容的(注意:dict 格式)
            relevant_fact = ""
            for added in (t.added_verified_facts or []):
                if not isinstance(added, dict): continue
                stmt = added.get("statement", "") or ""
                if stmt and _re.search(SPECIFIC_PATTERN, stmt):
                    relevant_fact = stmt[:140]
                    break

            # 优先用产出的具体事实 当 description
            if relevant_fact:
                desc = f"规则:{relevant_fact}"
            else:
                # 没具体 fact → 跳过(防止"基于()的估算"这种垃圾)
                continue

            if len(desc) < 20:
                continue

            candidates.append(DistilledCandidate(
                knowledge_type=KnowledgeType.RULE,
                content={
                    "trigger": t.summary[:80],  # 真正的触发/前提
                    "action": "应用此规则得出结果",
                    "fact": relevant_fact,
                    "resolved_items": t.removed_unresolved_items,
                },
                source_transitions=[t.transition_id],
                source_candidates=[t.candidate_id] if t.candidate_id else [],
                confidence=0.7,  # 高于 floor 0.6
                description=desc,
            ))
        return candidates


class FormulaDistiller(DistillerBase):
    """
    从 transitions 里提炼公式

    MVP 策略:
      如果某 transition 的 candidate 是 FORMULA 来源(即用了已有公式成功),
      不产生新公式;只对"启发式推导出了一个可重复计算的关系"提炼公式。
      (真实实现应该用 SymPy 检查表达式)

    这里只给一个占位骨架,真正的公式提炼 Reasoner 里有专门实现。
    """
    def distill(self, transitions, final_state):
        # MVP:暂不提炼(需要 symbolic 分析)
        return []


class AlgorithmDistiller(DistillerBase):
    """
    从 transitions 序列里提炼算法

    MVP 策略:
      如果一整次 run 的 transitions 按顺序解决了同一 goal,
      把这个"路径"本身作为一个 algorithm 提炼出来

    例:
      法律域某次 run 的路径:
        1. 先查时效 → 2. 再查证据 → 3. 最后综合判断
      这就是"劳动争议胜诉可能性评估"的算法骨架
    """
    def distill(self, transitions, final_state):
        if not transitions:
            return []
        if not final_state.is_solved():
            return []

        # 组装成一个 algorithm
        steps = []
        for t in transitions:
            steps.append({
                "from": t.from_residual_id,
                "to": t.to_residual_id,
                "summary": t.summary or "",
                "candidate_id": t.candidate_id,
            })

        # description 含 goal + step 关键词 + 具体fact
        goal_text = ""
        if final_state.goals:
            goal_text = (final_state.goals[0].description or "")[:60]

        # 抽 verified_facts 里第一个含具体数字的(让 description 有内容)
        import re as _re
        first_concrete = ""
        for f in (final_state.verified_facts or []):
            stmt = f.statement or ""
            if _re.search(
                r"\d.*(年|万元|分|条|处|m|mm|kg)|"
                r"(主梁|桥墩|桥台|盖梁|横隔板|支座|伸缩缝|护栏)",
                stmt,
            ):
                first_concrete = stmt[:80]
                break

        if not goal_text or not first_concrete:
            return []

        desc = f"算法:{goal_text} → {first_concrete} (经 {len(steps)} 步推导)"
        if len(desc) < 30:
            return []

        return [DistilledCandidate(
            knowledge_type=KnowledgeType.ALGORITHM,
            content={
                "goal": ([g.description for g in final_state.goals]
                         if final_state.goals else []),
                "steps": steps,
                "step_count": len(steps),
            },
            source_transitions=[t.transition_id for t in transitions],
            confidence=0.75,
            description=desc,
        )]


# ══════════════════════════════════════════════════════════════
# KnowledgeDistiller 总蒸馏器
# ══════════════════════════════════════════════════════════════
class KnowledgeDistiller:
    """聚合多种蒸馏器"""
    def __init__(
        self,
        rule_distiller: Optional[RuleDistiller] = None,
        formula_distiller: Optional[FormulaDistiller] = None,
        algorithm_distiller: Optional[AlgorithmDistiller] = None,
    ):
        self.rule = rule_distiller or RuleDistiller()
        self.formula = formula_distiller or FormulaDistiller()
        self.algorithm = algorithm_distiller or AlgorithmDistiller()

    def distill(
        self,
        transitions: List[StateTransition],
        final_state: ResidualState,
    ) -> List[DistilledCandidate]:
        all_cands: List[DistilledCandidate] = []
        all_cands.extend(self.rule.distill(transitions, final_state))
        all_cands.extend(self.formula.distill(transitions, final_state))
        all_cands.extend(self.algorithm.distill(transitions, final_state))
        return all_cands


# ══════════════════════════════════════════════════════════════
# ReviewPipeline · 4 项审核
# ══════════════════════════════════════════════════════════════
def trace_review(cand: DistilledCandidate) -> "tuple[bool, List[str], Dict]":
    """可追溯:必须有 source_transitions 或 source_candidates"""
    if not cand.source_transitions and not cand.source_candidates:
        return False, ["no_source_trace"], {}
    return True, [], {}


def consistency_review(cand: DistilledCandidate) -> "tuple[bool, List[str], Dict]":
    """一致性:content 不能为空"""
    if not cand.content:
        return False, ["empty_content"], {}
    return True, [], {}


def generalization_review(cand: DistilledCandidate) -> "tuple[bool, List[str], Dict]":
    """
    泛化性:不能是 N=1 的特殊情况

    MVP 策略:
      - Algorithm 至少要有 2 步(单步的不算"算法")
      - Rule 必须有 trigger 字段
    """
    t = cand.knowledge_type
    if t == KnowledgeType.ALGORITHM:
        if cand.content.get("step_count", 0) < 2:
            return False, ["algorithm_too_simple"], {}
    elif t == KnowledgeType.RULE:
        if not cand.content.get("trigger"):
            return False, ["no_trigger"], {}
    return True, [], {}


def executability_review(cand: DistilledCandidate) -> "tuple[bool, List[str], Dict]":
    """可执行:必须有 action 或 steps"""
    has_action = cand.content.get("action") or cand.content.get("steps")
    if not has_action:
        return False, ["no_executable_content"], {}
    return True, [], {}


# ══════════════════════════════════════════════════════════════
# 🆕 v0.5.1 严格内容质量 checker(防止"垃圾知识"入库)
# ══════════════════════════════════════════════════════════════
import re

def meaningful_content_review(cand: DistilledCandidate) -> "tuple[bool, List[str], Dict]":
    """
    严格内容质量审核:防止 description 是模板未填充/空话/无信息密度

    拒绝:
      · description 含未填充的占位符 ('()'/'??'/'_'/'未知'/'?')
      · description 长度 < 15 字
      · description 末尾以 "估算"/"判断"/"分析" 这类空动词结尾
      · description 全是结构性描述无具体内容("4 步解决的工作流"等)
    """
    desc = (cand.description or "").strip()
    codes = []

    if len(desc) < 15:
        codes.append("desc_too_short")

    # 未填充占位符
    if re.search(r"\(\s*\)|\?\?|__|未知|占位", desc):
        codes.append("contains_unfilled_placeholder")

    # 空动词结尾(无具体内容)
    if re.search(r"(估算|判断|分析|评估|计算)$|经验估算$", desc):
        codes.append("vague_ending")

    # "N 步工作流"这种通用描述
    if re.search(r"\d+\s*步\s*解决的(工作流|流程|步骤)", desc):
        codes.append("generic_workflow")

    # 必须含至少 1 个具体词(数字/规范号/构件名/病害名)
    has_specific = bool(re.search(
        r"\d|JTG|JT\b|主梁|桥墩|桥台|盖梁|横隔板|支座|伸缩缝|护栏|"
        r"裂缝|破损|露筋|锈蚀|脱空|渗水|剥落|蜂窝|网状|麻面",
        desc,
    ))
    if not has_specific:
        codes.append("no_specific_concrete_term")

    if codes:
        return False, codes, {"description": desc}
    return True, [], {}


def confidence_floor_review(cand: DistilledCandidate) -> "tuple[bool, List[str], Dict]":
    """置信度底线:< 0.6 自动拒(可由人工审核救回)"""
    if cand.confidence < 0.6:
        return False, ["confidence_below_floor"], {"confidence": cand.confidence}
    return True, [], {}


def evidence_required_review(cand: DistilledCandidate) -> "tuple[bool, List[str], Dict]":
    """
    证据要求:
      · Rule 类型必须 source_transitions 或 source_candidates 至少 1 条
      · Algorithm 类型必须 step_count >= 3(2 步以下不构成算法)
      · 必须有 confidence 字段
    """
    t = cand.knowledge_type
    codes = []
    if not cand.source_transitions and not cand.source_candidates:
        codes.append("no_evidence_trace")
    if t == KnowledgeType.ALGORITHM:
        steps = cand.content.get("step_count", 0)
        if steps < 3:
            codes.append("algorithm_steps_lt_3")
    if cand.confidence is None:
        codes.append("no_confidence")
    if codes:
        return False, codes, {}
    return True, [], {}


def domain_relevance_review(cand: DistilledCandidate) -> "tuple[bool, List[str], Dict]":
    """
    领域相关性:description 必须含本域专业术语,否则该知识不属于本域

    本域术语清单(桥梁):构件名/病害名/规范号/评分体系
    """
    desc = (cand.description or "")
    bridge_terms = [
        # 构件
        "主梁", "桥墩", "桥台", "盖梁", "横隔板", "支座", "伸缩缝",
        "护栏", "雨篦", "湿接缝", "T梁", "翼缘", "桥面", "铺装",
        "拉索", "悬索", "拱肋",
        # 病害
        "裂缝", "破损", "露筋", "锈蚀", "脱空", "渗水", "剥落",
        "蜂窝", "麻面", "网状", "空洞", "锈胀", "堵塞", "缺失",
        # 规范+评分
        "JTG", "Dr", "BCI", "类技术等级",
        # 工程语境
        "标度", "严控", "高风险", "处治", "维修", "养护", "加固",
    ]
    if any(term in desc for term in bridge_terms):
        return True, [], {}
    return False, ["not_domain_relevant"], {"description": desc}


class ReviewPipeline:
    """8 项严格审核流水线(v0.5.1: 防止垃圾知识入库)"""

    DEFAULT_CHECKS = [
        # 原 4 个格式 checker
        ("trace_review", trace_review),
        ("consistency_review", consistency_review),
        ("generalization_review", generalization_review),
        ("executability_review", executability_review),
        # 🆕 v0.5.1: 4 个内容质量 checker
        ("meaningful_content_review", meaningful_content_review),
        ("confidence_floor_review", confidence_floor_review),
        ("evidence_required_review", evidence_required_review),
        ("domain_relevance_review", domain_relevance_review),
    ]

    def __init__(self, checks=None):
        self._checks = checks or self.DEFAULT_CHECKS

    def review(
        self,
        candidate: DistilledCandidate,
    ) -> "tuple[Dict, AdmissionDecision]":
        report: Dict[str, Any] = {
            "candidate_id": candidate.candidate_id,
            "checks": {},
            "reason_codes": [],
        }
        passed = True
        for check_name, check_fn in self._checks:
            ok, codes, details = check_fn(candidate)
            report["checks"][check_name] = {"ok": ok, "codes": codes, "details": details}
            report["reason_codes"].extend(codes)
            if not ok:
                passed = False

        if passed:
            decision = AdmissionDecision(
                candidate_id=candidate.candidate_id,
                action=AdmissionAction.APPROVED,
                reason_codes=report["reason_codes"],
                message="知识候选通过审核,允许入库。",
            )
        else:
            decision = AdmissionDecision(
                candidate_id=candidate.candidate_id,
                action=AdmissionAction.REJECTED,
                reason_codes=report["reason_codes"],
                message="知识候选未通过审核,不入库。",
            )
        return report, decision


# ══════════════════════════════════════════════════════════════
# Library 知识库骨架
# ══════════════════════════════════════════════════════════════
class KnowledgeLibrary:
    """
    通用知识库 - 存放蒸馏审核后的知识

    v0.5.1 新增:
      · _pending 队列: 自动审核通过的进 pending,等待人工审核
      · approve(id) / reject(id): 人工审核接口
      · require_human_review: True=入库前人工审核 / False=自动入库

    支持按 type/usage_count/confidence 查询
    """
    def __init__(self, require_human_review: bool = False):
        self._store: Dict[str, DistilledCandidate] = {}
        self._pending: Dict[str, DistilledCandidate] = {}  # 待审核
        self._rejected: Dict[str, DistilledCandidate] = {}  # 已拒绝(留痕迹)
        self.require_human_review = require_human_review

    def admit(self, candidate: DistilledCandidate) -> str:
        """
        准入 - 根据 require_human_review 决定直接入库还是入 pending

        Returns: "approved" or "pending"
        """
        if self.require_human_review:
            self._pending[candidate.candidate_id] = candidate
            return "pending"
        self._store[candidate.candidate_id] = candidate
        return "approved"

    def approve(self, cand_id: str, reviewer: str = "human") -> bool:
        """人工审核通过 → 进 _store"""
        if cand_id not in self._pending:
            return False
        cand = self._pending.pop(cand_id)
        cand.metadata = cand.metadata or {}
        cand.metadata["approved_by"] = reviewer
        from datetime import datetime
        cand.metadata["approved_at"] = datetime.now().isoformat(timespec='seconds')
        self._store[cand_id] = cand
        return True

    def reject(self, cand_id: str, reviewer: str = "human", reason: str = "") -> bool:
        """人工拒绝 → 进 _rejected"""
        if cand_id not in self._pending:
            return False
        cand = self._pending.pop(cand_id)
        cand.metadata = cand.metadata or {}
        cand.metadata["rejected_by"] = reviewer
        cand.metadata["reject_reason"] = reason
        from datetime import datetime
        cand.metadata["rejected_at"] = datetime.now().isoformat(timespec='seconds')
        self._rejected[cand_id] = cand
        return True

    def list_pending(self) -> List[DistilledCandidate]:
        """列出待审核"""
        return list(self._pending.values())

    def list_rejected(self) -> List[DistilledCandidate]:
        return list(self._rejected.values())

    def pending_count(self) -> int:
        return len(self._pending)

    def get(self, cand_id: str) -> Optional[DistilledCandidate]:
        return self._store.get(cand_id) or self._pending.get(cand_id)

    def list_by_type(self, ktype: KnowledgeType) -> List[DistilledCandidate]:
        return [c for c in self._store.values() if c.knowledge_type == ktype]

    def size(self) -> int:
        """已审核入库的数量"""
        return len(self._store)

    def all(self) -> List[DistilledCandidate]:
        return list(self._store.values())

    def bump_usage(self, cand_id: str) -> None:
        if cand_id in self._store:
            self._store[cand_id].usage_count += 1


# ══════════════════════════════════════════════════════════════
# DistillationPipeline - 端到端蒸馏
# ══════════════════════════════════════════════════════════════
class DistillationPipeline:
    """
    端到端的蒸馏管线:
      RuntimeController.run() 成功后,调 pipeline.process(transitions, final_state)
      自动完成 distill → review → admit
    """
    def __init__(
        self,
        distiller: Optional[KnowledgeDistiller] = None,
        review_pipeline: Optional[ReviewPipeline] = None,
        library: Optional[KnowledgeLibrary] = None,
    ):
        self.distiller = distiller or KnowledgeDistiller()
        self.review = review_pipeline or ReviewPipeline()
        self.library = library or KnowledgeLibrary()

    def process(
        self,
        transitions: List[StateTransition],
        final_state: ResidualState,
    ) -> Dict[str, Any]:
        """
        Returns: {
            "distilled": N,              # 蒸馏出多少候选
            "approved": N,               # 自动审核通过的(直接入库或进 pending)
            "rejected": N,               # 自动审核拒绝的
            "to_human_review": N,        # 进 pending 等人工审核的
            "auto_admitted": N,          # 直接入 _store 的
            "library_size": N,           # 当前 _store 大小
            "pending_count": N,          # 当前 _pending 大小
            "details": [...]
        }
        """
        cands = self.distiller.distill(transitions, final_state)
        approved = 0
        rejected = 0
        to_human = 0
        auto_admitted = 0
        details = []
        for c in cands:
            report, decision = self.review.review(c)
            if decision.is_approved():
                # 自动审核通过 → 看 library 是否需要人工再审
                action = self.library.admit(c)
                approved += 1
                if action == "pending":
                    to_human += 1
                else:
                    auto_admitted += 1
            else:
                rejected += 1
            details.append({
                "candidate_id": c.candidate_id,
                "type": c.knowledge_type.value if hasattr(c.knowledge_type, 'value') else str(c.knowledge_type),
                "description": c.description,
                "action": decision.action.value,
                "reason_codes": decision.reason_codes,
            })
        return {
            "distilled": len(cands),
            "approved": approved,
            "rejected": rejected,
            "to_human_review": to_human,
            "auto_admitted": auto_admitted,
            "library_size": self.library.size(),
            "pending_count": self.library.pending_count(),
            "details": details,
        }

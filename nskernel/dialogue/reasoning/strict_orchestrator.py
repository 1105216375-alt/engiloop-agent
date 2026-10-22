"""
StrictOrchestrator · 纯 Kautz Type 6 严格模式(RRI-12)
========================================================

对比现有的(flexible)Orchestrator:
  flexible:  LLM 合成最终回复(可能幻觉)
  strict:    符号层基于 verified_facts 组装 → LLM 只做风格化 → 再过 verifier

适用场景:
  - 法律意见书(错一个字可能败诉)
  - 医疗分诊建议(剂量/禁忌绝对不能错)
  - 金融合规分析(数字必须精确)
  - 任何"专业人士替代"场景

⚠️ 当前版本:**骨架 + 基础实现**(RRI-12 的基线)
   真正的 FactRealizer(把事实转自然语言)需要领域提供模板
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from nskernel.contracts import DomainProfile
    from nskernel.dialogue.reasoning import (
        ResidualState, VerifiedFact,
    )
    from nskernel.dialogue.session import Session
    from nskernel.verification import ClaimVerifier
    from nskernel.llm import LLMClientBase


# ══════════════════════════════════════════════════════════════
# FactRealizer · 把 VerifiedFact 组装成自然语言骨架
# ══════════════════════════════════════════════════════════════
class FactRealizer:
    """
    把 verified_facts 组装成**结构化骨架文字**(确保不丢信息)

    领域可以通过重写 realize 来提供更专业的措辞
    """

    def realize(
        self,
        verified_facts: List["VerifiedFact"],
        goals_context: Optional[List[str]] = None,
    ) -> str:
        """
        把一组事实渲染成自然语言骨架

        默认实现:编号列表
        领域应该 override 这个方法,按业务模板组装
        """
        if not verified_facts:
            return "(暂无已验证的事实)"

        lines = []
        if goals_context:
            lines.append("针对目标:")
            for g in goals_context:
                lines.append(f"  · {g}")
            lines.append("")

        lines.append("已验证的事实:")
        for i, f in enumerate(verified_facts, 1):
            stmt = f.statement
            src = f.source
            src_str = f" [来源: {src}]" if src else ""
            lines.append(f"  {i}. {stmt}{src_str}")

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# StyleRefinement · LLM 只做风格化,不准改事实
# ══════════════════════════════════════════════════════════════
class StyleRefinement:
    """
    用 LLM 把骨架文字改写为更自然的表述
    但在 prompt 里严格限制:只改风格,不改事实/不加内容/不删信息

    这一步可选:strict 模式可以完全不用,纯符号合成也行
    """

    STRICT_SYSTEM_PROMPT = """你是一个严谨的语言编辑,职责只有一个:
把下方的"事实骨架"改写成更自然、更流畅的语言。

**绝对不可**:
  - 添加任何新事实/新数据/新判断
  - 删除或弱化任何已有事实
  - 修改任何数字/姓名/日期/引用
  - 做推断/预测/建议(骨架没提过就不能说)

**只允许**:
  - 调整语序让表述更顺
  - 把列表改写成段落(但信息一对一保留)
  - 补充连接词("此外"、"因此")让文本连贯

如果骨架包含 N 条事实,你的回复必须包含这 N 条事实,一条不多一条不少。
"""

    def __init__(
        self,
        llm_client: Optional["LLMClientBase"] = None,
        claim_verifier: Optional["ClaimVerifier"] = None,
    ):
        self.llm = llm_client
        self.verifier = claim_verifier

    def refine(
        self,
        skeleton: str,
        facts: List["VerifiedFact"],
        max_retry: int = 2,
    ) -> str:
        """
        把骨架交给 LLM 改写,输出再过 verifier

        如果 verifier 发现 LLM 加戏/改事实,自动降级到原骨架
        """
        if self.llm is None:
            # 无 LLM -> 直接返回骨架
            return skeleton

        # 约束 prompt:告诉 LLM 有哪些事实必须一对一保留
        fact_checklist = "\n".join(
            f"  - {f.statement}" for f in facts
        )
        user_msg = (
            f"【事实骨架】\n{skeleton}\n\n"
            f"【必须保留的事实清单】\n{fact_checklist}\n\n"
            f"请把骨架改写成自然语言,但保留清单里的每一条事实。"
        )

        for attempt in range(max_retry):
            try:
                reply = self.llm.complete(
                    sys_prompt=self.STRICT_SYSTEM_PROMPT,
                    user_content=user_msg,
                    temperature=0.3,  # 低温,不放飞
                )
            except Exception:
                return skeleton

            if not reply:
                return skeleton

            # 过 verifier 检查
            if self.verifier:
                report = self.verifier.verify(reply, context={
                    "truth_numbers": self._extract_truth_numbers(facts),
                })
                if report.is_clean:
                    return reply
                # 有问题 -> 用清理后的版本
                if attempt == max_retry - 1:
                    return report.cleaned_text or skeleton
            else:
                return reply

        return skeleton

    @staticmethod
    def _extract_truth_numbers(facts: List["VerifiedFact"]) -> Dict[str, Any]:
        """从事实里抽出可用于校验的数字"""
        # MVP:简化实现
        import re
        nums = {}
        for f in facts:
            matches = re.findall(r"(\d+(?:\.\d+)?)", f.statement)
            for m in matches:
                nums[f"fact_{f.fact_id}_num"] = float(m)
        return nums


# ══════════════════════════════════════════════════════════════
# StrictOrchestrator · 严格合成
# ══════════════════════════════════════════════════════════════
@dataclass
class StrictResponse:
    """严格模式的响应"""
    reply: str
    facts_used: List[str] = field(default_factory=list)  # fact_id 清单
    mode: str = "strict"
    skeleton: str = ""                                    # 原骨架
    llm_refined: bool = False                             # 是否经过 LLM 风格化
    verifier_flagged: bool = False                        # verifier 是否干预过


class StrictOrchestrator:
    """
    纯 Kautz Type 6 严格合成器

    流程:
      1. 接收 ResidualState(包含 verified_facts)
      2. FactRealizer 把 facts 组装成骨架
      3. (可选) StyleRefinement 让 LLM 改写风格
      4. ClaimVerifier 最后把关
      5. 返回 StrictResponse

    不使用 LLM 生成"新内容",只做风格化
    """

    def __init__(
        self,
        domain_profile: "DomainProfile",
        fact_realizer: Optional[FactRealizer] = None,
        style_refinement: Optional[StyleRefinement] = None,
        enable_style_llm: bool = True,
    ):
        self.profile = domain_profile
        self.realizer = fact_realizer or FactRealizer()
        self.refinement = style_refinement
        self.enable_style_llm = enable_style_llm

    def synthesize(
        self,
        residual_state: "ResidualState",
        question: Optional[str] = None,
    ) -> StrictResponse:
        """
        基于 residual state 严格合成回复

        Args:
          residual_state: 包含 verified_facts
          question: 用户问题(可选,仅用于决定 goals_context)

        Returns:
          StrictResponse
        """
        # 1. 组装骨架
        goals_context = [g.description for g in residual_state.goals]
        skeleton = self.realizer.realize(
            residual_state.verified_facts,
            goals_context=goals_context,
        )

        # 2. 决定是否 LLM 风格化
        if self.enable_style_llm and self.refinement is not None:
            refined = self.refinement.refine(
                skeleton=skeleton,
                facts=residual_state.verified_facts,
            )
            return StrictResponse(
                reply=refined,
                facts_used=[f.fact_id for f in residual_state.verified_facts],
                skeleton=skeleton,
                llm_refined=True,
            )

        # 3. 不做风格化,纯符号回复
        return StrictResponse(
            reply=skeleton,
            facts_used=[f.fact_id for f in residual_state.verified_facts],
            skeleton=skeleton,
            llm_refined=False,
        )

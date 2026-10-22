"""
BasePersona · 通用人格基类
==========================

一个 Persona 做三件事:
  1. 持有自己的采样参数(temperature / top_p)
  2. 持有自己的 prompt 变换策略(透传 / 软化 / 激进重写)
  3. 在变换 prompt 时,**保留身份锁**(来自上层的身份保护标记)

关键设计(对比原型系统现有实现):
  - nskernel 的 BasePersona **完全不含领域词**
  - 领域特色(角色名/口吻/案例库)来自 DomainProfile 的 PersonaProfile
  - Persona 把 PersonaProfile 的内容注入到 prompt transform 里

工作流:
  client 在调用 LLM 前:
    spec = router.get(level).build_spec(
        sys_prompt=原prompt,
        user_content=用户问题,
        domain_profile=本领域档案,      # 决定 persona 如何本地化
        task="chat",
    )
  → spec.sys_prompt 是经过 persona 变换后的 prompt
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, TYPE_CHECKING
import re

if TYPE_CHECKING:
    from nskernel.contracts import DomainProfile


# ══════════════════════════════════════════════════════════════
# 身份锁标记 - 领域无关的字符串约定
# ══════════════════════════════════════════════════════════════
# 上层(orchestrator/guardrails)可以在 sys_prompt 里插入这样的段:
#   ━━━━ 严格身份边界 ━━━━
#   ...一些必须保留的硬约束...
#   ━━━━ 严格身份边界 ━━━━
# Persona 变换 prompt 时必须识别并保留这段,不管自己多激进
IDENTITY_LOCK_START = "━━━━ 严格身份边界 ━━━━"
IDENTITY_LOCK_END = "━━━━ 严格身份边界 ━━━━"


def extract_identity_lock(sys_prompt: str) -> Optional[str]:
    """
    从 sys_prompt 里提取身份锁段落(如有)
    返回完整的锁段落(含首尾标记),无则 None
    """
    if not sys_prompt or IDENTITY_LOCK_START not in sys_prompt:
        return None
    # 匹配 START...END 之间的全部内容(包括标记本身)
    pattern = re.compile(
        re.escape(IDENTITY_LOCK_START) + r".*?" + re.escape(IDENTITY_LOCK_END),
        re.DOTALL,
    )
    m = pattern.search(sys_prompt)
    return m.group(0) if m else None


# ══════════════════════════════════════════════════════════════
# LLMCallSpec - 一次 LLM 调用的完整规格
# ══════════════════════════════════════════════════════════════
@dataclass
class LLMCallSpec:
    """由 Persona 决定后返回给 LLM client 执行的完整调用规格"""
    sys_prompt: str                              # 变换后的系统 prompt
    user_content: str                            # user 消息
    temperature: float
    top_p: float
    persona_tag: str = ""                        # 当前 persona 名,供日志/调试
    extra: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# BasePersona - 通用人格基类(nskernel 级,无领域依赖)
# ══════════════════════════════════════════════════════════════
class BasePersona:
    """
    Persona 抽象基类 - 不含任何领域专有词

    三档 persona 的预期行为(名字约定):
      "rigor"  → 严谨基线    (低温度,透传原 prompt)
      "senior" → 资深风范    (中温度,软化结构+注入资深口吻)
      "master" → 大师手笔    (高温度,激进重写+注入案例库)

    每档 persona 的子类只需:
      1. 重写 temperature/top_p 属性
      2. 重写 transform_sys_prompt(sys_prompt, domain_profile, task, context)
         - 从 domain_profile 取本领域的 PersonaProfile(role_label/tone_hint/case_library)
         - 构造本档风格的 sys_prompt(但要保留身份锁)
    """

    # 档位名 (子类重写)
    level: str = "base"
    # 采样参数 (子类重写)
    temperature: float = 0.5
    top_p: float = 0.85

    def __init__(self):
        self._call_count = 0

    # -----------------------------------------------------------------
    # 主入口:把一次调用的信息包装成 LLMCallSpec
    # -----------------------------------------------------------------
    def build_spec(
        self,
        sys_prompt: str,
        user_content: str,
        domain_profile: Optional["DomainProfile"] = None,
        task: str = "generic",
        context: Optional[Dict[str, Any]] = None,
    ) -> LLMCallSpec:
        """
        构造一次 LLM 调用规格

        Args:
          sys_prompt: 原始 system prompt(orchestrator/agent 准备的)
          user_content: user 消息
          domain_profile: 当前领域档案(含本域的 persona role_label/tone_hint/case_library)
                         没传就用默认(通用专家)口吻
          task: 任务类型(chat/expert_analysis/summarize/polish) — 子类可差异化
          context: 任务上下文(has_severe_advice 等)
        """
        self._call_count += 1
        transformed = self.transform_sys_prompt(
            sys_prompt=sys_prompt,
            domain_profile=domain_profile,
            task=task,
            context=context or {},
        )
        return LLMCallSpec(
            sys_prompt=transformed,
            user_content=user_content,
            temperature=self.temperature,
            top_p=self.top_p,
            persona_tag=self.level,
            extra={"task": task, "call_seq": self._call_count},
        )

    # -----------------------------------------------------------------
    # 核心钩子:子类重写
    # -----------------------------------------------------------------
    def transform_sys_prompt(
        self,
        sys_prompt: str,
        domain_profile: Optional["DomainProfile"] = None,
        task: str = "generic",
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        默认透传不变 - 子类按档位重写

        子类实现时务必调用 extract_identity_lock(sys_prompt),
        如有身份锁则放在变换后 prompt 的最前面(高优先级)
        """
        return sys_prompt

    # -----------------------------------------------------------------
    # 工具方法:子类可用
    # -----------------------------------------------------------------
    def get_role_label(self, domain_profile: Optional["DomainProfile"]) -> str:
        """
        取本领域在本档位的角色称谓
        某业务-rigor: "某业务检测工程师"
        某业务-master: "某业务诊断大师"
        法律-master: "金牌大律师"

        domain_profile 未提供时返回通用称谓
        """
        if domain_profile is None:
            return {"rigor": "助理", "senior": "资深专家",
                    "master": "首席专家"}.get(self.level, "专家")
        persona_cfg = domain_profile.get_persona(self.level)
        if persona_cfg and persona_cfg.role_label:
            return persona_cfg.role_label
        return "专家"

    def get_tone_hint(self, domain_profile: Optional["DomainProfile"]) -> str:
        """取本领域在本档位的口吻提示"""
        if domain_profile is None:
            return ""
        persona_cfg = domain_profile.get_persona(self.level)
        return persona_cfg.tone_hint if persona_cfg else ""

    def get_case_library(self, domain_profile: Optional["DomainProfile"]) -> list:
        """取本领域在本档位的案例库(仅 master 档常用)"""
        if domain_profile is None:
            return []
        persona_cfg = domain_profile.get_persona(self.level)
        return persona_cfg.case_library if persona_cfg else []

    # -----------------------------------------------------------------
    # 元信息
    # -----------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "call_count": self._call_count,
        }

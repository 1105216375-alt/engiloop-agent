"""
RigorPersona · 严谨基线档(通用)
=================================

低温度、原 prompt 透传、不注入花哨修辞。
这是每个领域的"默认档",也是其他档的对照基准。

工作:
  - 保留身份锁(如有)
  - 不改变原 prompt 的结构和内容
  - 温度 0.5,top_p 0.85
"""
from typing import Optional, Dict, Any
from .base import BasePersona, extract_identity_lock


class RigorPersona(BasePersona):
    level = "rigor"
    temperature = 0.5
    top_p = 0.85

    def transform_sys_prompt(
        self,
        sys_prompt: str,
        domain_profile=None,
        task: str = "generic",
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        # 严谨档:直接透传原 prompt
        # 身份锁在原 prompt 里已有,不用额外处理
        # (上游 orchestrator 自己放进 sys_prompt 的)
        return sys_prompt

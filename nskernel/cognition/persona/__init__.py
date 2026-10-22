"""
nskernel.cognition.persona · 三档人格路由

用法:
  from nskernel.cognition.persona import PersonaRouter, BasePersona

  router = PersonaRouter()
  persona = router.get("master")    # 或 "high"
  spec = persona.build_spec(
      sys_prompt="原prompt",
      user_content="用户问题",
      domain_profile=bridge_profile,
      task="chat",
  )
  # spec.sys_prompt 已经被 master 档变换+注入某业务领域案例库
"""
from .base import BasePersona, LLMCallSpec, extract_identity_lock
from .rigor import RigorPersona
from .senior import SeniorPersona
from .master import MasterPersona
from .router import PersonaRouter
from .shared_guards import (
    extract_hard_rules,
    build_floor_rules,
    UNIVERSAL_FLOOR_RULES,
)

__all__ = [
    "BasePersona", "LLMCallSpec", "extract_identity_lock",
    "RigorPersona", "SeniorPersona", "MasterPersona",
    "PersonaRouter",
    "extract_hard_rules", "build_floor_rules", "UNIVERSAL_FLOOR_RULES",
]

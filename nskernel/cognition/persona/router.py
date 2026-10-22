"""
PersonaRouter · 三档人格路由器(通用)
======================================

架构:
  客户端用 PersonaRouter 按档位字符串取到 Persona 实例,
  再调用 persona.build_spec(...) 得到 LLMCallSpec,最后交给 LLM client。

  PersonaRouter 是单例池 —— 每个档位的 Persona 只实例化一次,
  这样 call_count 等观测字段可以累加。

档位约定(nskernel 级 — 所有领域共用这三档):
  "rigor"  → RigorPersona   严谨基线(低温度,透传)
  "senior" → SeniorPersona  资深风范(中温度,软化+领域口吻注入)
  "master" → MasterPersona  大师手笔(高温度,激进重写+领域案例)

别名映射(兼容通俗叫法):
  "low"    → "rigor"
  "medium" → "senior"
  "high"   → "master"
"""
from typing import Dict, List, Optional
from .base import BasePersona
from .rigor import RigorPersona
from .senior import SeniorPersona
from .master import MasterPersona


class PersonaRouter:
    """三档人格路由器 - 通用,与领域无关"""

    # 档位 → Persona 类
    _LEVEL_TO_CLS: Dict[str, type] = {
        "rigor":  RigorPersona,
        "senior": SeniorPersona,
        "master": MasterPersona,
    }

    # 通俗别名 → 标准档位
    _ALIAS: Dict[str, str] = {
        "low":    "rigor",
        "medium": "senior",
        "high":   "master",
    }

    DEFAULT_LEVEL = "rigor"

    def __init__(self):
        # 单例池:每档只建一次
        self._pool: Dict[str, BasePersona] = {
            lv: cls() for lv, cls in self._LEVEL_TO_CLS.items()
        }

    # ---------- 查询 ----------
    @classmethod
    def all_levels(cls) -> List[str]:
        """返回所有标准档位名(不含别名)"""
        return list(cls._LEVEL_TO_CLS.keys())

    @classmethod
    def all_aliases(cls) -> Dict[str, str]:
        """返回别名映射表"""
        return dict(cls._ALIAS)

    @classmethod
    def is_valid_level(cls, level: str) -> bool:
        """档位名是否合法(含别名)"""
        normalized = cls._normalize(level)
        return normalized in cls._LEVEL_TO_CLS

    @classmethod
    def _normalize(cls, level: str) -> str:
        """归一化档位名:把 low/medium/high 转成 rigor/senior/master"""
        if level in cls._ALIAS:
            return cls._ALIAS[level]
        return level

    def get(self, level: str) -> BasePersona:
        """
        按档位取 persona 实例
        非法档位 → 回退到默认 rigor 档(不抛异常,保守)
        """
        normalized = self._normalize(level)
        if normalized not in self._pool:
            normalized = self.DEFAULT_LEVEL
        return self._pool[normalized]

    def describe_all(self) -> Dict[str, Dict]:
        """所有档位的元信息(调试/UI 展示用)"""
        return {lv: p.describe() for lv, p in self._pool.items()}

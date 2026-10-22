"""
IntentDetectorBase · 意图识别基类
==================================

把用户问题映射成一组"意图标签",供 Orchestrator 路由 Agent。

设计:
  - 默认:关键词映射(简单可靠)
  - 扩展:可以接 LLM/NLU 做更精准的意图识别

意图举例(领域可自定义):
  "计算"     → 用户在问数值计算
  "检索"     → 用户在问权威引用
  "分析"     → 用户在问原因/机理
  "处置"     → 用户在问怎么办/建议
  "统计"     → 用户在问汇总/全局指标
  "闲聊"     → 兜底
"""
from __future__ import annotations
from typing import Callable, Dict, List, Optional


class IntentDetectorBase:
    """
    基于关键词的通用意图检测器

    用法:
      detector = IntentDetectorBase({
          "计算":  ["算", "多少", "几", "计算"],
          "检索":  ["规范", "法条", "教材", "依据"],
          "分析":  ["为什么", "原因", "机理", "怎么回事"],
          "处置":  ["怎么办", "如何", "建议", "处理"],
      })
      intents = detector(question)  # 返回命中的意图列表
    """

    def __init__(self, intent_keywords: Optional[Dict[str, List[str]]] = None):
        self._patterns = intent_keywords or {}

    def add(self, intent: str, keywords: List[str]) -> None:
        """动态添加意图"""
        self._patterns.setdefault(intent, []).extend(keywords)

    def detect(self, question: str) -> List[str]:
        """
        检测问题触发的全部意图(可多意图)
        返回意图列表(按注册顺序)
        """
        if not question:
            return []
        q_lower = question.lower()
        hits = []
        for intent, kws in self._patterns.items():
            if any(k.lower() in q_lower for k in kws):
                hits.append(intent)
        return hits

    # 让它可以直接作为 callable 使用(Orchestrator 接收 callable)
    def __call__(self, question: str) -> List[str]:
        return self.detect(question)

    def describe(self) -> Dict[str, int]:
        return {k: len(v) for k, v in self._patterns.items()}

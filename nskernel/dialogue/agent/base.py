"""
AgentBase · Agent 基类
=======================

一个 Agent 是 Orchestrator 可调用的"专业工具",接受标准输入,返回标准输出。

对比原型系统的 core/agents.py:
  - 旧版 agents(StatsAgent/ReviewAgent/RegulationRAGAgent)是某业务专有
  - 新版 AgentBase 提供统一接口(run + describe),领域无关
  - 通用组件(Calculator/ClaimVerifier)会实现 AgentBase,让 Orchestrator 统一调度

常见 Agent 类型(领域应各自实现):
  - RetrievalAgent: 从知识库/KG 检索相关条目
  - ScoringAgent:  调用评分器
  - ReviewAgent:   校验观察质量
  - DecisionAgent: 生成处治方案

架构位置:
  Orchestrator 调度 Agents,Agents 依赖 Ontology/Calculator/AuthoritySchema 做符号计算,
  最终把多个 Agents 的 output 送到 LLM synthesis 里合成自然语言回复。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentInput:
    """Agent 通用输入"""
    question: str                                 # 用户当前问题
    observations: List[Any] = field(default_factory=list)
                                                  # 相关 observations(投影后的,领域类型)
    context: Dict[str, Any] = field(default_factory=dict)
                                                  # 任意上下文(session 元信息等)
    intents: List[str] = field(default_factory=list)
                                                  # 已识别的意图列表


@dataclass
class AgentOutput:
    """Agent 通用输出"""
    agent_name: str
    success: bool = True
    data: str = ""                                # 给 LLM synthesis 看的文本
    structured: Dict[str, Any] = field(default_factory=dict)
                                                  # 结构化结果(决策数据)
    authority_refs: List[str] = field(default_factory=list)
                                                  # 引用的权威
    confidence: str = "medium"                    # low/medium/high
    error: str = ""


class AgentBase(ABC):
    """
    Agent 抽象基类

    实现要点:
      1. 定义 agent_name(显示名,日志用)
      2. 实现 run(input) → output
      3. 可选实现 applies_to(input) → bool(是否处理该输入)

    使用:
      orchestrator 根据 intent 筛选一批 Agent,依次 run,把 output 送去 synthesis
    """

    agent_name: str = "base_agent"
    description: str = ""

    @abstractmethod
    def run(self, input: AgentInput) -> AgentOutput:
        """执行 Agent 主逻辑"""
        ...

    def applies_to(self, input: AgentInput) -> bool:
        """
        该 Agent 是否适用于当前输入?
        默认总是适用;Orchestrator 的 trigger_intents 过滤也可做同样的事
        """
        return True

    def describe(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "description": self.description,
        }


# ══════════════════════════════════════════════════════════════
# 内置通用 Agent: PassthroughAgent(调试用)
# ══════════════════════════════════════════════════════════════
class PassthroughAgent(AgentBase):
    """
    最简单的 Agent:把 observations 数量汇报一下就返回
    主要用于 Orchestrator 的基础冒烟测试
    """
    agent_name = "passthrough"
    description = "直接汇报输入 observation 数量,不做处理"

    def run(self, input: AgentInput) -> AgentOutput:
        count = len(input.observations)
        return AgentOutput(
            agent_name=self.agent_name,
            success=True,
            data=f"共 {count} 条观察",
            structured={"count": count},
        )

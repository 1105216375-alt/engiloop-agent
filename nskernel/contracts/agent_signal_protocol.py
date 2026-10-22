"""
Agent Signal Protocol · Agent 间协作信号契约
=================================================

通用领域契约,描述"Agent 之间识别出的事如何串联,触发其他 Agent 补充工作"。

设计理念:
  · 当某 Agent 发现关键信号(critical hazard / chain detected / hotspot)
    应当自动触发相关 Agent 补充工作 - 不能各自为战
  · 解决 "agent 大杂烩" 问题:每个 agent 各自跑,但 LLM context 没串起来
  · 跨领域同构:
    - 桥梁: DeepAnalysis 找到 critical 链 → RegulationRAG 自动补检索条款
    - 道路: SeverityExtractor 识别 7.2.3 触发项 → 对应紧急度 + 规范引用
    - 法律: 风险识别出"诉讼时效将过" → 立即触发证据收集 + 当事人通知
    - 医疗: 急症识别 → 立即触发科室转诊 + 家属通知

核心模型(借鉴道路医生 AgentCoordinator):

  Signal {
    source            : 信号来源 ('severity'/'causal'/'env'/'hotspot')
    signal_type       : 信号类型 ('critical_hazard'/'chain_detected'/'urgency_bump')
    content           : 自然语言描述
    priority          : 1=紧急 / 99=普通
    related_authorities: 关联规范/法条
    target_agents     : 哪些 agent 应当响应该信号
    metadata          : 自由扩展
  }

  CoordinationResult {
    signals           : [Signal] 所有触发的信号
    supplementary_authority : 补检索的权威源
    llm_extra_context : 给 LLM 的增强 context
  }

工作流程:
  1. 各 Agent 跑完后吐出 [Signal]
  2. AgentCoordinator 收集 signals → 按 target_agents 路由
  3. 触发新一轮 Agent 调用(如自动调 RegulationRAGAgent)
  4. 合并所有 supplementary 信息 → llm_extra_context
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════

@dataclass
class CoordinationSignal:
    """协作信号 · 某 Agent 识别后触发的动作"""
    source: str                              # 'severity_extractor'/'causal_analysis'/'env_agent'
    signal_type: str                         # 'critical_hazard'/'chain_detected'/'urgency_bump'
    content: str                             # 自然语言描述
    priority: int = 99                       # 1 高 99 低
    target_agents: List[str] = field(default_factory=list)
    # 哪些 agent 名应当响应(空 = 所有人看)
    related_authorities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_critical(self) -> bool:
        return self.priority == 1


@dataclass
class CoordinationResult:
    """协作层最终输出"""
    signals: List[CoordinationSignal] = field(default_factory=list)
    supplementary_data: Dict[str, Any] = field(default_factory=dict)
    # 各 agent 的补充数据 (key = agent_name, value = 补检索结果)
    llm_extra_context: str = ""              # 给 LLM 拼到 sys_prompt 尾巴
    audit_log: List[str] = field(default_factory=list)

    def has_critical(self) -> bool:
        return any(s.is_critical() for s in self.signals)

    def critical_signals(self) -> List[CoordinationSignal]:
        return [s for s in self.signals if s.is_critical()]


# ══════════════════════════════════════════════════════════════
# 抽象契约
# ══════════════════════════════════════════════════════════════

class AgentSignalProtocol(ABC):
    """
    Agent 协作契约 - 任何想做"agent 间协同"的领域要实现

    最小实现:
      class MyDomainCoordinator(AgentSignalProtocol):
          def collect_signals(self, agent_outputs): ...

      其他用默认实现。
    """

    # ────────────────────────────────────────────────────
    # 必须实现
    # ────────────────────────────────────────────────────

    @abstractmethod
    def collect_signals(
        self,
        agent_outputs: Dict[str, Any],
    ) -> List[CoordinationSignal]:
        """
        从各 Agent 的产出里收集 signals

        agent_outputs: {agent_name: agent_output_object}
        返回 [CoordinationSignal]
        """
        ...

    # ────────────────────────────────────────────────────
    # 默认实现 - 路由 / 触发
    # ────────────────────────────────────────────────────

    def route_signals(
        self,
        signals: List[CoordinationSignal],
        agent_handlers: Dict[str, Callable],
    ) -> Dict[str, Any]:
        """
        把 signals 路由到 target_agents,触发它们补充工作

        agent_handlers: {agent_name: callable(signal) → result}
        返回 {agent_name: result}
        """
        results = {}
        for signal in signals:
            targets = signal.target_agents or list(agent_handlers.keys())
            for agent_name in targets:
                handler = agent_handlers.get(agent_name)
                if not handler:
                    continue
                try:
                    out = handler(signal)
                    if out is not None:
                        if agent_name not in results:
                            results[agent_name] = []
                        results[agent_name].append(out)
                except Exception as e:
                    # 静默降级
                    continue
        return results

    def build_extra_context(
        self,
        signals: List[CoordinationSignal],
        supplementary_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        把所有 signals + supplementary 拼成给 LLM 的 extra context

        子类可覆盖以定制格式
        """
        lines = []
        critical = [s for s in signals if s.is_critical()]
        if critical:
            lines.append("【⚠️ 关键信号】")
            for s in critical:
                lines.append(f"  · [{s.source}/{s.signal_type}] {s.content}")
                if s.related_authorities:
                    lines.append(f"    依据: {', '.join(s.related_authorities)}")

        non_critical = [s for s in signals if not s.is_critical()]
        if non_critical:
            lines.append("")
            lines.append("【其他协作信号】")
            for s in non_critical[:5]:  # 限制
                lines.append(f"  · [{s.source}] {s.content}")

        if supplementary_data:
            lines.append("")
            lines.append("【补充数据】")
            for agent, data in supplementary_data.items():
                if isinstance(data, list):
                    lines.append(f"  ▸ {agent}: {len(data)} 项")
                else:
                    lines.append(f"  ▸ {agent}: {str(data)[:200]}")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────────

    def coordinate(
        self,
        agent_outputs: Dict[str, Any],
        agent_handlers: Optional[Dict[str, Callable]] = None,
    ) -> CoordinationResult:
        """协调主入口: collect → route → build context"""
        result = CoordinationResult()

        try:
            signals = self.collect_signals(agent_outputs) or []
            result.signals = signals
            result.audit_log.append(f"收集到 {len(signals)} 信号")

            if agent_handlers:
                supp = self.route_signals(signals, agent_handlers)
                result.supplementary_data = supp
                result.audit_log.append(
                    f"路由后触发 {len(supp)} 个 agent 补充"
                )

            result.llm_extra_context = self.build_extra_context(
                signals, result.supplementary_data,
            )
        except Exception as e:
            result.audit_log.append(f"协调异常: {e}")

        return result

    def describe(self) -> Dict[str, Any]:
        return {"schema": self.__class__.__name__}

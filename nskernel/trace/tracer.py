"""
Trace · 全链追踪层
===================

完整记录推理会话:
  - CandidateLog    所有候选
  - DecisionLog     所有决策
  - TransitionLog   所有状态转移
  - StateSnapshot   快照(便于回滚/复现)
  - TraceLogger     总入口
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time
import uuid


@dataclass
class LogEntry:
    """通用日志条目"""
    entry_id: str = field(default_factory=lambda: f"L_{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)
    log_type: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


class InMemoryLog:
    """通用内存日志(生产可替换为 DB/文件)"""
    def __init__(self, log_type: str, max_size: int = 5000):
        self.log_type = log_type
        self.max_size = max_size
        self._entries: List[LogEntry] = []

    def append(self, **data) -> LogEntry:
        e = LogEntry(log_type=self.log_type, data=data)
        self._entries.append(e)
        if len(self._entries) > self.max_size:
            self._entries = self._entries[-self.max_size:]
        return e

    def all(self) -> List[LogEntry]:
        return list(self._entries)

    def recent(self, n: int = 50) -> List[LogEntry]:
        return self._entries[-n:]

    def size(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()


class TraceLogger:
    """
    总入口 - 聚合多个子日志

    用法:
      trace = TraceLogger()
      trace.candidate_log.append(candidate_id="C1", ...)
      trace.decision_log.append(candidate_id="C1", action="accept", ...)
      trace.transition_log.append(from_id="R1", to_id="R2", ...)
      trace.snapshot_log.append(state_snapshot={...})
    """
    def __init__(self):
        self.candidate_log = InMemoryLog("candidate")
        self.decision_log = InMemoryLog("decision")
        self.transition_log = InMemoryLog("transition")
        self.snapshot_log = InMemoryLog("snapshot", max_size=100)  # 快照比较大
        self.verifier_log = InMemoryLog("verifier")
        self.interrupt_log = InMemoryLog("interrupt")

    def export_all(self) -> Dict[str, Any]:
        """导出全部日志(审计/调试用)"""
        return {
            "candidates": [e.data for e in self.candidate_log.all()],
            "decisions": [e.data for e in self.decision_log.all()],
            "transitions": [e.data for e in self.transition_log.all()],
            "snapshots": [e.data for e in self.snapshot_log.all()],
            "verifier_results": [e.data for e in self.verifier_log.all()],
            "interrupts": [e.data for e in self.interrupt_log.all()],
        }

    def summary(self) -> Dict[str, int]:
        return {
            "candidates": self.candidate_log.size(),
            "decisions": self.decision_log.size(),
            "transitions": self.transition_log.size(),
            "snapshots": self.snapshot_log.size(),
            "verifier_results": self.verifier_log.size(),
            "interrupts": self.interrupt_log.size(),
        }

"""
Monitor · 可观测性层
======================

生产级监控,轻量实现:
  - MetricsCollector: 累计指标
  - EventStream:      事件流(FIFO)
  - AlertManager:     告警触发
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional
import time


@dataclass
class Event:
    event_type: str                       # "step_accepted" / "step_rejected" / "escalation" / ...
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)


class EventStream:
    """事件流(内存 ring buffer)"""
    def __init__(self, max_size: int = 1000):
        self._events: Deque[Event] = deque(maxlen=max_size)

    def emit(self, event_type: str, **data) -> Event:
        e = Event(event_type=event_type, data=data)
        self._events.append(e)
        return e

    def recent(self, n: int = 20) -> List[Event]:
        return list(self._events)[-n:]

    def filter(self, event_type: str) -> List[Event]:
        return [e for e in self._events if e.event_type == event_type]

    def size(self) -> int:
        return len(self._events)

    def clear(self) -> None:
        self._events.clear()


class MetricsCollector:
    """累计指标"""
    def __init__(self):
        self._counters: Dict[str, int] = {}
        self._sums: Dict[str, float] = {}
        self._max: Dict[str, float] = {}

    def incr(self, name: str, by: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + by

    def add(self, name: str, value: float) -> None:
        self._sums[name] = self._sums.get(name, 0.0) + value

    def max(self, name: str, value: float) -> None:
        self._max[name] = max(self._max.get(name, float("-inf")), value)

    def get(self, name: str) -> Any:
        return self._counters.get(name) or self._sums.get(name) or self._max.get(name)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "counters": dict(self._counters),
            "sums": dict(self._sums),
            "max": dict(self._max),
        }


class AlertManager:
    """简单阈值告警"""
    def __init__(self):
        self._rules: List[Dict[str, Any]] = []
        self._fired: List[Dict[str, Any]] = []

    def add_rule(
        self,
        name: str,
        condition: Callable[[MetricsCollector], bool],
        message: str,
    ) -> "AlertManager":
        self._rules.append({"name": name, "condition": condition, "message": message})
        return self

    def check(self, metrics: MetricsCollector) -> List[Dict[str, Any]]:
        """检查所有规则,返回触发的告警"""
        triggered = []
        for rule in self._rules:
            try:
                if rule["condition"](metrics):
                    alert = {"name": rule["name"], "message": rule["message"],
                             "ts": time.time()}
                    self._fired.append(alert)
                    triggered.append(alert)
            except Exception:
                continue
        return triggered

    def history(self) -> List[Dict[str, Any]]:
        return list(self._fired)

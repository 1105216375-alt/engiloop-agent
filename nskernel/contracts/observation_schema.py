"""
ObservationSchema · 观察数据契约
=================================

回答一个问题:**本领域的原始观察数据长什么样?**

场景 1: 一条观察记录(业务对象+观察类型+位置+量测参数)
场景 2: 一个案情事实(行为+主体+客体+后果)
场景 3: 一个错题(题目+错误答案+正确答案+误区)
场景 4: 一个症状(部位+性质+程度+时间)
场景 5: 一笔交易/一个持仓(标的+数量+金额+日期)

ObservationSchema 决定:
  1. 观察数据的统一外形(必选字段 + 可选字段)
  2. 如何序列化/反序列化
  3. 如何提取"观察摘要"给 LLM 看

这个契约让 nskernel 里的通用能力(Memory 累积、
Projection 投影、ClaimVerifier 校验)可以不关心领域细节,
统一按 Observation 处理。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Observation:
    """
    通用观察数据

    设计说明:
      - observation_id: 唯一标识(可选,便于去重)
      - entity_key: 归属实体(如:案件=案号/学号/账户号)— 投影的关键字段
      - entity_display_name: 实体的人类可读名(学生:姓名/场景 1: 城市/案件:案由简称)
      - category: 观察大类(场景 1: "上部结构"/场景 2: "民事侵权")
      - subject: 观察对象(场景 1: "某关键类别"/场景 2: "被告")
      - observation: 观察内容本身(场景 1: "裂缝0.3mm"/场景 2: "未还款")
      - severity: 严重度标记(场景 1: 标度3/场景 2: 重大违约/场景 3: 概念性错误)
      - raw_data: 领域特有的原始数据(不强制,按需)
      - metadata: 元信息(时间戳、来源、置信度)
    """
    observation_id: str = ""
    entity_key: str = ""
    entity_display_name: str = ""             # 实体的人类可读名(可选)
    category: str = ""
    subject: str = ""
    observation: str = ""
    severity: Any = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_summary_line(self) -> str:
        """
        把这条观察压缩成一行摘要(喂给 LLM 用)
        格式: "{subject}·{observation}" (如 "某关键类别·斜向裂缝0.3mm")

        领域可以通过 ObservationSchema.format_summary_line(obs) 重写
        """
        if self.subject and self.observation:
            return f"{self.subject}·{self.observation}"
        return self.observation or self.subject or str(self.raw_data)[:50]


class ObservationSchema(ABC):
    """
    观察数据抽象基类

    领域实现职责:
      1. 告诉 nskernel "我的数据有哪些必选字段"
      2. 告诉 nskernel "怎么从我的原始 dict 构造 Observation"
      3. 告诉 nskernel "怎么把 Observation 渲染成领域专业的摘要文字"
    """

    # 本领域观察的显示标签("观察" / "案情" / "错题" / "症状")
    observation_label: str = "观察"

    @abstractmethod
    def from_raw(self, raw: Dict[str, Any]) -> Observation:
        """
        从领域原始 dict 构造 Observation

        某业务实现:
          return Observation(
              entity_key=raw["城市"],
              category=raw["大类"],
              subject=raw["业务对象"],
              observation=raw["观察"],
              severity=raw.get("标度"),
              raw_data=raw,
          )

        法律实现:
          return Observation(
              entity_key=raw["案号"],
              category=raw["案由"],
              subject=raw["当事人"],
              observation=raw["事实"],
              severity=raw.get("损失金额"),
              raw_data=raw,
          )
        """
        ...

    def format_summary_line(self, obs: Observation) -> str:
        """格式化一条观察(领域可重写,默认用 Observation.to_summary_line)"""
        return obs.to_summary_line()

    def format_observation_list(self, observations: List[Observation]) -> str:
        """
        把一个观察列表格式化成给 LLM 看的结构化文本。

        默认实现是编号列表;领域可重写成表格/分类块等。
        """
        if not observations:
            return "(无观察)"
        lines = []
        for i, obs in enumerate(observations, 1):
            lines.append(f"  {i}. {self.format_summary_line(obs)}")
        return "\n".join(lines)

    def required_fields(self) -> List[str]:
        """
        本领域从外部导入数据时要求的必选字段名。
        用于 app 层做前置校验。
        """
        return ["entity_key", "observation"]

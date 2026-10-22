"""
DecisionSchema · 决策输出契约
==============================

回答一个问题:**本领域给用户的"建议/方案"长什么样?**

场景 1: 处治建议(施工工艺 + 材料 + 工期 + 规范依据)
场景 2: 应诉策略(立场 + 证据链 + 引用法条 + 预期结果)
场景 3: 辅导方案(知识点补强 + 练习题 + 教学视频 + 时间表)
场景 4: 治疗方案(诊断 + 用药/处置 + 禁忌 + 随访)
场景 5: 投资建议(仓位调整 + 风险等级 + 合规声明)

这个契约统一了"决策输出"的数据结构,让 nskernel 的
合成层(synthesis)可以用通用算法把多个 Agent 的输出组合成
结构化决策,避免"一团乱麻的自由文本"。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DecisionItem:
    """
    单条决策/建议

    场景 1: 一条处治措施("注浆封闭表面裂缝")
    场景 2: 一条应诉要点("否认签署该合同")
    场景 3: 一条辅导动作("先复习二次函数顶点式")
    场景 4: 一条处置("每日服药 2 次,饭后")
    场景 5: 一条操作("减仓科技股至 30%")
    """
    item_id: str = ""                              # 可选唯一 id
    action: str = ""                               # 动作描述
    rationale: str = ""                            # 理由(符号给,不许 LLM 编)
    authority_refs: List[str] = field(default_factory=list)
                                                   # 引用的权威 ref_id
    urgency: str = ""                              # 紧急度(低/中/高/极高)
    resources: List[str] = field(default_factory=list)
                                                   # 所需资源("材料/工具/前置课")
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    """
    对一个观察/问题的完整决策输出

    设计说明:
      - decision_id: 唯一标识
      - subject: 决策对象("某关键类别·斜向裂缝" / "(2024)京0105 案" / "某个体 A·二次函数")
      - diagnosis: 诊断文字("III 类技术状况" / "原告请求成立可能性 70%" / "根本原因是因式分解不熟")
      - items: 结构化建议列表
      - summary: 总结文字(LLM 合成,但要基于 items)
      - confidence: 整体置信度(low/medium/high)
    """
    decision_id: str = ""
    subject: str = ""
    diagnosis: str = ""
    items: List[DecisionItem] = field(default_factory=list)
    summary: str = ""
    confidence: str = "medium"
    metadata: Dict[str, Any] = field(default_factory=dict)


class DecisionSchema(ABC):
    """
    决策输出抽象基类

    领域实现职责:
      1. 定义本域决策的 item_categories("处治/材料/工期" vs "证据/策略/法条")
      2. 渲染模板(怎么把 Decision 渲染成 Markdown/文字/UI 卡片)
    """

    # 本领域决策的显示标签
    # 示例 A: "处治建议" / 场景 2: "应诉策略" / 场景 3: "辅导方案"
    decision_label: str = "建议"

    # 本领域决策项的分类
    # 场景 1: ["措施", "材料", "工期", "规范"]
    # 场景 2: ["立场", "证据", "法条", "预期"]
    item_categories: List[str] = ()

    @abstractmethod
    def render_markdown(self, decision: Decision) -> str:
        """
        把 Decision 渲染成 Markdown(给前端展示)

        领域可以自己决定格式(表格 / 编号列表 / 分类块)
        """
        ...

    def render_plain(self, decision: Decision) -> str:
        """渲染成纯文字(CLI/日志用),默认用 markdown 剥格式"""
        import re
        md = self.render_markdown(decision)
        # 简易剥:去掉 markdown 标记
        plain = re.sub(r"[#*`_\[\]\(\)]", "", md)
        return plain

    def to_dict(self, decision: Decision) -> Dict[str, Any]:
        """
        序列化成 dict(便于 JSON 传输)。
        默认把 dataclass 递归转 dict。
        """
        from dataclasses import asdict
        return asdict(decision)

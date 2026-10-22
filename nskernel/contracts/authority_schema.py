"""
AuthoritySchema · 权威源契约
=============================

回答一个问题:**本领域的"权威依据"从哪里来,长什么样?**

场景 1: 技术规范 — 《示例规范》第 X.X 条
场景 2: 法条/司法解释/判例(《民法典》第 188 条 / 最高法判例)
场景 3: 教材/考纲(人教版必修一 3.4 / 2024 考纲重点)
场景 4: 指南/循证(《内科学》第 9 版 / 《2024 糖尿病防治指南》)
场景 5: 会计准则/监管办法

nskernel 的 RAG 不能假设"权威源=法条号+正文",各领域结构天差地别。
AuthoritySchema 统一了:
  1. 怎么表达一条"权威引用"(AuthorityRef)
  2. 怎么从权威库里检索相关条目(retrieve)
  3. 怎么校验一条引用是否存在(is_known_reference) ⭐ 防幻觉

这个契约让 ClaimVerifier 能做"幻觉规范号/法条号"的校验——
LLM 说"XX 规范-2021 第 100 条",符号层能立刻核对这条是否存在。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AuthorityRef:
    """
    对一条权威的统一引用

    ref_id 是领域自定义的唯一标识,格式举例:
      场景 1: "XX 规范-2021#6.3.4"
      场景 2: "民法典#188"
      场景 3: "人教版必修一#3.4"
      场景 4: "糖尿病防治指南-2024#3.2"
    """
    ref_id: str                                    # 权威条目唯一 id
    source_name: str = ""                          # 来源名("民法典" / "XX 规范")
    section: str = ""                              # 条目位置("第 188 条" / "3.4 节")
    title: str = ""                                # 条目标题(可选)
    text: str = ""                                 # 全文或摘要
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_citation(self) -> str:
        """
        生成人类可读的引用字符串
        例: "《民法典》第 188 条" / "XX 规范-2021 第 6.3.4 条"
        """
        if self.source_name and self.section:
            return f"《{self.source_name}》{self.section}"
        return self.ref_id


@dataclass
class RetrievalQuery:
    """检索请求"""
    query_text: str                                # 查询文本
    topic_keywords: List[str] = field(default_factory=list)
                                                   # 主题词(领域专业词)
    entity_context: Optional[str] = None           # 当前实体上下文
    top_k: int = 5                                 # 返回前 K 个


@dataclass
class RetrievalResult:
    """检索结果"""
    refs: List[AuthorityRef] = field(default_factory=list)
    total_matched: int = 0
    query_expanded_to: List[str] = field(default_factory=list)
                                                   # 查询扩展词(同义词展开)


class AuthoritySchema(ABC):
    """
    权威源抽象基类

    领域实现:
      class BridgeAuthority(AuthoritySchema):
          authority_label = "规范"          # 用户侧显示

          def retrieve(self, query):
              # 调现有 BM25/向量检索,返回 AuthorityRef 列表
              ...

          def is_known_reference(self, ref_id):
              # 检查权威索引里有无这条
              return ref_id in self._index

          def parse_citation(self, text):
              # 从文本里抽出 "XX 规范-2021 第 6.3.4 条" → ref_id
              ...
    """

    # 本领域权威的显示标签
    authority_label: str = "权威依据"

    @abstractmethod
    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """
        根据查询检索相关权威条目。
        领域用自己的索引(全文/BM25/向量)实现。
        """
        ...

    @abstractmethod
    def is_known_reference(self, ref_id: str) -> bool:
        """
        ⭐ 关键防幻觉接口:
        检查一条引用是否真的存在于权威库。
        ClaimVerifier 会用这个校验 LLM 输出里引用的条款号。

        实现示例:
          return ref_id in self._index_of_all_clauses
        """
        ...

    def parse_citation(self, text: str) -> List[str]:
        """
        从任意文本里抽取所有权威引用(返回 ref_id 列表)

        某业务抽: "XX 规范-2021 第 6.3.4 条" → "XX 规范-2021#6.3.4"
        法律抽: "《民法典》第 188 条" → "民法典#188"

        默认实现返回空列表,领域可重写(用 regex 或 NER)
        """
        return []

    def get_reference(self, ref_id: str) -> Optional[AuthorityRef]:
        """按 id 查单条(可选实现,默认 None)"""
        return None

    def format_citations(self, refs: List[AuthorityRef]) -> str:
        """
        把一组引用格式化成人类可读的引用串
        默认:"依据《X》第 Y 条、《Z》第 W 条"
        """
        if not refs:
            return ""
        parts = [r.to_citation() for r in refs]
        return "依据" + "、".join(parts)

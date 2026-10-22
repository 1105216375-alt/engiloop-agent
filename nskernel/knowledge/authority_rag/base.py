"""
AuthorityRAGBase · 权威源检索基类
===================================

领域无关的权威引用检索框架,实现 AuthoritySchema 契约的常用部分。

设计:
  - 内存索引:{ref_id → AuthorityRef} 字典
  - 默认检索:基于关键词匹配 + 简易 BM25-like 打分
  - 领域可注入更复杂的检索器(向量/ES)

这是 MVP 级实现,生产环境可继承这个类,重写 retrieve 接入向量库。
"""
from __future__ import annotations
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from nskernel.contracts import (
        AuthoritySchema, AuthorityRef,
        RetrievalQuery, RetrievalResult,
    )


class AuthorityRAGBase:
    """
    权威源检索基类 - 内存索引版

    领域用法:
      rag = AuthorityRAGBase()
      rag.add(AuthorityRef(ref_id="民法典#188", source_name="民法典",
                           section="第 188 条", text="..."))
      refs = rag.retrieve(RetrievalQuery(query_text="诉讼时效"))

    可以配合 AuthoritySchema 实现使用:
      class LawAuthority(AuthoritySchema):
          def __init__(self):
              self._rag = AuthorityRAGBase()
              # 加载法条到 _rag
          def retrieve(self, query):
              return self._rag.retrieve(query)
          def is_known_reference(self, ref_id):
              return self._rag.contains(ref_id)
    """

    def __init__(self):
        self._index: Dict[str, "AuthorityRef"] = {}

    # -----------------------------------------------------------------
    # 索引管理
    # -----------------------------------------------------------------
    def add(self, ref: "AuthorityRef") -> None:
        if ref and ref.ref_id:
            self._index[ref.ref_id] = ref

    def add_batch(self, refs: List["AuthorityRef"]) -> None:
        for r in refs:
            self.add(r)

    def remove(self, ref_id: str) -> bool:
        if ref_id in self._index:
            del self._index[ref_id]
            return True
        return False

    def contains(self, ref_id: str) -> bool:
        return ref_id in self._index

    def get(self, ref_id: str) -> Optional["AuthorityRef"]:
        return self._index.get(ref_id)

    def size(self) -> int:
        return len(self._index)

    # -----------------------------------------------------------------
    # 检索
    # -----------------------------------------------------------------
    def retrieve(self, query: "RetrievalQuery") -> "RetrievalResult":
        """
        默认实现:关键词匹配 + 简易打分

        每个 ref 的打分 = query 关键词在 text/title/section 里出现的次数
        """
        from nskernel.contracts import RetrievalResult

        q_text = (query.query_text or "").strip()
        q_keywords = [k for k in (query.topic_keywords or []) if k]
        # 如果没给 keywords,从 query_text 里切简单 bigram
        if not q_keywords and q_text:
            q_keywords = [q_text]

        if not q_keywords:
            return RetrievalResult(refs=[], total_matched=0)

        scored = []
        for ref in self._index.values():
            blob = " ".join([
                ref.text or "",
                ref.title or "",
                ref.section or "",
                ref.source_name or "",
            ])
            score = sum(blob.count(k) for k in q_keywords if k in blob)
            if score > 0:
                scored.append((score, ref))

        scored.sort(key=lambda x: -x[0])
        top_refs = [r for _, r in scored[:query.top_k]]

        return RetrievalResult(
            refs=top_refs,
            total_matched=len(scored),
            query_expanded_to=q_keywords,
        )

    # -----------------------------------------------------------------
    # 实用方法
    # -----------------------------------------------------------------
    def list_all_ref_ids(self) -> List[str]:
        return list(self._index.keys())

    def list_by_source(self, source_name: str) -> List["AuthorityRef"]:
        return [r for r in self._index.values() if r.source_name == source_name]

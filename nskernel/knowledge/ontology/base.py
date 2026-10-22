"""
OntologyBase · 同义词本体基类
==============================

回答:"用户说'写小说'、'编故事'、'作小说',这三个词指同一件事吗?"

垂域 AI 里同义词归一(canonicalization)至关重要:
  - 用户口语/行话多,本体不一 → 检索/校验全失效
  - 规则引擎、KG、权威检索都依赖 canonical 名
  - 这是 input-side grounding 的基础

设计:
  - 数据源:canonical → [aliases] 字典(领域自定义)
  - 核心接口:canonicalize(text) → canonical 名(或 None)
  - 扩展:fuzzy_canonicalize 支持编辑距离兜底(可选)

领域实现示例(伪代码):
  class LawOntology(OntologyBase):
      def __init__(self):
          super().__init__({
              "盗窃罪": ["偷窃", "盗取", "顺手牵羊"],
              "诈骗罪": ["骗钱", "欺诈", "诈骗"],
              "诉讼时效": ["告的时间", "还能告吗", "时效"],
          })

  ontology = LawOntology()
  ontology.canonicalize("偷窃") == "盗窃罪"
"""
from __future__ import annotations
from typing import Dict, List, Optional, Set


class OntologyBase:
    """
    通用本体基类

    维护 canonical ↔ aliases 的双向映射,提供归一查询
    """

    def __init__(self, definitions: Optional[Dict[str, List[str]]] = None):
        """
        Args:
          definitions: {canonical_name: [alias, alias, ...]}
        """
        self._canonical_to_aliases: Dict[str, Set[str]] = {}
        self._alias_to_canonical: Dict[str, str] = {}

        for canonical, aliases in (definitions or {}).items():
            self.register(canonical, aliases)

    # -----------------------------------------------------------------
    # 注册/管理
    # -----------------------------------------------------------------
    def register(self, canonical: str, aliases: Optional[List[str]] = None) -> None:
        """
        注册一个 canonical + 别名组
        canonical 自己也算作别名(指向自己)
        """
        if not canonical:
            return
        aset = self._canonical_to_aliases.setdefault(canonical, set())
        # canonical 自己
        aset.add(canonical)
        self._alias_to_canonical[canonical] = canonical
        # 别名们
        for a in (aliases or []):
            if a:
                aset.add(a)
                self._alias_to_canonical[a] = canonical

    def unregister(self, canonical: str) -> None:
        """移除一个 canonical(及其所有别名)"""
        aliases = self._canonical_to_aliases.pop(canonical, set())
        for a in aliases:
            if self._alias_to_canonical.get(a) == canonical:
                del self._alias_to_canonical[a]

    # -----------------------------------------------------------------
    # 查询
    # -----------------------------------------------------------------
    def canonicalize(self, text: str) -> Optional[str]:
        """
        精确归一:text 必须是已注册的 canonical 或 alias 之一
        返回对应 canonical 名(或 None)
        """
        if not text:
            return None
        return self._alias_to_canonical.get(text.strip())

    def fuzzy_canonicalize(
        self, text: str,
        substring: bool = True,
    ) -> Optional[str]:
        """
        模糊归一:
          substring=True 时,text 包含某个别名就归入该别名的 canonical
                         (短别名优先匹配,避免"主梁"错匹到"梁"又错匹到"主梁")
        适用于 NLU 抽取:"你这个主梁斜裂缝" → 包含"斜裂缝" → canonicalize
        """
        if not text:
            return None
        text = text.strip()
        hit = self.canonicalize(text)
        if hit:
            return hit

        if substring:
            # 按别名长度降序,避免短别名吃掉长别名的匹配
            aliases_sorted = sorted(
                self._alias_to_canonical.keys(),
                key=len, reverse=True,
            )
            for alias in aliases_sorted:
                if len(alias) >= 2 and alias in text:
                    return self._alias_to_canonical[alias]
        return None

    def get_aliases(self, canonical: str) -> Set[str]:
        """取 canonical 对应的全部别名(包含自己)"""
        return set(self._canonical_to_aliases.get(canonical, set()))

    def list_canonicals(self) -> List[str]:
        """全部注册的 canonical 列表"""
        return list(self._canonical_to_aliases.keys())

    def are_equivalent(self, a: str, b: str) -> bool:
        """a 和 b 是否等价(指向同一个 canonical)"""
        ca = self.canonicalize(a)
        cb = self.canonicalize(b)
        return ca is not None and ca == cb

    def size(self) -> Dict[str, int]:
        """统计信息"""
        return {
            "canonicals": len(self._canonical_to_aliases),
            "total_aliases": sum(len(s) for s in self._canonical_to_aliases.values()),
        }

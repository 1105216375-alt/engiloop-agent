"""
桥梁病害知识图谱(Agent 13 · KnowledgeGraph)

节点类型:
  - Component:具体构件,如"主梁""盖梁""支座"
  - Category:大类,如"上部结构""下部结构""桥面系"
  - Disease:病害,如"裂缝""混凝土破损""支座脱空"
  - Clause:规范条款,如 ("JTG 5120-2021", "第4.3.2条")
  - Measure:处治措施
  - Cause:成因

边类型:
  - CONTAINS:           Category → Component
  - HAS_DISEASE:        Component → Disease
  - CAUSED_BY:          Disease → Cause
  - TREATED_BY:         Disease → Measure
  - GOVERNED_BY:        (Component, Disease) → Clause
                        ※ 关键边:双重锚定,治掉"问盖梁引到拱桥"的脏数据问题

设计原则:
  1. 启动时一次性从现有 JSON 离线加载,内存图,无外部依赖
  2. 图查询失败时,调用方自行回退到原有关键词检索作为 fallback
  3. 提供解释性查询 explain_path,支持"为什么引这条?"
"""
import os
import json
from collections import defaultdict
from typing import List, Set, Tuple, Dict, Optional

from . import ontology


# ──────────────────────────────────────────────────────────
# 构件同义词族(从 ontology 自动生成)
# 用于把规范条文挂到图上时的"构件命中"判断
# ──────────────────────────────────────────────────────────
COMPONENT_KEYWORDS = ontology.build_kg_component_keywords()

# 病害词根(从 ontology 自动生成)
DISEASE_KEYWORDS = {
    canonical: [canonical] + info.get("aliases", [])
    for canonical, info in ontology._DISEASE_DEFS.items()
}

# 通用噪声标志:出现这些词通常表示是养护检查通则,不是病害专项条款
NOISE_MARKERS = [
    "养护检查等级",
    "经常检查每月",
    "定期检查每",
    "桥面系的检查应包括",
    "桥面铺装层纵、横坡是否",
]


# ──────────────────────────────────────────────────────────
# 节点 / 边 数据结构
# ──────────────────────────────────────────────────────────
class Node:
    __slots__ = ("type", "key", "props")

    def __init__(self, type_: str, key, props=None):
        self.type = type_
        self.key = key  # 唯一标识
        self.props = props or {}

    def __repr__(self):
        return f"<{self.type}:{self.key}>"

    def __hash__(self):
        return hash((self.type, self._hashable_key()))

    def __eq__(self, other):
        return (isinstance(other, Node)
                and self.type == other.type
                and self._hashable_key() == other._hashable_key())

    def _hashable_key(self):
        if isinstance(self.key, list):
            return tuple(self.key)
        return self.key


class Edge:
    __slots__ = ("source", "rel", "target", "props")

    def __init__(self, source: Node, rel: str, target: Node, props=None):
        self.source = source
        self.rel = rel
        self.target = target
        self.props = props or {}

    def __repr__(self):
        return f"({self.source}) -[{self.rel}]-> ({self.target})"


# ──────────────────────────────────────────────────────────
# 知识图谱主类
# ──────────────────────────────────────────────────────────
class BridgeKnowledgeGraph:
    """
    桥梁病害知识图谱。

    使用方式:
      kg = BridgeKnowledgeGraph()
      kg.build()                                    # 离线建图
      clauses = kg.find_clauses("盖梁", "混凝土破损")  # 图遍历查询
      path = kg.explain_path("盖梁", "混凝土破损")    # 可解释路径
    """

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.base_dir = base_dir

        # 节点存储:type → key → Node
        self._nodes: Dict[str, Dict] = defaultdict(dict)
        # 出边索引:(source_type, source_key) → list[Edge]
        self._out_edges: Dict[Tuple, List[Edge]] = defaultdict(list)
        # 入边索引:(target_type, target_key) → list[Edge]
        self._in_edges: Dict[Tuple, List[Edge]] = defaultdict(list)

        self._built = False
        self.stats = {}

    # ─────────────── 节点 / 边 操作 ───────────────
    def add_node(self, type_: str, key, **props) -> Node:
        if isinstance(key, list):
            key = tuple(key)
        existing = self._nodes[type_].get(key)
        if existing:
            existing.props.update(props)
            return existing
        node = Node(type_, key, props)
        self._nodes[type_][key] = node
        return node

    def add_edge(self, source: Node, rel: str, target: Node, **props):
        edge = Edge(source, rel, target, props)
        s_key = (source.type, source._hashable_key())
        t_key = (target.type, target._hashable_key())
        self._out_edges[s_key].append(edge)
        self._in_edges[t_key].append(edge)

    def get_node(self, type_: str, key) -> Optional[Node]:
        if isinstance(key, list):
            key = tuple(key)
        return self._nodes[type_].get(key)

    def out_edges(self, node: Node, rel: Optional[str] = None) -> List[Edge]:
        edges = self._out_edges.get((node.type, node._hashable_key()), [])
        if rel:
            return [e for e in edges if e.rel == rel]
        return edges

    def in_edges(self, node: Node, rel: Optional[str] = None) -> List[Edge]:
        edges = self._in_edges.get((node.type, node._hashable_key()), [])
        if rel:
            return [e for e in edges if e.rel == rel]
        return edges

    # ─────────────── 建图 ───────────────
    def build(self) -> "BridgeKnowledgeGraph":
        """从 expert_kb.json + regulation_index.json + scoring_disease_db.json 离线建图"""
        if self._built:
            return self

        self._load_expert_kb()
        self._load_scoring_db()
        self._load_regulations_and_link()
        self._built = True

        # 统计
        self.stats = {
            "构件节点": len(self._nodes.get("Component", {})),
            "病害节点": len(self._nodes.get("Disease", {})),
            "条款节点": len(self._nodes.get("Clause", {})),
            "大类节点": len(self._nodes.get("Category", {})),
            "处治节点": len(self._nodes.get("Measure", {})),
            "成因节点": len(self._nodes.get("Cause", {})),
            "HAS_DISEASE 边": sum(
                1 for k, es in self._out_edges.items()
                if k[0] == "Component"
                for e in es if e.rel == "HAS_DISEASE"
            ),
            "GOVERNED_BY 边": sum(
                1 for es in self._out_edges.values()
                for e in es if e.rel == "GOVERNED_BY"
            ),
        }
        print(f"📊 知识图谱已构建：{self.stats}")
        return self

    def _load_expert_kb(self):
        """从 expert_kb.json 直接建立 Component → Disease → (Cause/Measure) 边"""
        path = os.path.join(self.base_dir, "data", "expert_kb.json")
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            kb = json.load(f)

        for item in kb:
            comp_name = (item.get("components") or "").strip()
            disease_name_raw = (item.get("defect_name") or "").strip()
            category_name = (item.get("category") or "").strip()
            if not comp_name or not disease_name_raw:
                continue

            # Round 34: 强制规范化 — 把"腹板斜裂缝"→"斜向裂缝","底板纵向裂缝"→"纵向裂缝"
            # 用 ontology canonical 名建 KG 边,这样 user query 用 canonical 名就能找到
            disease_name = ontology.canonicalize_disease(disease_name_raw) or disease_name_raw

            # 节点(同时建一个原始名节点作为别名,方便溯源)
            comp_node = self.add_node("Component", comp_name)
            disease_node = self.add_node("Disease", disease_name)

            # Category
            if category_name:
                cat_node = self.add_node("Category", category_name)
                self.add_edge(cat_node, "CONTAINS", comp_node)

            # Component → Disease
            self.add_edge(comp_node, "HAS_DISEASE", disease_node)

            # 成因
            for cause in item.get("causes", []) or []:
                cause_node = self.add_node("Cause", cause[:60])
                self.add_edge(disease_node, "CAUSED_BY", cause_node)

            # 处治措施
            measures = item.get("measures") or {}
            if isinstance(measures, dict):
                for level, mlist in measures.items():
                    if not isinstance(mlist, list):
                        continue
                    for m in mlist:
                        measure_node = self.add_node("Measure", m[:80])
                        self.add_edge(disease_node, "TREATED_BY", measure_node, level=level)

    def _load_scoring_db(self):
        """从 scoring_disease_db.json 补充大类→构件的层级映射"""
        path = os.path.join(self.base_dir, "data", "scoring_disease_db.json")
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            sdb = json.load(f)

        # key 形如 "下部结构-盖梁" 或 "桥面系" 或 "支座" 或 "破损"(病害评定表)
        for key, val in sdb.items():
            if "-" in key:
                # 大类-构件 形式
                cat, comp = key.split("-", 1)
                cat_node = self.add_node("Category", cat.strip())
                comp_node = self.add_node("Component", comp.strip())
                self.add_edge(cat_node, "CONTAINS", comp_node)
                # 该构件的常见病害
                if isinstance(val, dict):
                    for disease_raw in val:
                        # 跳过纯数字 key(那是评分等级标度,不是病害)
                        if disease_raw.isdigit():
                            continue
                        # Round 34: 规范化 disease 名
                        disease = ontology.canonicalize_disease(disease_raw) or disease_raw
                        d_node = self.add_node("Disease", disease)
                        self.add_edge(comp_node, "HAS_DISEASE", d_node)

    def _load_regulations_and_link(self):
        """加载规范条款,并自动挂边到 (Component, Disease),边带权重"""
        path = os.path.join(self.base_dir, "data", "regulation_index.json")
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        # clause_index 是 病害名 → 条款列表 的倒排索引
        # 把所有条款"展平"出来去重,然后逐条挂边
        all_clauses = {}
        for bucket_clauses in data.get("clause_index", {}).values():
            for c in bucket_clauses:
                cid = (c.get("reg", ""), c.get("clause", ""))
                if cid not in all_clauses:
                    all_clauses[cid] = c

        for cid, clause in all_clauses.items():
            reg, clause_num = cid
            title = clause.get("title", "") or ""
            content = clause.get("content", "") or clause.get("summary", "") or ""
            text = title + " " + content

            # 创建 Clause 节点
            clause_node = self.add_node(
                "Clause",
                cid,
                reg=reg,
                clause_num=clause_num,
                title=title[:120],
                content=content,
            )

            # 噪声判断:命中通则标志的条文权重打折,但不完全过滤
            is_noise = any(marker in text for marker in NOISE_MARKERS)

            # 找出文本里命中的所有"构件"和"病害",分别记录命中位置(标题/正文)
            hit_components: Dict[str, str] = {}  # comp → "title" or "content"
            for comp, kws in COMPONENT_KEYWORDS.items():
                if any(kw in title for kw in kws):
                    hit_components[comp] = "title"
                elif any(kw in content for kw in kws):
                    hit_components[comp] = "content"

            hit_diseases: Dict[str, str] = {}
            for disease, kws in DISEASE_KEYWORDS.items():
                if any(kw in title for kw in kws):
                    hit_diseases[disease] = "title"
                elif any(kw in content for kw in kws):
                    hit_diseases[disease] = "content"

            # 双重命中才挂边(治掉单维污染)
            if not (hit_components and hit_diseases):
                continue

            for comp, comp_pos in hit_components.items():
                comp_node = self.add_node("Component", comp)
                for disease, disease_pos in hit_diseases.items():
                    disease_node = self.add_node("Disease", disease)

                    # ── 边权重打分 ──
                    weight = 0
                    # 构件命中加分
                    weight += 10 if comp_pos == "title" else 4
                    # 病害命中加分
                    weight += 5 if disease_pos == "title" else 2
                    # 章节号偏好:第4章(养护与维修) > 第5章(评定) > 第3章(检查)
                    if clause_num.startswith("第4."):
                        weight += 6
                    elif clause_num.startswith("第5.") or clause_num.startswith("第9."):
                        weight += 4
                    elif clause_num.startswith("第3."):
                        weight -= 1
                    # 噪声标志降权
                    if is_noise:
                        weight -= 5
                    # 特殊桥型(拱桥/悬索/斜拉)的检查条款一般不主语于通用构件
                    if any(t in title for t in ["拱桥", "悬索桥", "斜拉桥", "抗震性能评价"]):
                        weight -= 6

                    self.add_edge(
                        comp_node,
                        "GOVERNED_BY",
                        clause_node,
                        disease=disease,
                        weight=weight,
                        comp_pos=comp_pos,
                        disease_pos=disease_pos,
                    )

    # ─────────────── 查询 API ───────────────
    def find_clauses(self, component: str, disease: str,
                     top_k: int = 5, min_weight: int = 1) -> List[Dict]:
        """
        图遍历查询:给定 (构件, 病害) → 找相关规范条款,按边权重排序。

        参数:
          component: 构件名
          disease: 病害名
          top_k: 返回前 N 条
          min_weight: 最低权重阈值,低于此值的边会被丢弃

        返回: List[Dict],每条包含 reg/clause/title/content/match/weight
        """
        comp_node = self.get_node("Component", component)
        if not comp_node:
            return []

        # 收集所有边并打分
        candidates = []  # List[(weight, match_type, edge)]
        seen = set()

        for edge in self.out_edges(comp_node, "GOVERNED_BY"):
            edge_disease = edge.props.get("disease", "")
            weight = edge.props.get("weight", 0)
            cid = edge.target.key
            if cid in seen:
                # 同一条款被多次连入(多病害命中),保留权重最高的那次
                for i, (w, m, e) in enumerate(candidates):
                    if e.target.key == cid and weight > w:
                        candidates[i] = (weight, m, edge)
                        break
                continue

            if edge_disease == disease:
                candidates.append((weight, "exact", edge))
                seen.add(cid)
            elif (edge_disease and disease and
                  (edge_disease in disease or disease in edge_disease) and
                  len(edge_disease) >= 2 and len(disease) >= 2):
                # 病害词根模糊匹配,权重打折
                candidates.append((weight - 2, "fuzzy_disease", edge))
                seen.add(cid)

        # 过滤低权重 + 排序
        candidates = [(w, m, e) for (w, m, e) in candidates if w >= min_weight]
        candidates.sort(key=lambda x: -x[0])

        results = []
        for weight, match_type, edge in candidates[:top_k]:
            results.append({
                "reg": edge.target.props.get("reg", ""),
                "clause": edge.target.props.get("clause_num", ""),
                "title": edge.target.props.get("title", ""),
                "content": edge.target.props.get("content", ""),
                "match": match_type,
                "weight": weight,
                "_source": "graph",
            })
        return results

    def explain_path(self, component: str, disease: str) -> List[str]:
        """
        可解释性查询:给定 (构件, 病害),返回推理路径的人类可读描述。
        例如:
          - 用户问的是 [盖梁] 的 [混凝土破损]
          - 知识图谱中 [盖梁] 属于 [下部结构]
          - [盖梁] 已知会发生 [混凝土破损]
          - [混凝土破损] 的成因有: ...
          - [混凝土破损] 的处治措施有: ...
          - 适用规范条款: JTG xxx 第 x.x 条

        Round 34: 入口先把 disease 名规范化(腹板斜裂缝 → 斜向裂缝),
                  避免 KG 边用 canonical 而 query 用 raw 导致的"未登记"误报
        """
        # 规范化 disease(把工程师写法的"腹板斜裂缝"→"斜向裂缝")
        disease_canon = ontology.canonicalize_disease(disease) or disease
        # 规范化 component
        comp_canon = ontology.canonicalize_component(component) or component

        lines = [f"诊断对象:{comp_canon} · {disease_canon}"]

        comp_node = self.get_node("Component", comp_canon)
        if not comp_node:
            # 尝试用 raw 名再查一次(向后兼容老的 KG 节点)
            comp_node = self.get_node("Component", component)
            if not comp_node:
                lines.append(f"  · 知识图谱中未收录构件 [{component}]")
                return lines

        # 大类(去重)
        cats = list({e.source.key for e in self.in_edges(comp_node, "CONTAINS")})
        if cats:
            lines.append(f"  · 构件分类:{comp_canon} 属于 {' / '.join(cats)}")

        # 病害是否在该构件的已知病害集合里(用 canonical 查)
        has_this_disease = False
        for edge in self.out_edges(comp_node, "HAS_DISEASE"):
            edge_disease_canon = ontology.canonicalize_disease(edge.target.key) or edge.target.key
            if edge_disease_canon == disease_canon or edge.target.key == disease:
                has_this_disease = True
                break

        if has_this_disease:
            # 显示受力裂缝标签(如果是受力缝)
            extra = ""
            if ontology.is_load_bearing_crack(disease_canon):
                extra = "(受力裂缝)"
            lines.append(f"  · {comp_canon} 已知会发生 [{disease_canon}]{extra}")
        else:
            lines.append(f"  · 警告:知识图谱中 {comp_canon} 未登记 [{disease_canon}],可能为新型病害或表述不规范")

        # 适用条款
        clauses = self.find_clauses(comp_canon, disease_canon)
        if clauses:
            lines.append(f"  · 适用规范条款({len(clauses)} 条):")
            for c in clauses:
                lines.append(f"      - {c['reg']} {c['clause']}")
        else:
            lines.append(f"  · 知识图谱中未挂载直接适用条款")

        return lines

    # 调试
    def dump_summary(self) -> str:
        out = ["=== 知识图谱摘要 ==="]
        for k, v in self.stats.items():
            out.append(f"  {k}: {v}")
        return "\n".join(out)

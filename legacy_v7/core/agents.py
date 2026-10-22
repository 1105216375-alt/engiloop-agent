"""
桥梁医生 · 智能体集合

Agent 3 · 统计智能体：对诊断结果做多维统计汇总
Agent 4 · 校验审核智能体：对低置信度/异常结果做 LLM 二次审核
Agent 5 · 规范 RAG 智能体：检索 JTG 规范条文佐证诊断结论
"""

import json
import os
from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional

from . import scoring_config as config
from . import ontology


# ══════════════════════════════════════════════
# Agent 3 · 统计智能体
# ══════════════════════════════════════════════

class StatsAgent:
    """
    对诊断结果做多维统计：
    - 按构件分布、按等级分布、按紧急程度分布
    - 高风险病害清单
    - 严控区占比
    - 统计摘要文本（给 LLM 和用户看）
    """

    def analyze(self, results: List[Dict]) -> Dict[str, Any]:
        if not results:
            return {"summary": "无诊断数据", "stats": {}}

        total = len(results)

        # 按等级分布
        level_dist = Counter()
        for r in results:
            level_dist[f"{r.get('标度', 0)}类"] += 1

        # 按构件分布
        comp_dist = Counter(r.get("构件", "未知") for r in results)

        # 按病害类型分布
        disease_dist = Counter(r.get("病害", "未知") for r in results)

        # 按紧急程度分布
        urgency_dist = Counter(r.get("紧急程度", "低") for r in results)

        # 高风险（评分>=60）
        high_risk = [r for r in results if r.get("风险评分", 0) >= 60]

        # 严控区
        critical = [r for r in results if r.get("严控区")]

        # 平均风险评分
        scores = [r.get("风险评分", 0) for r in results]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0
        max_score = max(scores) if scores else 0

        # 最危险的 top3
        top3 = sorted(results, key=lambda x: x.get("风险评分", 0), reverse=True)[:3]

        stats = {
            "总数": total,
            "等级分布": dict(level_dist.most_common()),
            "构件分布": dict(comp_dist.most_common()),
            "病害分布": dict(disease_dist.most_common(10)),
            "紧急程度分布": dict(urgency_dist.most_common()),
            "高风险数": len(high_risk),
            "严控区数": len(critical),
            "平均风险评分": avg_score,
            "最高风险评分": max_score,
            "最危险top3": [
                f"{r.get('构件', '')}·{r.get('病害', '')}={r.get('标度', '')}类({r.get('风险评分', 0)}分)"
                for r in top3
            ],
        }

        # 生成文字摘要
        summary_parts = [
            f"共诊断 {total} 条病害",
            f"高风险 {len(high_risk)} 条",
            f"严控区 {len(critical)} 条",
            f"平均风险 {avg_score} 分，最高 {max_score} 分",
        ]

        level_str = "、".join(f"{k}{v}条" for k, v in level_dist.most_common())
        if level_str:
            summary_parts.append(f"等级分布：{level_str}")

        comp_str = "、".join(f"{k}{v}条" for k, v in comp_dist.most_common(5))
        if comp_str:
            summary_parts.append(f"构件分布：{comp_str}")

        stats["summary"] = "；".join(summary_parts)
        return stats


# ══════════════════════════════════════════════
# Agent 4 · 校验审核智能体
# ══════════════════════════════════════════════

class ReviewAgent:
    """
    对诊断结果做质量审核，标记需要人工复核的条目：
    1. 置信度为 manual（知识库未命中）的条目
    2. 病害等级=0（自动标度未匹配到规则）的条目
    3. 处治措施与病害类型不匹配的异常检测
    4. 如果有 LLM，对可疑条目做二次判断
    """

    def __init__(self, ai_client=None):
        self._ai = ai_client

    def review(self, results: List[Dict]) -> Dict[str, Any]:
        flags = []
        passed = 0

        for i, r in enumerate(results):
            issues = []

            # 1. 知识库未命中
            if r.get("置信度") == "manual":
                issues.append("知识库未命中，处治措施为兜底建议")

            # 2. 自动标度未匹配
            if r.get("标度", 0) == 0 or r.get("DP值", -1) == 0 and r.get("标度", 0) > 1:
                issues.append(f"DP值为0但标度为{r.get('标度')}类，可能评定规则未覆盖")

            # 3. 严控区但紧急程度低
            if r.get("严控区") and r.get("紧急程度") == "低":
                issues.append("严控区（受力裂缝）但紧急程度为低，建议复核")

            # 4. 高标度但低风险评分
            if r.get("标度", 0) >= 4 and r.get("风险评分", 0) < 40:
                issues.append(f"标度{r.get('标度')}类但风险评分仅{r.get('风险评分')}，评分可能偏低")

            if issues:
                flags.append({
                    "序号": i + 1,
                    "构件": r.get("构件", ""),
                    "病害": r.get("病害", ""),
                    "问题": issues,
                })
            else:
                passed += 1

        # LLM 二次审核（仅对有问题的条目，且 LLM 可用时）
        llm_review = ""
        if flags and self._ai and self._ai.client:
            llm_review = self._llm_review(flags, results)

        return {
            "通过数": passed,
            "待复核数": len(flags),
            "待复核明细": flags,
            "LLM审核意见": llm_review,
            "summary": f"审核完成：{passed}条通过，{len(flags)}条待复核"
                + (f"。AI补充意见：{llm_review[:100]}..." if llm_review else ""),
        }

    def _llm_review(self, flags, results):
        """让 LLM 对可疑条目给出专业意见"""
        flag_text = ""
        for f in flags[:5]:  # 最多审5条
            flag_text += f"- {f['构件']}·{f['病害']}：{'；'.join(f['问题'])}\n"

        sys_prompt = (
            "你是桥梁检测质量审核专家。以下病害诊断结果被系统标记为可疑，"
            "请逐条给出你的专业判断：是否确实需要人工复核，以及可能的原因。简洁回答。"
        )
        return self._ai._call_zhipu(sys_prompt, flag_text)


# ══════════════════════════════════════════════
# Agent 5 · 规范 RAG 智能体
# ══════════════════════════════════════════════

class RegulationRAGAgent:
    """
    规范 RAG 智能体：根据病害类型检索相关 JTG 规范条文。

    公开版由调用方显式注入条款索引和图谱；不自动读取应用目录。
    本仓库不分发规范全文，不声明内置条款为当前有效标准。
    """

    def __init__(self, ai_client=None, *, clause_index=None, graph=None):
        self._ai = ai_client
        self._clause_index = dict(clause_index or {})
        self._regulations = []
        self._kg = graph

    def _load_index(self):
        """显式加载已授权规则包；公开版禁止当前工作目录隐式兜底。"""
        if not os.environ.get("ENGINEERING_RULES_DIR", "").strip():
            raise ValueError("显式加载规则包需配置 ENGINEERING_RULES_DIR")
        # 候选路径列表,按优先级排
        candidates = [
            os.path.join(config.BASE_DIR, "data", "regulation_index.json"),
            os.path.join(config.BASE_DIR, "data", "regulation_52XX2026_index.json"),
            os.path.join(config.BASE_DIR, "database_regulations.json"),
        ]
        index_path = None
        for p in candidates:
            if os.path.exists(p):
                index_path = p
                break

        if index_path is None:
            print(f"⚠️  规范索引未找到,尝试了 {len(candidates)} 个候选路径,RAG 功能受限")
            print(f"    候选路径: {candidates[:3]}")
            return

        try:
            with open(index_path, encoding="utf-8") as f:
                data = json.load(f)
            self._clause_index = data.get("clause_index", {})
            self._regulations = data.get("regulations", [])
            total = sum(len(v) for v in self._clause_index.values())
            print(f"📖 规范索引已加载：{len(self._regulations)} 部规范,"
                  f"{total} 条引用 (from {os.path.basename(index_path)})")
        except Exception as e:
            print(f"⚠️  规范索引加载失败 from {index_path}: {e}")
            self._clause_index = {}
            self._regulations = []

    # ── 相关性打分配置 ──
    # 构件名 → 该构件相关的语义词族(从 ontology 自动生成)
    _COMPONENT_KEYWORDS = ontology.build_kg_component_keywords()

    # 通用噪声标志:出现在条文里通常表示这是养护检查通则,不是病害专项条款
    _NOISE_MARKERS = [
        "养护检查等级", "经常检查每月", "定期检查",
        "拱桥", "悬索桥", "斜拉桥",  # 特殊桥型,跟"盖梁混凝土破损"无关
        "桥面系的检查", "桥面铺装层纵",
    ]

    def _score_clause(self, clause: Dict, disease: str, component: str = None) -> int:
        """
        给单条规范条文打相关性分数。
        分数越高越相关。负分会被丢弃。
        """
        title = clause.get("title", "") or ""
        content = clause.get("content", "") or clause.get("summary", "") or ""
        text = title + " " + content

        score = 0

        # ── 构件维度 ──
        if component:
            # 先把 component 归一化到 canonical(沥青铺装/桥墩/...)
            canonical = ontology.canonicalize_component(component) or component
            comp_keywords = self._COMPONENT_KEYWORDS.get(canonical, [canonical])
            comp_hit = any(kw in text for kw in comp_keywords)
            if comp_hit:
                score += 10
            else:
                # 没命中构件名,看是否命中"通用结构"词族
                # 例如"钢筋混凝土"对所有混凝土构件都通用
                generic_hit = any(kw in text for kw in ["钢筋混凝土", "预应力混凝土", "混凝土结构"])
                if generic_hit:
                    score += 2
                else:
                    score -= 3  # 既无构件名又无通用结构词,大概率不相关

        # ── 病害维度 ──
        if disease and disease in text:
            score += 5
        # 病害的核心字根(如"破损""裂缝")
        disease_root = disease.replace("混凝土", "").replace("斜向", "").replace("纵向", "").replace("横向", "")
        if disease_root and disease_root != disease and disease_root in text:
            score += 2

        # ── 噪声惩罚 ──
        for marker in self._NOISE_MARKERS:
            if marker in text:
                score -= 6

        # ── 章节号偏好:养护类病害条款一般在第 4 章(各类桥梁养护与维修)──
        clause_num = clause.get("clause", "")
        if clause_num.startswith("第4."):
            score += 3
        # 第3.4 / 3.5.x 一般是"检查内容"通则,在病害问答场景里相关性低
        if any(clause_num.startswith(f"第3.{n}") for n in ("4", "5")):
            score -= 2

        return score

    def retrieve(self, disease_name: str, component: str = None, top_k: int = 5) -> List[Dict]:
        """
        根据病害名 + 构件名检索相关规范条文,按相关性排序。

        新策略(多层验证):
          1. 优先走知识图谱(精准、可解释、按 (构件,病害) 双重锚定)
          2. 图查询无结果时,回退到关键词倒排索引 + 打分排序

        参数:
          disease_name: 病害名,如 "混凝土破损"
          component: 构件名,如 "盖梁"。提供后会显著提升精度。
          top_k: 最多返回多少条。默认 5。
        """
        # ── 第 1 优先级:知识图谱 ──
        if self._kg is not None and component:
            kg_results = self._kg.find_clauses(component, disease_name, top_k=top_k)
            if kg_results:
                return kg_results

        # ── 第 2 优先级:关键词倒排 + 打分排序(原算法) ──
        # 召回
        candidates = []
        seen = set()

        def _add(clauses):
            for c in clauses:
                ident = f"{c.get('reg', '')}|{c.get('clause', '')}"
                if ident not in seen:
                    candidates.append(c)
                    seen.add(ident)

        if disease_name in self._clause_index:
            _add(self._clause_index[disease_name])

        for key, clauses in self._clause_index.items():
            if len(key) < 2:
                continue
            if key == disease_name:
                continue
            if (key in disease_name and len(key) >= 3) or (disease_name in key and len(disease_name) >= 3):
                _add(clauses)

        if not candidates:
            return []

        # 排序
        scored = [(self._score_clause(c, disease_name, component), c) for c in candidates]
        scored = [(s, c) for s, c in scored if s > 0]
        scored.sort(key=lambda x: -x[0])

        # 标记 source 为 keyword,与图结果区分
        out = []
        for _, c in scored[:top_k]:
            c2 = dict(c)
            c2["_source"] = "keyword"
            out.append(c2)
        return out

    def explain(self, component: str, disease: str) -> str:
        """
        可解释性查询:返回 (构件, 病害) 在知识图谱里的推理路径。
        如果图未加载或查询失败,返回空字符串。
        """
        if self._kg is None or not component:
            return ""
        try:
            lines = self._kg.explain_path(component, disease)
            return "\n".join(lines)
        except Exception:
            return ""

    def retrieve_for_results(self, results: List[Dict]) -> Dict[str, List[Dict]]:
        """
        为一批诊断结果检索规范条文。
        关键改动:把构件信息也传进去,实现"构件 × 病害"双维检索。
        """
        all_refs = {}
        for r in results:
            disease = r.get("病害", "")
            component = r.get("构件", "")
            if not disease:
                continue
            # key 用 "构件·病害" 避免不同构件的同名病害互相覆盖
            key = f"{component}·{disease}" if component else disease
            if key in all_refs:
                continue
            refs = self.retrieve(disease, component=component)
            if refs:
                all_refs[key] = refs
        return all_refs

    def format_references(self, refs: Dict[str, List[Dict]]) -> str:
        """格式化规范引用文本"""
        if not refs:
            return ""
        parts = []
        for key, clauses in refs.items():
            clause_strs = []
            for c in clauses:
                reg = c.get("reg", "")
                clause = c.get("clause", "")
                title = c.get("title", "")
                content = c.get("content", c.get("summary", ""))
                clause_strs.append(f"  · {reg} {clause}《{title}》：{content[:150]}")
            parts.append(f"【{key}】\n" + "\n".join(clause_strs))
        return "\n\n".join(parts)

    def enrich_with_regulations(self, question: str, results: List[Dict],
                                 conversation_context: str = "") -> str:
        """用 LLM 将诊断结果与规范条文结合，生成带规范引用的专业回复。"""
        refs = self.retrieve_for_results(results)
        ref_text = self.format_references(refs)

        if not ref_text:
            return ""

        if not self._ai or not self._ai.client:
            return f"\n📖 相关规范依据：\n{ref_text}"

        sys_prompt = (
            "你是桥梁养护规范专家。请结合以下JTG规范条文和诊断数据，"
            "对处治方案进行规范性论证。引用具体条款号，说明为什么推荐这种处治方式。简洁专业。"
        )

        data_parts = []
        for r in results[:5]:
            data_parts.append(f"{r.get('构件', '')}·{r.get('病害', '')}={r.get('标度', '')}类")

        user_msg = f"诊断概况：{'; '.join(data_parts)}\n\n规范条文：\n{ref_text}"
        return self._ai._call_zhipu(sys_prompt, user_msg)

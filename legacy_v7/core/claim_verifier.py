"""
Agent 15 · ClaimVerifier(输出侧符号验证器)

这是多层验证系统的"硬约束闭环":LLM 自由生成自然语言回复后,
本 Agent 用符号侧的权威数据(ontology / 知识图谱 / 当前 result)
逐句校验 LLM 输出里的所有"可机械判定的原子主张",删除或修正错误的句子。

定位:Kautz LingJing 6 层分类的 Layer 6(neural-symbolic loop)的输出侧实现。
之前 ontology 重构已经做了 input-side grounding(确保 LLM 看到的数据干净),
本 Agent 做 output-side verification(确保 LLM 输出的内容干净)。

6 类 claim 抽取 + 验证:
  1. 禁用词模式       → 乐园/小病小痛/跨界 等历史 bug 词直接删 (forbidden)
  2. 桥梁等级幻觉      → 用户没提的"I/II级桥梁"概念直接删 (hallucination)
  3. 规范条款引用     → 查知识图谱是否存在 + 是否对应当前 (构件×病害) (clause)
  4. 数值断言         → 评定级/DP值/风险评分必须跟 result 里的实际值匹配 (number)
  5. 严控区/相关性    → 提到严控区时实际必须有 (relevance, soft)
  6. 构件一致性       → 用户问 X,LLM 不能列非 X 构件(题文一致性)(component_mismatch) ⭐ 新增

错误等级:
  HARD  → 句子直接删除
  SOFT  → 句子保留但加 ⚠️ 标记
  PASS  → 完全通过
"""
import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from . import ontology
from bip_runtime.claim_grounding import check_diagnostic_counts, check_diagnostic_numbers


REJECTED_OUTPUT = "生成内容未通过证据核验，已停止展示原文。请核对原始资料后再生成。"


# ──────────────────────────────────────────────────────────
# 配置:禁用词模式
# ──────────────────────────────────────────────────────────

# 历史 bug 词(口语化/不专业/边界外推/油腻)
FORBIDDEN_PATTERNS = [
    r"乐园",
    r"小病小痛",
    r"哎呀",
    r"小毛病",
    r"跨界",
    r"超出范围",
    r"超出我的",
    r"无法回答",
    r"这个问题不",
    r"作为AI",
    r"作为一个AI",
    r"作为人工智能",
]

# 禁止凭空引入的概念(用户没提就不能出现)
HALLUCINATION_PATTERNS = {
    r"[IⅠ一]级桥梁": "桥梁等级",
    r"[IIⅡ二]级桥梁": "桥梁等级",
    r"[IIIⅢ三]级桥梁": "桥梁等级",
    r"特大桥": "桥型规模",
    r"高速公路桥": "公路等级",
    r"一级公路": "公路等级",
}


# ──────────────────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────────────────

@dataclass
class VerificationIssue:
    """单个验证问题"""
    severity: str          # "hard" or "soft"
    category: str          # "clause" / "component" / "disease" / "number" / "hallucination" / "forbidden"
    sentence: str          # 出问题的整句
    detail: str            # 描述

@dataclass
class VerificationReport:
    """单次校验的完整报告"""
    original: str
    cleaned: str
    issues: List[VerificationIssue] = field(default_factory=list)
    deleted_count: int = 0
    flagged_count: int = 0

    def is_clean(self) -> bool:
        return len(self.issues) == 0


# ──────────────────────────────────────────────────────────
# 主类
# ──────────────────────────────────────────────────────────

class ClaimVerifier:
    """
    使用方式:
      verifier = ClaimVerifier(rag_agent=rag_agent)
      report = verifier.verify(summary, results, original_query)
      cleaned_summary = report.cleaned
    """

    def __init__(self, rag_agent=None):
        """
        rag_agent: 可选的 RegulationRAGAgent 实例,用于查知识图谱
                   不传也能用,只是规范条款验证会降级为"格式合法性"检查
        """
        self._rag = rag_agent
        # 提取 LLM 知道的所有合法规范号(用于格式后还要查存在性)
        self._all_clauses_index = None
        if rag_agent and hasattr(rag_agent, '_clause_index'):
            self._all_clauses_index = rag_agent._clause_index

    # ──────────────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────────────

    def verify(
        self,
        summary: str,
        results: List[Dict],
        original_query: str = "",
    ) -> VerificationReport:
        """
        校验 LLM 输出,返回净化后的文本和报告。
        """
        if not summary:
            return VerificationReport(original="", cleaned="")

        # 收集 result 里的"事实集合"用于对账
        facts = self._collect_facts(results)
        bound_issues = (
            check_diagnostic_numbers(summary, results, original_query=original_query)
            + check_diagnostic_counts(summary, results, original_query=original_query)
        )

        # 按句子拆分(中文句号、感叹号、问号、换行)
        sentences = self._split_sentences(summary)

        all_issues = []
        kept_sentences = []
        deleted = 0
        flagged = 0
        offset = 0

        for sent in sentences:
            sentence_start = offset
            offset += len(sent)
            if not sent.strip():
                kept_sentences.append(sent)
                continue

            issues = self._verify_sentence(sent, facts, original_query)
            for issue in bound_issues:
                if sentence_start <= issue["start"] < offset:
                    issues.append(VerificationIssue(
                        severity="hard", category="number", sentence=sent,
                        detail=(f"{issue['scope']}的{issue['metric']}={issue['asserted']}缺少对应证据"
                                f"(依据:{issue['expected']}; {issue['reason']})"),
                    ))

            if not issues:
                kept_sentences.append(sent)
                continue

            # 任何 hard 问题 → 删除整句
            has_hard = any(i.severity == "hard" for i in issues)
            if has_hard:
                deleted += 1
                all_issues.extend(issues)
                # 不保留这个句子
            else:
                # 只有 soft → 保留并加 ⚠️ 标记
                flagged += 1
                soft_marks = "/".join(i.category for i in issues)
                kept_sentences.append(f"{sent} ⚠️[{soft_marks}]")
                all_issues.extend(issues)

        cleaned = "".join(kept_sentences).strip()

        return VerificationReport(
            original=summary,
            cleaned=cleaned,
            issues=all_issues,
            deleted_count=deleted,
            flagged_count=flagged,
        )

    # ──────────────────────────────────────────────────────
    # 事实收集
    # ──────────────────────────────────────────────────────

    def _collect_facts(self, results: List[Dict]) -> Dict:
        """
        从 results 里抽取"已知事实",用于跟 LLM 输出对账。
        """
        facts = {
            "components": set(),       # 用户实际诊断的构件(canonical)
            "diseases": set(),         # 用户实际诊断的病害(canonical)
            "levels": set(),           # 评定标度集合,如 {2, 4}
            "dp_values": set(),        # DP 值
            "risk_scores": set(),      # 风险评分
            "avg_risk_score": None,    # 统计口径:平均风险
            "max_risk_score": None,    # 统计口径:最高风险
            "is_critical": False,      # 是否有任一进入严控区
            "result_count": len(results),
        }
        risk_numbers = []
        for r in results:
            comp = r.get("构件", "")
            dis = r.get("病害", "")
            if comp:
                facts["components"].add(ontology.canonicalize_component(comp) or comp)
            if dis:
                facts["diseases"].add(ontology.canonicalize_disease(dis) or dis)

            level = r.get("标度")
            if level is not None:
                facts["levels"].add(int(level))

            dp = r.get("DP值")
            if dp is not None:
                facts["dp_values"].add(int(dp))

            risk = r.get("风险评分")
            if risk is not None:
                # 风险评分允许小数,记录到 0.5 精度
                risk_value = round(float(risk), 1)
                facts["risk_scores"].add(risk_value)
                risk_numbers.append(risk_value)

            if r.get("严控区"):
                facts["is_critical"] = True
        if risk_numbers:
            facts["avg_risk_score"] = round(sum(risk_numbers) / len(risk_numbers), 1)
            facts["max_risk_score"] = round(max(risk_numbers), 1)
        return facts

    # ──────────────────────────────────────────────────────
    # 分句
    # ──────────────────────────────────────────────────────

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """
        按中文标点分句,但**保留分隔符**(以便 join 还原)
        实现:用正则在标点后切分,标点跟在前面句子末尾
        """
        # 不切分换行(让段落结构保持)
        # 切分点:。!?;以及它们的全角变体后面
        parts = re.split(r"([。!?;\n])", text)
        # parts 是 [s1, sep1, s2, sep2, ...]
        sentences = []
        i = 0
        while i < len(parts):
            sent = parts[i]
            sep = parts[i + 1] if i + 1 < len(parts) else ""
            sentences.append(sent + sep)
            i += 2
        return sentences

    # ──────────────────────────────────────────────────────
    # 单句验证
    # ──────────────────────────────────────────────────────

    def _verify_sentence(
        self,
        sentence: str,
        facts: Dict,
        original_query: str,
    ) -> List[VerificationIssue]:
        """对单个句子做 5 类校验,返回所有发现的问题"""
        issues = []

        # ── 1. 禁用词模式 ──
        for pat in FORBIDDEN_PATTERNS:
            m = re.search(pat, sentence)
            if m:
                issues.append(VerificationIssue(
                    severity="hard",
                    category="forbidden",
                    sentence=sentence,
                    detail=f"包含禁用词:{m.group(0)}",
                ))
                # 一个禁用词就够,不重复报
                break

        # ── 2. 幻觉概念(用户没提就不能出现) ──
        for pat, concept_name in HALLUCINATION_PATTERNS.items():
            m = re.search(pat, sentence)
            if m and m.group(0) not in original_query and concept_name not in original_query:
                issues.append(VerificationIssue(
                    severity="hard",
                    category="hallucination",
                    sentence=sentence,
                    detail=f"凭空引入了'{concept_name}'概念(用户原文没提):{m.group(0)}",
                ))
                break

        # ── 3. 规范条款引用校验 ──
        # 匹配形如 "JTG 5120-2021 第4.3条" / "JTG/T H21-2011 第9.1.2条"
        clause_pattern = r"(JTG[/T\s]*[A-Z]?\s*\d+[-—]\d+)\s*第\s*([\d.]+)\s*条"
        for m in re.finditer(clause_pattern, sentence):
            reg = m.group(1).strip()
            clause_num = "第" + m.group(2) + "条"
            if not self._verify_clause_exists(reg, clause_num):
                issues.append(VerificationIssue(
                    severity="hard",
                    category="clause",
                    sentence=sentence,
                    detail=f"引用了不存在的条款:{reg} {clause_num}",
                ))
                continue
            # 进一步:这条款是否对应当前 (构件×病害)?
            if facts["components"] and facts["diseases"]:
                if not self._verify_clause_relevance(reg, clause_num, facts):
                    issues.append(VerificationIssue(
                        severity="soft",
                        category="clause",
                        sentence=sentence,
                        detail=f"条款 {reg} {clause_num} 可能与当前构件/病害不直接对应",
                    ))

        # Numeric assertions are checked in verify() with their own component,
        # disease and aggregation scope, not with the former global value sets.

        # 4d. 严控区/普控区
        explicitly_zero_critical = bool(
            re.search(r"严控区\s*(?:为|共|有)?\s*0\s*(?:条|处|项|个)?", sentence)
            or re.search(r"(?:没有|未发现|无)\s*[^。；;\n]{0,8}严控区", sentence)
        )
        critical_assertion = bool(
            re.search(
                r"(?:存在|发现|出现|包含|进入|落入|命中|属于|达到|共有|有)"
                r"[^。；;\n]{0,12}严控区",
                sentence,
            )
            or re.search(
                r"严控区(?:病害)?\s*(?:为|共|有|达到|出现|存在|包含)?\s*[1-9]\d*\s*"
                r"(?:条|处|项|个)?",
                sentence,
            )
        )
        if critical_assertion and not facts["is_critical"] and not explicitly_zero_critical:
            # 只核验“存在/数量”断言；“建议复核严控区”属于处置动作，不代表当前已命中。
            issues.append(VerificationIssue(
                severity="soft",
                category="number",
                sentence=sentence,
                detail="提到'严控区'但实际诊断中无严控区病害",
            ))

        # ── 5. 构件名硬幻觉(出现了一个 ontology 里完全没有的"构件式"词) ──
        # 这一条比较保守,只检查很显眼的不存在构件
        # 实际上太严会误伤,这里跳过细粒度检查
        # (如果未来需要,可以加 NER 抽构件名再 ontology 校验)

        # ── 6. 构件一致性校验(关键新增 - Layer 6 第 6 类硬约束) ──
        # 修"用户问主梁,LLM 答了伸缩缝"的 bug
        # 算法:
        #   a) 用 ontology 抽取 original_query 里提到的构件
        #   b) 抽取 sentence 里提到的构件
        #   c) 如果用户提了构件 X,而 sentence 里出现的构件**包含非 X 的其他构件** → 删该句
        # 这是题文一致性的最终防线
        comp_issue = self._check_component_consistency(sentence, original_query, facts)
        if comp_issue:
            issues.append(comp_issue)

        return issues

    # ──────────────────────────────────────────────────────
    # 第 6 类:构件一致性校验
    # ──────────────────────────────────────────────────────

    def _check_component_consistency(
        self,
        sentence: str,
        original_query: str,
        facts: Dict,
    ) -> Optional["VerificationIssue"]:
        """
        如果用户在 query 里指定了构件 X,而 sentence 里列出了非 X 的其他构件,
        判定为题文不一致,严重度 hard,返回 VerificationIssue。
        否则返回 None。

        关键防御:不能因为 sentence 出现"主梁"两个字就误伤(用户可能就在问主梁,
        sentence 里出现是合理的)。逻辑必须是 — query 提了 X,sentence 出现了 Y(且 Y ≠ X)。
        """
        from .component_scope import extract_mentioned_components

        query_comps = extract_mentioned_components(original_query)
        if not query_comps:
            return None  # 用户没指定构件,跳过这一项

        sent_comps = extract_mentioned_components(sentence)
        if not sent_comps:
            return None  # 句子里没提构件,可能是描述性句子,跳过

        # 检查 sent 里有没有 query 没提的构件
        query_set = set(query_comps)
        violators = [c for c in sent_comps if c not in query_set]

        if not violators:
            return None  # 完全一致,通过

        return VerificationIssue(
            severity="hard",
            category="component_mismatch",
            sentence=sentence,
            detail=(
                f"用户问的是 {'/'.join(query_comps)},但本句出现了非目标构件 "
                f"{'/'.join(violators)} — 题文不一致"
            ),
        )

    # ──────────────────────────────────────────────────────
    # 规范条款验证
    # ──────────────────────────────────────────────────────

    # 已知规范的白名单(降级模式用)
    KNOWN_REGULATIONS = {
        "JTG 5120-2021", "JTG/T J21-2011", "JTG/T J21-01-2015",
        "JTG 5220-2020", "JTG/T J22-2008", "JTG/T 5214-2022",
        "JTG/T H21-2011",
        # R60: 新增 2025 规范(2026-04-01 实施)
        "JTG/T 5431-2025", "JTG/T 5531-2025",
    }

    def _verify_clause_exists(self, reg: str, clause_num: str) -> bool:
        """检查 (reg, clause_num) 是否在规范库里真实存在"""
        if self._all_clauses_index:
            # 完整模式:在所有 clause buckets 里找精确匹配
            for bucket in self._all_clauses_index.values():
                for c in bucket:
                    if (c.get("reg", "").strip() == reg and
                            c.get("clause", "").strip() == clause_num):
                        return True
            return False
        # 降级模式:至少要规范号在白名单里(挡住"JTG 9999-9999"这种)
        # 兼容空格归一化
        reg_norm = reg.replace(" ", "").replace("/", "/")
        for known in self.KNOWN_REGULATIONS:
            if known.replace(" ", "") == reg_norm:
                return True
        return False

    def _verify_clause_relevance(
        self, reg: str, clause_num: str, facts: Dict
    ) -> bool:
        """
        检查条款是否与当前 (构件 × 病害) 相关。
        策略:用知识图谱 find_clauses 查 (构件,病害),只在 top_5 + weight>5 里才算相关
        (太宽松会让"广义匹中"的不相关条款也通过)
        """
        if not self._rag or not hasattr(self._rag, '_kg') or self._rag._kg is None:
            return True  # 没图谱,跳过相关性检查
        kg = self._rag._kg
        for comp in facts["components"]:
            for dis in facts["diseases"]:
                try:
                    clauses = kg.find_clauses(comp, dis, top_k=5)
                except Exception:
                    continue
                for c in clauses:
                    weight = c.get("weight", 0)
                    if weight < 5:
                        continue
                    if (c.get("reg", "").strip() == reg and
                            c.get("clause", "").strip() == clause_num):
                        return True
        return False

    # ──────────────────────────────────────────────────────
    # 调试 / 报告渲染
    # ──────────────────────────────────────────────────────

    @staticmethod
    def render_report(report: VerificationReport) -> str:
        """把 report 渲染成可读文本(给 debug 或日志用)"""
        if report.is_clean():
            return "[ClaimVerifier] 全部通过"
        lines = [f"[ClaimVerifier] 删除 {report.deleted_count} 句, 标记 {report.flagged_count} 句"]
        for issue in report.issues:
            mark = "❌" if issue.severity == "hard" else "⚠️"
            lines.append(f"  {mark} [{issue.category}] {issue.detail}")
        return "\n".join(lines)

"""
ClaimVerifier · 输出侧符号验证器(通用骨架)
============================================

位置:neural-symbolic loop 的输出侧闭环
  - input-side grounding:  ontology/KG 让 LLM 看到干净数据(已有)
  - output-side verification: 本模块让 LLM 输出的内容被符号层校验 ⭐

工作流:
  LLM 生成回复 → 按句切分 → 每句抽 claim → 查符号源校验 →
  HARD 错误 → 删句; SOFT 错误 → 加⚠️; PASS → 保留

6 类 claim 抽取(原型系统+另一个参考实现沉淀的 pattern,已领域无关化):
  1. forbidden      禁用词(油腻用语"乐园"、"小病小痛"、"作为 AI"等)
  2. hallucination_authority   引用了不存在的权威条款号
  3. hallucination_entity       引用了上下文里不存在的实体
  4. hallucination_term         用了领域外术语(domain 说不该出现的)
  5. number_mismatch           数字与硬数据不符
  6. grade_mismatch            评级/等级词与实际评分不匹配

架构:
  - ClaimVerifier 是"抽 + 判 + 清"的骨架,自己不知道具体规则
  - 具体规则由 ClaimRule(可插拔)实现,每条规则决定自己抓什么、判什么
  - 领域可以注册自己的 ClaimRule(比如某业务"领域归因铁律")
"""
from __future__ import annotations
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Pattern, TYPE_CHECKING

if TYPE_CHECKING:
    from nskernel.contracts import AuthoritySchema, DomainProfile


# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════

class Severity(Enum):
    HARD = "hard"     # 严重错误,直接删句
    SOFT = "soft"     # 可疑,保留但加标记
    PASS = "pass"     # 通过

    @property
    def should_remove(self) -> bool:
        return self == Severity.HARD

    @property
    def should_mark(self) -> bool:
        return self == Severity.SOFT


@dataclass
class VerificationIssue:
    """一个 claim 验证失败产生的问题"""
    rule_name: str                       # 哪条规则抓到的
    severity: Severity                   # HARD / SOFT
    sentence: str                        # 出问题的句子
    reason: str                          # 人类可读的理由
    evidence: Dict[str, Any] = field(default_factory=dict)
                                         # 相关证据(命中词/期望值等)


@dataclass
class VerificationReport:
    """一次 verify 的完整报告"""
    original_text: str
    cleaned_text: str                    # 清理后的文本(删了 HARD 句 + 加了 SOFT 标)
    issues: List[VerificationIssue] = field(default_factory=list)
    sentences_total: int = 0
    sentences_removed: int = 0
    sentences_marked: int = 0

    @property
    def is_clean(self) -> bool:
        return len(self.issues) == 0

    def summary(self) -> str:
        if self.is_clean:
            return "✓ 校验通过"
        return (f"校验发现 {len(self.issues)} 个问题 "
                f"(删 {self.sentences_removed} 句 / 标 {self.sentences_marked} 句)")


# ══════════════════════════════════════════════════════════════
# ClaimRule - 规则插件接口
# ══════════════════════════════════════════════════════════════
class ClaimRule(ABC):
    """
    一条 claim 规则 - 决定"抓什么、怎么判"

    每条规则只做一件事,实现 check(sentence, context) → List[VerificationIssue]
    """

    rule_name: str = "base_rule"
    default_severity: Severity = Severity.HARD

    @abstractmethod
    def check(
        self,
        sentence: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[VerificationIssue]:
        """
        校验一个句子
        context 字段由 ClaimVerifier 构造传入,包含:
          - question: 用户当前问题
          - observations: 投影后的硬数据
          - authority_schema: 权威源(用于 hallucination_authority 规则)
          - 其他领域自定义字段
        """
        ...


# ══════════════════════════════════════════════════════════════
# 内置规则 1:禁用词(领域无关)
# ══════════════════════════════════════════════════════════════
class ForbiddenWordsRule(ClaimRule):
    """检测 LLM 常见的油腻/套话词"""

    rule_name = "forbidden_words"
    default_severity = Severity.HARD

    # 通用禁用词(跨所有领域)
    DEFAULT_PATTERNS = [
        r"作为\s*AI", r"作为\s*一个\s*AI", r"作为\s*人工智能",
        r"无法回答", r"这个问题不", r"超出我的",
        r"乐园", r"小病小痛", r"小毛病",     # 原型系统沉淀的经验
        r"哎呀",
    ]

    def __init__(self, extra_patterns: Optional[List[str]] = None):
        patterns = list(self.DEFAULT_PATTERNS) + list(extra_patterns or [])
        self._compiled = [re.compile(p) for p in patterns]

    def check(self, sentence, context=None):
        issues = []
        for pat in self._compiled:
            m = pat.search(sentence)
            if m:
                issues.append(VerificationIssue(
                    rule_name=self.rule_name,
                    severity=Severity.HARD,
                    sentence=sentence,
                    reason=f"含禁用词 '{m.group(0)}'",
                    evidence={"pattern": pat.pattern, "match": m.group(0)},
                ))
        return issues


# ══════════════════════════════════════════════════════════════
# 内置规则 2:幻觉权威引用(需要 AuthoritySchema)
# ══════════════════════════════════════════════════════════════
class HallucinationAuthorityRule(ClaimRule):
    """
    检测 LLM 编造的权威引用(法条号/规范号/教材章节)

    依赖:
      - AuthoritySchema.is_known_reference(ref_id) 校验是否存在
      - AuthoritySchema.parse_citation(text) 从句子里抽引用

    领域可以往 context["authority_schema"] 里塞实现,本规则自动启用
    """

    rule_name = "hallucination_authority"
    default_severity = Severity.HARD

    def check(self, sentence, context=None):
        authority = (context or {}).get("authority_schema")
        if authority is None:
            return []

        refs = []
        try:
            refs = authority.parse_citation(sentence) or []
        except Exception:
            return []

        issues = []
        for ref_id in refs:
            try:
                exists = authority.is_known_reference(ref_id)
            except Exception:
                continue
            if not exists:
                issues.append(VerificationIssue(
                    rule_name=self.rule_name,
                    severity=Severity.HARD,
                    sentence=sentence,
                    reason=f"引用了不存在的权威条目 '{ref_id}'",
                    evidence={"ref_id": ref_id},
                ))
        return issues


# ══════════════════════════════════════════════════════════════
# 内置规则 3:数字匹配(需要 observations 里的权威数字)
# ══════════════════════════════════════════════════════════════
class NumberMismatchRule(ClaimRule):
    """
    检测 LLM 说的关键数值是否与 observations 里的硬数据一致。

    策略:
      context["truth_numbers"] 是一个 dict:
        {"风险评分": 47.5, "DP": 45, ...}
      本规则在句子里寻找"风险评分 X 分"这种 pattern,如果 X 跟
      truth_numbers 对不上 → HARD(数字不符在严肃垂域里是严重事故)

    这个规则是示例性的;真正的 pattern 由领域注册扩展
    """

    rule_name = "number_mismatch"
    default_severity = Severity.HARD

    def __init__(self, patterns: Optional[List[Dict]] = None):
        r"""
        patterns 格式:
          [{
             "regex": r"风险评分\s*([\d.]+)\s*分",
             "truth_key": "风险评分",
             "tolerance": 0.5,  # 允许误差
          }]
        """
        self._patterns = []
        for p in (patterns or []):
            self._patterns.append({
                "regex": re.compile(p["regex"]),
                "truth_key": p["truth_key"],
                "tolerance": p.get("tolerance", 0.01),
            })

    def check(self, sentence, context=None):
        truth = (context or {}).get("truth_numbers") or {}
        if not truth or not self._patterns:
            return []

        issues = []
        for p in self._patterns:
            m = p["regex"].search(sentence)
            if not m:
                continue
            try:
                llm_val = float(m.group(1))
            except (ValueError, IndexError):
                continue
            truth_val = truth.get(p["truth_key"])
            if truth_val is None:
                continue
            try:
                truth_f = float(truth_val)
            except (TypeError, ValueError):
                continue
            if abs(llm_val - truth_f) > p["tolerance"]:
                issues.append(VerificationIssue(
                    rule_name=self.rule_name,
                    severity=Severity.HARD,
                    sentence=sentence,
                    reason=f"{p['truth_key']}数值不符:LLM说 {llm_val},实际 {truth_f}",
                    evidence={
                        "llm_value": llm_val,
                        "truth_value": truth_f,
                        "tolerance": p["tolerance"],
                    },
                ))
        return issues


# ══════════════════════════════════════════════════════════════
# 内置规则 4:实体串扰(用户问 A,LLM 讲 B)
# ══════════════════════════════════════════════════════════════
class EntityCrossContaminationRule(ClaimRule):
    """
    用户 active_entity 是 A,但 LLM 回复里提到了 B(之前讨论过但不是本次作用域)
    → 串扰警告

    依赖 context["active_entity"] 和 context["other_entity_keys"]
    """

    rule_name = "entity_cross_contamination"
    default_severity = Severity.HARD

    def check(self, sentence, context=None):
        ctx = context or {}
        active = ctx.get("active_entity")
        other_keys = ctx.get("other_entity_keys") or []
        if not active or not other_keys:
            return []

        issues = []
        for other in other_keys:
            if other and other in sentence and other != active:
                issues.append(VerificationIssue(
                    rule_name=self.rule_name,
                    severity=Severity.HARD,
                    sentence=sentence,
                    reason=f"作用域是 [{active}],但提到了其他实体 [{other}]",
                    evidence={"active": active, "contaminated_with": other},
                ))
        return issues


# ══════════════════════════════════════════════════════════════
# ClaimVerifier - 主入口
# ══════════════════════════════════════════════════════════════
class ClaimVerifier:
    """
    通用 claim 验证器

    用法:
      verifier = ClaimVerifier()
      verifier.add_rule(ForbiddenWordsRule())
      verifier.add_rule(HallucinationAuthorityRule())

      report = verifier.verify(text, context={...})
      cleaned = verifier.clean_text(text, context={...})
    """

    def __init__(self, rules: Optional[List[ClaimRule]] = None):
        self._rules: List[ClaimRule] = list(rules or [])

    def add_rule(self, rule: ClaimRule) -> "ClaimVerifier":
        self._rules.append(rule)
        return self

    def verify(
        self,
        text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationReport:
        """全文校验,返回详细报告"""
        sentences = self._split_sentences(text)
        cleaned_sentences = []
        all_issues = []
        removed = 0
        marked = 0

        for sent in sentences:
            if not sent.strip():
                cleaned_sentences.append(sent)
                continue

            sent_issues = []
            for rule in self._rules:
                try:
                    sent_issues.extend(rule.check(sent, context))
                except Exception as e:
                    # 规则自己出 bug 不应该影响其他规则
                    continue

            if not sent_issues:
                cleaned_sentences.append(sent)
                continue

            # 取最高严重度
            max_sev = max(i.severity for i in sent_issues)
            all_issues.extend(sent_issues)

            if max_sev.should_remove:
                # HARD → 删句
                removed += 1
                # 用空字符串替换,后续 join 时会保留原结构(标点消失)
                cleaned_sentences.append("")
            elif max_sev.should_mark:
                # SOFT → 加标
                marked += 1
                cleaned_sentences.append(sent + " ⚠️")
            else:
                cleaned_sentences.append(sent)

        cleaned_text = "".join(cleaned_sentences)
        # 清理连续空句产生的标点冗余
        cleaned_text = re.sub(r"[。.!?;;\s]{3,}", " ", cleaned_text).strip()

        return VerificationReport(
            original_text=text,
            cleaned_text=cleaned_text,
            issues=all_issues,
            sentences_total=sum(1 for s in sentences if s.strip()),
            sentences_removed=removed,
            sentences_marked=marked,
        )

    def clean_text(
        self,
        text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """便捷方法:只返回清理后的文本"""
        return self.verify(text, context).cleaned_text

    # -----------------------------------------------------------------
    # 句子切分
    # -----------------------------------------------------------------
    def _split_sentences(self, text: str) -> List[str]:
        """
        中文句子切分(保留标点便于还原)

        规则:
          - 在 。!?;!?; 处切(中文标点是明确的句子结束符)
          - 英文 . 需要上下文判断:前后若是数字则是小数点,不切
          - 保留标点(切分后每个 sentence 都含尾部标点)
        """
        if not text:
            return []

        # 先把"小数点"保护起来(数字.数字 → 数字PROTECTED_DOT数字)
        import re as _re
        PROTECTED = "\x01"  # 不会出现在正常文本的字符
        protected_text = _re.sub(
            r"(\d)\.(\d)",
            lambda m: m.group(1) + PROTECTED + m.group(2),
            text,
        )

        # 在中文句末标点+英文 .!? 处切
        parts = _re.split(r"(?<=[。!?;;!?])\s*", protected_text)

        # 还原小数点
        parts = [p.replace(PROTECTED, ".") for p in parts]
        return [p for p in parts if p]
